#!/usr/bin/env python3
"""Ripara le misure numeriche scambiate per date dal resolver temporale.

Il dry-run valida i cinque nodi osservati senza scrivere. Con ``--apply`` crea
un backup Redis integrale per ciascun nodo, ripristina i due contenuti alterati,
elimina gli event-time falsi e riallinea embedding, assi, indice Loop 2e e Vault.
I turni verbatim restano invariati e costituiscono la fonte della riparazione.
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
from core.temporal_context import resolve_text_event_time
from utils.obsidian_sync import write_memory
from utils.redis_client import get_client


REPAIR_VERSION = "numeric-measurement-temporal-boundary-v1"
AUDIT_EVENT_ID = "repair-20260901-numeric-temporal-ranges-v1"
BACKUP_PREFIX = "euri:repair_backup:20260901:numeric-temporal:"

CASES = (
    {
        "id": "f4de64c8-6be2-491f-9491-db19a37aa502",
        "expression": "7-8",
        "old_content": (
            "Nel Progetto UBQ (ex BQ), la percentuale di UBQ nella formulazione "
            "del PP nero grado 25 è del 07/08/2026%. Verrà realizzato un campione "
            "per dimostrare la fattibilità e procedere con i test sia per Plast "
            "Meccanica che, eventualmente, per Keter."
        ),
        "new_content": (
            "Nel Progetto UBQ (ex BQ), la percentuale di UBQ nella formulazione "
            "del PP nero grado 25 è del 7-8%. Verrà realizzato un campione per "
            "dimostrare la fattibilità e procedere con i test sia per Plast "
            "Meccanica che, eventualmente, per Keter."
        ),
    },
    {
        "id": "684d3e9b-18a1-49de-85ed-b582d261ecea",
        "expression": "7-8",
        "old_content": (
            "Stefano realizzerà a breve un campione per dimostrare la fattibilità "
            "dell'uso di UBQ e sottoporlo nuovamente a prova sia per Plasma "
            "Meccanica che, eventualmente, per Keter."
        ),
        "new_content": (
            "Stefano realizzerà a breve un campione per dimostrare la fattibilità "
            "dell'uso di UBQ e sottoporlo nuovamente a prova sia per Plasma "
            "Meccanica che, eventualmente, per Keter."
        ),
    },
    {
        "id": "c737300f-a7f9-456c-b65e-f795b33459d6",
        "expression": "7-8",
        "old_content": (
            "La percentuale di materiale UBQ utilizzata nel polimero per Plasma "
            "Meccanica è stata del 07/08/2026%."
        ),
        "new_content": (
            "La percentuale di materiale UBQ utilizzata nel polimero per Plasma "
            "Meccanica è stata del 7-8%."
        ),
    },
    {
        "id": "f46cdd47-9b02-4421-8ef6-f3d7a7c4ed31",
        "expression": "17/25",
        "old_content": (
            "[confronto] Entrambe le voci descrivono un affinamento della memoria "
            "operativa piuttosto che due alternative in competizione, ma operano "
            "su entità distinte: A si riferisce a un lotto specifico di PP bianco "
            "caricato al 35% di CaCO3 destinato a Gio Style, mentre B riguarda i "
            "flussi dei gradi 17 e 25 gestiti tramite Safic Alcan per P-Pile e ICS "
            "SPA. Il punto in comune è il passaggio da una visione generale a una "
            "precisione logistica e temporale concreta, ma differiscono nei "
            "materiali specifici (PP bianco vs gradi 17/25) e nelle destinazioni "
            "finali indicate. Poiché le fonti sono marcate come incerte o da "
            "verificare, queste precisazioni vanno considerate come ipotesi "
            "operative da confermare, senza assumerle come dati definitivi o "
            "validati."
        ),
        "new_content": (
            "[confronto] Entrambe le voci descrivono un affinamento della memoria "
            "operativa piuttosto che due alternative in competizione, ma operano "
            "su entità distinte: A si riferisce a un lotto specifico di PP bianco "
            "caricato al 35% di CaCO3 destinato a Gio Style, mentre B riguarda i "
            "flussi dei gradi 17 e 25 gestiti tramite Safic Alcan per P-Pile e ICS "
            "SPA. Il punto in comune è il passaggio da una visione generale a una "
            "precisione logistica e temporale concreta, ma differiscono nei "
            "materiali specifici (PP bianco vs gradi 17/25) e nelle destinazioni "
            "finali indicate. Poiché le fonti sono marcate come incerte o da "
            "verificare, queste precisazioni vanno considerate come ipotesi "
            "operative da confermare, senza assumerle come dati definitivi o "
            "validati."
        ),
    },
    {
        "id": "7bd10b48-b513-42d9-9b2d-1f41b35249b5",
        "expression": "1-2",
        "old_content": (
            "Checkpoint Fase 1-2 interocezione hardware: progettare e testare un "
            "riflesso deterministico su CRITICAL persistente e fresco, seguito da "
            "una percezione cognitiva che esponga al modello solo stato stabilizzato "
            "ed esito. Usare un test controllato, non terminare processi e non "
            "attivare azioni dalla sola percentuale VRAM."
        ),
        "new_content": (
            "Checkpoint Fase 1-2 interocezione hardware: progettare e testare un "
            "riflesso deterministico su CRITICAL persistente e fresco, seguito da "
            "una percezione cognitiva che esponga al modello solo stato stabilizzato "
            "ed esito. Usare un test controllato, non terminare processi e non "
            "attivare azioni dalla sola percentuale VRAM."
        ),
    },
)


def _doc(redis, key: str) -> dict:
    raw = redis.json().get(key, "$")
    if not raw or not isinstance(raw[0], dict):
        raise RuntimeError(f"documento assente: {key}")
    return dict(raw[0])


def _clean_temporal_context(memory: dict) -> dict:
    temporal = dict(memory.get("temporal_context") or {})
    for field in (
        "source_temporal_expression",
        "content_temporal_expression",
        "content_original_date",
    ):
        if field in temporal:
            temporal[field] = ""
    temporal.update(
        {
            "schema_version": 2,
            "asserted_at": float(
                memory.get("asserted_at")
                or temporal.get("asserted_at")
                or memory.get("created_at")
            ),
            "temporal_expression": "",
            "canonical_temporal_expression": "",
            "temporal_relation": "none",
            "event_precision": "unspecified",
            "event_start": None,
            "event_end": None,
            "event_target_start": None,
            "event_target_end": None,
            "resolved_from_asserted_at": False,
        }
    )
    return temporal


def repair(*, apply: bool) -> None:
    redis = get_client()
    staged: list[tuple[dict, dict, dict]] = []
    for case in CASES:
        key = f"euri:memory:{case['id']}"
        memory = _doc(redis, key)
        already_repaired = memory.get("temporal_repair_version") == REPAIR_VERSION
        expected_content = case["new_content"] if already_repaired else case["old_content"]
        if memory.get("content") != expected_content:
            raise RuntimeError(f"contenuto inatteso per {case['id']}")
        if already_repaired:
            print(f"{case['id'][:8]}: già riparata")
            continue

        temporal = dict(memory.get("temporal_context") or {})
        if temporal.get("temporal_expression") != case["expression"]:
            raise RuntimeError(f"espressione temporale inattesa per {case['id']}")
        resolved = resolve_text_event_time(
            case["new_content"],
            asserted_at=float(memory.get("asserted_at") or memory.get("created_at")),
        )
        if resolved.get("event_start") is not None:
            raise RuntimeError(f"il resolver continua a datare {case['id']}")

        updated = {
            **memory,
            "content": case["new_content"],
            "event_start": None,
            "event_end": None,
            "temporal_context": _clean_temporal_context(memory),
            "memory_axes": analyze_memory_axes(
                case["new_content"],
                source=str(memory.get("source") or ""),
                created_at=float(
                    memory.get("asserted_at") or memory.get("created_at")
                ),
            ),
            "temporal_repair_version": REPAIR_VERSION,
            "temporal_repair_original_expression": case["expression"],
            "temporal_repair_original_context": temporal,
            "temporal_repair_original_content": case["old_content"],
        }
        staged.append((case, memory, updated))
        changed = "testo+tempo" if case["old_content"] != case["new_content"] else "solo tempo"
        print(f"{case['id'][:8]}: {case['expression']} ({changed})")

    if not staged:
        print("APPLICATO: riparazione già completa")
        return
    if not apply:
        print(f"DRY-RUN: {len(staged)} nodi validati, nessuna modifica applicata")
        return

    changed_content = [row for row in staged if row[0]["old_content"] != row[0]["new_content"]]
    if changed_content:
        embedder = Embedder()
        embedder.load()
        for case, _memory, updated in changed_content:
            vector = embedder.encode(case["new_content"], mode="passage")
            if vector is None:
                raise RuntimeError(f"embedding non disponibile per {case['id']}")
            updated["embedding"] = vector.tolist()

    repaired_at = time.time()
    pipe = redis.pipeline(transaction=True)
    for case, memory, updated in staged:
        backup_key = f"{BACKUP_PREFIX}{case['id']}"
        if not redis.exists(backup_key):
            pipe.json().set(backup_key, "$", memory)
        updated["temporal_repaired_at"] = repaired_at
        pipe.json().set(f"euri:memory:{case['id']}", "$", updated)
    pipe.execute()

    for case, _memory, _updated in staged:
        current = _doc(redis, f"euri:memory:{case['id']}")
        update_loop2e_candidate_index(redis, current, strict=True)
        if write_memory(current) is False:
            raise RuntimeError(f"scrittura Vault fallita per {case['id']}")
        if (
            current.get("content") != case["new_content"]
            or current.get("event_start") is not None
            or current.get("event_end") is not None
            or "dated" in (current.get("memory_axes") or {}).get("temporal_markers", [])
        ):
            raise RuntimeError(f"postcondizioni fallite per {case['id']}")

    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "case": "numeric_measurements_misclassified_as_dates",
            "repair_version": REPAIR_VERSION,
            "memory_ids": [case["id"] for case, _memory, _updated in staged],
            "content_repairs": len(changed_content),
            "temporal_repairs": len(staged),
        },
        salience=0.78,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260901_numeric_temporal_ranges",
        trace_id=f"repair:{AUDIT_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": case["id"], "role": "time_repaired"}
            for case, _memory, _updated in staged
        ],
        parent_refs=[case["id"] for case, _memory, _updated in staged],
        epistemic_before="numeric_measurement_materialized_as_calendar_date",
        epistemic_after="numeric_measurement_preserved_without_event_time",
    )
    print(
        f"APPLICATO: {len(staged)} nodi riallineati; "
        f"{len(changed_content)} contenuti ripristinati"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    repair(apply=parser.parse_args().apply)
