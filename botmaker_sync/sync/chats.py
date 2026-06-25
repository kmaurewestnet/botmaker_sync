from datetime import datetime

import psycopg

from botmaker_sync.client import BotmakerClient, format_datetime
from botmaker_sync.db import replace_children, upsert_rows
from botmaker_sync.models import ChatModel, ChatsPage

TABLE = "chats"


def _row(item: ChatModel) -> dict | None:
    ref = item.chat
    if ref is None or not ref.chat_id:
        return None
    return {
        "chat_id": ref.chat_id,
        "channel_id": ref.channel_id,
        "contact_id": ref.contact_id,
        "creation_time": item.creation_time,
        "last_session_creation_time": item.last_session_creation_time,
        "external_id": item.external_id,
        "first_name": item.first_name,
        "last_name": item.last_name,
        "country": item.country,
        "email": item.email,
        "whatsapp_window_close_at": item.whatsapp_window_close_datetime,
        "queue_id": item.queue_id,
        "agent_id": item.agent_id,
        "on_hold_agent_id": item.on_hold_agent_id,
        "last_user_message_at": item.last_user_message_datetime,
        "is_banned": item.is_banned,
        "is_tester": item.is_tester,
        "is_bot_muted": item.is_bot_muted,
    }


def sync_chats(
    client: BotmakerClient,
    conn: psycopg.Connection,
    since: datetime | None,
    until: datetime,
) -> set[tuple[str, str]]:
    """Incremental by last activity. Returns the (channel_id, contact_id) pairs
    touched this run, so the caller can fetch only those contacts."""
    params: dict[str, str] = {"to": format_datetime(until)}
    if since is not None:
        params["from"] = format_datetime(since)

    touched: set[tuple[str, str]] = set()
    for page in client.get_pages("/chats", params=params):
        parsed = ChatsPage.model_validate(page)
        rows = []
        for item in parsed.items:
            row = _row(item)
            if row is None:
                continue
            rows.append(row)
            if row["channel_id"] and row["contact_id"]:
                touched.add((row["channel_id"], row["contact_id"]))
        upsert_rows(conn, TABLE, rows, pk_cols=["chat_id"])
        for item in parsed.items:
            if item.chat is None or not item.chat.chat_id:
                continue
            chat_id = item.chat.chat_id
            replace_children(
                conn,
                "chat_tags",
                "chat_id",
                chat_id,
                [{"chat_id": chat_id, "tag": t} for t in item.tags],
            )
            replace_children(
                conn,
                "chat_variables",
                "chat_id",
                chat_id,
                [{"chat_id": chat_id, "key": k, "value": v} for k, v in item.variables.items()],
            )
        conn.commit()
    return touched
