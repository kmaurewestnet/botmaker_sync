"""Diagnostic: dump the raw `aiAnalysis` blocks returned by GET /sessions.

The sync writes a session_ai_analysis row whenever the API returns an
`aiAnalysis` object, even an empty/partial one -- so all-NULL rows in that
table mean the API sent nothing useful, not that the mapping is broken. This
script shows exactly which keys arrive, so the two cases can be told apart.

    python scripts/probe_ai_analysis.py [HOURS_BACK] [--max N]

Read-only: it hits the API and prints. It never touches the database.
Note this counts against BI data source consumption, same as a normal run.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Run as `python scripts/probe_ai_analysis.py`: sys.path[0] is scripts/, not the
# repo root, so the package wouldn't import. The package isn't pip-installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botmaker_sync.client import BotmakerClient
from botmaker_sync.config import load_settings

API_FMT = "%Y-%m-%dT%H:%M:%SZ"


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe_ai_analysis")
    parser.add_argument("hours", nargs="?", type=int, default=24, help="Hours back from now (default: 24)")
    parser.add_argument("--max", dest="max_sessions", type=int, default=300, help="Stop after N sessions (default: 300)")
    parser.add_argument("--samples", type=int, default=3, help="Non-empty blocks to print in full (default: 3)")
    args = parser.parse_args()

    settings = load_settings()
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=args.hours)
    params = {
        "from": since.strftime(API_FMT),
        "to": until.strftime(API_FMT),
        "include-ai-analysis": "true",
        "include-open-sessions": "true",
    }

    shapes: Counter[str] = Counter()
    total = present = 0
    samples: list[tuple[str | None, dict]] = []

    with BotmakerClient(settings.access_token, settings.api_base_url) as client:
        for page in client.get_pages("/sessions", params=params):
            for item in page.get("items", []):
                total += 1
                analysis = item.get("aiAnalysis")
                if analysis is None:
                    shapes["<no aiAnalysis field>"] += 1
                    continue
                present += 1
                shapes[",".join(sorted(analysis)) or "<empty object {}>"] += 1
                if analysis and len(samples) < args.samples:
                    samples.append((item.get("id"), analysis))
            if total >= args.max_sessions:
                break

    print(f"window: {params['from']} -> {params['to']}")
    print(f"sessions: {total} | with an aiAnalysis field: {present}\n")
    print("aiAnalysis shapes (keys -> count):")
    for keys, n in shapes.most_common():
        print(f"  {n:5d}  {keys}")
    for session_id, analysis in samples:
        print(f"\n--- sample {session_id} ---")
        print(json.dumps(analysis, indent=2, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
