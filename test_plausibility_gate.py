#!/usr/bin/env python3
"""
Test manuale / integrativo — Plausibility gate.

Richiede Ollama acceso e config.DREAM_OLLAMA_MODEL disponibile. Non scrive in Redis:
interroga solo il giudice di plausibilita' del Dream Engine sulla frase REALE della
memoria-campione 332e18b6 (bicarbonato di calcio fra le cariche) e su un controllo
plausibile (carbonato di calcio). L'esito usa la STESSA regola di soglia del gate
(_plausibility_should_flag), cosi' il test non puo' divergere dal comportamento reale.

Uso: venv/bin/python test_plausibility_gate.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.dream_engine import DreamEngine


def show(label, result):
    verdict = result.get("verdict")
    confidence = result.get("confidence")
    reason = result.get("reason")
    print(f"{label:<12} verdict={verdict!r} confidence={confidence!r}")
    print(f"             reason={reason!r}")
    return verdict, float(confidence or 0)


def main():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).read()
    except Exception:
        print("SKIP: Ollama non raggiungibile su localhost:11434.")
        return 0

    engine = DreamEngine.__new__(DreamEngine)

    print("\n══════════════════════════════════════════")
    print("  Plausibility gate — caso bicarbonato")
    print("══════════════════════════════════════════\n")

    # Frase REALE della memoria 332e18b6 (stringata, com'è in Redis), non una più esplicita.
    bad = "Il macinato contiene circa il 20% di carica (bicarbonato di calcio, ossido di calcio e talco)."
    good = "Il macinato contiene circa il 20% di carica (carbonato di calcio e talco)."

    bad_v, bad_c = show("bicarbonato", engine._llm_plausibility_check(bad, "chimica polimeri"))
    good_v, good_c = show("carbonato", engine._llm_plausibility_check(good, "chimica polimeri"))

    # Stessa identica regola del gate (soglia differenziata per verdetto): niente drift.
    bad_flag = DreamEngine._plausibility_should_flag(bad_v, bad_c)
    good_flag = DreamEngine._plausibility_should_flag(good_v, good_c)
    print(f"\n  flag bicarbonato: {bad_flag}   flag carbonato: {good_flag}")

    ok = bad_flag and not good_flag

    print("\n── esito ─────────────────────────────────")
    if ok:
        print("PASS: la frase reale 332e18b6 viene flaggata, il carbonato no.")
        return 0
    print("FAIL: comportamento inatteso del giudice di plausibilita'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
