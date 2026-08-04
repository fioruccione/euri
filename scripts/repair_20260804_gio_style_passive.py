#!/usr/bin/env python3
"""Ripara append-only le quattro memorie passive del caso Gio Style 04/08.

Le fonti verbatim restano intatte. Ogni nodo errato viene sostituito da un
nuovo nodo con identità, modalità e tempo corretti, quindi soft-superseded.
Le vecchie copie Markdown vengono spostate in quarantena recuperabile.
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

from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from core.temporal_context import resolve_text_event_time
from utils.redis_client import get_client


CASES = (
    {
        "old_id": "f7a52f13-da12-44b4-968b-862947a9bd7a",
        "expected": "Giostyle S.p.A. acquista in maniera continuativa il materiale con codice 03 PPR 056, colore antracite.",
        "content": "Gio Style S.p.A. acquista in maniera continuativa il materiale con codice 03 PPR 056, colore antracite.",
        "support": "owner_confirmed",
        "epistemic": "externally_confirmed",
    },
    {
        "old_id": "50db4f8f-5df7-4fd4-9b37-04f6c959a6bb",
        "expected": "Il materiale con potenziale codice 03 PPR730 è oggetto di una prova effettuata lunedì 3 agosto per ottenere una nuova certificazione.",
        "content": "Il materiale con potenziale codice 03 PPR730 è stato sottoposto lunedì 3 agosto 2026 a una prova finalizzata a una nuova certificazione.",
        "support": "owner_asserted",
        "epistemic": "user_asserted",
    },
    {
        "old_id": "3df84801-c04c-4494-b1f2-409810df63ae",
        "expected": "Il cliente Giostyle sta testando il nuovo materiale (03 PPR730) per realizzarne un blend con un proprio materiale, finalizzato alla produzione di uno stendino per i panni.",
        "content": "Gio Style sta testando il nuovo materiale 03 PPR730 in un blend con un proprio materiale, finalizzato alla produzione di uno stendino per i panni; l'esito del blend è ancora atteso.",
        "support": "owner_asserted",
        "epistemic": "user_asserted",
    },
    {
        "old_id": "103e1e3e-c6f1-4a12-b489-8d7082a27113",
        "expected": "Stefano ha introdotto l'esito della prova fatta lunedì 3 agosto relativa al nuovo materiale; resta in attesa della risposta del cliente sull'efficacia del blend con il proprio materiale per confermare il codice.",
        "content": "Stefano ha descritto la prova svolta lunedì 3 agosto 2026 sul nuovo materiale; resta in attesa della risposta di Gio Style sull'efficacia del blend con il proprio materiale prima di confermare il codice.",
        "support": "owner_asserted",
        "epistemic": "user_asserted",
    },
)
REPAIR_EVENT_ID = "repair-20260804-gio-style-passive-v1"


def _doc(redis, memory_id: str) -> dict:
    raw = redis.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _temporal_context(old: dict, content: str) -> dict:
    previous = dict(old.get("temporal_context") or {})
    asserted_at = float(previous.get("asserted_at") or old.get("asserted_at") or time.time())
    resolved = resolve_text_event_time(content, asserted_at=asserted_at)
    return {
        "schema_version": 1,
        "asserted_at": asserted_at,
        "source_turn_ids": list(previous.get("source_turn_ids") or []),
        "source_turn_refs": list(previous.get("source_turn_refs") or []),
        "conversation_id": previous.get("conversation_id"),
        "segment_id": previous.get("segment_id"),
        "source_temporal_expression": resolved["temporal_expression"],
        "content_temporal_expression": resolved["temporal_expression"],
        "content_date_corrected": False,
        "content_original_date": "",
        **resolved,
    }


def repair(*, apply: bool) -> None:
    redis = get_client()
    rows: list[tuple[dict, dict]] = []
    for case in CASES:
        old = _doc(redis, case["old_id"])
        if str(old.get("content") or "").strip() != case["expected"]:
            raise RuntimeError(f"contenuto inatteso per {case['old_id']}")
        rows.append((case, old))
        print(f"{case['old_id'][:8]}: {case['content']}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    embedder = Embedder()
    embedder.load()
    manager = MemoryManager(redis, embedder=embedder)
    replacements: list[tuple[str, str]] = []
    for case, old in rows:
        linked = str(old.get("superseded_by") or "").strip()
        if linked:
            replacement = _doc(redis, linked)
            if replacement.get("repair_of") != case["old_id"]:
                raise RuntimeError(f"{case['old_id']} già superseded verso un nodo estraneo")
            new_id = linked
        else:
            inherited = {
                "passive_provenance": dict(old.get("passive_provenance") or {}),
                "repair_of": case["old_id"],
                "repair_reason": "semantic_identity_modality_and_time",
                "requires_verification": False,
                "passive_support": case["support"],
                "epistemic_status": case["epistemic"],
                "verification_status": (
                    old.get("verification_status")
                    if case["support"] == "owner_confirmed"
                    else "owner_asserted_in_authenticated_turn"
                ),
            }
            if old.get("confirmed_by_user_at") is not None:
                inherited["confirmed_by_user_at"] = old["confirmed_by_user_at"]
            new_id = manager.save_memory(
                case["content"],
                category=str(old.get("category") or "passivo"),
                source="passive",
                memory_kind=str(old.get("memory_kind") or "semantic_fact"),
                temporal_context=_temporal_context(old, case["content"]),
                final_fields=inherited,
                memory_scope=str(old.get("memory_scope") or "personal"),
            )
            if not new_id or not manager.supersede_memory(case["old_id"], new_id):
                raise RuntimeError(f"riparazione fallita per {case['old_id']}")
            redis.json().set(
                f"euri:memory:{case['old_id']}",
                "$.audit_flag",
                ["semantic_identity_modality_and_time"],
            )
            redis.json().set(
                f"euri:memory:{case['old_id']}", "$.repaired_at", time.time()
            )
        replacements.append((case["old_id"], new_id))

    quarantine = Path("/home/fio/EuriVault/.euri-quarantine/2026-08-04-gio-style-passive")
    for case, _old in rows:
        matches = list(Path("/home/fio/EuriVault/Memories").glob(
            f"**/*_{case['old_id'][:8]}.md"
        ))
        for source in matches:
            text = source.read_text(encoding="utf-8")
            if case["old_id"] not in text or case["expected"] not in text:
                raise RuntimeError(f"Markdown inatteso: {source}")
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / source.name
            if not target.exists():
                shutil.move(str(source), str(target))

    pulse_emit_once(
        redis,
        REPAIR_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={"case": "gio_style_passive", "replacements": replacements},
        salience=0.72,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260804_gio_style_passive",
        trace_id=f"repair:{REPAIR_EVENT_ID}",
        entity_refs=[{"type": "memory", "id": old} for old, _new in replacements],
        parent_refs=[value for pair in replacements for value in pair],
        epistemic_before="semantic_identity_modality_time_error",
        epistemic_after="corrected_owner_grounded_memories",
    )
    print("APPLICATO:")
    for old_id, new_id in replacements:
        print(f"  {old_id} -> {new_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
