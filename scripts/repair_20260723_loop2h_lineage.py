#!/usr/bin/env python3
"""Completa la lineage della reflection Loop 2h nata il 23 luglio.

Senza ``--apply`` esegue soltanto validazione e anteprima. Il contenuto non viene
riscritto: era coerente con le correzioni, ma mancavano parent e confine epistemico.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from utils.obsidian_sync import write_memory
from utils.redis_client import get_client


REFLECTION_ID = "92bac556-5037-4ed7-91ea-4c128ae11d7b"
PARENTS = [
    "10b6d176-df90-431c-a4c0-1eb88ddc29c9",
    "fd66ecb3-3057-4f1b-a93c-95029126c48a",
    "8f95c747-194a-493f-826a-98d5090530f4",
    "03948410-29dc-436f-a0bd-557121a02b30",
]
PAIRS = [
    {
        "loser_id": "10b6d176-df90-431c-a4c0-1eb88ddc29c9",
        "winner_id": "fd66ecb3-3057-4f1b-a93c-95029126c48a",
        "pair_key": (
            "10b6d176-df90-431c-a4c0-1eb88ddc29c9|"
            "fd66ecb3-3057-4f1b-a93c-95029126c48a"
        ),
    },
    {
        "loser_id": "8f95c747-194a-493f-826a-98d5090530f4",
        "winner_id": "03948410-29dc-436f-a0bd-557121a02b30",
        "pair_key": (
            "03948410-29dc-436f-a0bd-557121a02b30|"
            "8f95c747-194a-493f-826a-98d5090530f4"
        ),
    },
]
AUDIT_EVENT_ID = "repair-20260723-loop2h-lineage-v1"


def _doc(redis, memory_id: str) -> dict:
    raw = redis.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _validate(redis, reflection: dict) -> None:
    tags = set(reflection.get("tags") or [])
    if reflection.get("source") != "reflection" or "loop2h" not in tags:
        raise RuntimeError("il record non è la reflection Loop 2h attesa")
    if "`03ppr102`" not in (reflection.get("content") or ""):
        raise RuntimeError("contenuto della reflection diverso dal caso atteso")
    for pair in PAIRS:
        loser = _doc(redis, pair["loser_id"])
        _doc(redis, pair["winner_id"])
        current_winner = loser.get("superseded_by")
        if isinstance(current_winner, list):
            current_winner = current_winner[0] if current_winner else None
        if current_winner != pair["winner_id"]:
            raise RuntimeError(
                f"arco cambiato: {pair['loser_id']} -> {current_winner}"
            )


def repair(*, apply: bool) -> None:
    redis = get_client()
    reflection = _doc(redis, REFLECTION_ID)
    _validate(redis, reflection)

    print("reflection:", REFLECTION_ID)
    print("parent unici:", len(PARENTS))
    print("coppie causali:", len(PAIRS), "(il log live ne contava 3 per uno SCAN duplicato)")
    print(
        "stato epistemico:",
        reflection.get("epistemic_status"),
        "-> internal_self_observation",
    )
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    key = f"euri:memory:{REFLECTION_ID}"
    # Un retry deve convergere allo stesso documento, incluso il timestamp audit.
    repaired_at = float(reflection.get("lineage_repaired_at") or time.time())
    redis.json().set(key, "$.requires_verification", True)
    redis.json().set(key, "$.epistemic_status", "internal_self_observation")
    redis.json().set(
        key,
        "$.verification_status",
        "narrative_derived_from_supersession",
    )
    redis.json().set(key, "$.source_memory_ids", PARENTS)
    redis.json().set(key, "$.self_observation_pairs", PAIRS)
    redis.json().set(key, "$.lineage_repaired_at", repaired_at)

    repaired_doc = _doc(redis, REFLECTION_ID)
    if write_memory(repaired_doc) is False:
        raise RuntimeError("aggiornamento Obsidian non completato")

    emitted = pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "incident_id": AUDIT_EVENT_ID,
            "reflection_id": REFLECTION_ID,
            "source_memory_ids": PARENTS,
            "supersession_pairs": PAIRS,
            "duplicate_scan_pair_removed": 1,
        },
        salience=0.65,
        marker_key=f"euri:audit:{AUDIT_EVENT_ID}",
        event_class=COGNITIVE_EVENT,
        producer="repair_20260723_loop2h_lineage",
        trace_id=f"reflection:{REFLECTION_ID}",
        entity_refs=[{
            "type": "memory",
            "id": REFLECTION_ID,
            "role": "lineage_repaired",
        }],
        parent_refs=PARENTS,
        epistemic_before="unscoped_reflection",
        epistemic_after="internal_self_observation_requires_verification",
    )
    if not emitted:
        raise RuntimeError("evento audit non emesso; ripetere l'apply")
    print("APPLY: lineage Loop 2h completata")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
