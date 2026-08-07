#!/usr/bin/env python3
"""
Contro-caso N3 — paraurti di richiamo del Loop 2f (deterministico).

Verifica sui DATI REALI (le coppie soft-deletate etichettate a mano nello scavo) la
decisione del paraurti: un atomo con recalled_count >= LOOP2F_RECALL_GUARD non viene
auto-cancellato via contraddizione → tieni entrambi.

Garanzie (assert duri):
  - i 2 DANNI DURI (atomo fattuale molto richiamato perso in nota affine) → BLOCCATI;
  - la vera contraddizione a basso-richiamo → PASSA (il valore obsoleto si risolve).
Costo accettato (documentato, non un fallimento): un arricchimento legittimo molto
richiamato viene tenuto in doppio (clutter) — è il fail-safe "tieni entrambi".

Niente LLM: la fidelity-probe sbagliava ~metà delle volte (vedi git log N3). La decisione
è una soglia sul recalled_count reale → deterministica e riproducibile.

Uso: venv/bin/python test_plane_guard.py
"""
import sys
from pathlib import Path

import redis as redis_lib

sys.path.insert(0, str(Path(__file__).parent))
import config

# (loser_prefix, winner_prefix, etichetta, atteso_BLOCCA, categoria)
#   categoria: "garanzia" = assert duro; "clutter" = blocco accettato (non fallisce il test)
CASES = [
    ("4f70b1cb", "5c544ad6", "DANNO MFI 11,2 lotto PR043 (recalled 12)", True,  "garanzia"),
    ("0aa1089c", "e58b926a", "DANNO struttura Lucy Plast (recalled 7)",  True,  "garanzia"),
    ("5ed23ccf", "b6333e1d", "vera contraddizione, basso richiamo (0)",  False, "garanzia"),
    ("ad0b52ca", "5279eda2", "arricchimento legittimo, richiamo 4 (<5)", False, "garanzia"),
    ("88a97610", "7525248c", "arricchimento legittimo, richiamo 5",      True,  "clutter"),
    ("6cbb676a", "0e1be1fe", "quasi-dup fedele, richiamo 13",            True,  "clutter"),
]


def _recalled(r, prefix):
    keys = list(r.scan_iter(f"euri:memory:{prefix}*"))
    if not keys:
        return None
    v = r.json().get(keys[0], "$.recalled_count")
    return int((v[0] if isinstance(v, list) else v) or 0)


def main():
    r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                        db=config.REDIS_DB, decode_responses=True)
    guard = config.LOOP2F_RECALL_GUARD
    print(f"Paraurti deterministico: blocca il soft-delete se recalled >= {guard}\n")

    ok = 0
    hard_fails = []
    for lp, wp, label, expect_block, cat in CASES:
        recalled = _recalled(r, lp)
        if recalled is None:
            print(f"  ⚠ SKIP  {label}  (memoria {lp} non trovata)")
            continue
        blocks = recalled >= guard
        passed = blocks == expect_block
        ok += passed
        if cat == "garanzia" and not passed:
            hard_fails.append(label)
        if cat == "clutter":
            mark = "•" if blocks else "✗"  # ci aspettiamo il blocco-clutter
            note = "tenute entrambe (clutter accettato)" if blocks else "INATTESO: passato"
        else:
            mark = "✓" if passed else "✗ FAIL"
            note = "BLOCCA" if blocks else "passa"
        print(f"  {mark}  recalled={recalled:2} → {note:34} | [{cat}] {label}")

    garanzie = sum(1 for c in CASES if c[4] == "garanzia")
    print("\n" + "=" * 64)
    print(f"Garanzie passate: {garanzie - len(hard_fails)}/{garanzie}")
    if hard_fails:
        print("FALLITE:", "; ".join(hard_fails))
    else:
        print("Paraurti corretto: ferma i 2 danni duri, lascia risolvere la contraddizione vera.")
    print("=" * 64)
    sys.exit(0 if not hard_fails else 1)


if __name__ == "__main__":
    main()
