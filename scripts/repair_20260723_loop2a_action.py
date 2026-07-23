#!/usr/bin/env python3
"""Riparazione idempotente dell'incidente Loop 2a / agenda del 23 luglio.

Senza ``--apply`` mostra soltanto le modifiche. Non cancella documenti: la
reflection errata viene ritratta via soft-supersede e il todo resta pending.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_attention import remove_loop2e_candidate
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from core.reflection_policy import LOOP2A_CHECKPOINT_KEY
from utils.redis_client import get_client


STALE_REFLECTION_ID = "98bb04db-e5ca-4f04-a726-76b88487008f"
RESTORED_REFLECTION_ID = "ae103970-8cbf-49d5-b2d6-aadc36677db9"
TODO_ID = "7bd10b48-b513-42d9-9b2d-1f41b35249b5"
ERRONEOUS_DUE_AT = 1785144600.0
RETRACTION_ID = "audit:loop2a-stale-window:20260723"
AUDIT_EVENT_ID = "repair-20260723-loop2a-action-v1"


def _doc(redis, memory_id: str) -> dict:
    raw = redis.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _validate(stale: dict, restored: dict, todo: dict) -> None:
    if stale.get("source") != "reflection" or "`03ppr102`" not in stale.get("content", ""):
        raise RuntimeError("reflection da ritrarre non corrisponde all'incidente atteso")
    if restored.get("source") != "reflection":
        raise RuntimeError("reflection precedente non valida")
    restored_pointer = restored.get("superseded_by")
    if restored_pointer not in (None, "", STALE_REFLECTION_ID):
        raise RuntimeError(f"reflection precedente già sostituita da altro: {restored_pointer}")
    if "interocezione hardware" not in todo.get("content", "").lower():
        raise RuntimeError("todo bersaglio non corrisponde al checkpoint hardware")
    due_at = todo.get("due_at")
    if due_at is not None and abs(float(due_at) - ERRONEOUS_DUE_AT) > 0.001:
        raise RuntimeError(f"la scadenza del todo è cambiata dopo l'incidente: {due_at}")


def repair(*, apply: bool) -> None:
    redis = get_client()
    stale = _doc(redis, STALE_REFLECTION_ID)
    restored = _doc(redis, RESTORED_REFLECTION_ID)
    todo = _doc(redis, TODO_ID)
    _validate(stale, restored, todo)

    print(
        "reflection errata:",
        STALE_REFLECTION_ID,
        "->",
        stale.get("superseded_by") or RETRACTION_ID,
    )
    print(
        "reflection precedente:",
        RESTORED_REFLECTION_ID,
        "superseded_by=",
        restored.get("superseded_by"),
        "-> attiva",
    )
    print(
        "todo hardware:",
        TODO_ID,
        "due_at=",
        todo.get("due_at"),
        "-> pending senza scadenza",
    )
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    repaired_at = time.time()
    stale_key = f"euri:memory:{STALE_REFLECTION_ID}"
    restored_key = f"euri:memory:{RESTORED_REFLECTION_ID}"
    todo_key = f"euri:memory:{TODO_ID}"

    redis.json().set(stale_key, "$.superseded_by", RETRACTION_ID)
    redis.json().set(stale_key, "$.requires_verification", True)
    redis.json().set(stale_key, "$.epistemic_status", "retracted_internal_reflection")
    redis.json().set(stale_key, "$.retracted_at", repaired_at)
    flags = list(stale.get("audit_flag") or [])
    if "loop2a_stale_session_window" not in flags:
        flags.append("loop2a_stale_session_window")
    redis.json().set(stale_key, "$.audit_flag", flags)
    remove_loop2e_candidate(redis, STALE_REFLECTION_ID)

    if restored.get("superseded_by") == STALE_REFLECTION_ID:
        redis.json().delete(restored_key, "$.superseded_by")

    before_due = todo.get("due_at")
    redis.json().set(todo_key, "$.status", "pending")
    redis.json().set(todo_key, "$.due_at", None)
    redis.json().set(todo_key, "$.suspended_at", repaired_at)
    redis.json().set(todo_key, "$.reminded_count", 0)
    redis.json().set(todo_key, "$.last_reminded_at", None)
    history = list(todo.get("action_history") or [])
    if not any(item.get("incident_id") == AUDIT_EVENT_ID for item in history):
        history.append({
            "incident_id": AUDIT_EVENT_ID,
            "action": "repair_erroneous_reschedule",
            "at": repaired_at,
            "before": {"due_at": before_due, "status": todo.get("status")},
            "after": {"due_at": None, "status": "pending"},
            "reason": "descriptive UBQ future was misread as agenda.reschedule",
        })
    redis.json().set(todo_key, "$.action_history", history[-20:])
    redis.srem("euri:pulse:clock_emitted", TODO_ID)

    checkpoint = float(stale.get("created_at") or repaired_at)
    redis.set(LOOP2A_CHECKPOINT_KEY, checkpoint)

    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "incident_id": AUDIT_EVENT_ID,
            "stale_reflection_id": STALE_REFLECTION_ID,
            "restored_reflection_id": RESTORED_REFLECTION_ID,
            "todo_id": TODO_ID,
            "erroneous_due_at": before_due,
            "todo_due_at_after": None,
            "loop2a_checkpoint": checkpoint,
        },
        salience=0.8,
        marker_key=f"euri:audit:{AUDIT_EVENT_ID}",
        event_class=COGNITIVE_EVENT,
        producer="repair_20260723_loop2a_action",
        trace_id=f"audit:{AUDIT_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": STALE_REFLECTION_ID, "role": "retracted"},
            {"type": "memory", "id": RESTORED_REFLECTION_ID, "role": "restored"},
            {"type": "todo", "id": TODO_ID, "role": "repaired"},
        ],
        parent_refs=[STALE_REFLECTION_ID, RESTORED_REFLECTION_ID, TODO_ID],
        epistemic_before="stale_reflection_and_unauthorized_reschedule",
        epistemic_after="retracted_and_pending_without_due",
    )
    print("APPLY: riparazione completata")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
