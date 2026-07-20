#!/usr/bin/env python3
"""Riclassifica le lezioni da correzione storiche senza cambiarne il contenuto.

Prima del 20/07/2026 il Loop 2g salvava queste interpretazioni di Euri come
``source=passive``. La migrazione le rende ``reaction_lesson``: restano
richiamabili, ma non vengono piu' presentate come fatti estratti dalla voce di
Stefano. E' idempotente e applica modifiche solo con ``--apply``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory_attention import update_loop2e_candidate_index
from redis.commands.search.query import Query
from utils.redis_client import get_client


def migrate(*, apply: bool = False) -> tuple[int, int]:
    redis = get_client()
    found = 0
    changed = 0
    result = redis.ft("idx:memories").search(
        Query("@tags:{from_correction}").paging(0, 1000).no_content()
    )
    for hit in result.docs:
        key = hit.id
        raw = redis.json().get(key, "$")
        if not raw:
            continue
        doc = raw[0]
        tags = set(doc.get("tags") or [])
        if "from_correction" not in tags:
            continue
        found += 1
        ttl = redis.ttl(key)
        needs_change = (
            doc.get("source") != "reaction"
            or doc.get("memory_kind") != "reaction_lesson"
            or doc.get("expires_at") is not None
            or ttl >= 0
        )
        if not needs_change:
            continue
        if not apply:
            continue
        redis.json().set(key, "$.source", "reaction")
        redis.json().set(key, "$.memory_kind", "reaction_lesson")
        redis.json().set(key, "$.expires_at", None)
        redis.persist(key)
        doc["source"] = "reaction"
        doc["memory_kind"] = "reaction_lesson"
        doc["expires_at"] = None
        update_loop2e_candidate_index(redis, doc)
        changed += 1
    return found, changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="applica la riclassificazione")
    args = parser.parse_args()
    found, changed = migrate(apply=args.apply)
    mode = "applicata" if args.apply else "dry-run"
    print(f"Migrazione correction lessons ({mode}): trovate={found}, modificate={changed}")


if __name__ == "__main__":
    main()
