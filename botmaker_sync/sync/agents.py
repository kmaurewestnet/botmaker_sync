from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from botmaker_sync.client import BotmakerClient
from botmaker_sync.db import replace_children, upsert_rows
from botmaker_sync.models import AgentModel, AgentsPage

TABLE = "agents"


def _row(item: AgentModel) -> dict:
    return {
        "id": item.id,
        "email": item.email,
        "name": item.name,
        "alias": item.alias,
        "is_online": item.is_online,
        "status": item.status,
        "role": item.role,
        "slots": item.slots,
        "priority": item.priority,
        "creation_time": item.creation_time,
        "additional_info": Jsonb(item.additional_info) if item.additional_info is not None else None,
    }


def sync_agents(
    client: BotmakerClient,
    conn: psycopg.Connection,
    online: bool | None = None,
    emails: str | None = None,
) -> int:
    """Full refresh: /agents has no time filter, so every run upserts the
    current full list (optionally narrowed by `online`/`emails`)."""
    params: dict[str, str] = {}
    if online is not None:
        params["online"] = "true" if online else "false"
    if emails:
        params["emails"] = emails

    count = 0
    for page in client.get_pages("/agents", params=params):
        parsed = AgentsPage.model_validate(page)
        items = [item for item in parsed.items if item.id]
        upsert_rows(conn, TABLE, [_row(item) for item in items], pk_cols=["id"])
        for item in items:
            replace_children(
                conn,
                "agent_queues",
                "agent_id",
                item.id,
                [{"agent_id": item.id, "queue_id": q} for q in item.queues],
            )
            replace_children(
                conn,
                "agent_groups",
                "agent_id",
                item.id,
                [{"agent_id": item.id, "group_name": g} for g in item.groups],
            )
        count += len(items)
    conn.commit()
    return count
