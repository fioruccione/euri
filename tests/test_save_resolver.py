#!/usr/bin/env python3
"""
Test Gradino 1 — risolutore SAVE semantico (controllore di memoria, ruolo del modello caldo).
Caso Seari: "ricordati il macinato di Seari" NON deve salvare l'etichetta ma la SOSTANZA
discussa negli scambi precedenti. Vedi [[feedback_insegnamento_naturale]].

⚠️ TEST MANUALE / INTEGRATIVO — NON è uno unit test sempre eseguibile.
Richiede Ollama acceso e il modello reale (config.OLLAMA_MODEL): interroga la Gemma calda
e ne valuta l'output. In un ambiente senza Ollama va saltato, NON considerato un fallimento.
Non tocca Redis: esercita solo brain.resolve_save_intent e save_service._resolve_content.

Uso: venv/bin/python test_save_resolver.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from core.brain import Brain
from core import save_service

print("\n══════════════════════════════════════════")
print("  GRADINO 1 — Semantic SAVE resolver (Seari)")
print("══════════════════════════════════════════\n")

brain = Brain()

# Conversazione recente: Stefano valuta il macinato di Seari (più scambi, ~i "10 minuti").
seari_history = [
    {"role": "user", "content": "Senti, è arrivato un campione di macinato da Seari, lo stiamo valutando per gli interni auto."},
    {"role": "assistant", "content": "Ok, di che materiale si tratta e per quale componente?"},
    {"role": "user", "content": "È polipropilene rimacinato, viene da paraurti post-industriali. Lo proveremmo per le plance interne."},
    {"role": "assistant", "content": "Capito. Hai fatto delle prove sul materiale?"},
    {"role": "user", "content": "Sì, il problema è che ha troppe impurità e l'MFI è ballerino, varia parecchio da lotto a lotto. Il colore tira al grigio scuro, non uniforme."},
    {"role": "assistant", "content": "Quindi non sarebbe adatto a un pezzo a vista senza correzioni?"},
    {"role": "user", "content": "Esatto. Per ora lo scarterei per le plance a vista, al massimo va bene per pezzi nascosti. Il prezzo era buono ma la costanza no."},
    {"role": "assistant", "content": "Chiaro, te lo segno come valutazione in sospeso."},
]

command = "ricordati il macinato di Seari"
label_only = "Macinato di interno auto di Seari."   # ciò che il vecchio path salvava

print(f"Comando: {command!r}\n")
print("── 1) brain.resolve_save_intent ────────────")
res = brain.resolve_save_intent(command, seari_history)
print(f"  mode       = {res.get('mode')!r}")
print(f"  confidence = {res.get('confidence')!r}")
print(f"  memory     = {res.get('memory')!r}\n")

# Sostanza attesa: almeno un dettaglio discusso, non solo l'etichetta.
substance_keywords = ["impurit", "mfi", "lotto", "grigio", "polipropilene",
                      "rimacinato", "paraurti", "plance", "vista", "scart", "costanza"]
mem = (res.get("memory") or "").lower()
hits = [k for k in substance_keywords if k in mem]

ok1 = res.get("mode") == "recent_topic"
ok2 = len(res.get("memory", "")) > len(label_only) + 10
ok3 = len(hits) >= 2

print(f"  [{'PASS' if ok1 else 'FAIL'}] mode == 'recent_topic'")
print(f"  [{'PASS' if ok2 else 'FAIL'}] memory più ricca della sola etichetta")
print(f"  [{'PASS' if ok3 else 'FAIL'}] memory contiene la sostanza discussa (hits={hits})\n")

print("── 2) save_service._resolve_content (path completo) ──")
content, kind = save_service._resolve_content(
    command, brain, prev_user_text=seari_history[-2]["content"],
    prev_assistant_text=seari_history[-1]["content"], fresh=True,
    recent_history=seari_history,
)
print(f"  kind    = {kind!r}")
print(f"  content = {content!r}\n")
ok4 = kind == "mix" and content and content.lower() != label_only.lower()
print(f"  [{'PASS' if ok4 else 'FAIL'}] kind=='mix' e content != etichetta\n")

print("── 3) fallback: comando diretto senza history ──")
# Senza history il resolver semantico cede → regex (comportamento attuale invariato).
c2, k2 = save_service._resolve_content(
    "memorizza che la pressa 4 ha il termoregolatore rotto", brain,
    prev_user_text="", prev_assistant_text="", fresh=True, recent_history=None,
)
print(f"  kind={k2!r} content={c2!r}")
ok5 = k2 == "direct" and "termoregolatore" in (c2 or "").lower()
print(f"  [{'PASS' if ok5 else 'FAIL'}] fallback a regex intatto\n")

all_ok = all([ok1, ok2, ok3, ok4, ok5])
print("══════════════════════════════════════════")
print(f"  RISULTATO: {'TUTTI PASS ✓' if all_ok else 'QUALCHE FAIL ✗'}")
print("══════════════════════════════════════════\n")
sys.exit(0 if all_ok else 1)
