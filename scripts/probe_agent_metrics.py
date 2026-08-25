"""Diagnostic: find which /dashboards/agent-metrics parameter set the API accepts.

The endpoint returned 400 for the documented minimum (`session-status` alone),
and the spec is ambiguous about which of `from`, `to` and `channel-ids` are
really required. This tries the combinations one by one and prints the status
plus the response body, which is where Botmaker names the offending param.

    python scripts/probe_agent_metrics.py

Read-only: it hits the API and prints. It never touches the database.
Requests are spaced ~1.2s apart -- the endpoint allows one per second.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botmaker_sync.config import load_settings

API_FMT = "%Y-%m-%dT%H:%M:%SZ"
PATH = "/dashboards/agent-metrics"
RATE_LIMIT_SLEEP = 1.2


def main() -> int:
    settings = load_settings()
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=15)
    frm, to = since.strftime(API_FMT), until.strftime(API_FMT)

    with httpx.Client(
        base_url=settings.api_base_url,
        headers={"access-token": settings.access_token},
        timeout=60.0,
    ) as client:
        channel_ids: list[str] = []
        resp = client.get("/channels")
        if resp.status_code == 200:
            channel_ids = [c["id"] for c in resp.json().get("items", []) if c.get("id")]
            print(f"channels: {len(channel_ids)} -> {channel_ids[:3]}{'...' if len(channel_ids) > 3 else ''}\n")
        else:
            print(f"channels: {resp.status_code}, continuing without channel-ids\n")

        attempts: list[tuple[str, dict]] = [
            ("status only (open)", {"session-status": "open"}),
            ("status only (closed)", {"session-status": "closed"}),
            ("status=both", {"session-status": "both"}),
            ("open + from", {"session-status": "open", "from": frm}),
            ("open + from + to", {"session-status": "open", "from": frm, "to": to}),
            ("closed + from + to", {"session-status": "closed", "from": frm, "to": to}),
            ("open + from + to + online-status=all",
             {"session-status": "open", "from": frm, "to": to, "online-status": "all"}),
        ]
        if channel_ids:
            attempts.append(
                ("open + from + to + channel-ids",
                 {"session-status": "open", "from": frm, "to": to, "channel-ids": channel_ids})
            )
            attempts.append(
                ("open + channel-ids",
                 {"session-status": "open", "channel-ids": channel_ids})
            )

        for label, params in attempts:
            time.sleep(RATE_LIMIT_SLEEP)
            resp = client.get(PATH, params=params)
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                extra = f"{len(items)} items"
                if items:
                    extra += f" | keys: {','.join(sorted(items[0]))[:120]}"
                print(f"  200  {label:38s} {extra}")
            else:
                print(f"  {resp.status_code}  {label:38s} {resp.text[:200]}")

    print("\nwindow used:", frm, "->", to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
