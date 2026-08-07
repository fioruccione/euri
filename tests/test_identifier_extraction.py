#!/usr/bin/env python3
"""
Regressione permanente di MemoryManager._extract_identifiers (retrieval identifier-first).

Protegge il fix del 20/06 (bug: "03 PPR 738P" → estraeva solo "PPR" → identifier-first
inondava gli slot e soffocava la semantica) E la sua indurita (le DATE non sono codici).
Riconoscimento DOMAIN-AGNOSTIC per FORMA: nessun codice/materiale cablato.

Questo file è anche il LIBRO MASTRO dei casi di recupero: quando un fallimento REALE
(non-regex) emerge dall'uso, aggiungerlo qui. Quando i fallimenti non-banali si accumulano
(3-5), è il segnale misurato che serve il memory-controller in lettura — non prima.

Uso:  venv/bin/python test_identifier_extraction.py   (non richiede Redis/Ollama)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.memory_manager import MemoryManager

# istanza senza __init__: _extract_identifiers usa solo regex a livello di classe
mem = MemoryManager.__new__(MemoryManager)

# (query, must_include, must_exclude) — must_* sono liste di sottostringhe attese/vietate
CASES = [
    # — codici che DEVONO essere riconosciuti —
    ("chi gestisce il 03 PPR 738P in azienda?", ["03 PPR 738P"], []),
    ("il materiale 03 PPR738P",                 ["03 PPR738P"],  []),
    ("il lotto 738P è pronto",                  ["738P"],        []),
    ("parlami del T REX 001",                   ["T REX 001"],   []),   # non-industriale (dinosauro)
    ("cosa sai della ROSA DAM 12?",             ["ROSA DAM 12"], []),   # non-industriale (fiore)
    ("qual è l'MFI del lotto?",                 ["MFI"],         []),
    ("il valore è 4.7 a 230 gradi",             ["4.7"],         []),
    ("codice PPR-738P confermato",              ["PPR-738P"],    []),
    # — NON-codici che NON devono essere scambiati per identificatori —
    ("Il 20 06 2026 cosa è successo?",          [], ["20 06 2026"]),    # data → path temporale, non codice
    ("tra 2 ore ricordami una cosa",            [], ["2 ore", "2"]),    # tempo relativo
    ("Mario Rossi mi ha scritto",               [], ["Mario", "Rossi", "Mario Rossi"]),  # nome proprio (no cifra)
]

def run():
    ok = 0; fails = []
    for query, must_inc, must_exc in CASES:
        ids = mem._extract_identifiers(query)
        missing = [s for s in must_inc if not any(s == i or s in i for i in ids)]
        present = [s for s in must_exc if any(s == i for i in ids)]
        passed = not missing and not present
        ok += passed
        flag = "✅" if passed else "❌"
        print(f"  {flag} {ids!s:32} ← «{query}»")
        if not passed:
            fails.append((query, ids, missing, present))
    print(f"\n  → {ok}/{len(CASES)} casi ok")
    if fails:
        print("\n  FALLITI:")
        for q, ids, miss, pres in fails:
            print(f"    «{q}» → {ids}  (mancano: {miss}, vietati presenti: {pres})")
    return not fails

if __name__ == "__main__":
    print("=== Regressione _extract_identifiers ===")
    sys.exit(0 if run() else 1)
