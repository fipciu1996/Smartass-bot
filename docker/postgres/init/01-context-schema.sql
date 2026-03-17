CREATE SCHEMA IF NOT EXISTS bot_context;

CREATE TABLE IF NOT EXISTS bot_context.conversation_session (
    session_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    language_code TEXT,
    summary TEXT NOT NULL DEFAULT '',
    session_state JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(session_state) = 'object'),
    last_user_message_at TIMESTAMPTZ,
    last_assistant_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_context.conversation_message (
    message_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id BIGINT NOT NULL
        REFERENCES bot_context.conversation_session(session_id)
        ON DELETE CASCADE,
    telegram_update_id BIGINT,
    role TEXT NOT NULL
        CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    language_code TEXT,
    content TEXT,
    message_payload JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(message_payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_update_id)
);

CREATE INDEX IF NOT EXISTS conversation_session_updated_at_idx
    ON bot_context.conversation_session (updated_at);

CREATE INDEX IF NOT EXISTS conversation_message_session_created_at_idx
    ON bot_context.conversation_message (session_id, created_at);

CREATE INDEX IF NOT EXISTS conversation_message_payload_gin_idx
    ON bot_context.conversation_message
    USING GIN (message_payload);

CREATE OR REPLACE FUNCTION bot_context.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS conversation_session_set_updated_at
ON bot_context.conversation_session;

CREATE TRIGGER conversation_session_set_updated_at
BEFORE UPDATE ON bot_context.conversation_session
FOR EACH ROW
EXECUTE FUNCTION bot_context.set_updated_at();
