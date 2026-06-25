import psycopg

from botmaker_sync.client import BotmakerClient
from botmaker_sync.db import upsert_rows
from botmaker_sync.models import ChannelModel, ChannelsListResponse

TABLE = "channels"


def _row(item: ChannelModel) -> dict:
    return {
        "id": item.id,
        "platform": item.platform,
        "active": item.active,
        "name": item.name,
        "webhook_id": item.webhook_id,
        "number": item.number,
        "status": item.status,
        "quality": item.quality,
        "waba_id": item.waba_id,
        "trial": item.trial,
        "recipient_id": item.recipient_id,
        "days_to_expire": item.days_to_expire,
        "token": item.token,
        "page_id": item.page_id,
    }


def sync_channels(client: BotmakerClient, conn: psycopg.Connection) -> int:
    """Full refresh: /channels has no time filter and no pagination, so every
    run just upserts the current full list."""
    count = 0
    for page in client.get_pages("/channels"):
        parsed = ChannelsListResponse.model_validate(page)
        rows = [_row(item) for item in parsed.items if item.id]
        upsert_rows(conn, TABLE, rows, pk_cols=["id"])
        count += len(rows)
    conn.commit()
    return count
