#!/usr/bin/env python3
"""Spiega perché un insight è o non è promosso, senza modificarlo."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory_utility_shadow import explain_insight_promotion
from utils.redis_client import get_client


def _text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _latest_trace(redis, insight_key: str) -> dict:
    for _event_id, raw in redis.xrevrange("euri:convergence:trace"):
        fields = {_text(k): _text(v) for k, v in raw.items()}
        if fields.get("seed_id") == insight_key:
            return fields
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("insight_id", help="UUID, suffisso univoco o chiave Redis completa")
    args = parser.parse_args()
    redis = get_client()
    token = args.insight_id.strip()
    if token.startswith("euri:insight:"):
        keys = [token]
    else:
        keys = [
            _text(key)
            for key in redis.scan_iter(match=f"euri:insight:*{token}*")
        ]
    if len(keys) != 1:
        print(json.dumps({
            "error": "insight_not_uniquely_resolved",
            "matches": keys,
        }, ensure_ascii=False, indent=2))
        return 2
    raw = redis.json().get(keys[0], "$")
    if not raw:
        return 2
    insight = raw[0]
    explanation = explain_insight_promotion(
        insight,
        latest_trace=_latest_trace(redis, keys[0]),
    )
    explanation["redis_key"] = keys[0]
    print(json.dumps(explanation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
