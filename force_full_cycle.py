#!/usr/bin/env python3
"""
Force-run di un ciclo Dream Engine completo, senza aspettare l'idle notturno.
Esegue in ordine: Loop 2b (sogni) → Loop 2c (insight eval) → Loop 2f
(contraddizioni) → cleanup expired/stale → Loop 2d (pruning) → Loop 2e
(consolidation, gated 24h ma _consolidation_last_run parte a 0 in fresh init).

Stampa snapshot before/after per misurare l'effetto del ciclo.

Uso: venv/bin/python force_full_cycle.py
"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.dream_engine import DreamEngine


def snapshot(r):
    by_source = Counter()
    superseded = 0
    for key in r.scan_iter("euri:memory:*"):
        try:
            data = r.json().get(key, "$")
            if not data:
                continue
            doc = data[0]
            by_source[doc.get("source", "?")] += 1
            if doc.get("superseded_by"):
                superseded += 1
        except Exception:
            pass

    candidates = promoted = 0
    for key in r.scan_iter("euri:insight:*"):
        try:
            data = r.json().get(key, "$")
            if not data:
                continue
            status = data[0].get("status", "?")
            if status == "candidate":
                candidates += 1
            elif status == "promoted":
                promoted += 1
        except Exception:
            pass

    return dict(by_source), superseded, candidates, promoted


def _print_state(by_src, sup, cand, prom):
    for src, n in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {src:<18} {n:>4}")
    print(f"  superseded_by:     {sup}")
    print(f"  insight candidate: {cand}")
    print(f"  insight promoted:  {prom}")


def _delta(a, b):
    d = a - b
    return f"+{d}" if d > 0 else (str(d) if d < 0 else " 0")


def main():
    print("\n══════════════════════════════════════════")
    print("  DREAM ENGINE — Ciclo completo forzato")
    print("══════════════════════════════════════════\n")

    r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                        db=config.REDIS_DB, decode_responses=True)
    r.ping()
    print("✓ Redis connesso")

    emb = Embedder()
    emb.load()
    print("✓ Embedder caricato")

    mem = MemoryManager(r, embedder=emb)
    engine = DreamEngine(r, emb, memory=mem)
    print("✓ DreamEngine costruito\n")

    print("── Stato PRIMA ─────────────────────────────")
    by_src_b, sup_b, cand_b, prom_b = snapshot(r)
    _print_state(by_src_b, sup_b, cand_b, prom_b)

    print("\n── Esecuzione ciclo completo ───────────────")
    t0 = time.time()
    engine._run_dream_cycle()
    elapsed = time.time() - t0
    print(f"\n✓ Ciclo completato in {elapsed:.1f}s")

    print("\n── Stato DOPO ──────────────────────────────")
    by_src_a, sup_a, cand_a, prom_a = snapshot(r)
    all_sources = sorted(set(by_src_b) | set(by_src_a),
                         key=lambda s: -by_src_a.get(s, 0))
    for src in all_sources:
        b = by_src_b.get(src, 0)
        a = by_src_a.get(src, 0)
        print(f"  {src:<18} {a:>4}  ({_delta(a, b)})")
    print(f"  superseded_by:     {sup_a}  ({_delta(sup_a, sup_b)})")
    print(f"  insight candidate: {cand_a}  ({_delta(cand_a, cand_b)})")
    print(f"  insight promoted:  {prom_a}  ({_delta(prom_a, prom_b)})")


if __name__ == "__main__":
    main()
