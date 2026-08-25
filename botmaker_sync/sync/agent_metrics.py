from __future__ import annotations

import logging
from datetime import datetime, timedelta

import psycopg

from botmaker_sync.client import BotmakerClient, format_datetime
from botmaker_sync.db import upsert_rows
from botmaker_sync.models import AgentMetricModel, AgentMetricsPage

logger = logging.getLogger(__name__)

TABLE = "agent_metrics"
PATH = "/dashboards/agent-metrics"

# `session-status` is required by the API and its documented values are a single
# state, so covering both costs one request each. The endpoint's own description
# mentions a 'both' value, but it is absent from the parameter's examples and
# unverified against the live API -- two explicit calls is the documented path.
SESSION_STATUSES = ("open", "closed")

# `queues` and `channel-ids` are deliberately not sent: both are filters, and
# omitting them is what returns every queue and every channel. Verified against
# the live API on 2026-08-25: the same window returned an identical 41 items
# with and without an explicit list of all 21 channel ids.

# `from` is documented as optional ("defaults to the last hour"). It is not.
# Omitting it makes the API default `from` to roughly 100 days back and `to` to
# now, which then trips its own range limit:
#   400 INVALID_DATETIME_INTERVAL -- "The difference between 'from' and 'to'
#   cannot be greater than 1 month"
# So the first run, before any watermark exists, has to supply its own lower
# bound instead of letting the API pick one.
FIRST_RUN_WINDOW = timedelta(hours=1)

# Undocumented for this endpoint, but enforced by it (see above) and the same
# limit /sessions applies.
MAX_API_RANGE = timedelta(days=30)


def _row(item: AgentMetricModel, session_status: str) -> dict | None:
    if not item.session_id:
        return None
    return {
        "session_id": item.session_id,
        # '' rather than NULL: agent_id is half the primary key. An unassigned
        # conversation still carries queue-level metrics worth keeping.
        "agent_id": item.agent_id or "",
        "chat_id": item.chat_id,
        "session_creation_time": item.session_creation_time,
        "closed_time": item.closed_time,
        "session_status": session_status,
        "queue": item.queue,
        "agent_name": item.agent_name,
        "typification": item.typification,
        "conversation_link": item.conversation_link,
        "avg_attending_time": item.avg_attending_time,
        "avg_response_time": item.avg_response_time,
        "op_response_time": item.op_response_time,
        "from_queue_asign_to_op_assigned": item.from_queue_asign_to_op_assigned,
        "from_session_start_to_op_first_response": item.from_session_start_to_op_first_response,
        "from_queue_asign_to_op_first_response": item.from_queue_asign_to_op_first_response,
        "from_op_assigned_to_op_first_response": item.from_op_assigned_to_op_first_response,
        "from_queue_asign_to_session_closed": item.from_queue_asign_to_session_closed,
        "from_op_assignation_to_session_closed": item.from_op_assignation_to_session_closed,
        "open_sessions": item.open_sessions,
        "closed_sessions": item.closed_sessions,
        "on_hold": item.on_hold,
        "operator_responses": item.operator_responses,
        "session_transfer_in": item.session_transfer_in,
        "session_transfer_out": item.session_transfer_out,
        "session_transfer_out_no_messages": item.session_transfer_out_no_messages,
        "closed_with_no_messages": item.closed_with_no_messages,
        "timeout_no_messages": item.timeout_no_messages,
        "agent_timeout": item.agent_timeout,
        "user_timeout": item.user_timeout,
        "session_timeout": item.session_timeout,
    }


def sync_agent_metrics(
    client: BotmakerClient,
    conn: psycopg.Connection,
    since: datetime | None,
    until: datetime,
) -> int:
    """Incremental by session creation time, one pass per session status.

    Scoped to the run's window only (no lookback): a conversation that starts in
    one window and closes in a later one is re-reported by the API under its
    original creation time, so its final metrics land in whichever run still
    covers that timestamp -- past that, the closed-side numbers stay as last
    seen. Widening the window would fix that at a BI cost per run, and was
    weighed and declined; see README."""
    if since is None:
        since = until - FIRST_RUN_WINDOW
    if until - since > MAX_API_RANGE:
        raise ValueError(
            f"agent_metrics: requested range {until - since} exceeds the API limit of "
            f"{MAX_API_RANGE}. Narrow --since/--until."
        )

    params_base = {"from": format_datetime(since), "to": format_datetime(until)}

    count = 0
    for status in SESSION_STATUSES:
        params = {**params_base, "session-status": status}
        for page in client.get_pages(PATH, params=params):
            parsed = AgentMetricsPage.model_validate(page)
            rows = [row for item in parsed.items if (row := _row(item, status)) is not None]
            upsert_rows(conn, TABLE, rows, pk_cols=["session_id", "agent_id"])
            conn.commit()
            count += len(rows)
        logger.info("agent_metrics: %s pass done (%d rows so far)", status, count)
    return count
