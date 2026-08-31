#!/usr/bin/env python3
"""Ripara in modo append-only il caso organico ICMA2/FIMIC del 31/08/2026.

Il dry-run valida lo stato atteso senza scrivere. Con ``--apply`` crea prima
copie Redis integrali, pubblica una versione completa con LAS500 -> RAS500,
collega atomicamente la correzione e ritira il duplicato corto. I raw turn non
vengono modificati; i Markdown storici vengono spostati in quarantena.
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
from utils.redis_client import get_client


OLD_ID = "bc8f7583-b331-4eff-a606-c6d3afed7bbf"
PASSIVE_ID = "78399f5d-6fe1-4581-8d16-a28f1f882401"
SHORT_USER_ID = "dd9fc3cf-7040-42d0-842c-38527ccf5617"
SIGNAL_ID = "7eace62c-30cc-4c3a-bed6-7c71dc08c6d8"
REPAIR_EVENT_ID = "repair-20260831-icma2-correction-v1"
REPAIR_REASON = "explicit_icma2_filter_correction_not_linked"

OLD_CONTENT = (
    "Progetto per l'estrusore ICMA 2: sostituzione della pompa a ingranaggi "
    "con la pompa a pistoni FIMIC FPP20 tra la bivite e il filtro FIMIC LAS 500. "
    "L'obiettivo è aumentare la produzione da 1300 a circa 1500 kg/h "
    "(+23.200 kg/settimana), compensando l'usura delle viti. La modifica "
    "migliorerà la qualità della filtrazione, la stabilità del processo di "
    "taglio e ridurrà i costi di manutenzione. Il costo stimato è di 146.000 "
    "euro, con un tempo di rientro di circa 22 mesi; è prevista una prova "
    "gratuita di due mesi per testare la tecnologia."
)
SHORT_CONTENT = (
    "La macchina ICMA 2 utilizza una pompa FIMIC FPP20 e un filtro FIMIC RAS500."
)
CORRECTED_CONTENT = OLD_CONTENT.replace("FIMIC LAS 500", "FIMIC RAS500")
QUARANTINE = Path(
    "/home/fio/EuriVault/.euri-quarantine/2026-08-31-icma2-correction"
)


def _doc(redis, key: str) -> dict:
    raw = redis.json().get(key, "$")
    if not raw:
        raise RuntimeError(f"documento assente: {key}")
    return raw[0]


def _memory(redis, memory_id: str) -> dict:
    return _doc(redis, f"euri:memory:{memory_id}")


def _backup(redis, source_key: str, backup_key: str) -> None:
    current = _doc(redis, source_key)
    existing = redis.json().get(backup_key, "$")
    if existing:
        return
    redis.json().set(backup_key, "$", current)


def _find_replacement(redis) -> dict | None:
    for key in redis.scan_iter("euri:memory:*"):
        try:
            doc = _doc(redis, str(key))
        except Exception:
            continue
        if (
            doc.get("repair_event_id") == REPAIR_EVENT_ID
            and doc.get("correction_of") == OLD_ID
            and doc.get("content") == CORRECTED_CONTENT
        ):
            return doc
    return None


def _validate_initial(redis) -> tuple[dict, dict, dict, dict]:
    old = _memory(redis, OLD_ID)
    passive = _memory(redis, PASSIVE_ID)
    short_user = _memory(redis, SHORT_USER_ID)
    signal = _doc(redis, f"euri:correction:{SIGNAL_ID}")
    if old.get("content") != OLD_CONTENT:
        raise RuntimeError("contenuto completo ICMA2 diverso dalla baseline congelata")
    if passive.get("content") != SHORT_CONTENT or passive.get("source") != "passive":
        raise RuntimeError("duplicato passivo diverso dalla baseline congelata")
    if short_user.get("content") != SHORT_CONTENT or short_user.get("source") != "user":
        raise RuntimeError("nodo user corto diverso dalla baseline congelata")
    if passive.get("superseded_by") != SHORT_USER_ID:
        raise RuntimeError("relazione passive -> user corta inattesa")
    if signal.get("mutation_policy") != "explicit_correction":
        raise RuntimeError("correction signal privo di autorità esplicita")
    return old, passive, short_user, signal


def _quarantine_markdown() -> None:
    for memory_id in (OLD_ID, PASSIVE_ID, SHORT_USER_ID):
        for source in Path("/home/fio/EuriVault/Memories").glob(
            f"**/*_{memory_id[:8]}.md"
        ):
            text = source.read_text(encoding="utf-8")
            if memory_id not in text:
                raise RuntimeError(f"Markdown inatteso: {source}")
            QUARANTINE.mkdir(parents=True, exist_ok=True)
            target = QUARANTINE / source.name
            if not target.exists():
                shutil.move(str(source), str(target))


def repair(*, apply: bool) -> None:
    redis = get_client()
    old, passive, short_user, signal = _validate_initial(redis)
    replacement = _find_replacement(redis)
    if replacement:
        print(f"replacement esistente: {replacement['id']}")
    else:
        print("replacement: <nuova memoria user completa>")
    print(f"old completo: {OLD_ID} (LAS 500)")
    print(f"duplicato corto: {SHORT_USER_ID} (RAS500)")
    print(f"signal: {SIGNAL_ID} status={signal.get('status')}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    for source_key, bare_id in (
        (f"euri:memory:{OLD_ID}", OLD_ID),
        (f"euri:memory:{PASSIVE_ID}", PASSIVE_ID),
        (f"euri:memory:{SHORT_USER_ID}", SHORT_USER_ID),
        (f"euri:correction:{SIGNAL_ID}", SIGNAL_ID),
    ):
        _backup(
            redis,
            source_key,
            f"euri:repair_backup:20260831:{bare_id}",
        )

    embedder = Embedder()
    embedder.load()
    manager = MemoryManager(redis, embedder=embedder)
    repaired_at = time.time()

    if replacement is None:
        correction_context = dict(passive.get("temporal_context") or {})
        correction_context["asserted_at"] = float(
            short_user.get("asserted_at") or repaired_at
        )
        new_id = manager.save_memory(
            CORRECTED_CONTENT,
            category=str(old.get("category") or "personale"),
            source="user",
            idempotent=False,
            memory_kind=str(old.get("memory_kind") or "semantic_fact"),
            temporal_context=correction_context,
            final_fields={
                "correction_of": OLD_ID,
                "correction_relation": "explicit_fact_correction",
                "correction_pending": True,
                "correction_signal_id": SIGNAL_ID,
                "source_memory_ids": [OLD_ID, SHORT_USER_ID],
                "epistemic_status": "user_corrected",
                "verification_status": "owner_corrected_explicitly",
                "requires_verification": True,
                "repair_event_id": REPAIR_EVENT_ID,
                "repair_reason": REPAIR_REASON,
                "repaired_at": repaired_at,
            },
            memory_scope=str(old.get("memory_scope") or "personal"),
        )
        if not new_id:
            raise RuntimeError("pubblicazione della versione completa fallita")
        replacement = _memory(redis, new_id)
    else:
        new_id = str(replacement["id"])
        if replacement.get("content") != CORRECTED_CONTENT:
            raise RuntimeError("replacement di un tentativo precedente inatteso")

    if not old.get("superseded_by"):
        quarantined = manager.extend_correction_signal_context(SIGNAL_ID, [OLD_ID])
        refreshed_old = _memory(redis, OLD_ID)
        if OLD_ID not in quarantined and not refreshed_old.get("correction_pending"):
            raise RuntimeError("antecedente non entrato nella quarantena correttiva")
        if not manager.link_correction(OLD_ID, new_id):
            raise RuntimeError("link atomico della correzione fallito")
    elif old.get("superseded_by") != new_id:
        raise RuntimeError("memoria completa già superseded verso un nodo estraneo")

    refreshed_short = _memory(redis, SHORT_USER_ID)
    if not refreshed_short.get("superseded_by"):
        if not manager.supersede_memory(SHORT_USER_ID, new_id):
            raise RuntimeError("ritiro del duplicato user corto fallito")
    elif refreshed_short.get("superseded_by") != new_id:
        raise RuntimeError("duplicato user corto già collegato a un nodo estraneo")

    for memory_id in (OLD_ID, PASSIVE_ID, SHORT_USER_ID):
        redis.json().set(
            f"euri:memory:{memory_id}", "$.repair_event_id", REPAIR_EVENT_ID
        )
        redis.json().set(
            f"euri:memory:{memory_id}", "$.repair_reason", REPAIR_REASON
        )
        redis.json().set(
            f"euri:memory:{memory_id}", "$.repaired_at", repaired_at
        )

    _quarantine_markdown()
    pulse_emit_once(
        redis,
        REPAIR_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "case": "icma2_fimic_ras500",
            "old_id": OLD_ID,
            "short_user_id": SHORT_USER_ID,
            "replacement_id": new_id,
            "correction_signal_id": SIGNAL_ID,
        },
        salience=0.76,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260831_icma2_correction",
        trace_id=f"repair:{REPAIR_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": OLD_ID, "role": "antecedent"},
            {"type": "memory", "id": new_id, "role": "corrected"},
        ],
        parent_refs=[OLD_ID, PASSIVE_ID, SHORT_USER_ID, SIGNAL_ID, new_id],
        epistemic_before="explicit_correction_unlinked",
        epistemic_after="explicit_correction_linked",
    )

    final_old = _memory(redis, OLD_ID)
    final_new = _memory(redis, new_id)
    final_short = _memory(redis, SHORT_USER_ID)
    final_signal = _doc(redis, f"euri:correction:{SIGNAL_ID}")
    if not (
        final_old.get("superseded_by") == new_id
        and final_new.get("correction_of") == OLD_ID
        and final_new.get("correction_pending") is False
        and final_short.get("superseded_by") == new_id
        and final_signal.get("status") == "resolved"
    ):
        raise RuntimeError("postcondizioni della riparazione non soddisfatte")
    print(f"APPLICATO: {OLD_ID} -> {new_id}; duplicato {SHORT_USER_ID} ritirato")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
