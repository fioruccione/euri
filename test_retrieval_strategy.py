#!/usr/bin/env python3
"""
Test Gradino 2 — classificatore di STRATEGIA di retrieval (controllore di memoria).
Verifica che la pre-gate + il modello caldo scelgano la strategia giusta sui casi richiesti.

⚠️ TEST MANUALE / INTEGRATIVO — NON è uno unit test sempre eseguibile.
Richiede Ollama acceso e il modello reale (config.OLLAMA_MODEL). Senza Ollama va saltato,
NON considerato un fallimento. Non genera risposte e non scrive in Redis (legge solo, per
il subject_recall reale su Seari/Poseidon).

Uso: venv/bin/python test_retrieval_strategy.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from core.brain import Brain
from core.retrieval_strategy import _maybe_nonspecific, choose_strategy

print("\n══════════════════════════════════════════")
print("  GRADINO 2 — Retrieval strategy classifier")
print("══════════════════════════════════════════\n")

brain = Brain()

# (query, strategia attesa, token-soggetto atteso o None)
cases = [
    ("cosa sai di me",                "wide_recall",     None),
    ("che progetti conosci",          "wide_recall",     None),
    ("cosa sai del macinato Seari",   "subject_recall",  "seari"),
    ("parlami di Poseidon",           "subject_recall",  "poseidon"),
    ("quanto pesa il Poseidon?",      "specific_search", None),
]

results = []
for query, expected, subj_tok in cases:
    pregate = _maybe_nonspecific(query)
    strat, subject = choose_strategy(query, brain)
    ok = strat == expected
    if expected == "subject_recall" and subj_tok:
        ok = ok and subj_tok in (subject or "").lower()
    # 'quanto pesa il Poseidon?' deve saltare Gemma (pre-gate spenta)
    if expected == "specific_search":
        ok = ok and (pregate is False)
    results.append(ok)
    flag = "PASS" if ok else "FAIL"
    pg = "pre-gate ON " if pregate else "pre-gate OFF"
    print(f"  [{flag}] {pg} | strat={strat!r} subject={subject!r}")
    print(f"         atteso={expected!r}  <- {query!r}\n")

all_ok = all(results)
print("══════════════════════════════════════════")
print(f"  RISULTATO: {'TUTTI PASS ✓' if all_ok else 'QUALCHE FAIL ✗'}")
print("══════════════════════════════════════════\n")
sys.exit(0 if all_ok else 1)
