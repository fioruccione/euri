#!/usr/bin/env python3
"""Ripara il salvataggio UBQ nominato creato dal routing TEACH errato.

La riparazione è append-only: crea una memoria user corretta, ritira via
``superseded_by`` l'orphan originale e sposta la copia Obsidian in quarantena
solo se il file corrisponde esattamente al record atteso.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_manager import MemoryManager
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from utils.redis_client import get_client


OLD_ID = "2e88eb30-3741-4ace-8529-ad6fc69f6026"
TITLE = "Compuand UBQ 2026"
NEW_CONTENT = (
    "Stefano ha ricevuto una nuova variante di materiale UBQ, più economica e "
    "con minori problemi in fase di additivazione nei polimeri, mantenendo il "
    "beneficio dichiarato sulla CO2. Ha eseguito una prova in trafila con 10 kg "
    "e ha stampato provini con additivo UBQ e con materiale ritrafilato; le prove "
    "meccaniche devono ancora stabilire il risultato."
)
REPAIR_EVENT_ID = "repair-20260724-named-ubq-v1"


def _doc(r, memory_id: str) -> dict:
    raw = r.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _validate_old(old: dict) -> None:
    if old.get("source") != "teach":
        raise RuntimeError(f"source inattesa: {old.get('source')!r}")
    if old.get("content", "").strip() != "# Memoria (2026-07-23 17:47:19)\n\ncon il nome Compuand UBQ 2026":
        raise RuntimeError("il contenuto dell'orphan non corrisponde al caso UBQ")


def repair(*, apply: bool) -> None:
    r = get_client()
    old = _doc(r, OLD_ID)
    _validate_old(old)
    print(f"orphan: {OLD_ID} ({old.get('source')}/{old.get('domain')})")
    print(f"nuovo contenuto: {NEW_CONTENT}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    manager = MemoryManager(r, embedder=None)
    new_id = manager.save_memory(
        NEW_CONTENT,
        category="conoscenza",
        source="user",
        idempotent=True,
        final_fields={
            "memory_title": TITLE,
            "repair_of": OLD_ID,
            "repair_reason": "named_save_routed_to_teach",
            "requires_verification": True,
            "verification_status": "user_reported_pending_mechanical_tests",
        },
    )
    if not new_id:
        raise RuntimeError("creazione della memoria corretta fallita")
    if old.get("superseded_by") not in (None, "", new_id):
        raise RuntimeError(f"orphan già ritirato verso un altro id: {old.get('superseded_by')}")
    if old.get("superseded_by") != new_id:
        if not manager.supersede_memory(OLD_ID, new_id):
            raise RuntimeError("supersede dell'orphan fallito")

    repaired_at = time.time()
    old_key = f"euri:memory:{OLD_ID}"
    r.json().set(old_key, "$.audit_flag", ["named_save_routed_to_teach"])
    r.json().set(old_key, "$.repaired_at", repaired_at)
    r.json().set(old_key, "$.epistemic_status", "retracted_save_routing_error")

    old_path = Path("/home/fio/EuriVault/Memories/tecnologia/Memory_20260723_174719_2e88eb30.md")
    if old_path.exists():
        text = old_path.read_text(encoding="utf-8")
        if OLD_ID in text and "Compuand UBQ 2026" in text:
            quarantine = Path("/home/fio/EuriVault/.euri-quarantine/2026-07-24-ubq-named")
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / old_path.name
            if not target.exists():
                shutil.move(str(old_path), str(target))

    pulse_emit_once(
        r,
        REPAIR_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "case": "named_ubq_memory",
            "old_id": OLD_ID,
            "new_id": new_id,
            "reason": "named_save_routed_to_teach",
        },
        salience=0.7,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260724_named_ubq_memory",
        trace_id=f"repair:{REPAIR_EVENT_ID}",
        entity_refs=[{"type": "memory", "id": OLD_ID}, {"type": "memory", "id": new_id}],
        parent_refs=[OLD_ID, new_id],
        epistemic_before="routing_error",
        epistemic_after="corrected_user_memory",
    )
    print(f"APPLICATO: {OLD_ID} -> {new_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
