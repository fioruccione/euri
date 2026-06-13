#!/usr/bin/env python3
"""
Diagnostico READ-ONLY del recall temporale (fix A+B).
Per "cosa abbiamo fatto ieri?" mostra il context PRIMA (vecchio: finestra limit=5 senza
priorità di fonte → pescava le reflection tardo-serali) e DOPO (nuovo: finestra ampia +
prioritize_window → fonti vissute in testa). Verifica che emergano Seari/carbonato/
perossido/Lucy/Fanti/Poseidon invece di Wi-Fi/Superbike. NON scrive nulla (touch=False).

Uso: venv/bin/python diag_temporal_recall.py
"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from core.memory_manager import MemoryManager
from core.temporal_recall import prioritize_window
from utils.temporal import extract_temporal_range

QUERY = "cosa abbiamo fatto ieri?"

r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
r.ping()
mem = MemoryManager(r)
print(f"✓ Redis connesso\n\nQuery: {QUERY!r}")

tr = extract_temporal_range(QUERY, datetime.now())
if not tr:
    print("Nessun range temporale estratto — query non riconosciuta come temporale.")
    sys.exit(1)
ts_start, ts_end = tr
print(f"Finestra: {datetime.fromtimestamp(ts_start):%Y-%m-%d %H:%M} → {datetime.fromtimestamp(ts_end):%Y-%m-%d %H:%M}\n")


def show(label, mems, cap):
    print(f"── {label} (top {cap}) ──")
    for m in mems[:cap]:
        cid = (m.get("id") or "")[:8]
        print(f"  [{m.get('source'):<10}/{m.get('domain','?')}] {cid} {(m.get('content') or '').strip()[:80]}")
    print()
    return mems[:cap]


# PRIMA: vecchio comportamento del blocco temporale (limit=5, nessuna priorità fonte, cap 6)
old = mem.search_memories_by_timerange(ts_start, ts_end, limit=5, touch=False)
top_old = show("PRIMA — finestra limit=5, ordine created_at", old, 6)

# DOPO: nuovo (finestra di TUTTO il giorno + prioritize_window, cap temporale 10)
window = mem.search_memories_by_timerange(ts_start, ts_end, limit=200, touch=False)
new = prioritize_window(window)
top_new = show("DOPO — finestra giorno intero + prioritize_window (parlato → consolidato → reflection)", new, 10)

# Verifica: nel DOPO devono emergere i temi reali di ieri
joined = " ".join((m.get("content") or "").lower() for m in top_new)
checks = {
    "Seari": "seari" in joined,
    "carbonato": "carbonato" in joined,
    "perossido": "perossido" in joined,
    "Lucy/Fanti": ("lucy" in joined or "fanti" in joined),
    "Poseidon": "poseidon" in joined,
}
print("── Verifica temi reali di ieri nel DOPO (top 6) ──")
for k, v in checks.items():
    print(f"  [{'PASS' if v else '----'}] {k}")

# Confronto Wi-Fi/telecom nei due
def telecom_count(mems):
    return sum(1 for m in mems if any(w in (m.get("content") or "").lower()
              for w in ["wi-fi", "wifi", "ponti radio", "trasmissione dati", "telecomunicaz", "5 ghz"]))
print(f"\n  nodi Wi-Fi/telecom nel top6  PRIMA={telecom_count(top_old)}  DOPO={telecom_count(top_new)}")
hits = sum(checks.values())
print(f"\n{'PASS' if hits >= 3 else 'FAIL'}: {hits}/5 temi reali emergono nel DOPO.")
sys.exit(0 if hits >= 3 else 1)
