"""ponytail: one focused check per non-trivial branch (pagination, retry,
row-mapping, watermark window math) -- not an exhaustive per-function suite.
No DB needed: db.py's actual SQL is exercised by the manual end-to-end run
against a real Postgres (see README), not here."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from psycopg.types.json import Jsonb

import botmaker_sync.client as client_module
import botmaker_sync.db as db_module
import botmaker_sync.sync.agent_metrics as agent_metrics_module
import botmaker_sync.sync.chats as chats_module
import botmaker_sync.sync.sessions as sessions_module
from botmaker_sync.client import BotmakerClient, format_datetime
from botmaker_sync.db import replace_children, resolve_window, upsert_rows
from botmaker_sync.models import AgentMetricModel, ChatModel, SessionModel
from botmaker_sync.sync.agent_metrics import _row as metric_row, sync_agent_metrics
from botmaker_sync.sync.chats import _row as chat_row
from botmaker_sync.sync.contacts import sync_contacts
from botmaker_sync.sync.sessions import _row as session_row, sync_sessions

BASE = "https://api.botmaker.com/v2.0"


def _parse_sent(value: str) -> datetime:
    """Inverse of client.format_datetime, for asserting on window width."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@respx.mock
def test_get_pages_follows_absolute_url_next_page():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200, json={"nextPage": f"{BASE}/agents?cursor=abc", "items": [{"id": "a1"}]}
            )
        return httpx.Response(200, json={"items": [{"id": "a2"}]})

    respx.get(f"{BASE}/agents").mock(side_effect=handler)
    client = BotmakerClient("tok", BASE)
    pages = list(client.get_pages("/agents"))
    ids = [item["id"] for page in pages for item in page["items"]]
    assert ids == ["a1", "a2"]
    assert calls["n"] == 2


@respx.mock
def test_get_pages_follows_opaque_token_next_page():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            assert "next-page-token" not in request.url.params
            return httpx.Response(200, json={"nextPage": "tok123", "items": [{"id": "c1"}]})
        assert request.url.params["next-page-token"] == "tok123"
        assert request.url.params["channel-id"] == "ch1"
        return httpx.Response(200, json={"items": [{"id": "c2"}]})

    respx.get(f"{BASE}/contacts").mock(side_effect=handler)
    client = BotmakerClient("tok", BASE)
    pages = list(client.get_pages("/contacts", params={"channel-id": "ch1"}))
    ids = [item["id"] for page in pages for item in page["items"]]
    assert ids == ["c1", "c2"]
    assert calls["n"] == 2


@respx.mock
def test_get_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"items": []})

    respx.get(f"{BASE}/channels").mock(side_effect=handler)
    client = BotmakerClient("tok", BASE)
    pages = list(client.get_pages("/channels"))
    assert calls["n"] == 2
    assert pages == [{"items": []}]


def test_chat_row_maps_nested_chat_reference():
    item = ChatModel.model_validate(
        {
            "chat": {"chatId": "ch1", "channelId": "cc1", "contactId": "549"},
            "tags": ["vip"],
            "variables": {"k": "v"},
            "isBanned": False,
        }
    )
    row = chat_row(item)
    assert row["chat_id"] == "ch1"
    assert row["channel_id"] == "cc1"
    assert row["contact_id"] == "549"
    assert row["is_banned"] is False


def test_chat_row_skips_when_no_chat_reference():
    assert chat_row(ChatModel.model_validate({})) is None


def test_session_row_pulls_refs_and_variables_from_nested_chat():
    item = SessionModel.model_validate(
        {
            "id": "s1",
            "chat": {
                "chat": {"chatId": "ch1", "channelId": "cc1", "contactId": "549"},
                "variables": {"plan": "pro"},
            },
        }
    )
    row = session_row(item)
    assert (row["chat_id"], row["channel_id"], row["contact_id"]) == ("ch1", "cc1", "549")
    assert item.chat.variables == {"plan": "pro"}


def test_session_ai_analysis_maps_every_documented_field():
    """The block as Botmaker sends it since September 2026: aspectScores is an
    open map of {result, weight}, not a fixed object of ints."""
    item = SessionModel.model_validate(
        {
            "id": "s1",
            "aiAnalysis": {
                "summary": "resumen",
                "doesNotMeetCriteria": False,
                "name": "analisis",
                "justification": "porque si",
                "aspectScores": {
                    "conciseness": {"result": 40, "weight": 20},
                    "clarity": {"result": 55, "weight": 20},
                },
                "qualityScore": 90,
            },
        }
    )
    a = item.ai_analysis
    assert (a.summary, a.name, a.justification) == ("resumen", "analisis", "porque si")
    assert (a.does_not_meet_criteria, a.quality_score) == (False, 90)
    assert (a.aspect_scores["conciseness"].result, a.aspect_scores["conciseness"].weight) == (40, 20)
    assert (a.aspect_scores["clarity"].result, a.aspect_scores["clarity"].weight) == (55, 20)


def test_session_ai_analysis_keeps_an_aspect_name_it_has_never_seen():
    """The aspect list is configurable per account, so an unknown key must land
    as data instead of being dropped or raising."""
    item = SessionModel.model_validate(
        {"id": "s1", "aiAnalysis": {"aspectScores": {"tonoDeVoz": {"result": 10, "weight": 5}}}}
    )
    assert item.ai_analysis.aspect_scores["tonoDeVoz"].result == 10


def test_session_ai_analysis_still_reads_the_pre_2026_09_flat_shape():
    """A rollback on Botmaker's side must not break the collector again."""
    item = SessionModel.model_validate(
        {"id": "s1", "aiAnalysis": {"aspectScores": {"clarity": 40}}}
    )
    assert item.ai_analysis.aspect_scores["clarity"].result == 40
    assert item.ai_analysis.aspect_scores["clarity"].weight is None


def test_session_ai_analysis_partial_block_leaves_the_rest_none():
    """What the API actually returns today: the flag alone, no aspectScores."""
    item = SessionModel.model_validate({"id": "s1", "aiAnalysis": {"doesNotMeetCriteria": True}})
    assert item.ai_analysis.does_not_meet_criteria is True
    assert item.ai_analysis.summary is None
    assert item.ai_analysis.aspect_scores is None


class _FakeCursor:
    def __init__(self, value, rows=None, sql_log=None, executemany_rows=None):
        self._value = value
        self._rows = rows or []
        self._sql_log = sql_log if sql_log is not None else []
        self._executemany_rows = executemany_rows if executemany_rows is not None else []
        self._last = (None, None)
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql=None, params=None, *args, **kwargs):
        self._sql_log.append(sql)
        self._last = (sql, params)

    def executemany(self, sql=None, params_seq=None, *args, **kwargs):
        self._sql_log.append(sql)
        self._executemany_rows.append(list(params_seq or []))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        sql, params = self._last
        # Stand in for Postgres on the open-session probe only: without honoring
        # its floor bound the clamp under test would be invisible here.
        if self._value is not None and sql and "MIN(creation_time)" in sql and params:
            return (None,) if self._value < params[0] else (self._value,)
        return (self._value,) if self._value is not None else None


class _FakeConn:
    """`watermark` feeds every fetchone() -- in sessions it stands in for the
    MIN(creation_time) of open sessions, in db.py for the stored watermark."""

    def __init__(self, watermark=None, rows=None):
        self._watermark = watermark
        self._rows = rows or []
        self.sql_log = []
        self.executemany_rows = []

    def cursor(self):
        return _FakeCursor(self._watermark, self._rows, self.sql_log, self.executemany_rows)

    def commit(self):
        pass


def test_resolve_window_uses_watermark_minus_overlap():
    wm = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    since, until = resolve_window(_FakeConn(wm), "chats", None, None)
    assert since == wm - timedelta(minutes=5)
    assert until > wm


def test_resolve_window_explicit_range_bypasses_watermark():
    conn = _FakeConn(datetime(2020, 1, 1, tzinfo=timezone.utc))
    explicit_since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    explicit_until = datetime(2026, 1, 2, tzinfo=timezone.utc)
    since, until = resolve_window(conn, "chats", explicit_since, explicit_until)
    assert (since, until) == (explicit_since, explicit_until)


def test_resolve_window_first_run_has_no_lower_bound():
    since, _ = resolve_window(_FakeConn(None), "chats", None, None)
    assert since is None


def test_upsert_gates_synced_at_without_gating_the_data_columns():
    """The CASE must leave the plain `col = EXCLUDED.col` sets unconditional --
    an `ON CONFLICT ... WHERE` would skip the whole UPDATE and stop refreshing
    queue_id/agent_id on chats whose timestamps didn't move."""
    conn = _FakeConn()
    upsert_rows(
        conn,
        "chats",
        [{"chat_id": "c1", "queue_id": "q1", "last_user_message_at": None}],
        pk_cols=["chat_id"],
        synced_at_on=["last_user_message_at"],
    )
    sql = conn.sql_log[0]
    assert "queue_id = EXCLUDED.queue_id" in sql
    assert (
        "synced_at = CASE WHEN (chats.last_user_message_at)"
        " IS DISTINCT FROM (EXCLUDED.last_user_message_at)"
        " THEN now() ELSE chats.synced_at END" in sql
    )
    assert "WHERE" not in sql


def test_upsert_without_synced_at_on_leaves_synced_at_untouched():
    """Full-sweep tables (channels/agents/contacts) keep first-seen semantics."""
    conn = _FakeConn()
    upsert_rows(conn, "agents", [{"id": "a1", "is_online": True}], pk_cols=["id"])
    assert "synced_at" not in conn.sql_log[0]


def test_upsert_rejects_synced_at_on_column_missing_from_the_row():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="last_user_message_at"):
        upsert_rows(
            conn,
            "chats",
            [{"chat_id": "c1", "queue_id": "q1"}],
            pk_cols=["chat_id"],
            synced_at_on=["last_user_message_at"],
        )


def test_sync_chats_passes_the_four_api_timestamps():
    assert chats_module.SYNCED_AT_ON == [
        "creation_time",
        "last_session_creation_time",
        "whatsapp_window_close_at",
        "last_user_message_at",
    ]
    # every gating column must exist in the row mapping, or the upsert raises
    row = chat_row(ChatModel.model_validate({"chat": {"chatId": "ch1"}}))
    assert all(c in row for c in chats_module.SYNCED_AT_ON)


def _sessions_route(json=None, status=200):
    return respx.get(f"{BASE}/sessions").mock(
        return_value=httpx.Response(status, json=json if json is not None else {"items": []})
    )


@respx.mock
def test_sync_sessions_ignores_an_open_session_older_than_the_lookback():
    """The bug this fixes: a session stuck open since 2026-06-24 anchored `from`
    to its creation_time, so every run asked for a 41-day range and got a 400."""
    route = _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    stuck_open = datetime(2026, 6, 24, 15, 47, tzinfo=timezone.utc)
    since = until - timedelta(minutes=5)
    sync_sessions(BotmakerClient("tok", BASE), _FakeConn(stuck_open), since, until, include_open=True)
    sent = route.calls[0].request.url.params["from"]
    assert sent == format_datetime(since)
    assert until - _parse_sent(sent) <= sessions_module.MAX_API_RANGE


@respx.mock
def test_sync_sessions_still_widens_for_an_open_session_inside_the_lookback():
    """The clamp must not kill the mechanism: an open session newer than the
    floor but older than `since` still pulls `from` back to it."""
    route = _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    recent_open = until - sessions_module.OPEN_SESSION_LOOKBACK / 2
    since = until - timedelta(minutes=5)
    sync_sessions(BotmakerClient("tok", BASE), _FakeConn(recent_open), since, until, include_open=True)
    assert route.calls[0].request.url.params["from"] == format_datetime(recent_open)


@respx.mock
def test_sync_sessions_keeps_since_when_no_open_session_in_window():
    route = _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    since = until - timedelta(minutes=5)
    sync_sessions(BotmakerClient("tok", BASE), _FakeConn(None), since, until, include_open=True)
    assert route.calls[0].request.url.params["from"] == format_datetime(since)


@respx.mock
def test_sync_sessions_does_not_shrink_a_wider_since():
    """An open session newer than `since` must not narrow the window."""
    route = _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    since = until - timedelta(days=10)
    newer_open = until - timedelta(days=1)
    sync_sessions(BotmakerClient("tok", BASE), _FakeConn(newer_open), since, until, include_open=True)
    assert route.calls[0].request.url.params["from"] == format_datetime(since)


@respx.mock
def test_sync_sessions_rejects_a_manual_range_wider_than_the_api_limit():
    route = _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="exceeds the API limit"):
        sync_sessions(BotmakerClient("tok", BASE), _FakeConn(None), until - timedelta(days=60), until)
    assert route.call_count == 0


@respx.mock
def test_sync_sessions_propagates_400_instead_of_dropping_from():
    """Regression for the removed retry: it swallowed the 400, refetched without
    `from`, and let the run report success over the API's ~1-day default."""
    route = _sessions_route(json={"errors": [{"code": "INVALID_DATETIME_INTERVAL"}]}, status=400)
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    since = until - timedelta(minutes=5)
    with pytest.raises(httpx.HTTPStatusError):
        sync_sessions(BotmakerClient("tok", BASE), _FakeConn(None), since, until, include_open=True)
    assert route.call_count == 1


@respx.mock
def test_sync_sessions_sweeps_expired_open_sessions_after_paging():
    """Order matters: sessions that closed in this run are marked 'event' by the
    upsert first, so the sweep can't relabel them 'window_expired'."""
    _sessions_route(json={"items": [{"id": "s1", "events": [{"name": "conversation-close"}]}]})
    conn = _FakeConn(None)
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    sync_sessions(BotmakerClient("tok", BASE), conn, until - timedelta(minutes=5), until, include_open=True)
    upsert_at = next(i for i, s in enumerate(conn.sql_log) if s.startswith("INSERT INTO sessions"))
    sweep_at = next(i for i, s in enumerate(conn.sql_log) if "window_expired" in s)
    assert upsert_at < sweep_at


@respx.mock
def test_sync_sessions_skips_the_sweep_when_not_tracking_open_sessions():
    _sessions_route()
    until = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)
    since = until - timedelta(minutes=5)
    for kwargs in ({"include_open": False}, {"include_open": True, "close_expired": False}):
        conn = _FakeConn(None)
        sync_sessions(BotmakerClient("tok", BASE), conn, since, until, **kwargs)
        assert not any("window_expired" in s for s in conn.sql_log), kwargs


@respx.mock
def test_sync_sessions_writes_one_row_per_scored_aspect():
    """End to end: the new aspectScores map must reach session_aspect_scores,
    which is what the flat columns can no longer hold."""
    _sessions_route(
        {
            "items": [
                {
                    "id": "s1",
                    "aiAnalysis": {
                        "qualityScore": 80,
                        "aspectScores": {
                            "clarity": {"result": 55, "weight": 20},
                            "empathyTone": {"result": 30, "weight": 10},
                        },
                    },
                }
            ]
        }
    )
    conn = _FakeConn()
    until = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    sync_sessions(BotmakerClient("t", BASE), conn, until - timedelta(minutes=5), until)

    sql = " ".join(str(q) for q in conn.sql_log)
    assert "INSERT INTO session_ai_analysis" in sql
    assert "INSERT INTO session_aspect_scores" in sql
    assert "DELETE FROM session_aspect_scores" in sql


def test_session_row_records_how_the_session_was_closed():
    closed = session_row(
        SessionModel.model_validate({"id": "s1", "events": [{"name": "conversation-close"}]})
    )
    assert (closed["is_open"], closed["closed_reason"]) == (False, "event")
    still_open = session_row(SessionModel.model_validate({"id": "s2", "events": []}))
    assert (still_open["is_open"], still_open["closed_reason"]) == (True, None)


def test_sessions_upsert_latches_the_close_state():
    """A reopened session would resurrect the unbounded-lookback loop, and a
    still-open fetch must not erase a reason already on record."""
    conn = _FakeConn()
    upsert_rows(
        conn,
        "sessions",
        [{"id": "s1", "is_open": True, "closed_reason": None}],
        pk_cols=["id"],
        set_overrides=sessions_module._CLOSE_LATCH,
    )
    sql = conn.sql_log[0]
    assert "is_open = sessions.is_open AND EXCLUDED.is_open" in sql
    assert "closed_reason = COALESCE(EXCLUDED.closed_reason, sessions.closed_reason)" in sql


def test_upsert_rejects_set_overrides_column_missing_from_the_row():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="closed_reason"):
        upsert_rows(
            conn,
            "sessions",
            [{"id": "s1", "is_open": True}],
            pk_cols=["id"],
            set_overrides={"closed_reason": "'event'"},
        )


@respx.mock
def test_sync_contacts_full_sweep_upserts_all_items():
    """sync_contacts reads channel ids from the DB and upserts every contact
    returned — no filtering by touched set (contacts run daily, not per-cron)."""
    respx.get(f"{BASE}/contacts").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "internal-xyz"}, {"id": "internal-abc"}]},
        )
    )
    client = BotmakerClient("tok", BASE)
    # _FakeConn rows simulates SELECT id FROM channels returning one channel
    n = sync_contacts(client, _FakeConn(rows=[("cc1",)]))
    assert n == 2


@respx.mock
def test_sync_agent_metrics_queries_open_and_closed_over_the_same_window():
    """session-status is required and single-valued, so both states cost one
    request each -- and both must carry the identical from/to."""
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"items": []})

    respx.get(f"{BASE}/dashboards/agent-metrics").mock(side_effect=handler)
    since = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    until = since + timedelta(minutes=15)
    sync_agent_metrics(BotmakerClient("t", BASE), _FakeConn(), since, until)

    assert [p["session-status"] for p in seen] == ["open", "closed"]
    assert {p["from"] for p in seen} == {format_datetime(since)}
    assert {p["to"] for p in seen} == {format_datetime(until)}


@respx.mock
def test_sync_agent_metrics_sends_no_queue_or_channel_filter():
    """Omitting both filters is what makes the API return every queue and
    channel; sending either would silently narrow the mirror."""
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"items": []})

    respx.get(f"{BASE}/dashboards/agent-metrics").mock(side_effect=handler)
    since = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    sync_agent_metrics(BotmakerClient("t", BASE), _FakeConn(), since, since + timedelta(minutes=15))

    for params in seen:
        assert "queues" not in params and "channel-ids" not in params


@respx.mock
def test_sync_agent_metrics_supplies_its_own_from_on_the_first_run():
    """Letting the API pick the lower bound is a 400: it defaults `from` to
    ~100 days back and then rejects the range as wider than its 1-month limit."""
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"items": []})

    respx.get(f"{BASE}/dashboards/agent-metrics").mock(side_effect=handler)
    until = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    sync_agent_metrics(BotmakerClient("t", BASE), _FakeConn(), None, until)

    assert seen, "no request was made"
    for params in seen:
        assert params["to"] == format_datetime(until)
        assert params["from"] == format_datetime(until - agent_metrics_module.FIRST_RUN_WINDOW)


def test_sync_agent_metrics_rejects_a_range_wider_than_the_api_limit():
    """Fail before spending the call, so the watermark is never advanced past a
    window that was never actually fetched."""
    until = datetime(2026, 8, 25, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="exceeds the API limit"):
        sync_agent_metrics(
            BotmakerClient("t", BASE), _FakeConn(), until - timedelta(days=45), until
        )


def test_agent_metric_row_coerces_quoted_numbers_and_keeps_the_status():
    item = AgentMetricModel.model_validate(
        {
            "sessionId": "s1",
            "agentId": "a1",
            "chatId": "c1",
            "queue": "Customer Service",
            "avgAttendingTime": "3358",
            "operatorResponses": "5",
            "fromOpAssignedToOpFirstResponse": "615",
        }
    )
    row = metric_row(item, "closed")
    assert (row["session_id"], row["agent_id"], row["session_status"]) == ("s1", "a1", "closed")
    assert row["avg_attending_time"] == 3358
    assert row["operator_responses"] == 5
    assert row["from_op_assigned_to_op_first_response"] == 615


def test_agent_metric_row_keys_an_unassigned_conversation_on_empty_agent():
    """agent_id is half the PK, and a PK column cannot be NULL."""
    item = AgentMetricModel.model_validate({"sessionId": "s1", "queue": "Ventas"})
    assert metric_row(item, "open")["agent_id"] == ""


def test_agent_metric_row_skips_an_item_with_no_session_id():
    assert metric_row(AgentMetricModel.model_validate({"queue": "Ventas"}), "open") is None


def test_agent_metric_reads_a_dash_as_no_value():
    """What the API actually sends for a still-open session: every metric is
    "-". Typing these as plain int aborted the whole page with 2440 errors."""
    item = AgentMetricModel.model_validate(
        {
            "sessionId": "s1",
            "agentId": "a1",
            "avgAttendingTime": "-",
            "onHold": "-",
            "operatorResponses": "3",
        }
    )
    assert item.avg_attending_time is None
    assert item.on_hold is None
    assert item.operator_responses == 3


def test_agent_metric_drops_an_unparseable_number_instead_of_raising(caplog):
    """A single odd value must not abort an unattended 15-minute run."""
    with caplog.at_level("WARNING"):
        item = AgentMetricModel.model_validate({"sessionId": "s1", "onHold": "3,5"})
    assert item.on_hold is None
    assert "3,5" in caplog.text


def test_upsert_strips_nul_bytes_postgres_cannot_store():
    """Botmaker sends NUL (0x00) inside AI-analysis summaries. psycopg raises
    DataError on them, which loses the whole batch, not just the bad row."""
    conn = _FakeConn()
    rows = [{"session_id": "s1", "summary": "hola" + chr(0) + "mundo"}]
    upsert_rows(conn, "session_ai_analysis", rows, pk_cols=["session_id"])
    written = conn.executemany_rows[-1]
    assert written[0]["summary"] == "holamundo"
    # the caller's own row is left untouched
    assert chr(0) in rows[0]["summary"]


def test_replace_children_strips_nul_inside_jsonb():
    """Message content is jsonb, which rejects NUL exactly like text does."""
    conn = _FakeConn()
    payload = Jsonb({"text": "hola" + chr(0), "buttons": ["si" + chr(0), "no"]})
    replace_children(conn, "session_messages", "session_id", "s1", [{"id": "m1", "content": payload}])
    written = conn.executemany_rows[-1][0]["content"]
    assert written.obj == {"text": "hola", "buttons": ["si", "no"]}


def test_scrub_leaves_clean_rows_untouched():
    """No NUL anywhere -> the same objects come back, nothing is rebuilt."""
    payload = Jsonb({"text": "hola"})
    row = {"id": "m1", "content": payload, "from_role": "agent"}
    scrubbed = db_module._scrub_rows("session_messages", [row])[0]
    assert scrubbed["content"] is payload
    assert scrubbed["from_role"] is row["from_role"]


def test_empty_ai_analysis_block_has_no_content():
    """`aiAnalysis: {}` -- what the API returned all through the outage."""
    item = SessionModel.model_validate({"id": "s1", "aiAnalysis": {}})
    assert item.ai_analysis.has_content() is False


def test_does_not_meet_criteria_alone_counts_as_content():
    """It records that Botmaker declined to score the conversation, which is
    not the same as never having analysed it."""
    item = SessionModel.model_validate({"id": "s1", "aiAnalysis": {"doesNotMeetCriteria": True}})
    assert item.ai_analysis.has_content() is True


def test_aspect_scores_alone_count_as_content():
    item = SessionModel.model_validate(
        {"id": "s1", "aiAnalysis": {"aspectScores": {"clarity": {"result": 40, "weight": 20}}}}
    )
    assert item.ai_analysis.has_content() is True


@respx.mock
def test_sync_sessions_writes_no_row_for_an_empty_analysis_block():
    _sessions_route({"items": [{"id": "s1", "aiAnalysis": {}}]})
    conn = _FakeConn()
    until = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    sync_sessions(BotmakerClient("t", BASE), conn, until - timedelta(minutes=5), until)
    assert "session_ai_analysis" not in " ".join(str(q) for q in conn.sql_log)
