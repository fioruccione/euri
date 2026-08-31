#!/usr/bin/env python3
"""Ripara la correzione Orione/BX17 contaminata dal contesto ICMA2.

La procedura e' idempotente e reversibile: conserva copie integrali Redis,
riattiva il nodo BX17, ritrae soltanto il nodo user misto e la reflection 2a,
e chiude il correction signal come incidente di selezione. Non tocca i turni
verbatim ne' la memoria organica ICMA2.
"""
from __future__ import annotations

import argparse
import sys
import time

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_attention import remove_loop2e_candidate
from utils.redis_client import get_client


BASELINE_ID = "1d2cca10-6e12-408c-9bd3-00e18e5a1207"
WRONG_TARGET_ID = "a291e092-a50f-495c-830f-2c17f2eb10e4"
WRONG_NEW_ID = "a18178b6-f83a-4d6b-b80a-53b9b14caadd"
WRONG_REFLECTION_ID = "33cea469-6f14-4035-9613-75652d96c76a"
SIGNAL_ID = "c0943256-b8e5-425d-9c21-49b22db01766"
INCIDENT_ID = "audit:orione-icma-cross-context:20260831"


def _doc(redis, key: str) -> dict:
    raw = redis.json().get(key, "$")
    if not raw:
        raise RuntimeError(f"documento assente: {key}")
    return raw[0]


def _validate(redis) -> tuple[dict, dict, dict, dict, dict]:
    baseline = _doc(redis, f"euri:memory:{BASELINE_ID}")
    wrong_target = _doc(redis, f"euri:memory:{WRONG_TARGET_ID}")
    wrong_new = _doc(redis, f"euri:memory:{WRONG_NEW_ID}")
    reflection = _doc(redis, f"euri:memory:{WRONG_REFLECTION_ID}")
    signal = _doc(redis, f"euri:correction:{SIGNAL_ID}")
    if "Orione 31" not in str(baseline.get("content") or "") or "BX17" not in str(baseline.get("content") or ""):
        raise RuntimeError("baseline Orione/BX17 inatteso")
    if wrong_target.get("superseded_by") not in (None, "", WRONG_NEW_ID):
        raise RuntimeError("bersaglio ICMA2 gia' collegato a un altro nodo")
    if wrong_new.get("correction_of") != WRONG_TARGET_ID or "ICMA" not in str(wrong_new.get("content") or ""):
        raise RuntimeError("nuovo nodo contaminato inatteso")
    if reflection.get("source") != "reflection" or "BX17" not in str(reflection.get("content") or ""):
        raise RuntimeError("reflection contaminata inattesa")
    if signal.get("status") not in ("pending", "dismissed"):
        raise RuntimeError("signal gia' chiuso con un verdetto incompatibile")
    return baseline, wrong_target, wrong_new, reflection, signal


def repair(*, apply: bool) -> None:
    redis = get_client()
    baseline, wrong_target, wrong_new, reflection, signal = _validate(redis)
    print(f"baseline invariata: {BASELINE_ID} (superseded_by={baseline.get('superseded_by')})")
    print(f"ripristino bersaglio estraneo: {WRONG_TARGET_ID} -> attivo")
    print(f"ritiro nodo misto: {WRONG_NEW_ID} -> {INCIDENT_ID}")
    print(f"ritiro reflection Loop 2a: {WRONG_REFLECTION_ID} -> {INCIDENT_ID}")
    print(f"chiusura signal: {SIGNAL_ID} -> aborted_cross_context_target")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return

    repaired_at = time.time()
    backup_prefix = f"euri:repair_backup:20260831:orione-cross-context:"
    for key, doc in {
        f"euri:memory:{WRONG_TARGET_ID}": wrong_target,
        f"euri:memory:{WRONG_NEW_ID}": wrong_new,
        f"euri:memory:{WRONG_REFLECTION_ID}": reflection,
        f"euri:correction:{SIGNAL_ID}": signal,
    }.items():
        redis.json().set(backup_prefix + key.split(":", 2)[-1], "$", doc)

    target_key = f"euri:memory:{WRONG_TARGET_ID}"
    if wrong_target.get("superseded_by") == WRONG_NEW_ID:
        redis.json().delete(target_key, "$.superseded_by")

    for memory_id, doc in (
        (WRONG_NEW_ID, wrong_new),
        (WRONG_REFLECTION_ID, reflection),
    ):
        key = f"euri:memory:{memory_id}"
        redis.json().set(key, "$.superseded_by", INCIDENT_ID)
        redis.json().set(key, "$.requires_verification", True)
        redis.json().set(key, "$.epistemic_status", "retracted_cross_context_contamination")
        redis.json().set(key, "$.retracted_at", repaired_at)
        flags = list(doc.get("audit_flag") or [])
        if "cross_context_target_mismatch" not in flags:
            flags.append("cross_context_target_mismatch")
        redis.json().set(key, "$.audit_flag", flags)
        remove_loop2e_candidate(redis, memory_id)

    signal_key = f"euri:correction:{SIGNAL_ID}"
    redis.json().set(signal_key, "$.status", "dismissed")
    redis.json().set(signal_key, "$.verdict", "aborted_cross_context_target")
    redis.json().set(signal_key, "$.dismiss_reason", "cross_context_target_mismatch")
    redis.json().set(signal_key, "$.analyzed_at", repaired_at)
    redis.json().set(signal_key, "$.repair_incident_id", INCIDENT_ID)

    print("APPLICATO: baseline Orione/BX17 preservata; i due derivati contaminati sono ritirati.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    repair(apply=parser.parse_args().apply)
