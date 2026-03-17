from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request
from uuid import UUID

SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
FORBIDDEN_SQL_RE = re.compile(
    r"\b(insert|update|delete|alter|drop|truncate|create|grant|revoke|"
    r"comment|copy|call|do|vacuum|merge)\b",
    re.IGNORECASE,
)

HttpJsonCallable = Callable[..., dict[str, Any]]
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})
SUPPORTED_CHAT_TYPES = frozenset({"private", "group", "supergroup"})
GROUP_LISTEN_MODES = frozenset({"mentioned", "all"})
BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"


@cache
def load_prompt_file(filename: str) -> str:
    """Load a prompt file once and keep it cached in memory."""
    path = PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Prompt file not found: {path}") from exc


GYM_SCHEMA_DDL = load_prompt_file("gym_schema_ddl.md")
SYSTEM_PROMPT_TEMPLATE = load_prompt_file("system_prompt.md")
CONVERSATION_SUMMARY_PROMPT_TEMPLATE = load_prompt_file(
    "conversation_summary_prompt.md"
)


def load_dotenv(path: Path) -> None:
    """Load .env values into process environment without external dependencies."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        cleaned_value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), cleaned_value)


def parse_chat_id_allowlist(raw_value: str | None) -> frozenset[int]:
    """Parse a comma-separated list of Telegram chat IDs."""
    if not raw_value:
        return frozenset()

    chat_ids: set[int] = set()
    for part in raw_value.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            parsed_chat_id = int(stripped)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid TELEGRAM_ALLOWED_CHAT_IDS entry: {stripped}"
            ) from exc
        chat_ids.add(parsed_chat_id)
        digits = str(abs(parsed_chat_id))
        if parsed_chat_id < 0 and len(digits) >= 10 and not digits.startswith("100"):
            chat_ids.add(int(f"-100{digits}"))
    return frozenset(chat_ids)


def parse_group_listen_mode(raw_value: str | None) -> str:
    """Validate the group listen mode from .env."""
    mode = (raw_value or "mentioned").strip().lower()
    if mode not in GROUP_LISTEN_MODES:
        supported = ", ".join(sorted(GROUP_LISTEN_MODES))
        raise RuntimeError(
            f"Invalid TELEGRAM_GROUP_LISTEN_MODE '{mode}'. Use one of: {supported}."
        )
    return mode


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    telegram_group_listen_mode: str
    telegram_allowed_chat_ids: frozenset[int]
    openrouter_api_key: str
    openrouter_model: str
    openrouter_base_url: str
    openrouter_timeout_seconds: int
    telegram_poll_timeout_seconds: int
    retry_delay_seconds: int
    openrouter_http_referer: str
    openrouter_app_title: str
    gym_db_host: str
    gym_db_port: int
    gym_db_name: str
    gym_db_user: str
    gym_db_password: str
    gym_db_sslmode: str
    gym_db_connect_timeout_seconds: int
    context_db_host: str | None
    context_db_port: int
    context_db_name: str | None
    context_db_user: str | None
    context_db_password: str | None
    context_db_sslmode: str
    context_db_connect_timeout_seconds: int
    context_history_messages: int
    context_summary_trigger_messages: int
    context_summary_keep_recent_messages: int
    max_tool_iterations: int

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "Settings":
        load_dotenv(Path(env_path))

        required = (
            "TELEGRAM_BOT_TOKEN",
            "OPENROUTER_API_KEY",
            "OPENROUTER_MODEL",
            "GYM_DB_HOST",
            "GYM_DB_NAME",
            "GYM_DB_USER",
            "GYM_DB_PASSWORD",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            missing_list = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {missing_list}")

        return cls(
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_group_listen_mode=parse_group_listen_mode(
                os.getenv("TELEGRAM_GROUP_LISTEN_MODE")
            ),
            telegram_allowed_chat_ids=parse_chat_id_allowlist(
                os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
            ),
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            openrouter_model=os.environ["OPENROUTER_MODEL"],
            openrouter_base_url=os.getenv(
                "OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ).rstrip("/"),
            openrouter_timeout_seconds=int(
                os.getenv("OPENROUTER_TIMEOUT_SECONDS", "60")
            ),
            telegram_poll_timeout_seconds=int(
                os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "20")
            ),
            retry_delay_seconds=int(os.getenv("RETRY_DELAY_SECONDS", "3")),
            openrouter_http_referer=os.getenv(
                "OPENROUTER_HTTP_REFERER",
                "https://smartass-bot.local",
            ),
            openrouter_app_title=os.getenv(
                "OPENROUTER_APP_TITLE",
                "Smartass SQL Bot",
            ),
            gym_db_host=os.environ["GYM_DB_HOST"],
            gym_db_port=int(os.getenv("GYM_DB_PORT", "5432")),
            gym_db_name=os.environ["GYM_DB_NAME"],
            gym_db_user=os.environ["GYM_DB_USER"],
            gym_db_password=os.environ["GYM_DB_PASSWORD"],
            gym_db_sslmode=os.getenv("GYM_DB_SSLMODE", "prefer"),
            gym_db_connect_timeout_seconds=int(
                os.getenv("GYM_DB_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            context_db_host=os.getenv("CONTEXT_DB_HOST"),
            context_db_port=int(os.getenv("CONTEXT_DB_PORT", "5432")),
            context_db_name=os.getenv("CONTEXT_DB_NAME"),
            context_db_user=os.getenv("CONTEXT_DB_USER"),
            context_db_password=os.getenv("CONTEXT_DB_PASSWORD"),
            context_db_sslmode=os.getenv("CONTEXT_DB_SSLMODE", "disable"),
            context_db_connect_timeout_seconds=int(
                os.getenv("CONTEXT_DB_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            context_history_messages=int(os.getenv("CONTEXT_HISTORY_MESSAGES", "8")),
            context_summary_trigger_messages=int(
                os.getenv("CONTEXT_SUMMARY_TRIGGER_MESSAGES", "20")
            ),
            context_summary_keep_recent_messages=int(
                os.getenv("CONTEXT_SUMMARY_KEEP_RECENT_MESSAGES", "8")
            ),
            max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "6")),
        )


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Send an HTTP request and parse a JSON response."""
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error for {url}: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}: {raw}") from exc


def normalize_openrouter_content(content: Any) -> str:
    """Normalize OpenRouter message content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts).strip()

    return str(content).strip()


def normalize_json_value(value: Any) -> Any:
    """Convert database values to JSON-serializable primitives."""
    if isinstance(value, dict):
        return {str(key): normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, (datetime, date, dt_time, Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def build_system_prompt(
    today: date | None = None,
    language_hint: str | None = None,
) -> str:
    """Build a compact prompt for SQL generation and final response synthesis."""
    current_date = (today or date.today()).isoformat()
    locale_hint = language_hint or "unknown"
    return SYSTEM_PROMPT_TEMPLATE.format(
        current_date=current_date,
        language_hint=locale_hint,
        gym_schema_ddl=GYM_SCHEMA_DDL,
    )


def build_conversation_summary_prompt(conversation_summary: str) -> str:
    """Build the system message that injects summarized older context."""
    return CONVERSATION_SUMMARY_PROMPT_TEMPLATE.format(
        conversation_summary=conversation_summary.strip()
    )


def build_tools() -> list[dict[str, Any]]:
    """Define the tool schema exposed to OpenRouter."""
    return [
        {
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": (
                    "Execute one read-only PostgreSQL query against the smartass "
                    "database and return rows from the gym schema."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": (
                                "One read-only PostgreSQL SELECT or WITH query. "
                                "Must reference gym.* tables explicitly."
                            ),
                        }
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def extract_sql(text: str) -> str:
    """Extract SQL from a plain response or a fenced code block."""
    match = SQL_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def validate_sql(sql: str) -> str:
    """Ensure the generated SQL is read-only and single-statement."""
    cleaned = extract_sql(sql).strip()
    if not cleaned:
        raise ValueError("OpenRouter returned an empty response.")

    if cleaned.startswith("--"):
        return cleaned

    statement = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    lowered = statement.lower()

    if ";" in statement:
        raise ValueError("Only one SQL statement is allowed.")
    if FORBIDDEN_SQL_RE.search(statement):
        raise ValueError("Mutating SQL detected in model output.")
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("SQL must start with SELECT or WITH.")

    return f"{statement};"


def trim_for_telegram(text: str, limit: int = 3900) -> str:
    """Keep the response below Telegram's message length limit."""
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


@dataclass(slots=True)
class TelegramMessage:
    update_id: int
    message_id: int
    chat_id: int
    chat_type: str
    text: str
    language_code: str | None = None
    reply_to_message_id: int | None = None
    message_thread_id: int | None = None
    mentions_bot: bool = False
    is_reply_to_bot: bool = False


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        poll_timeout_seconds: int,
        *,
        group_listen_mode: str = "mentioned",
        allowed_chat_ids: frozenset[int] | None = None,
        http_client: HttpJsonCallable = http_json,
        bot_username: str | None = None,
        bot_user_id: int | None = None,
    ) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.poll_timeout_seconds = poll_timeout_seconds
        self.group_listen_mode = group_listen_mode
        self.allowed_chat_ids = allowed_chat_ids or frozenset()
        self.http_client = http_client
        self.bot_username = (bot_username or "").lstrip("@").lower() or None
        self.bot_user_id = bot_user_id

    def _ensure_bot_identity(self) -> tuple[str | None, int | None]:
        if self.bot_username is not None and self.bot_user_id is not None:
            return self.bot_username, self.bot_user_id

        payload = self.http_client(f"{self.base_url}/getMe", timeout=20)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {payload}")

        result = payload.get("result") or {}
        username = result.get("username")
        user_id = result.get("id")
        if isinstance(username, str) and username.strip():
            self.bot_username = username.lstrip("@").lower()
        if isinstance(user_id, int):
            self.bot_user_id = user_id
        return self.bot_username, self.bot_user_id

    def _mentions_bot(self, message: dict[str, Any], text: str) -> bool:
        bot_username, bot_user_id = self._ensure_bot_identity()
        lowered_text = text.lower()

        if bot_username and f"@{bot_username}" in lowered_text:
            return True

        for entity in message.get("entities") or []:
            if not isinstance(entity, dict):
                continue

            offset = entity.get("offset")
            length = entity.get("length")
            if not isinstance(offset, int) or not isinstance(length, int):
                continue

            fragment = text[offset : offset + length].lower()
            entity_type = entity.get("type")

            if entity_type == "mention" and bot_username and fragment == f"@{bot_username}":
                return True
            if (
                entity_type == "bot_command"
                and bot_username
                and fragment.endswith(f"@{bot_username}")
            ):
                return True
            if entity_type == "text_mention":
                user = entity.get("user") or {}
                if isinstance(bot_user_id, int) and user.get("id") == bot_user_id:
                    return True

        return False

    def _is_reply_to_bot(self, message: dict[str, Any]) -> bool:
        _, bot_user_id = self._ensure_bot_identity()
        reply = message.get("reply_to_message") or {}
        reply_sender = reply.get("from") or {}
        return isinstance(bot_user_id, int) and reply_sender.get("id") == bot_user_id

    def _should_process_message(
        self,
        *,
        chat_id: int,
        chat_type: str,
        mentions_bot: bool,
        is_reply_to_bot: bool,
    ) -> bool:
        if chat_type not in SUPPORTED_CHAT_TYPES:
            return False
        if chat_type == "private":
            return True
        if chat_type not in GROUP_CHAT_TYPES:
            return False
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return False
        if self.group_listen_mode == "all":
            return True
        return mentions_bot or is_reply_to_bot

    def get_updates(self, offset: int | None) -> list[TelegramMessage]:
        params = {"timeout": self.poll_timeout_seconds}
        if offset is not None:
            params["offset"] = offset

        url = f"{self.base_url}/getUpdates?{parse.urlencode(params)}"
        payload = self.http_client(url, timeout=self.poll_timeout_seconds + 10)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")

        messages: list[TelegramMessage] = []
        for update in payload.get("result", []):
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            chat_type = str((message.get("chat") or {}).get("type") or "")
            update_id = update.get("update_id")
            message_id = message.get("message_id")
            language_code = (message.get("from") or {}).get("language_code")
            mentions_bot = False
            is_reply_to_bot = False
            if text and chat_type in GROUP_CHAT_TYPES and self.group_listen_mode != "all":
                mentions_bot = self._mentions_bot(message, text)
                is_reply_to_bot = self._is_reply_to_bot(message)

            if (
                isinstance(update_id, int)
                and isinstance(message_id, int)
                and isinstance(chat_id, int)
                and text
                and self._should_process_message(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    mentions_bot=mentions_bot,
                    is_reply_to_bot=is_reply_to_bot,
                )
            ):
                messages.append(
                    TelegramMessage(
                        update_id=update_id,
                        message_id=message_id,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        text=text,
                        language_code=language_code,
                        reply_to_message_id=message_id,
                        message_thread_id=message.get("message_thread_id"),
                        mentions_bot=mentions_bot,
                        is_reply_to_bot=is_reply_to_bot,
                    )
                )

        return messages

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        payload_data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": trim_for_telegram(text),
        }
        if reply_to_message_id is not None:
            payload_data["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            payload_data["message_thread_id"] = message_thread_id

        payload = self.http_client(
            f"{self.base_url}/sendMessage",
            method="POST",
            payload=payload_data,
            timeout=20,
        )
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {payload}")


class PostgresConversationStore:
    """Persist and load recent conversation turns from the local context database."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connection: Any | None = None

    @property
    def enabled(self) -> bool:
        return all(
            (
                self.settings.context_db_host,
                self.settings.context_db_name,
                self.settings.context_db_user,
                self.settings.context_db_password,
            )
        )

    def _connect(self) -> Any:
        if not self.enabled:
            raise RuntimeError("Conversation store is not configured.")
        if self._connection is not None and not getattr(self._connection, "closed", False):
            return self._connection

        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is not installed. Install psycopg[binary] in the virtualenv."
            ) from exc

        self._connection = psycopg.connect(
            host=self.settings.context_db_host,
            port=self.settings.context_db_port,
            dbname=self.settings.context_db_name,
            user=self.settings.context_db_user,
            password=self.settings.context_db_password,
            sslmode=self.settings.context_db_sslmode,
            connect_timeout=self.settings.context_db_connect_timeout_seconds,
            autocommit=True,
            row_factory=dict_row,
        )
        self._ensure_schema_compatibility()
        return self._connection

    def _ensure_schema_compatibility(self) -> None:
        """Fix legacy uniqueness rules for telegram_update_id on existing volumes."""
        connection = self._connection
        if connection is None:
            raise RuntimeError("Conversation store connection is not initialized.")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE bot_context.conversation_message
                DROP CONSTRAINT IF EXISTS conversation_message_telegram_update_id_key
                """
            )
            cursor.execute(
                """
                ALTER TABLE bot_context.conversation_message
                ADD CONSTRAINT conversation_message_telegram_update_id_key
                UNIQUE (telegram_update_id)
                """
            )

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.close()
        finally:
            self._connection = None

    def _ensure_session(self, chat_id: int, language_code: str | None) -> int:
        connection = self._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_context.conversation_session (
                    chat_id,
                    language_code
                )
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                SET language_code = COALESCE(EXCLUDED.language_code,
                                             bot_context.conversation_session.language_code)
                RETURNING session_id
                """,
                (chat_id, language_code),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Failed to create or load conversation session.")
        return int(row["session_id"])

    def load_context(self, chat_id: int) -> tuple[str, list[dict[str, str]]]:
        """Load the rolling summary and recent turns for a chat."""
        if not self.enabled:
            return "", []

        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT summary
                    FROM bot_context.conversation_session
                    WHERE chat_id = %s
                    """,
                    (chat_id,),
                )
                session_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT m.role, m.content
                    FROM bot_context.conversation_message AS m
                    INNER JOIN bot_context.conversation_session AS s
                        ON s.session_id = m.session_id
                    WHERE s.chat_id = %s
                      AND m.role IN ('user', 'assistant')
                      AND m.content IS NOT NULL
                      AND m.content <> ''
                    ORDER BY m.created_at DESC, m.message_id DESC
                    LIMIT %s
                    """,
                    (chat_id, self.settings.context_history_messages),
                )
                rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            if self._connection is not None and getattr(self._connection, "closed", False):
                self._connection = None
            raise RuntimeError(f"Conversation history load failed: {exc}") from exc

        summary = ""
        if session_row is not None and session_row.get("summary"):
            summary = str(session_row["summary"])
        history = [
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in reversed(rows)
        ]
        return summary, history

    def _build_summary_block(self, parts: list[dict[str, str]], previous_summary: str) -> str:
        lines: list[str] = []
        if previous_summary.strip():
            lines.append(previous_summary.strip())

        for part in parts:
            role = part["role"].upper()
            content = " ".join(part["content"].split())
            if content:
                lines.append(f"{role}: {content}")

        return "\n".join(lines).strip()

    def _refresh_summary(self, session_id: int) -> None:
        """Compact old turns into the session summary when the history grows."""
        connection = self._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT summary
                FROM bot_context.conversation_session
                WHERE session_id = %s
                """,
                (session_id,),
            )
            session_row = cursor.fetchone()
            current_summary = ""
            if session_row is not None and session_row.get("summary"):
                current_summary = str(session_row["summary"])

            cursor.execute(
                """
                SELECT message_id, role, content
                FROM bot_context.conversation_message
                WHERE session_id = %s
                  AND role IN ('user', 'assistant')
                  AND content IS NOT NULL
                  AND content <> ''
                ORDER BY created_at ASC, message_id ASC
                """,
                (session_id,),
            )
            rows = cursor.fetchall()

            if len(rows) <= self.settings.context_summary_trigger_messages:
                return

            keep_recent = max(self.settings.context_summary_keep_recent_messages, 2)
            cutoff_index = max(len(rows) - keep_recent, 0)
            rows_to_summarize = rows[:cutoff_index]
            if not rows_to_summarize:
                return

            summary_parts = [
                {"role": str(row["role"]), "content": str(row["content"])}
                for row in rows_to_summarize
            ]
            new_summary = self._build_summary_block(summary_parts, current_summary)
            last_message_id = int(rows_to_summarize[-1]["message_id"])

            cursor.execute(
                """
                UPDATE bot_context.conversation_session
                SET summary = %s
                WHERE session_id = %s
                """,
                (new_summary, session_id),
            )
            cursor.execute(
                """
                DELETE FROM bot_context.conversation_message
                WHERE session_id = %s
                  AND message_id <= %s
                """,
                (session_id, last_message_id),
            )

    def save_turn(
        self,
        *,
        chat_id: int,
        telegram_update_id: int | None,
        user_message: str,
        assistant_message: str,
        language_code: str | None,
    ) -> None:
        """Save one user/assistant exchange."""
        if not self.enabled:
            return

        try:
            session_id = self._ensure_session(chat_id, language_code)
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO bot_context.conversation_message (
                        session_id,
                        telegram_update_id,
                        role,
                        language_code,
                        content,
                        message_payload
                    )
                    VALUES (%s, %s, 'user', %s, %s, %s::jsonb)
                    ON CONFLICT (telegram_update_id) DO NOTHING
                    """,
                    (
                        session_id,
                        telegram_update_id,
                        language_code,
                        user_message,
                        json.dumps({"source": "telegram"}, ensure_ascii=False),
                    ),
                )
                if telegram_update_id is not None and cursor.rowcount == 0:
                    return
                cursor.execute(
                    """
                    INSERT INTO bot_context.conversation_message (
                        session_id,
                        role,
                        language_code,
                        content,
                        message_payload
                    )
                    VALUES (%s, 'assistant', %s, %s, %s::jsonb)
                    """,
                    (
                        session_id,
                        language_code,
                        assistant_message,
                        json.dumps({"source": "openrouter"}, ensure_ascii=False),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE bot_context.conversation_session
                    SET
                        language_code = COALESCE(%s, language_code),
                        last_user_message_at = now(),
                        last_assistant_message_at = now()
                    WHERE session_id = %s
                    """,
                    (language_code, session_id),
                )
                self._refresh_summary(session_id)
        except Exception as exc:  # noqa: BLE001
            if self._connection is not None and getattr(self._connection, "closed", False):
                self._connection = None
            raise RuntimeError(f"Conversation history save failed: {exc}") from exc


class PostgresSqlRunner:
    """Execute read-only PostgreSQL queries with a reused connection."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connection: Any | None = None
        self._dict_row: Any | None = None
        self._psycopg: Any | None = None

    def _connect(self) -> Any:
        if self._connection is not None and not getattr(self._connection, "closed", False):
            return self._connection

        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is not installed. Install psycopg[binary] in the virtualenv."
            ) from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        self._connection = psycopg.connect(
            host=self.settings.gym_db_host,
            port=self.settings.gym_db_port,
            dbname=self.settings.gym_db_name,
            user=self.settings.gym_db_user,
            password=self.settings.gym_db_password,
            sslmode=self.settings.gym_db_sslmode,
            connect_timeout=self.settings.gym_db_connect_timeout_seconds,
            autocommit=True,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
        )
        return self._connection

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.close()
        finally:
            self._connection = None

    def execute_query(self, sql: str) -> dict[str, Any]:
        """Execute validated SQL and return a compact JSON-safe result."""
        try:
            validated_sql = validate_sql(sql)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if validated_sql.startswith("--"):
            return {"ok": False, "error": validated_sql}

        try:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(validated_sql)
                columns = [column.name for column in cursor.description or []]
                fetched = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            if self._connection is not None and getattr(self._connection, "closed", False):
                self._connection = None
            return {"ok": False, "error": str(exc)}

        rows = [normalize_json_value(row) for row in fetched]
        return {
            "ok": True,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }


class OpenRouterToolAgent:
    """Run the OpenRouter chat completion loop with local tool execution."""

    def __init__(
        self,
        settings: Settings,
        sql_runner: PostgresSqlRunner,
        http_client: HttpJsonCallable = http_json,
    ) -> None:
        self.settings = settings
        self.sql_runner = sql_runner
        self.http_client = http_client

    def _chat_completion(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.settings.openrouter_model,
            "temperature": 0,
            "messages": messages,
            "tools": build_tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "HTTP-Referer": self.settings.openrouter_http_referer,
            "X-OpenRouter-Title": self.settings.openrouter_app_title,
        }
        return self.http_client(
            f"{self.settings.openrouter_base_url}/chat/completions",
            method="POST",
            payload=payload,
            headers=headers,
            timeout=self.settings.openrouter_timeout_seconds,
        )

    def _tool_message(self, tool_call_id: str, tool_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(tool_result, ensure_ascii=False),
        }

    def _execute_tool_call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        tool_call_id = str(tool_call.get("id", "missing_tool_call_id"))
        function = tool_call.get("function") or {}
        tool_name = function.get("name")
        raw_arguments = function.get("arguments") or "{}"

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return self._tool_message(
                tool_call_id,
                {"ok": False, "error": f"Invalid tool arguments: {exc}"},
            )

        if tool_name != "run_sql":
            return self._tool_message(
                tool_call_id,
                {"ok": False, "error": f"Unsupported tool requested: {tool_name}"},
            )

        sql = str(arguments.get("sql", "")).strip()
        print(f"Executing tool run_sql: {sql}")
        tool_result = self.sql_runner.execute_query(sql)
        return self._tool_message(tool_call_id, tool_result)

    def respond(self, user_message: str, language_hint: str | None = None) -> str:
        """Generate a final end-user response, using tool calls when needed."""
        return self.respond_with_history(user_message, "", [], language_hint)

    def respond_with_history(
        self,
        user_message: str,
        conversation_summary: str,
        conversation_history: list[dict[str, str]],
        language_hint: str | None = None,
    ) -> str:
        """Generate a final end-user response, using tool calls and recent history."""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_system_prompt(language_hint=language_hint),
            }
        ]
        if conversation_summary.strip():
            messages.append(
                {
                    "role": "system",
                    "content": build_conversation_summary_prompt(
                        conversation_summary
                    ),
                }
            )
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})

        for _ in range(self.settings.max_tool_iterations):
            response = self._chat_completion(messages)
            choices = response.get("choices") or []
            if not choices:
                raise RuntimeError(f"OpenRouter returned no choices: {response}")

            assistant_message = choices[0].get("message") or {}
            tool_calls = assistant_message.get("tool_calls") or []

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.get("content"),
                        "tool_calls": tool_calls,
                    }
                )
                for tool_call in tool_calls:
                    messages.append(self._execute_tool_call(tool_call))
                continue

            content = normalize_openrouter_content(assistant_message.get("content"))
            if content:
                return content

            raise RuntimeError(f"OpenRouter returned no final content: {response}")

        raise RuntimeError("Maximum tool iterations reached before a final answer.")


def build_runtime_error_reply(language_hint: str | None) -> str:
    """Build a small fallback reply when the runtime fails before the model can."""
    if (language_hint or "").lower().startswith("pl"):
        return "Nie udalo mi sie teraz pobrac danych. Sprobuj ponownie za chwile."
    return "I could not fetch the data right now. Please try again in a moment."


def run_bot() -> None:
    settings = Settings.from_env()
    telegram = TelegramClient(
        settings.telegram_bot_token,
        settings.telegram_poll_timeout_seconds,
        group_listen_mode=settings.telegram_group_listen_mode,
        allowed_chat_ids=settings.telegram_allowed_chat_ids,
    )
    sql_runner = PostgresSqlRunner(settings)
    conversation_store = PostgresConversationStore(settings)
    agent = OpenRouterToolAgent(settings, sql_runner)
    offset: int | None = None

    print(f"Smartass SQL bot started. Model: {settings.openrouter_model}")

    try:
        while True:
            try:
                updates = telegram.get_updates(offset)
                for message in updates:
                    offset = message.update_id + 1
                    print(
                        "Received message "
                        f"[chat_type={message.chat_type} chat_id={message.chat_id}]: "
                        f"{message.text}"
                    )

                    conversation_summary = ""
                    conversation_history: list[dict[str, str]] = []
                    try:
                        (
                            conversation_summary,
                            conversation_history,
                        ) = conversation_store.load_context(
                            message.chat_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"Conversation history unavailable: {exc}", file=sys.stderr)

                    try:
                        reply = agent.respond_with_history(
                            message.text,
                            conversation_summary,
                            conversation_history,
                            message.language_code,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"Message handling failed: {exc}", file=sys.stderr)
                        reply = build_runtime_error_reply(message.language_code)

                    try:
                        conversation_store.save_turn(
                            chat_id=message.chat_id,
                            telegram_update_id=message.update_id,
                            user_message=message.text,
                            assistant_message=reply,
                            language_code=message.language_code,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"Conversation history save failed: {exc}", file=sys.stderr)

                    telegram.send_message(
                        message.chat_id,
                        reply,
                        reply_to_message_id=message.reply_to_message_id,
                        message_thread_id=message.message_thread_id,
                    )
            except KeyboardInterrupt:
                print("Bot stopped by user.")
                return
            except Exception as exc:  # noqa: BLE001
                print(f"Runtime error: {exc}", file=sys.stderr)
                time.sleep(settings.retry_delay_seconds)
    finally:
        conversation_store.close()
        sql_runner.close()


if __name__ == "__main__":
    run_bot()
