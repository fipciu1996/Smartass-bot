#!/usr/bin/env python3
"""Local smoke tests for the Telegram -> OpenRouter tool calling -> SQL flow."""

import json
from datetime import date
from pathlib import Path
from uuid import UUID

from main import (
    GYM_SCHEMA_DDL,
    OpenRouterToolAgent,
    PostgresConversationStore,
    Settings,
    TelegramClient,
    build_system_prompt,
    build_tools,
    extract_sql,
    normalize_json_value,
    parse_chat_id_allowlist,
    validate_sql,
)


class FakeSqlRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute_query(self, sql: str) -> dict[str, object]:
        self.calls.append(sql)
        return {
            "ok": True,
            "columns": ["member_count"],
            "rows": [{"member_count": 12}],
            "row_count": 1,
        }


class FakeHttpClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, **kwargs: object) -> dict[str, object]:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No more fake HTTP responses configured.")
        return self.responses.pop(0)


class FakeCursor:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def execute(self, sql: str, params: object | None = None) -> None:
        normalized = " ".join(sql.split())
        if params is not None:
            normalized = f"{normalized} params={params!r}"
        self.statements.append(normalized)


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.statements: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.statements)


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="telegram-token",
        telegram_group_listen_mode="mentioned",
        telegram_allowed_chat_ids=frozenset(),
        openrouter_api_key="openrouter-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_timeout_seconds=60,
        telegram_poll_timeout_seconds=20,
        retry_delay_seconds=3,
        openrouter_http_referer="https://smartass-bot.local",
        openrouter_app_title="Smartass SQL Bot",
        gym_db_host="127.0.0.1",
        gym_db_port=5432,
        gym_db_name="smartass",
        gym_db_user="readonly",
        gym_db_password="secret",
        gym_db_sslmode="prefer",
        gym_db_connect_timeout_seconds=10,
        context_db_host="127.0.0.1",
        context_db_port=5432,
        context_db_name="smartass_bot",
        context_db_user="smartass_bot",
        context_db_password="context-secret",
        context_db_sslmode="disable",
        context_db_connect_timeout_seconds=10,
        context_history_messages=8,
        context_summary_trigger_messages=20,
        context_summary_keep_recent_messages=8,
        max_tool_iterations=6,
    )


def test_prompt_contains_schema_and_language_rules() -> None:
    prompt = build_system_prompt(date(2026, 3, 17), "pl")
    assert "CURRENT_DATE = 2026-03-17" in prompt
    assert "TELEGRAM_LANGUAGE_HINT = pl" in prompt
    assert "CREATE TABLE gym.training_session" in prompt
    assert "Grafana dashboard source: gym-stats-smartass-codex / gym-statistics-final" in prompt
    assert "registered_members = registrations" in prompt
    assert "is_new_class" in prompt
    assert GYM_SCHEMA_DDL in prompt
    assert "call the run_sql tool" in prompt
    assert "Do not rely on application-side row limits" in prompt


def test_build_tools_contains_run_sql() -> None:
    tools = build_tools()
    assert tools[0]["function"]["name"] == "run_sql"


def test_parse_chat_id_allowlist_accepts_supergroup_alias_without_prefix() -> None:
    chat_ids = parse_chat_id_allowlist("-5243066256")
    assert -5243066256 in chat_ids
    assert -1005243066256 in chat_ids


def test_extract_sql_from_code_block() -> None:
    content = "```sql\nSELECT 1 AS value\n```"
    assert extract_sql(content) == "SELECT 1 AS value"


def test_validate_sql_accepts_read_only_query() -> None:
    query = "SELECT COUNT(*) AS member_count FROM gym.participant"
    assert validate_sql(query) == (
        "SELECT COUNT(*) AS member_count FROM gym.participant;"
    )


def test_validate_sql_rejects_mutation() -> None:
    try:
        validate_sql("DELETE FROM gym.participant")
    except ValueError as exc:
        assert "Mutating SQL detected" in str(exc)
        return
    raise AssertionError("Mutation should have been rejected.")


def test_normalize_json_value_converts_uuid() -> None:
    value = UUID("12345678-1234-5678-1234-567812345678")
    assert normalize_json_value(value) == "12345678-1234-5678-1234-567812345678"


def test_context_schema_uses_standard_unique_null_semantics() -> None:
    schema_sql = Path("docker/postgres/init/01-context-schema.sql").read_text(
        encoding="utf-8"
    )
    assert "UNIQUE (telegram_update_id)" in schema_sql
    assert "NULLS NOT DISTINCT" not in schema_sql


def test_conversation_store_migrates_legacy_telegram_update_constraint() -> None:
    store = PostgresConversationStore(make_settings())
    fake_connection = FakeConnection()
    store._connection = fake_connection

    store._ensure_schema_compatibility()

    assert any(
        "DROP CONSTRAINT IF EXISTS conversation_message_telegram_update_id_key"
        in statement
        for statement in fake_connection.statements
    )
    assert any(
        "ADD CONSTRAINT conversation_message_telegram_update_id_key UNIQUE (telegram_update_id)"
        in statement
        for statement in fake_connection.statements
    )


def test_telegram_client_accepts_group_mentions() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 101,
                        "message": {
                            "message_id": 501,
                            "text": "@smartass_bot Ilu bylo dzisiaj wizyt?",
                            "chat": {
                                "id": -1001234567890,
                                "type": "supergroup",
                                "title": "Smartass",
                            },
                            "from": {"language_code": "pl"},
                            "entities": [
                                {"type": "mention", "offset": 0, "length": 13}
                            ],
                        },
                    }
                ],
            }
        ]
    )
    telegram = TelegramClient(
        "telegram-token",
        20,
        group_listen_mode="mentioned",
        http_client=http_client,
        bot_username="smartass_bot",
        bot_user_id=999,
    )

    messages = telegram.get_updates(None)

    assert len(messages) == 1
    assert messages[0].chat_type == "supergroup"
    assert messages[0].mentions_bot is True


def test_telegram_client_skips_unaddressed_group_messages() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 102,
                        "message": {
                            "message_id": 502,
                            "text": "Ilu bylo dzisiaj wizyt?",
                            "chat": {"id": -1001234567890, "type": "supergroup"},
                            "from": {"language_code": "pl"},
                        },
                    }
                ],
            }
        ]
    )
    telegram = TelegramClient(
        "telegram-token",
        20,
        group_listen_mode="mentioned",
        http_client=http_client,
        bot_username="smartass_bot",
        bot_user_id=999,
    )

    assert telegram.get_updates(None) == []


def test_telegram_client_can_listen_to_all_group_messages() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 103,
                        "message": {
                            "message_id": 503,
                            "text": "Ilu bylo dzisiaj wizyt?",
                            "chat": {"id": -1001234567890, "type": "group"},
                            "from": {"language_code": "pl"},
                            "message_thread_id": 77,
                        },
                    }
                ],
            }
        ]
    )
    telegram = TelegramClient(
        "telegram-token",
        20,
        group_listen_mode="all",
        http_client=http_client,
        bot_username="smartass_bot",
        bot_user_id=999,
    )

    messages = telegram.get_updates(None)

    assert len(messages) == 1
    assert messages[0].message_thread_id == 77
    assert messages[0].reply_to_message_id == 503


def test_telegram_client_replies_in_same_thread() -> None:
    http_client = FakeHttpClient(responses=[{"ok": True, "result": {}}])
    telegram = TelegramClient(
        "telegram-token",
        20,
        group_listen_mode="all",
        http_client=http_client,
        bot_username="smartass_bot",
        bot_user_id=999,
    )

    telegram.send_message(
        -1001234567890,
        "Bylo 120 wizyt.",
        reply_to_message_id=503,
        message_thread_id=77,
    )

    assert http_client.calls[0]["payload"]["reply_to_message_id"] == 503
    assert http_client.calls[0]["payload"]["message_thread_id"] == 77


def test_agent_includes_conversation_history_in_first_request() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": "Kontynuacja w kontekscie poprzedniej rozmowy.",
                        }
                    }
                ]
            }
        ]
    )
    sql_runner = FakeSqlRunner()
    agent = OpenRouterToolAgent(make_settings(), sql_runner, http_client=http_client)

    history = [
        {"role": "user", "content": "Pokaz frekwencje z dzisiaj."},
        {"role": "assistant", "content": "Dzisiaj bylo 218 wizyt."},
    ]
    reply = agent.respond_with_history("A wczoraj?", "", history, "pl")

    assert reply == "Kontynuacja w kontekscie poprzedniej rozmowy."
    payload = http_client.calls[0]["payload"]
    messages = payload["messages"]
    assert messages[1] == history[0]
    assert messages[2] == history[1]
    assert messages[3] == {"role": "user", "content": "A wczoraj?"}


def test_agent_includes_summary_before_recent_messages() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": "Odpowiedz z wykorzystaniem summary.",
                        }
                    }
                ]
            }
        ]
    )
    sql_runner = FakeSqlRunner()
    agent = OpenRouterToolAgent(make_settings(), sql_runner, http_client=http_client)

    reply = agent.respond_with_history(
        "A teraz tylko dla Krakowa?",
        "USER: Pokaz frekwencje z zeszlego tygodnia.\nASSISTANT: Bylo 910 wizyt.",
        [{"role": "assistant", "content": "Najwiecej wizyt bylo w Warszawie."}],
        "pl",
    )

    assert reply == "Odpowiedz z wykorzystaniem summary."
    payload = http_client.calls[0]["payload"]
    messages = payload["messages"]
    assert messages[1]["role"] == "system"
    assert "Conversation summary from earlier turns" in messages[1]["content"]
    assert "Bylo 910 wizyt." in messages[1]["content"]
    assert messages[2] == {
        "role": "assistant",
        "content": "Najwiecej wizyt bylo w Warszawie.",
    }
    assert messages[3] == {"role": "user", "content": "A teraz tylko dla Krakowa?"}


def test_agent_runs_tool_and_returns_final_answer() -> None:
    http_client = FakeHttpClient(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_sql",
                                        "arguments": json.dumps(
                                            {
                                                "sql": (
                                                    "SELECT COUNT(*) AS member_count "
                                                    "FROM gym.participant"
                                                )
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "Masz teraz 12 uczestnikow w bazie.",
                        }
                    }
                ]
            },
        ]
    )
    sql_runner = FakeSqlRunner()
    agent = OpenRouterToolAgent(make_settings(), sql_runner, http_client=http_client)

    reply = agent.respond("Ilu mamy uczestnikow?", "pl")

    assert reply == "Masz teraz 12 uczestnikow w bazie."
    assert sql_runner.calls == ["SELECT COUNT(*) AS member_count FROM gym.participant"]
    second_payload = http_client.calls[1]["payload"]
    messages = second_payload["messages"]
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call_1"


def run_tests() -> None:
    tests = [
        test_prompt_contains_schema_and_language_rules,
        test_build_tools_contains_run_sql,
        test_parse_chat_id_allowlist_accepts_supergroup_alias_without_prefix,
        test_extract_sql_from_code_block,
        test_validate_sql_accepts_read_only_query,
        test_validate_sql_rejects_mutation,
        test_normalize_json_value_converts_uuid,
        test_context_schema_uses_standard_unique_null_semantics,
        test_conversation_store_migrates_legacy_telegram_update_constraint,
        test_telegram_client_accepts_group_mentions,
        test_telegram_client_skips_unaddressed_group_messages,
        test_telegram_client_can_listen_to_all_group_messages,
        test_telegram_client_replies_in_same_thread,
        test_agent_includes_conversation_history_in_first_request,
        test_agent_includes_summary_before_recent_messages,
        test_agent_runs_tool_and_returns_final_answer,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")


if __name__ == "__main__":
    run_tests()
