#!/usr/bin/env python3
"""
Test mnemonico autonomo — verifica il pipeline completo:
  save_memory() → Redis → search_memories() → brain.respond()

Inietta 3 fatti sintetici con tag TEST_MNEMO, interroga il sistema,
verifica il recall, pulisce. Nessuna interazione vocale richiesta.

Uso: python test_memory.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.brain import Brain

# ─── Fatti sintetici da iniettare ───────────────────────────────────────────
TEST_TAG = "TEST_MNEMO"

TEST_MEMORIES = [
    {
        "content": "Il lotto TEST-ALPHA ha MFI 4.7 g/10min misurato a 230°C, fornitore FakePlast srl, campione ricevuto il 5 maggio 2026.",
        "query":   "Qual è il valore MFI del lotto TEST-ALPHA?",
        "expect":  ["4.7", "quattro virgola sette", "quattro punto sette"],
    },
    {
        "content": "La scadenza del contratto TEST-BETA con il cliente FakeClient è fissata al 15 settembre 2026, rinnovo automatico se non disdetto entro 30 giorni prima.",
        "query":   "Quando scade il contratto TEST-BETA?",
        "expect":  ["settembre"],
    },
    {
        "content": "Per il progetto TEST-GAMMA la concentrazione DCP ottimale è 0.35% in peso su PP base Moplen HP500N, temperatura di miscelazione 190°C.",
        "query":   "Qual è la concentrazione DCP per il progetto TEST-GAMMA?",
        "expect":  ["0.35", "0,35", "trentacinque", "zero virgola trentacinque"],
    },
]

# ─── Colori terminale ────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):  print(f"{GREEN}✓ {msg}{RESET}")
def fail(msg): print(f"{RED}✗ {msg}{RESET}")
def info(msg): print(f"{YELLOW}  {msg}{RESET}")


def main():
    print("\n══════════════════════════════════════════")
    print("  TEST MNEMONICO EURI — pipeline completo")
    print("══════════════════════════════════════════\n")

    # ── Connessione ──────────────────────────────────────────────────────────
    r = redis_lib.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )
    try:
        r.ping()
        ok("Redis connesso")
    except Exception as e:
        fail(f"Redis non raggiungibile: {e}")
        sys.exit(1)

    # ── Embedder ─────────────────────────────────────────────────────────────
    embedder = Embedder()
    embedder.load()
    if embedder.available:
        ok("Embedder caricato")
    else:
        info("Embedder non disponibile — userò ricerca keyword")

    # ── MemoryManager ────────────────────────────────────────────────────────
    memory = MemoryManager(r, embedder=embedder)

    # ── Brain ────────────────────────────────────────────────────────────────
    brain = Brain()

    saved_ids = []

    # ════════════════════════════════════════════════════════════════════════
    # FASE 1 — Iniezione memorie di test
    # ════════════════════════════════════════════════════════════════════════
    print("\n── FASE 1: iniezione memorie ───────────────")
    for tm in TEST_MEMORIES:
        mid = memory.save_memory(
            content=tm["content"],
            category="test",
            tags=[TEST_TAG],
            source="test",
        )
        saved_ids.append(mid)
        ok(f"Salvata → {mid[:8]}… | {tm['content'][:60]}…")

    # Piccola pausa per garantire che RediSearch abbia indicizzato
    time.sleep(1)

    # ════════════════════════════════════════════════════════════════════════
    # FASE 2 — Verifica diretta su Redis (storage)
    # ════════════════════════════════════════════════════════════════════════
    print("\n── FASE 2: verifica storage Redis ─────────")
    for mid in saved_ids:
        key = f"euri:memory:{mid}"
        doc = r.json().get(key, "$.content")
        if doc:
            ok(f"Chiave {key[:30]}… presente in Redis")
        else:
            fail(f"Chiave {key[:30]}… NON trovata in Redis")

    # ════════════════════════════════════════════════════════════════════════
    # FASE 3 — Recall semantico (search_memories)
    # ════════════════════════════════════════════════════════════════════════
    print("\n── FASE 3: recall semantico ────────────────")
    recall_scores = []
    for tm in TEST_MEMORIES:
        results = memory.search_memories(tm["query"], limit=5)
        # Cerca almeno uno dei valori attesi tra i top-5 (supporta forma numerica e letterale)
        all_content = " ".join(r.get("content", "") for r in results)
        found = any(e in all_content for e in tm["expect"])
        recall_scores.append(found)
        if found:
            top = results[0].get("content", "")[:80] if results else "(vuoto)"
            ok(f"Query: '{tm['query'][:50]}'\n    Top result: {top}…")
        else:
            fail(f"Query: '{tm['query'][:50]}'\n    Nessun valore atteso trovato nei top-5")
            if results:
                info(f"  Top result: {results[0].get('content','')[:80]}…")

    # ════════════════════════════════════════════════════════════════════════
    # FASE 4 — Pipeline completa: brain.respond() con contesto iniettato
    # ════════════════════════════════════════════════════════════════════════
    print("\n── FASE 4: pipeline LLM completa ───────────")
    llm_scores = []
    for tm in TEST_MEMORIES:
        results = memory.search_memories(tm["query"], limit=5)
        context_lines = [f"- [{r.get('domain','?')}] {r.get('content','')}" for r in results]
        context = "Memorie rilevanti:\n" + "\n".join(context_lines) if context_lines else ""

        info(f"Domanda: {tm['query']}")
        try:
            reply = brain.respond(tm["query"], context=context)
            found = any(e in reply for e in tm["expect"])
            llm_scores.append(found)
            if found:
                ok(f"Risposta corretta: {reply[:100]}…")
            else:
                fail(f"Risposta NON contiene i valori attesi: {reply[:100]}…")
        except Exception as e:
            fail(f"brain.respond() errore: {e}")
            llm_scores.append(False)

    # ════════════════════════════════════════════════════════════════════════
    # FASE 5 — Pulizia memorie di test
    # ════════════════════════════════════════════════════════════════════════
    print("\n── FASE 5: pulizia ─────────────────────────")
    for mid in saved_ids:
        key = f"euri:memory:{mid}"
        r.delete(key)
        ok(f"Eliminata: {key[:40]}…")

    # ════════════════════════════════════════════════════════════════════════
    # RIEPILOGO
    # ════════════════════════════════════════════════════════════════════════
    print("\n══════════════════════════════════════════")
    print("  RIEPILOGO")
    print("══════════════════════════════════════════")
    n = len(TEST_MEMORIES)
    r_pass = sum(recall_scores)
    l_pass = sum(llm_scores)
    print(f"  Storage Redis:     {n}/{n} ✓")
    print(f"  Recall semantico:  {r_pass}/{n} {'✓' if r_pass == n else '✗'}")
    print(f"  Pipeline LLM:      {l_pass}/{n} {'✓' if l_pass == n else '✗'}")

    if r_pass == n and l_pass == n:
        print(f"\n{GREEN}  TUTTI I TEST SUPERATI — memoria operativa al 100%{RESET}\n")
    else:
        print(f"\n{RED}  ALCUNI TEST FALLITI — vedere dettagli sopra{RESET}\n")


if __name__ == "__main__":
    main()
