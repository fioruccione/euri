#!/usr/bin/env python3
"""Audit read-only della timeline Pulse cognitiva."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cognitive_projector import (
    COGNITIVE_PROJECTOR_GROUP,
    COGNITIVE_PROJECTOR_STATE,
    COGNITIVE_STREAM,
)
from core.pulse import PULSE_STREAM
from utils.redis_client import get_client


def _group_state(r) -> dict:
    try:
        for row in r.xinfo_groups(PULSE_STREAM):
            if row.get("name") == COGNITIVE_PROJECTOR_GROUP:
                return {
                    "name": row.get("name"),
                    "pending": row.get("pending"),
                    "lag": row.get("lag"),
                    "last_delivered_id": row.get("last-delivered-id"),
                }
    except Exception as exc:
        return {"error": str(exc)}
    return {"missing": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    r = get_client()
    rows = r.xrevrange(COGNITIVE_STREAM, count=max(1, args.limit))
    event_types = Counter()
    producers = Counter()
    missing_trace = 0
    with_cause = 0
    for _event_id, event in rows:
        event_types[f"{event.get('sense', '')}/{event.get('kind', '')}"] += 1
        producers[event.get("producer") or "(vuoto)"] += 1
        missing_trace += int(not event.get("trace_id"))
        with_cause += int(bool(event.get("causation_id")))

    report = {
        "pulse_events": r.xlen(PULSE_STREAM),
        "cognitive_events": r.xlen(COGNITIVE_STREAM),
        "sampled": len(rows),
        "sample_missing_trace": missing_trace,
        "sample_with_causation": with_cause,
        "event_types": dict(event_types.most_common()),
        "producers": dict(producers.most_common()),
        "projector_group": _group_state(r),
        "projector_state": r.hgetall(COGNITIVE_PROJECTOR_STATE),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
