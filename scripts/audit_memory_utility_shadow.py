#!/usr/bin/env python3
"""Mostra l'ultimo rapporto durevole sull'utilità osservata delle memorie."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory_utility_shadow import (
    UTILITY_REPORT_KEY,
    UTILITY_REVIEW_PENDING_KEY,
)
from utils.redis_client import get_client


def main() -> int:
    redis = get_client()
    raw_report = redis.get(UTILITY_REPORT_KEY)
    raw_pending = redis.get(UTILITY_REVIEW_PENDING_KEY)
    report = json.loads(raw_report) if raw_report else {
        "status": "not_collected_yet",
        "message": "Il primo rapporto nasce al prossimo ciclo manutentivo.",
    }
    pending = json.loads(raw_pending) if raw_pending else None
    print(json.dumps(
        {"report": report, "review_pending": pending},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
