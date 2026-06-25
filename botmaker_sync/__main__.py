from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from botmaker_sync.client import BotmakerClient
from botmaker_sync.config import load_settings
from botmaker_sync.db import connect, init_db, resolve_window, set_watermark
from botmaker_sync.sync.agents import sync_agents
from botmaker_sync.sync.channels import sync_channels
from botmaker_sync.sync.chats import sync_chats
from botmaker_sync.sync.contacts import sync_contacts
from botmaker_sync.sync.sessions import sync_sessions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("botmaker_sync")

ALL_ENTITIES = ["channels", "agents", "chats", "sessions"]


def _parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cmd_init_db(args: argparse.Namespace) -> None:
    settings = load_settings()
    with connect(settings.database_url) as conn:
        init_db(conn)
    logger.info("Schema applied.")


def cmd_run(args: argparse.Namespace) -> None:
    settings = load_settings()
    entities = args.entities.split(",") if args.entities else ALL_ENTITIES
    unknown = set(entities) - set(ALL_ENTITIES)
    if unknown:
        raise SystemExit(f"Unknown entities: {', '.join(sorted(unknown))}. Valid: {', '.join(ALL_ENTITIES)}")

    # --since/--until set explicitly -> ad-hoc manual range, watermark is left untouched.
    manual_range = args.since is not None or args.until is not None

    with connect(settings.database_url) as conn, BotmakerClient(settings.access_token, settings.api_base_url) as client:
        if "channels" in entities:
            n = sync_channels(client, conn)
            logger.info("channels: %d upserted", n)

        if "agents" in entities:
            n = sync_agents(client, conn)
            logger.info("agents: %d upserted", n)

        if "chats" in entities:
            since, until = resolve_window(conn, "chats", args.since, args.until)
            logger.info("chats: window %s -> %s", since, until)
            touched = sync_chats(client, conn, since, until)
            logger.info("chats: %d touched", len(touched))
            if not manual_range:
                set_watermark(conn, "chats", until)
                conn.commit()

            n = sync_contacts(client, conn, touched)
            logger.info("contacts: %d upserted (scoped to this run's chats)", n)

        if "sessions" in entities:
            since, until = resolve_window(conn, "sessions", args.since, args.until)
            logger.info("sessions: window %s -> %s", since, until)
            n = sync_sessions(
                client,
                conn,
                since,
                until,
                include_open=args.include_open_sessions,
                include_ai_analysis=args.include_ai_analysis,
            )
            logger.info("sessions: %d upserted", n)
            if not manual_range:
                set_watermark(conn, "sessions", until)
                conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="botmaker_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create/update the Postgres schema").set_defaults(func=cmd_init_db)

    run_parser = sub.add_parser("run", help="Sync entities from the Botmaker API")
    run_parser.add_argument("--since", type=_parse_datetime, default=None, help="ISO datetime; overrides the watermark and is not persisted")
    run_parser.add_argument("--until", type=_parse_datetime, default=None, help="ISO datetime; defaults to now()")
    run_parser.add_argument("--entities", default=None, help=f"Comma-separated subset of {ALL_ENTITIES} (default: all)")
    run_parser.add_argument("--include-ai-analysis", action="store_true")
    run_parser.add_argument("--include-open-sessions", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
