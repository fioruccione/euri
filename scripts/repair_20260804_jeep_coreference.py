#!/usr/bin/env python3
"""Ripara append-only l'incidente coreferenziale Jeep/Gio Style del 04/08.

Il turno verbatim resta intatto. La memoria passiva con soggetto errato viene
sostituita da un fatto sostenuto dalle fonti; la reflection contaminata viene
ritratta. Le copie Markdown originali finiscono in quarantena recuperabile.
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
from core.memory_attention import remove_loop2e_candidate
from core.memory_manager import MemoryManager
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from utils.redis_client import get_client


FALSE_PASSIVE_ID = "c2f200f1-028e-4637-af51-472852873258"
FALSE_REFLECTION_ID = "d0933d42-f1a3-479b-bbab-eb5777b028ba"
FALSE_PASSIVE_CONTENT = (
    "L'officina Gio Style ha presentato a Stefano un preventivo di 800 euro "
    "per la sostituzione del sensore e per un presunto corto nel termostato."
)
FALSE_REFLECTION_FRAGMENT = "preventivo ricevuto dall'officina Gio Style"
CORRECTED_CONTENT = (
    "L'officina autorizzata Jeep ha presentato a Stefano un preventivo di 800 "
    "euro per la sostituzione del sensore NTC e per un presunto corto nel termostato."
)
RETRACTION_ID = "audit:jeep-gio-style-coreference:20260804"
AUDIT_EVENT_ID = "repair-20260804-jeep-coreference-v1"
QUARANTINE = Path(
    "/home/fio/EuriVault/.euri-quarantine/2026-08-04-jeep-coreference"
)


def _doc(redis, memory_id: str) -> dict:
    raw = redis.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria assente: {memory_id}")
    return raw[0]


def _validate(passive: dict, reflection: dict) -> None:
    if passive.get("content") != FALSE_PASSIVE_CONTENT:
        raise RuntimeError("memoria passiva diversa dall'incidente atteso")
    if passive.get("source") != "passive":
        raise RuntimeError("il nodo passivo non ha la fonte attesa")
    if (
        reflection.get("source") != "reflection"
        or FALSE_REFLECTION_FRAGMENT not in str(reflection.get("content") or "")
    ):
        raise RuntimeError("reflection diversa dall'incidente atteso")
    pointer = reflection.get("superseded_by")
    if pointer not in (None, "", RETRACTION_ID):
        raise RuntimeError(f"reflection già ritirata verso un altro nodo: {pointer}")


def _quarantine_markdown(memory_id: str, expected_fragment: str) -> None:
    matches = list(Path("/home/fio/EuriVault/Memories").glob(
        f"**/*_{memory_id[:8]}.md"
    ))
    for source in matches:
        text = source.read_text(encoding="utf-8")
        if memory_id not in text or expected_fragment not in text:
            raise RuntimeError(f"Markdown inatteso: {source}")
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        target = QUARANTINE / source.name
        if not target.exists():
            shutil.move(str(source), str(target))


def repair(*, apply: bool) -> None:
    redis = get_client()
    passive = _doc(redis, FALSE_PASSIVE_ID)
    reflection = _doc(redis, FALSE_REFLECTION_ID)
    _validate(passive, reflection)

    linked = str(passive.get("superseded_by") or "").strip()
    if linked:
        replacement = _doc(redis, linked)
        if replacement.get("repair_of") != FALSE_PASSIVE_ID:
            raise RuntimeError("memoria passiva già superseded verso un nodo estraneo")
        replacement_id = linked
    else:
        replacement_id = "<nuovo nodo>"

    print(f"passive: {FALSE_PASSIVE_ID} -> {replacement_id}")
    print(f"  {CORRECTED_CONTENT}")
    print(f"reflection: {FALSE_REFLECTION_ID} -> {RETRACTION_ID}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    repaired_at = time.time()
    if not linked:
        embedder = Embedder()
        embedder.load()
        manager = MemoryManager(redis, embedder=embedder)
        replacement_id = manager.save_memory(
            CORRECTED_CONTENT,
            category=str(passive.get("category") or "passivo"),
            source="passive",
            memory_kind=str(passive.get("memory_kind") or "semantic_fact"),
            temporal_context=dict(passive.get("temporal_context") or {}),
            final_fields={
                "passive_provenance": dict(passive.get("passive_provenance") or {}),
                "repair_of": FALSE_PASSIVE_ID,
                "repair_reason": "invalid_anaphora_entity_projection",
                "requires_verification": False,
                "passive_support": "owner_asserted",
                "epistemic_status": "user_asserted",
                "verification_status": "owner_asserted_in_authenticated_turn",
            },
            memory_scope=str(passive.get("memory_scope") or "personal"),
        )
        if not replacement_id or not manager.supersede_memory(
            FALSE_PASSIVE_ID, replacement_id
        ):
            raise RuntimeError("sostituzione della memoria passiva fallita")
        redis.json().set(
            f"euri:memory:{FALSE_PASSIVE_ID}",
            "$.audit_flag",
            list(dict.fromkeys(
                list(passive.get("audit_flag") or [])
                + ["invalid_anaphora_entity_projection"]
            )),
        )
        redis.json().set(
            f"euri:memory:{FALSE_PASSIVE_ID}", "$.repaired_at", repaired_at
        )

    reflection_key = f"euri:memory:{FALSE_REFLECTION_ID}"
    redis.json().set(reflection_key, "$.superseded_by", RETRACTION_ID)
    redis.json().set(reflection_key, "$.requires_verification", True)
    redis.json().set(
        reflection_key, "$.epistemic_status", "retracted_internal_reflection"
    )
    redis.json().set(reflection_key, "$.retracted_at", repaired_at)
    redis.json().set(
        reflection_key,
        "$.audit_flag",
        list(dict.fromkeys(
            list(reflection.get("audit_flag") or [])
            + ["invalid_anaphora_entity_projection"]
        )),
    )
    remove_loop2e_candidate(redis, FALSE_REFLECTION_ID)

    _quarantine_markdown(FALSE_PASSIVE_ID, FALSE_PASSIVE_CONTENT)
    _quarantine_markdown(FALSE_REFLECTION_ID, FALSE_REFLECTION_FRAGMENT)

    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "incident_id": AUDIT_EVENT_ID,
            "false_passive_id": FALSE_PASSIVE_ID,
            "replacement_id": replacement_id,
            "false_reflection_id": FALSE_REFLECTION_ID,
            "cause": "anaphora_projected_as_known_entity",
        },
        salience=0.82,
        marker_key=f"euri:audit:{AUDIT_EVENT_ID}",
        event_class=COGNITIVE_EVENT,
        producer="repair_20260804_jeep_coreference",
        trace_id=f"audit:{AUDIT_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": FALSE_PASSIVE_ID, "role": "repaired"},
            {"type": "memory", "id": replacement_id, "role": "replacement"},
            {"type": "memory", "id": FALSE_REFLECTION_ID, "role": "retracted"},
        ],
        parent_refs=[FALSE_PASSIVE_ID, replacement_id, FALSE_REFLECTION_ID],
        epistemic_before="false_entity_projection_propagated",
        epistemic_after="corrected_passive_and_retracted_reflection",
    )
    print("APPLICATO:")
    print(f"  {FALSE_PASSIVE_ID} -> {replacement_id}")
    print(f"  {FALSE_REFLECTION_ID} -> {RETRACTION_ID}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
