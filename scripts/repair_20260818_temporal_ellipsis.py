#!/usr/bin/env python3
"""Materializza il 24 agosto nella memoria del fermo aziendale.

La riparazione è strettamente derivabile: usa il timestamp e il verbatim del
turno già referenziato dalla memoria. Default dry-run; ``--apply`` aggiorna lo
stesso nodo perché non cambia il fatto, ne rende esplicita la data implicita.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.embedder import Embedder
from core.memory_attention import update_loop2e_candidate_index
from core.memory_axes import analyze_memory_axes
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from core.temporal_context import derive_passive_memory_metadata
from utils.obsidian_sync import write_memory
from utils.redis_client import get_client


MEMORY_ID = "a8de20af-6c40-4fb5-858b-1790a13706ef"
TURN_REF = "83904387-f7dd-4a0f-9c04-0e55a4a6c03d:3"
OLD_CONTENT = "L'azienda di Stefano è ferma fino al 24."
NEW_CONTENT = "L'azienda di Stefano è ferma fino al 24 agosto 2026."
REPAIR_VERSION = "temporal-ellipsis-source-anchor-v1"
AUDIT_EVENT_ID = "repair-20260818-temporal-ellipsis-v1"


def _doc(redis, key: str) -> dict:
    raw = redis.json().get(key, "$")
    if not raw or not isinstance(raw[0], dict):
        raise RuntimeError(f"documento assente: {key}")
    return dict(raw[0])


def repair(*, apply: bool) -> None:
    redis = get_client()
    memory_key = f"euri:memory:{MEMORY_ID}"
    turn_key = f"euri:turn:{TURN_REF}"
    memory = _doc(redis, memory_key)
    turn = _doc(redis, turn_key)

    if memory.get("source") != "passive":
        raise RuntimeError("la memoria non è più il nodo passivo atteso")
    refs = list((memory.get("temporal_context") or {}).get("source_turn_refs") or [])
    if refs != [TURN_REF] or turn.get("turn_ref") != TURN_REF:
        raise RuntimeError("provenienza verbatim diversa da quella attesa")
    if memory.get("content") not in {OLD_CONTENT, NEW_CONTENT}:
        raise RuntimeError("contenuto diverso dall'incidente temporale atteso")

    metadata = derive_passive_memory_metadata(
        {
            "content": OLD_CONTENT,
            "memory_kind": memory.get("memory_kind") or "semantic_fact",
            "source_turn_ids": [int(turn["seq"])],
        },
        [turn],
    )
    temporal = {
        **dict(memory.get("temporal_context") or {}),
        **dict(metadata["temporal_context"]),
    }
    if metadata["canonical_content"] != NEW_CONTENT:
        raise RuntimeError("il resolver non produce la data canonica attesa")
    if temporal.get("temporal_relation") != "until":
        raise RuntimeError("relazione temporale non risolta come until")

    print(f"Memoria: {MEMORY_ID}")
    print(f"Fonte:   {TURN_REF} @ {turn['observed_at']}")
    print(f"Prima:   {memory['content']}")
    print(f"Dopo:    {metadata['canonical_content']}")
    print(
        "Evento:  "
        f"{temporal.get('event_start')} -> {temporal.get('event_end')} "
        f"({temporal.get('canonical_temporal_expression')})"
    )
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return
    if memory.get("temporal_repair_version") == REPAIR_VERSION:
        print("APPLICATO: riparazione già presente, nessuna nuova scrittura")
        return

    embedder = Embedder()
    embedder.load()
    vector = embedder.encode(NEW_CONTENT, mode="passage")
    if vector is None:
        raise RuntimeError("embedding canonico non disponibile")

    repaired_at = time.time()
    axes = analyze_memory_axes(
        NEW_CONTENT,
        source=str(memory.get("source") or "passive"),
        created_at=float(temporal["asserted_at"]),
    )
    updated = {
        **memory,
        "content": NEW_CONTENT,
        "embedding": vector.tolist(),
        "asserted_at": temporal["asserted_at"],
        "event_start": temporal["event_start"],
        "event_end": temporal["event_end"],
        "temporal_context": temporal,
        "memory_axes": axes,
        "temporal_repair_version": REPAIR_VERSION,
        "temporal_repaired_at": repaired_at,
        "temporal_repair_source_turn_ref": TURN_REF,
        "temporal_repair_original_content": OLD_CONTENT,
    }
    redis.json().set(memory_key, "$", updated)
    update_loop2e_candidate_index(redis, updated, strict=True)
    if write_memory(updated) is False:
        raise RuntimeError("scrittura della copia Obsidian fallita")

    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "memory_id": MEMORY_ID,
            "source_turn_ref": TURN_REF,
            "repair_version": REPAIR_VERSION,
            "event_start": temporal["event_start"],
            "event_end": temporal["event_end"],
        },
        salience=0.45,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260818_temporal_ellipsis",
        trace_id=f"repair:{AUDIT_EVENT_ID}",
        entity_refs=[{"type": "memory", "id": MEMORY_ID, "role": "time_repaired"}],
        parent_refs=[MEMORY_ID, TURN_REF],
        epistemic_before="elliptical_date_without_event_interval",
        epistemic_after="source_anchored_absolute_date_and_event_interval",
    )
    print("APPLICATO: contenuto, embedding, intervallo e copia Vault riallineati")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
