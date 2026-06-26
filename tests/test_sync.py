"""ponytail: one focused check per non-trivial branch (pagination, retry,
row-mapping, watermark window math) -- not an exhaustive per-function suite.
No DB needed: db.py's actual SQL is exercised by the manual end-to-end run
against a real Postgres (see README), not here."""

from datetime import datetime, timedelta, timezone

import httpx
import respx

import botmaker_sync.client as client_module
from botmaker_sync.client import BotmakerClient
from botmaker_sync.db import resolve_window
from botmaker_sync.models import ChatModel, SessionModel
from botmaker_sync.sync.chats import _row as chat_row
from botmaker_sync.sync.contacts import sync_contacts
from botmaker_sync.sync.sessions import _row as session_row

BASE = "https://api.botmaker.com/v2.0"


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


class _FakeCursor:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        pass

    def executemany(self, *args, **kwargs):
        pass

    def fetchall(self):
        return []

    def fetchone(self):
        return (self._value,) if self._value is not None else None


class _FakeConn:
    def __init__(self, watermark=None):
        self._watermark = watermark

    def cursor(self):
        return _FakeCursor(self._watermark)

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


@respx.mock
def test_sync_contacts_matches_by_platform_contact_id_not_internal_id():
    """item.id is Botmaker's internal contact id; touched holds the platform
    id (phone number) seen on the chat. A contact whose internal id differs
    from the touched value must still match via chats[].platformContactId."""
    respx.get(f"{BASE}/contacts").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "internal-xyz",
                        "chats": [{"platformContactId": "549", "chatChannelId": "cc1"}],
                    }
                ]
            },
        )
    )
    client = BotmakerClient("tok", BASE)
    n = sync_contacts(client, _FakeConn(), {("cc1", "549")})
    assert n == 1
