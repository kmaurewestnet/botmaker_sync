from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from botmaker_sync.client import BotmakerClient, format_datetime
from botmaker_sync.db import replace_children, upsert_rows
from botmaker_sync.models import SessionMessageModel, SessionModel, SessionsPage

TABLE = "sessions"


def _row(item: SessionModel) -> dict | None:
    if not item.id:
        return None
    ref = item.chat.chat if item.chat else None
    return {
        "id": item.id,
        "chat_id": ref.chat_id if ref else None,
        "channel_id": ref.channel_id if ref else None,
        "contact_id": ref.contact_id if ref else None,
        "creation_time": item.creation_time,
        "starting_cause": item.starting_cause,
    }


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
) -> int:
    """Incremental by session start time. A session's 'final variable state'
    (include-variables=true) comes back as `chat.variables` -- SessionResponse
    has no variables field of its own, it reuses ChatResponse's."""
    params: dict[str, str] = {
        "to": format_datetime(until),
        "include-messages": "true",
        "include-variables": "true",
        "include-events": "true",
    }
    if since is not None:
        params["from"] = format_datetime(since)
    if include_open:
        params["include-open-sessions"] = "true"
    if include_ai_analysis:
        params["include-ai-analysis"] = "true"

    count = 0
    for page in client.get_pages("/sessions", params=params):
        parsed = SessionsPage.model_validate(page)
        rows = [row for item in parsed.items if (row := _row(item)) is not None]
        upsert_rows(conn, TABLE, rows, pk_cols=["id"])

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
    return count
