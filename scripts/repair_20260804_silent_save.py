#!/usr/bin/env python3
"""Recupera come memoria esplicita il SAVE semantico eseguito solo dal learner."""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from utils.obsidian_sync import unwrap_generated_memory_content
from utils.redis_client import get_client


OLD_ID = "05095a79-0b21-4797-bc3c-f5edce54ada7"
EXPECTED = (
    "Stefano ha rifiutato il preventivo dell'officina Jeep e ha pianificato "
    "di portare l'auto da un altro meccanico."
)
REPAIR_REASON = "semantic_save_not_executed_by_silent_chat"
AUDIT_EVENT_ID = "repair-20260804-silent-save-v1"
QUARANTINE = Path(
    "/home/fio/EuriVault/.euri-quarantine/2026-08-04-silent-save"
)


def _doc(redis, memory_id: str) -> dict:
    raw = redis.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _audit_flags(value) -> list:
    if isinstance(value, list):
        return list(value)
    if value in (None, "", 0, False):
        return []
    return [str(value)]


def _quarantine_old_markdown() -> None:
    for source in Path("/home/fio/EuriVault/Memories").glob(
        f"**/*_{OLD_ID[:8]}.md"
    ):
        text = source.read_text(encoding="utf-8")
        if OLD_ID not in text or EXPECTED not in text:
            raise RuntimeError(f"Markdown inatteso: {source}")
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        target = QUARANTINE / source.name
        if not target.exists():
            shutil.move(str(source), str(target))


def repair(*, apply: bool) -> None:
    redis = get_client()
    old = _doc(redis, OLD_ID)
    clean, _wrapped = unwrap_generated_memory_content(old)
    if clean != EXPECTED or old.get("source") != "passive":
        raise RuntimeError("nodo Silent Chat diverso dall'incidente atteso")

    linked = str(old.get("superseded_by") or "").strip()
    if linked:
        replacement = _doc(redis, linked)
        if replacement.get("repair_of") != OLD_ID:
            raise RuntimeError("nodo già superseded verso una memoria estranea")
        new_id = linked
    else:
        new_id = "<nuova memoria user>"
    print(f"{OLD_ID} -> {new_id}")
    print(f"  {EXPECTED}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    repaired_at = time.time()
    if not linked:
        embedder = Embedder()
        embedder.load()
        manager = MemoryManager(redis, embedder=embedder)
        new_id = manager.save_memory(
            EXPECTED,
            category="personale",
            source="user",
            idempotent=True,
            memory_kind=str(old.get("memory_kind") or "semantic_fact"),
            temporal_context=dict(old.get("temporal_context") or {}),
            final_fields={
                "repair_of": OLD_ID,
                "repair_reason": REPAIR_REASON,
                "epistemic_status": "user_asserted",
                "verification_status": "owner_explicit_save_recovered",
                "repaired_at": repaired_at,
            },
            memory_scope=str(old.get("memory_scope") or "personal"),
        )
        if not new_id or not manager.supersede_memory(OLD_ID, new_id):
            raise RuntimeError("promozione append-only a memoria user fallita")
        redis.json().set(
            f"euri:memory:{OLD_ID}",
            "$.audit_flag",
            list(dict.fromkeys(
                _audit_flags(old.get("audit_flag")) + [REPAIR_REASON]
            )),
        )
        redis.json().set(
            f"euri:memory:{OLD_ID}", "$.repaired_at", repaired_at
        )

    _quarantine_old_markdown()
    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={"old_id": OLD_ID, "replacement_id": new_id},
        salience=0.68,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260804_silent_save",
        trace_id=f"repair:{AUDIT_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": OLD_ID, "role": "superseded"},
            {"type": "memory", "id": new_id, "role": "replacement"},
        ],
        parent_refs=[OLD_ID, new_id],
        epistemic_before="explicit_save_stored_as_passive",
        epistemic_after="explicit_user_memory_with_receipt_lineage",
    )
    print(f"APPLICATO: {OLD_ID} -> {new_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
