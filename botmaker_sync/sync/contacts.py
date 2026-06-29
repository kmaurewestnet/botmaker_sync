from __future__ import annotations

import psycopg

from botmaker_sync.client import BotmakerClient
from botmaker_sync.db import replace_children, upsert_rows
from botmaker_sync.models import ContactModel, ContactsPage

TABLE = "contacts"


def _row(item: ContactModel) -> dict:
    return {
        "id": item.id,
        "first_name": item.first_name,
        "last_name": item.last_name,
        "birthday": item.birthday,
        "picture_url": item.picture_url,
        "language": item.language,
        "country": item.country,
        "company_id": item.company_id,
        "job_title": item.job_title,
    }


def _replace_children(conn: psycopg.Connection, item: ContactModel) -> None:
    cid = item.id
    replace_children(
        conn,
        "contact_phones",
        "contact_id",
        cid,
        [{"contact_id": cid, "seq": i, "value": f.value, "label": f.label} for i, f in enumerate(item.phone_numbers)],
    )
    replace_children(
        conn,
        "contact_emails",
        "contact_id",
        cid,
        [{"contact_id": cid, "seq": i, "value": f.value, "label": f.label} for i, f in enumerate(item.emails)],
    )
    replace_children(
        conn,
        "contact_addresses",
        "contact_id",
        cid,
        [{"contact_id": cid, "seq": i, "value": f.value, "label": f.label} for i, f in enumerate(item.addresses)],
    )
    replace_children(
        conn,
        "contact_websites",
        "contact_id",
        cid,
        [{"contact_id": cid, "seq": i, "value": f.value, "label": f.label} for i, f in enumerate(item.websites)],
    )
    replace_children(
        conn,
        "contact_notes",
        "contact_id",
        cid,
        [{"contact_id": cid, "seq": i, "note": n} for i, n in enumerate(item.notes)],
    )
    social_rows = (
        [{"contact_id": cid, "network": "instagram", "value": v} for v in item.instagram_ids]
        + [{"contact_id": cid, "network": "facebook", "value": v} for v in item.facebook_ids]
        + [{"contact_id": cid, "network": "twitter", "value": v} for v in item.twitter_ids]
        + [{"contact_id": cid, "network": "whatsapp_bsuid", "value": v} for v in item.whatsapp_bsuids]
    )
    replace_children(conn, "contact_social", "contact_id", cid, social_rows)
    replace_children(
        conn,
        "contact_chats",
        "contact_id",
        cid,
        [
            {
                "contact_id": cid,
                "seq": i,
                "platform_chat_id": c.id,
                "platform_contact_id": c.platform_contact_id,
                "chat_channel_id": c.chat_channel_id,
                "bsuid": c.bsuid,
            }
            for i, c in enumerate(item.chats)
        ],
    )


def sync_contacts(client: BotmakerClient, conn: psycopg.Connection) -> int:
    """Full sweep: page through every channel and upsert all contacts found.

    Contacts are CRM profile data — slow-changing, not conversational. Running
    this on every 15-min cron caused hundreds of API pages per new contact
    (listings are oldest-first; new contacts appear at the end). Decoupled to
    a daily cron instead: one sweep per day, no per-run filtering needed.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM channels WHERE active = true OR active IS NULL")
        channel_ids = [row[0] for row in cur.fetchall()]

    count = 0
    for channel_id in channel_ids:
        for page in client.get_pages("/contacts", params={"channel-id": channel_id}):
            parsed = ContactsPage.model_validate(page)
            for item in parsed.items:
                if not item.id:
                    continue
                upsert_rows(conn, TABLE, [_row(item)], pk_cols=["id"])
                _replace_children(conn, item)
                count += 1
            conn.commit()
    return count
