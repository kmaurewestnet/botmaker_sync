from collections import defaultdict

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


def sync_contacts(client: BotmakerClient, conn: psycopg.Connection, touched: set[tuple[str, str]]) -> int:
    """No /contacts/{id} endpoint exists. Scope is enforced here: for each
    channel seen in this run's chats, page through GET /contacts?channel-id=...
    and keep only the contact_ids actually touched -- not a full channel mirror.

    `touched` holds (channel_id, contact_id) pairs from the /chats response,
    where contact_id is the platform id (e.g. a phone number). /contacts
    items expose that same value as chats[].platformContactId, not item.id
    (item.id is Botmaker's internal contact id) -- match on that, not item.id.

    ponytail: if a touched contact never turns up in its channel's listing
    (data drift on Botmaker's side), this scans that whole channel once. No
    page-count cutoff added; revisit if /contacts volume becomes a real cost.
    """
    by_channel: dict[str, set[str]] = defaultdict(set)
    for channel_id, contact_id in touched:
        by_channel[channel_id].add(contact_id)

    count = 0
    for channel_id, wanted_ids in by_channel.items():
        remaining = set(wanted_ids)

        # Skip contacts already in the DB — avoids full-channel pagination on
        # every incremental run (most contacts recur across cron windows).
        with conn.cursor() as cur:
            cur.execute(
                "SELECT platform_contact_id FROM contact_chats"
                " WHERE chat_channel_id = %s AND platform_contact_id = ANY(%s)",
                (channel_id, list(remaining)),
            )
            already_known = {row[0] for row in cur.fetchall()}
        remaining -= already_known
        if not remaining:
            continue

        for page in client.get_pages("/contacts", params={"channel-id": channel_id}):
            if not remaining:
                break
            parsed = ContactsPage.model_validate(page)
            for item in parsed.items:
                matched = {c.platform_contact_id for c in item.chats if c.chat_channel_id == channel_id} & remaining
                if not matched:
                    continue
                upsert_rows(conn, TABLE, [_row(item)], pk_cols=["id"])
                _replace_children(conn, item)
                remaining -= matched
                count += 1
            conn.commit()
    return count
