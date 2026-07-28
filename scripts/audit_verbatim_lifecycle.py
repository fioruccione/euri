#!/usr/bin/env python3
"""Audit read-only del ciclo di vita dei turni verbatim."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import redis

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from core.conversation_turns import audit_verbatim_lifecycle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--grace-days",
        type=int,
        default=config.VERBATIM_UNREFERENCED_GRACE_DAYS,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    client = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
    )
    report = audit_verbatim_lifecycle(client, grace_days=args.grace_days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        counts = report["counts"]
        print("Verbatim lifecycle — AUDIT ONLY")
        print(f"Turni totali: {counts['turns']}")
        print(f"Referenziati: {counts['referenced']}")
        print(f"Non referenziati nel grace period: {counts['recent_unreferenced']}")
        print(f"Candidati orfani: {counts['orphan_candidates']}")
        print(f"Riferimenti mancanti: {counts['missing_source_refs']}")
        print("Nessun dato è stato modificato o cancellato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
