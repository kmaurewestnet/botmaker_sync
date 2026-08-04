from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
from psycopg.types.json import Jsonb

from botmaker_sync.client import BotmakerClient, format_datetime
from botmaker_sync.db import replace_children, upsert_rows
from botmaker_sync.models import SessionMessageModel, SessionModel, SessionsPage

logger = logging.getLogger(__name__)

TABLE = "sessions"

# How far back the `from` extension will reach for still-open sessions. Kept
# safely under MAX_API_RANGE: an unbounded reach is what broke this sync before
# (a session stuck open since 2026-06-24 pushed every request to a 41-day range,
# past the API limit -> 400 on every run, forever).
OPEN_SESSION_LOOKBACK = timedelta(days=25)

# The API rejects from/to ranges wider than ~1 month unless long-term-search=true,
# which bills BI usage and is deliberately never sent (see README).
MAX_API_RANGE = timedelta(days=30)

# Latch the close state: once a session is closed it stays closed, and an
# observed 'event' close always wins over an assumed 'window_expired' one, while
# a NULL (still-open) fetch never erases a reason already on record.
_CLOSE_LATCH = {
    "is_open": f"{TABLE}.is_open AND EXCLUDED.is_open",
    "closed_reason": f"COALESCE(EXCLUDED.closed_reason, {TABLE}.closed_reason)",
}


def _row(item: SessionModel) -> dict | None:
    if not item.id:
        return None
    ref = item.chat.chat if item.chat else None
    is_open = not any(e.name == "conversation-close" for e in item.events)
    return {
        "id": item.id,
        "chat_id": ref.chat_id if ref else None,
        "channel_id": ref.channel_id if ref else None,
        "contact_id": ref.contact_id if ref else None,
        "creation_time": item.creation_time,
        "starting_cause": item.starting_cause,
        "is_open": is_open,
        # is_open is derived from the presence of conversation-close, so a False
        # here is always an observed close.
        "closed_reason": None if is_open else "event",
    }


def _close_expired_sessions(conn: psycopg.Connection, floor: datetime) -> int:
    """Force-close sessions that fell out of the lookback window. Past `floor`
    the API no longer returns them, so their close event can never be observed;
    leaving them open would re-anchor the `from` extension forever."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET is_open = false, closed_reason = 'window_expired'"
            " WHERE is_open = true AND creation_time < %s",
            (floor,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def _message_row(session_id: str, m: SessionMessageModel) -> dict:
    return {
        "id": m.id,
        "session_id": session_id,
        "creation_time": m.creation_time,
        "from_role": m.from_role,
        "agent_id": m.agent_id,
        "queue_id": m.queue_id,
        "content": Jsonb(m.content) if m.content is not None else None,
        "encryption_params": Jsonb(m.encryption_params) if m.encryption_params is not None else None,
    }


def sync_sessions(
    client: BotmakerClient,
    conn: psycopg.Connection,
    since: datetime | None,
    until: datetime,
    include_open: bool = False,
    include_ai_analysis: bool = False,
    close_expired: bool = True,
) -> int:
    """Incremental by session start time. A session's 'final variable state'
    (include-variables=true) comes back as `chat.variables` -- SessionResponse
    has no variables field of its own, it reuses ChatResponse's."""
    if since is not None and until - since > MAX_API_RANGE:
        raise ValueError(
            f"sessions: requested range {until - since} exceeds the API limit of "
            f"{MAX_API_RANGE} (long-term-search is disabled by policy, see README). "
            "Narrow --since/--until."
        )

    params: dict[str, str] = {
        "to": format_datetime(until),
        "include-messages": "true",
        "include-variables": "true",
        "include-events": "true",
    }
    if since is not None:
        params["from"] = format_datetime(since)
    floor = until - OPEN_SESSION_LOOKBACK
    if include_open:
        params["include-open-sessions"] = "true"
        # Extend 'from' to cover sessions that were open last run but may have
        # closed since — they're outside the current watermark window and won't
        # appear otherwise. Bounded by `floor`: reaching all the way back to the
        # oldest open session is what pushed the range past the API limit and
        # made every run 400. Sessions older than `floor` are unreachable anyway
        # and get force-closed by the sweep below.
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MIN(creation_time) FROM {TABLE}"
                " WHERE is_open = true AND creation_time >= %s",
                (floor,),
            )
            row = cur.fetchone()
        earliest_open: datetime | None = row[0] if row else None
        if earliest_open is not None:
            current_from = since or datetime.min.replace(tzinfo=timezone.utc)
            if earliest_open < current_from:
                params["from"] = format_datetime(earliest_open)
    if include_ai_analysis:
        params["include-ai-analysis"] = "true"

    count = 0
    # No retry-on-400 here. There used to be one that dropped 'from' and retried,
    # blaming container clock skew; the real cause was the unbounded lookback
    # above, and dropping 'from' made the API fall back to its ~1-day default so
    # the run reported success while silently syncing a fraction of the window.
    # With the range now bounded, a 400 means something we don't understand:
    # let it propagate so __main__ never reaches set_watermark and the next run
    # retries the same window instead of skipping past it.
    try:
        for page in client.get_pages("/sessions", params=params):
            parsed = SessionsPage.model_validate(page)
            rows = [row for item in parsed.items if (row := _row(item)) is not None]
            upsert_rows(conn, TABLE, rows, pk_cols=["id"], set_overrides=_CLOSE_LATCH)

            for item in parsed.items:
                if not item.id:
                    continue
                session_id = item.id

                msg_rows = [_message_row(session_id, m) for m in item.messages if m.id]
                replace_children(conn, "session_messages", "session_id", session_id, msg_rows)

                event_rows = [
                    {
                        "session_id": session_id,
                        "seq": i,
                        "name": e.name,
                        "creation_time": e.creation_time,
                        "info": Jsonb(e.info) if e.info is not None else None,
                    }
                    for i, e in enumerate(item.events)
                ]
                replace_children(conn, "session_events", "session_id", session_id, event_rows)

                variables = item.chat.variables if item.chat else {}
                var_rows = [{"session_id": session_id, "key": k, "value": v} for k, v in variables.items()]
                replace_children(conn, "session_variables", "session_id", session_id, var_rows)

                if include_ai_analysis and item.ai_analysis is not None:
                    a = item.ai_analysis
                    scores = a.aspect_scores
                    upsert_rows(
                        conn,
                        "session_ai_analysis",
                        [
                            {
                                "session_id": session_id,
                                "summary": a.summary,
                                "does_not_meet_criteria": a.does_not_meet_criteria,
                                "name": a.name,
                                "justification": a.justification,
                                "quality_score": a.quality_score,
                                "aspect_conciseness": scores.conciseness if scores else None,
                                "aspect_clarity": scores.clarity if scores else None,
                                "aspect_empathy_tone": scores.empathy_tone if scores else None,
                                "aspect_understanding": scores.understanding if scores else None,
                                "aspect_resolution": scores.resolution if scores else None,
                            }
                        ],
                        pk_cols=["session_id"],
                    )
            conn.commit()
            count += len(rows)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "sessions: %s for window %s -> %s (%s wide); long-term-search is disabled by policy",
            exc.response.status_code,
            params.get("from", "<API default>"),
            params["to"],
            until - since if since is not None else "<unbounded>",
        )
        raise

    # After paging, never before: sessions that genuinely closed in this run are
    # already marked 'event', so the sweep can't mislabel them. And because it
    # sits past the fetch, an API failure aborts before it runs — a bad request
    # must not mass-close sessions on the strength of stale data.
    if include_open and close_expired:
        expired = _close_expired_sessions(conn, floor)
        if expired:
            logger.info("sessions: %d open past the lookback window marked window_expired", expired)

    return count
