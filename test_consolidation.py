#!/usr/bin/env python3
"""
Test manuale Loop 2e — esegue la consolidazione senza aspettare l'idle notturno.
Uso: venv/bin/python test_consolidation.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.dream_engine import DreamEngine

print("\n══════════════════════════════════════════")
print("  LOOP 2e — Memory Consolidation (forzato)")
print("══════════════════════════════════════════\n")

r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
r.ping()
print("✓ Redis connesso")

emb = Embedder()
emb.load()
print("✓ Embedder caricato\n")

mem = MemoryManager(r, embedder=emb)
engine = DreamEngine(r, emb, memory=mem)

# Conta memorie consolidate prima
before = sum(1 for _ in r.scan_iter("euri:memory:*")
             if (r.json().get(_, "$.source") or [None])[0] == "loop2e")
print(f"Memorie consolidate esistenti: {before}\n")

print("── Avvio consolidazione ────────────────────")
engine._consolidation_pass()

after = sum(1 for _ in r.scan_iter("euri:memory:*")
            if (r.json().get(_, "$.source") or [None])[0] == "loop2e")

print(f"\n── Risultato ───────────────────────────────")
print(f"  Nuove consolidazioni: {after - before}")
print(f"  Totale consolidate:   {after}")

if after > before:
    print("\n── Contenuto nuove memorie consolidate ─────")
    for key in r.scan_iter("euri:memory:*"):
        d = r.json().get(key, "$")
        if d and d[0].get("source") == "loop2e":
            doc = d[0]
            print(f"\nDominio: {doc.get('domain','?')}")
            print(f"ID:      {doc.get('id','?')[:8]}…")
            print(f"Sintesi: {doc.get('content','')[:300]}")
            print(f"Da:      {len(doc.get('consolidated_from', []))} memorie sorgente")
