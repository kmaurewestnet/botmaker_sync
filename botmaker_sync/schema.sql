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
-- synced_at here means "last time an API timestamp on this chat changed" (see
-- SYNCED_AT_ON in sync/chats.py), not "last time the cron looked at it" -- the
-- 5-min window overlap re-fetches unchanged chats every run. On the other
-- tables synced_at is still first-seen: they're full sweeps, so now() would
-- stamp every row with the cron time and carry no information.
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
-- Three states, not two:
--   is_open = true                              -> ongoing as far as we know
--   is_open = false, closed_reason='event'      -> observed: the API sent a
--                                                  conversation-close event
--   is_open = false, closed_reason='window_expired'
--                                               -> ASSUMED: the session aged out
--                                                  of the lookback window, so the
--                                                  API will not return it again and
--                                                  we can never observe its close.
-- 'window_expired' is NOT a fact reported by Botmaker. Anything measuring session
-- duration or abandonment must filter on closed_reason.
CREATE TABLE IF NOT EXISTS sessions (
    id             text PRIMARY KEY,
    chat_id        text, -- soft ref: chats window (last activity) != sessions window (start time)
    channel_id     text,
    contact_id     text,
    creation_time  timestamptz,
    starting_cause text,
    is_open        boolean NOT NULL DEFAULT true,
    closed_reason  text, -- NULL while is_open; 'event' | 'window_expired'
    synced_at      timestamptz NOT NULL DEFAULT now()
);
-- Migration: add is_open/closed_reason to pre-existing installs.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_open boolean NOT NULL DEFAULT true;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS closed_reason text;
CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_creation_time ON sessions(creation_time);
-- Partial index: both the MIN(creation_time) lookback probe and the expiry sweep
-- scan only open sessions, which are a small slice of the table.
CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions(creation_time) WHERE is_open;

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

-- Populated from /sessions?include-ai-analysis=true (always on); sessions the
-- API returns without an aiAnalysis block simply get no row.
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

-- ===== dashboards / agent metrics =====
-- One row per (session, agent) from /dashboards/agent-metrics: a transferred
-- conversation is reported once per agent that handled it, so session_id alone
-- would collapse those into one row. agent_id is '' (not NULL) when the API
-- sends no agent, because a PK column cannot be nullable.
--
-- session_status is a data column, NOT part of the key: a session first seen
-- 'open' and later 'closed' must update in place, not leave a stale open row.
--
-- Soft ref to sessions(id), no FK: metrics are fetched in their own time window
-- and can arrive for a session this mirror hasn't synced yet.
CREATE TABLE IF NOT EXISTS agent_metrics (
    session_id            text NOT NULL,
    agent_id              text NOT NULL DEFAULT '',
    chat_id               text,
    session_creation_time timestamptz,
    closed_time           timestamptz,
    session_status        text, -- 'open' | 'closed', from the request that returned the row
    queue                 text,
    agent_name            text,
    typification          text,
    conversation_link     text,
    -- durations, seconds
    avg_attending_time    integer,
    avg_response_time     integer,
    op_response_time      integer,
    from_queue_asign_to_op_assigned         integer,
    from_session_start_to_op_first_response integer,
    from_queue_asign_to_op_first_response   integer,
    from_op_assigned_to_op_first_response   integer,
    from_queue_asign_to_session_closed      integer,
    from_op_assignation_to_session_closed   integer,
    -- counters
    open_sessions                    integer,
    closed_sessions                  integer,
    on_hold                          integer,
    operator_responses               integer,
    session_transfer_in              integer,
    session_transfer_out             integer,
    session_transfer_out_no_messages integer,
    closed_with_no_messages          integer,
    timeout_no_messages              integer,
    agent_timeout                    integer,
    user_timeout                     integer,
    session_timeout                  integer,
    synced_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_creation_time ON agent_metrics(session_creation_time);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent_id ON agent_metrics(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_queue ON agent_metrics(queue);

-- ===== sync watermark state =====
CREATE TABLE IF NOT EXISTS sync_state (
    entity         text PRIMARY KEY,
    last_watermark timestamptz,
    last_run_at    timestamptz,
    last_status    text,
    note           text
);
