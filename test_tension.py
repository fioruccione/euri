#!/usr/bin/env python3
"""
Unit test leggero per core.tension.

Non richiede Redis/Ollama. Verifica che la tensione sia domain-agnostic e risponda
a segnali generali: tempo, rischio, fonte, verifica, integrità.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.tension import evaluate_tension


def check(name, cond, detail):
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}: {detail}")
    return bool(cond)


def run():
    ok = []

    urgent = evaluate_tension({
        "sense": "clock",
        "source": "extero",
        "kind": "threshold",
        "salience": "0.8",
        "payload": json.dumps({"minutes_left": -2}),
    })
    ok.append(check(
        "todo scaduto = tensione alta",
        urgent.urgency == 1.0 and urgent.recommended_mode in {"notify", "act"},
        urgent,
    ))

    fragile = evaluate_tension(
        {"sense": "memory", "source": "intero", "kind": "saved", "salience": "0.5", "payload": "{}"},
        related={"source": "user", "requires_verification": True},
    )
    ok.append(check(
        "requires_verification produce ask/watch",
        fragile.needs_verification and fragile.recommended_mode in {"ask", "watch", "notify"},
        fragile,
    ))

    web_risky = evaluate_tension(
        {"sense": "memory", "source": "extero", "kind": "saved", "salience": "0.6", "payload": "{}"},
        related={"source": "web", "safety_flag": ["injection"]},
    )
    ok.append(check(
        "fonte web con safety_flag = rischio alto",
        web_risky.risk >= 0.9 and web_risky.source_reliability < 0.5,
        web_risky,
    ))

    normal = evaluate_tension({
        "sense": "dream",
        "source": "intero",
        "kind": "cycle_start",
        "salience": "0.25",
        "payload": "{}",
    })
    ok.append(check(
        "evento ordinario resta basso",
        normal.tension < 0.35 and normal.recommended_mode in {"ignore", "log"},
        normal,
    ))

    integrity = evaluate_tension({
        "sense": "integrity",
        "source": "intero",
        "kind": "write_failed",
        "salience": "0.7",
        "payload": "{}",
    })
    ok.append(check(
        "integrity failure interno = rischio alto ma non ask utente",
        integrity.risk >= 0.9 and integrity.recommended_mode == "watch",
        integrity,
    ))

    provenance = evaluate_tension({
        "sense": "provenance",
        "source": "intero",
        "kind": "propagated",
        "salience": "0.55",
        "payload": json.dumps({"staled": 2, "healed": 0}),
    })
    ok.append(check(
        "provenance interna non diventa ask",
        provenance.needs_verification and provenance.recommended_mode == "watch",
        provenance,
    ))

    agnostic = evaluate_tension({
        "sense": "vault",
        "source": "extero",
        "kind": "change",
        "salience": "0.6",
        "payload": json.dumps({"file": "T_REX_001.md"}),
    })
    ok.append(check(
        "nome dominio non cambia le regole",
        agnostic.tension > 0.0 and "T_REX" not in agnostic.reason,
        agnostic,
    ))

    passed = sum(ok)
    print(f"\nRisultato: {passed}/{len(ok)} casi ok")
    return passed == len(ok)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
