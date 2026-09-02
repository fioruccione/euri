#!/usr/bin/env python3
"""Replay read-only delle proposte 2g congelate il 01/09/2026.

Non acquisisce lease, non risolve signal e non salva memorie. Verifica inoltre
che i documenti dei signal e il namespace delle lease siano identici prima e
dopo la costruzione delle domande.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.correction_review import preview_signal_review
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from utils.redis_client import get_client


SIGNAL_PREFIXES = (
    "93528e0c",
    "b1af1a40",
    "079752d1",
    "71015624",
    "7300a04d",
)


def _resolve_signal_key(redis, prefix: str) -> str:
    matches = list(redis.scan_iter(f"euri:correction:{prefix}*"))
    if len(matches) != 1:
        raise RuntimeError(
            f"signal {prefix}: attesa una chiave, trovate {len(matches)}"
        )
    return str(matches[0])


def _snapshot(redis, keys: list[str]) -> tuple[dict, list[str]]:
    docs = {key: redis.json().get(key, "$") for key in keys}
    leases = sorted(str(key) for key in redis.scan_iter("euri:correction_review:*"))
    return docs, leases


def main() -> None:
    redis = get_client()
    keys = [_resolve_signal_key(redis, prefix) for prefix in SIGNAL_PREFIXES]
    before = _snapshot(redis, keys)

    embedder = Embedder()
    embedder.load()
    memory = MemoryManager(redis, embedder=embedder)
    rows = []
    for key in keys:
        review = preview_signal_review(
            redis,
            memory,
            signal_key=key,
            memory_scope="personal",
            include_legacy=True,
        )
        rows.append({
            "signal_id": key.rsplit(":", 1)[-1],
            "mode": (review or {}).get("mode"),
            "target_id": (review or {}).get("target_id"),
            "question": (review or {}).get("question"),
        })

    after = _snapshot(redis, keys)
    if before != after:
        raise RuntimeError("il replay dichiarato read-only ha mutato signal o lease")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("READ_ONLY_OK: signal e lease invariati")


if __name__ == "__main__":
    main()
