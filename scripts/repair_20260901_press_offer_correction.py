#!/usr/bin/env python3
"""Ripara append-only il confronto Yizumi/Chen Hsong del 01/09/2026.

Il dry-run valida lo stato senza scrivere. ``--apply`` crea backup Redis
integrali, pubblica la versione corretta, collega atomicamente la memoria
precedente e ritira la reflection nata dalla premessa ormai superseded. I turni
verbatim restano immutati; i Markdown storici sono spostati in quarantena.
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


OLD_ID = "dd2b94b5-dc74-4de2-8bd8-de9b2128a34c"
DERIVED_ID = "b0d5bd4a-c60c-46ad-9a4d-3396073eaa72"
SIGNAL_ID = "72d49f84-5549-45a6-b786-e13846a2b2e3"
REPAIR_EVENT_ID = "repair-20260901-press-offer-correction-v1"
REPAIR_REASON = "explicit_last_memory_correction_dismissed"
QUARANTINE = Path(
    "/home/fio/EuriVault/.euri-quarantine/2026-09-01-press-offer-correction"
)

OLD_CONTENT = (
    "Per la produzione di articoli da circa 6 kg in polipropilene caricato, il "
    "confronto tra le offerte Yizumi UN1100/11300 e Chen Hsong Supermaster "
    "SM1050-TP-P1 evidenzia la superiorità tecnica della prima. Nonostante "
    "entrambe le macchine siano altamente equipaggiate con tecnologie avanzate "
    "(come viti bimetalliche e interfacce Industry 4.0), la Yizumi offre un "
    "volume di iniezione di 6024 cm³ contro i 5224 cm³ della Chen Hsong. La "
    "Yizumi garantisce inoltre una forza di chiusura maggiore, pari a 11.000 kN "
    "rispetto ai 10.500 kN del modello concorrente. Questa maggiore capacità "
    "volumetrica è fondamentale per fornire il margine di sicurezza necessario "
    "a evitare *short-shot* durante la gestione di materiali con cariche "
    "minerali e densità variabili. In conclusione, la Yizumi rappresenta la "
    "scelta migliore per garantire stabilità e affidabilità del processo "
    "produttivo su pezzi pesanti."
)

CORRECTED_CONTENT = (
    "Nel confronto fra Yizumi UN1100/11300 D1S e Chen Hsong Supermaster "
    "SM1050-TP-P1 per articoli in PP caricato da circa 5-5,5 kg, fino a circa "
    "6 kg con il 15-20% di carica, la Yizumi resta la candidata preferibile ma "
    "la sufficienza del gruppo di iniezione va verificata sul volume reale di "
    "pezzo e materozza e sulla densità della formulazione. La Yizumi dichiara "
    "6024 cm³ e 11.000 kN, contro 5224 cm³ e 10.500 kN della Chen Hsong; il dato "
    "Yizumi di 5542 g per la vite da 116 mm è riferito a una densità di circa "
    "0,92 e non dimostra da solo un margine sopra i 6 kg di materiale caricato. "
    "La dotazione non è una macchina 'nuda': sull'offerta Yizumi vite stellitata "
    "e cilindro bimetallico sono di serie, insieme alle altre dotazioni descritte "
    "in prosa. Sulla Chen Hsong cilindro e vite bimetallici sono invece un "
    "optional da 27.000 euro; anche OPC-UA è indicato come optional da 1.500 "
    "euro. I prezzi base riportati sono 295.000 euro per Yizumi e 292.000 euro "
    "per Chen Hsong. Assistenza tecnica locale vicino a Fabriano e referenze "
    "positive di clienti di Stefano costituiscono ulteriori vantaggi pratici "
    "per Yizumi, distinti dalla verifica dimensionale ancora necessaria."
)


def _doc(redis, key: str) -> dict:
    raw = redis.json().get(key, "$")
    if not raw:
        raise RuntimeError(f"documento assente: {key}")
    return raw[0]


def _memory(redis, memory_id: str) -> dict:
    return _doc(redis, f"euri:memory:{memory_id}")


def _backup(redis, source_key: str, backup_key: str) -> None:
    if redis.json().get(backup_key, "$"):
        return
    redis.json().set(backup_key, "$", _doc(redis, source_key))


def _find_replacement(redis) -> dict | None:
    for key in redis.scan_iter("euri:memory:*"):
        try:
            candidate = _doc(redis, str(key))
        except Exception:
            continue
        if (
            candidate.get("repair_event_id") == REPAIR_EVENT_ID
            and candidate.get("correction_of") == OLD_ID
            and candidate.get("content") == CORRECTED_CONTENT
        ):
            return candidate
    return None


def _validate_initial(redis) -> tuple[dict, dict, dict]:
    old = _memory(redis, OLD_ID)
    derived = _memory(redis, DERIVED_ID)
    signal = _doc(redis, f"euri:correction:{SIGNAL_ID}")
    if old.get("content") != OLD_CONTENT:
        raise RuntimeError("memoria del confronto diversa dalla baseline congelata")
    if old.get("source") != "conversation":
        raise RuntimeError("provenienza della memoria del confronto inattesa")
    if OLD_ID not in (derived.get("source_memory_ids") or []):
        raise RuntimeError("reflection non collegata alla premessa attesa")
    signal_state = (signal.get("status"), signal.get("verdict"))
    if signal_state == ("resolved", "explicit_fact_correction"):
        if not (
            signal.get("repair_event_id") == REPAIR_EVENT_ID
            and signal.get("resolved_old_memory_id") == OLD_ID
            and signal.get("resolved_new_memory_id")
        ):
            raise RuntimeError("correction signal risolto da una riparazione estranea")
    elif signal_state != ("dismissed", "not_a_correction"):
        raise RuntimeError("correction signal diverso dall'esito organico congelato")
    return old, derived, signal


def _quarantine_markdown() -> None:
    for memory_id in (OLD_ID, DERIVED_ID):
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
    old, derived, signal = _validate_initial(redis)
    replacement = _find_replacement(redis)
    print(f"old confronto: {OLD_ID}")
    print(f"reflection derivata: {DERIVED_ID}")
    print(f"signal: {SIGNAL_ID} status={signal.get('status')}")
    print(f"replacement: {replacement.get('id') if replacement else '<nuova memoria user>'}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    for source_key, bare_id in (
        (f"euri:memory:{OLD_ID}", OLD_ID),
        (f"euri:memory:{DERIVED_ID}", DERIVED_ID),
        (f"euri:correction:{SIGNAL_ID}", SIGNAL_ID),
    ):
        _backup(
            redis,
            source_key,
            f"euri:repair_backup:20260901:{bare_id}",
        )

    embedder = Embedder()
    embedder.load()
    manager = MemoryManager(redis, embedder=embedder)
    repaired_at = time.time()

    if replacement is None:
        new_id = manager.save_memory(
            CORRECTED_CONTENT,
            category=str(old.get("category") or "conoscenza"),
            source="user",
            idempotent=False,
            memory_kind=str(old.get("memory_kind") or "semantic_fact"),
            final_fields={
                "correction_of": OLD_ID,
                "correction_relation": "explicit_fact_correction",
                "correction_pending": True,
                "source_memory_ids": [OLD_ID],
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
            raise RuntimeError("pubblicazione della memoria corretta fallita")
    else:
        new_id = str(replacement["id"])

    current_old = _memory(redis, OLD_ID)
    if not current_old.get("superseded_by"):
        if not manager.link_correction(OLD_ID, new_id):
            raise RuntimeError("link atomico della correzione fallito")
    elif current_old.get("superseded_by") != new_id:
        raise RuntimeError("memoria precedente già superseded verso un nodo estraneo")

    current_derived = _memory(redis, DERIVED_ID)
    if not current_derived.get("superseded_by"):
        if not manager.supersede_memory(DERIVED_ID, new_id):
            raise RuntimeError("ritiro della reflection derivata fallito")
    elif current_derived.get("superseded_by") != new_id:
        raise RuntimeError("reflection già superseded verso un nodo estraneo")

    for memory_id in (OLD_ID, DERIVED_ID):
        key = f"euri:memory:{memory_id}"
        redis.json().set(key, "$.repair_event_id", REPAIR_EVENT_ID)
        redis.json().set(key, "$.repair_reason", REPAIR_REASON)
        redis.json().set(key, "$.repaired_at", repaired_at)

    signal_key = f"euri:correction:{SIGNAL_ID}"
    redis.json().set(signal_key, "$.status", "resolved")
    redis.json().set(signal_key, "$.verdict", "explicit_fact_correction")
    redis.json().set(signal_key, "$.resolved_old_memory_id", OLD_ID)
    redis.json().set(signal_key, "$.resolved_new_memory_id", new_id)
    redis.json().set(signal_key, "$.repair_event_id", REPAIR_EVENT_ID)
    redis.json().set(signal_key, "$.repaired_at", repaired_at)

    _quarantine_markdown()
    pulse_emit_once(
        redis,
        REPAIR_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "case": "yizumi_chen_hsong_offer_correction",
            "old_id": OLD_ID,
            "derived_id": DERIVED_ID,
            "replacement_id": new_id,
            "correction_signal_id": SIGNAL_ID,
        },
        salience=0.78,
        event_class=COGNITIVE_EVENT,
        producer="repair_20260901_press_offer_correction",
        trace_id=f"repair:{REPAIR_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": OLD_ID, "role": "antecedent"},
            {"type": "memory", "id": new_id, "role": "corrected"},
        ],
        parent_refs=[OLD_ID, DERIVED_ID, SIGNAL_ID, new_id],
        epistemic_before="explicit_last_memory_correction_dismissed",
        epistemic_after="explicit_fact_correction_linked",
    )

    final_old = _memory(redis, OLD_ID)
    final_new = _memory(redis, new_id)
    final_derived = _memory(redis, DERIVED_ID)
    final_signal = _doc(redis, signal_key)
    if not (
        final_old.get("superseded_by") == new_id
        and final_new.get("correction_of") == OLD_ID
        and final_new.get("correction_pending") is False
        and final_derived.get("superseded_by") == new_id
        and final_signal.get("status") == "resolved"
    ):
        raise RuntimeError("postcondizioni della riparazione non soddisfatte")
    print(f"APPLICATO: {OLD_ID} -> {new_id}; reflection {DERIVED_ID} ritirata")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
