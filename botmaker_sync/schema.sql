-- Botmaker sync mirror schema.
-- Read-mirror of the Botmaker API (GET-only). Children of one entity use
-- hard FKs with cascade; references that cross entities (e.g. chats.contact_id)
-- are soft (indexed, no FK) because contacts/chats/sessions are synced from
-- independent time windows and a referenced row may not exist locally yet.

-- ===== channels =====
CREATE TABLE IF NOT EXISTS channels (
    id              text PRIMARY KEY,
    platform        text NOT NULL,
    active          boolean,
    name            text,
    webhook_id      text,
    -- platform-specific (nullable depending on platform)
    number          text,    -- whatsapp
    status          text,    -- whatsapp
    quality         text,    -- whatsapp
    waba_id         text,    -- whatsapp
    trial           boolean, -- whatsapp
    recipient_id    text,    -- messenger / instagram
    days_to_expire  integer, -- messenger
    token           text,    -- telegram
    page_id         text,    -- instagram
    synced_at       timestamptz NOT NULL DEFAULT now()
);

-- ===== agents =====
CREATE TABLE IF NOT EXISTS agents (
    id              text PRIMARY KEY,
    email           text,
    name            text,
    alias           text,
    is_online       boolean,
    status          text,
    role            text,
    slots           integer,
    priority        text,
    creation_time   timestamptz,
    additional_info jsonb,
    synced_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_queues (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    queue_id text NOT NULL,
    PRIMARY KEY (agent_id, queue_id)
);

CREATE TABLE IF NOT EXISTS agent_groups (
    agent_id   text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    group_name text NOT NULL,
    PRIMARY KEY (agent_id, group_name)
);

-- ===== contacts =====
-- Scope: only contacts referenced by chats touched in a sync run are fetched
-- and stored (no /contacts/{id} endpoint exists, so this is enforced in code
-- via GET /contacts?channel-id=... + in-memory filtering, not in SQL).
CREATE TABLE IF NOT EXISTS contacts (
    id          text PRIMARY KEY,
    first_name  text,
    last_name   text,
    birthday    text,
    picture_url text,
    language    text,
    country     text,
    company_id  text,
    job_title   text,
    synced_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contact_phones (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    value      text,
    label      text,
    PRIMARY KEY (contact_id, seq)
);

CREATE TABLE IF NOT EXISTS contact_emails (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    value      text,
    label      text,
    PRIMARY KEY (contact_id, seq)
);

CREATE TABLE IF NOT EXISTS contact_addresses (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    value      text,
    label      text,
    PRIMARY KEY (contact_id, seq)
);

CREATE TABLE IF NOT EXISTS contact_websites (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    value      text,
    label      text,
    PRIMARY KEY (contact_id, seq)
);

-- instagramIds / facebookIds / twitterIds / whatsappBsuids collapsed into one
-- (network, value) table -- they're all "social handle" lists of the same shape.
CREATE TABLE IF NOT EXISTS contact_social (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    network    text NOT NULL, -- instagram | facebook | twitter | whatsapp_bsuid
    value      text NOT NULL,
    PRIMARY KEY (contact_id, network, value)
);

CREATE TABLE IF NOT EXISTS contact_notes (
    contact_id text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq        integer NOT NULL,
    note       text,
    PRIMARY KEY (contact_id, seq)
);

CREATE TABLE IF NOT EXISTS contact_chats (
    contact_id          text NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    seq                 integer NOT NULL,
    platform_chat_id    text,
    platform_contact_id text,
    chat_channel_id     text,
    bsuid               text,
    PRIMARY KEY (contact_id, seq)
);

-- ===== chats =====
-- Incremental by last activity (`from`/`to` on GET /chats).
CREATE TABLE IF NOT EXISTS chats (
    chat_id                  text PRIMARY KEY,
    channel_id               text,
    contact_id               text,
    creation_time            timestamptz,
    last_session_creation_time timestamptz,
    external_id              text,
    first_name               text,
    last_name                text,
    country                  text,
    email                    text,
    whatsapp_window_close_at timestamptz,
    queue_id                 text,
    agent_id                 text,
    on_hold_agent_id         text,
    last_user_message_at     timestamptz,
    is_banned                boolean,
    is_tester                boolean,
    is_bot_muted             boolean,
    synced_at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chats_contact_id ON chats(contact_id);
CREATE INDEX IF NOT EXISTS idx_chats_channel_id ON chats(channel_id);
CREATE INDEX IF NOT EXISTS idx_chats_last_activity ON chats(last_session_creation_time);

CREATE TABLE IF NOT EXISTS chat_tags (
    chat_id text NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    tag     text NOT NULL,
    PRIMARY KEY (chat_id, tag)
);

CREATE TABLE IF NOT EXISTS chat_variables (
    chat_id text NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    key     text NOT NULL,
    value   text,
    PRIMARY KEY (chat_id, key)
);

-- ===== sessions (= conversations) =====
-- Incremental by session start time (`from`/`to` on GET /sessions).
CREATE TABLE IF NOT EXISTS sessions (
    id             text PRIMARY KEY,
    chat_id        text, -- soft ref: chats window (last activity) != sessions window (start time)
    channel_id     text,
    contact_id     text,
    creation_time  timestamptz,
    starting_cause text,
    synced_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_creation_time ON sessions(creation_time);

-- content/encryption_params stay jsonb: `content` is a tagged union (type
-- decides which sibling field is populated) on a high-volume table, not a
-- 1:N array -- child-tables-per-variant would multiply tables for no query
-- benefit. ponytail: flatten into typed columns if content is ever filtered
-- on directly (e.g. WHERE content->>'type' = ... shows up a lot).
CREATE TABLE IF NOT EXISTS session_messages (
    id                text PRIMARY KEY,
    session_id        text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    creation_time     timestamptz,
    from_role         text, -- bot | user | agent ('from' is a reserved word)
    agent_id          text,
    queue_id          text,
    content           jsonb,
    encryption_params jsonb
);
CREATE INDEX IF NOT EXISTS idx_session_messages_session_id ON session_messages(session_id);

-- info stays jsonb: EventInfo is a 16-variant oneOf keyed by `name`, used for
-- audit/debugging, never filtered relationally.
CREATE TABLE IF NOT EXISTS session_events (
    session_id    text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq           integer NOT NULL,
    name          text,
    creation_time timestamptz,
    info          jsonb,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS session_variables (
    session_id text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    key        text NOT NULL,
    value      text,
    PRIMARY KEY (session_id, key)
);

-- Only populated when sync runs with --include-ai-analysis.
-- aspectScores is a small fixed-shape object -> flattened to real columns.
CREATE TABLE IF NOT EXISTS session_ai_analysis (
    session_id             text PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    summary                text,
    does_not_meet_criteria boolean,
    name                   text,
    justification          text,
    quality_score          integer,
    aspect_conciseness     integer,
    aspect_clarity         integer,
    aspect_empathy_tone    integer,
    aspect_understanding   integer,
    aspect_resolution      integer
);

-- ===== sync watermark state =====
CREATE TABLE IF NOT EXISTS sync_state (
    entity         text PRIMARY KEY,
    last_watermark timestamptz,
    last_run_at    timestamptz,
    last_status    text,
    note           text
);
