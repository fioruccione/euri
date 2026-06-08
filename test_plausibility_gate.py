#!/usr/bin/env python3
"""
Test manuale / integrativo — Plausibility gate.

Richiede Ollama acceso e config.DREAM_OLLAMA_MODEL disponibile. Non scrive in Redis:
interroga solo il giudice di plausibilita' del Dream Engine sul caso-campione emerso
dal vivo (bicarbonato di calcio come filler minerale) e su un controllo plausibile
(carbonato di calcio come filler).

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

    bad = (
        "Il macinato Seari contiene circa il 20% di carica minerale composta da "
        "bicarbonato di calcio e talco, usata come filler in un compound polimerico."
    )
    good = (
        "Il macinato Seari contiene circa il 20% di carica minerale composta da "
        "carbonato di calcio e talco, usata come filler in un compound polimerico."
    )

    bad_v, bad_c = show("bicarbonato", engine._llm_plausibility_check(bad, "chimica polimeri"))
    good_v, good_c = show("carbonato", engine._llm_plausibility_check(good, "chimica polimeri"))

    ok = True
    ok &= bad_v in {"impossible", "suspicious"} and bad_c >= 0.82
    ok &= good_v in {"plausible", "uncertain"}

    print("\n── esito ─────────────────────────────────")
    if ok:
        print("PASS: il caso bicarbonato viene flaggato e il carbonato no.")
        return 0
    print("FAIL: comportamento inatteso del giudice di plausibilita'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
