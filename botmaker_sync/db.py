import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
OVERLAP = timedelta(minutes=5)
DB_CONNECT_MAX_RETRIES = 3
DB_CONNECT_BACKOFF_SECONDS = 1.0
DB_CONNECT_TIMEOUT_SECONDS = 5


def connect(database_url: str) -> psycopg.Connection:
    """Retries transient failures and caps hang time. Once the target is a
    real network host (domain/IP) instead of a local socket, connection
    failures are a real failure mode -- mirrors client.py's retry pattern.
    sslmode is the caller's responsibility via the DSN (e.g. `?sslmode=require`
    for non-localhost targets); not forced here so local dev without TLS still
    works."""
    last_exc: Exception | None = None
    for attempt in range(DB_CONNECT_MAX_RETRIES + 1):
        try:
            return psycopg.connect(database_url, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS)
        except psycopg.OperationalError as exc:
            last_exc = exc
            if attempt < DB_CONNECT_MAX_RETRIES:
                time.sleep(DB_CONNECT_BACKOFF_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


def init_db(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)  # no params -> psycopg sends it as a multi-statement simple query
    conn.commit()


def upsert_rows(conn: psycopg.Connection, table: str, rows: list[dict], pk_cols: list[str]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE. `table`/`pk_cols` are internal constants
    (never user input), so f-string identifiers here carry no injection risk;
    row values always go through parameterized placeholders."""
    if not rows:
        return
    columns = list(rows[0].keys())
    col_list = ", ".join(columns)
    placeholders = ", ".join(f"%({c})s" for c in columns)
    update_cols = [c for c in columns if c not in pk_cols]
    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_clause = f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
    else:
        conflict_clause = f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) {conflict_clause}"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def replace_children(
    conn: psycopg.Connection, table: str, parent_col: str, parent_id: str, rows: list[dict]
) -> None:
    """Wholesale replace of a parent's child rows: DELETE then INSERT. Correct and
    simple given per-parent child volumes here are small (tags, variables, messages)."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE {parent_col} = %s", (parent_id,))
        if rows:
            columns = list(rows[0].keys())
            col_list = ", ".join(columns)
            placeholders = ", ".join(f"%({c})s" for c in columns)
            cur.executemany(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", rows)


def get_watermark(conn: psycopg.Connection, entity: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT last_watermark FROM sync_state WHERE entity = %s", (entity,))
        row = cur.fetchone()
    return row[0] if row else None


def resolve_window(
    conn: psycopg.Connection,
    entity: str,
    since: datetime | None,
    until: datetime | None,
) -> tuple[datetime | None, datetime]:
    """Build the from/to window for an incremental entity sync.
    Explicit --since/--until (non-None) always win and bypass the watermark
    entirely -- callers should NOT advance the watermark when they were used,
    so an ad-hoc manual range doesn't disturb the regular incremental cursor.
    Otherwise: from = last watermark minus a small overlap (None on first run,
    letting the API apply its own default window), to = now()."""
    resolved_until = until or datetime.now(timezone.utc)
    if since is not None or until is not None:
        return since, resolved_until
    watermark = get_watermark(conn, entity)
    resolved_since = watermark - OVERLAP if watermark else None
    return resolved_since, resolved_until


def set_watermark(
    conn: psycopg.Connection, entity: str, watermark: datetime, status: str = "ok", note: str | None = None
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (entity, last_watermark, last_run_at, last_status, note)
            VALUES (%s, %s, now(), %s, %s)
            ON CONFLICT (entity) DO UPDATE SET
                last_watermark = EXCLUDED.last_watermark,
                last_run_at = EXCLUDED.last_run_at,
                last_status = EXCLUDED.last_status,
                note = EXCLUDED.note
            """,
            (entity, watermark, status, note),
        )
