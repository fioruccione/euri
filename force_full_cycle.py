#!/usr/bin/env python3
"""
Force-run di un ciclo Dream Engine completo, senza aspettare l'idle.
Esegue in ordine: Loop 2b (sogni) → Loop 2c (insight eval) → Loop 2f
(contraddizioni) → Loop 2g (correzioni) → plausibility gate (flag-only)
→ cleanup expired/stale → Loop 2d (pruning) → Loop 2e (consolidation,
gated 24h ma _consolidation_last_run parte a 0 in fresh init).

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
    audit_flagged = 0
    plausibility_flagged = 0
    for key in r.scan_iter("euri:memory:*"):
        try:
            data = r.json().get(key, "$")
            if not data:
                continue
            doc = data[0]
            by_source[doc.get("source", "?")] += 1
            if doc.get("superseded_by"):
                superseded += 1
            if doc.get("audit_flag", 0) > 0:
                audit_flagged += 1
            if doc.get("plausibility_flag"):
                plausibility_flagged += 1
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

    corr = Counter()
    for key in r.scan_iter("euri:correction:*"):
        try:
            data = r.json().get(key, "$")
            if not data:
                continue
            doc = data[0]
            if doc.get("status") == "pending":
                corr["pending"] += 1
            else:
                corr[doc.get("verdict") or "analyzed"] += 1
        except Exception:
            pass

    return dict(by_source), superseded, candidates, promoted, audit_flagged, plausibility_flagged, dict(corr)


def _print_state(by_src, sup, cand, prom, audit_flagged, plausibility_flagged, corr):
    for src, n in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {src:<18} {n:>4}")
    print(f"  superseded_by:     {sup}")
    print(f"  audit_flagged:     {audit_flagged}")
    print(f"  plausibility_flag: {plausibility_flagged}")
    print(f"  insight candidate: {cand}")
    print(f"  insight promoted:  {prom}")
    if corr:
        print(f"  correction signals: {dict(corr)}")


def _delta(a, b):
    d = a - b
    return f"+{d}" if d > 0 else (str(d) if d < 0 else " 0")


def _inject_test_correction(mem):
    """Inietta un correction signal sintetico per testare il Loop 2g end-to-end."""
    sid = mem.save_correction_signal(
        prompt_originale="Quanto pesava il campione ICS che dovevamo consegnare?",
        risposta_euri="Era da 25 chili, grado 15.",
        correzione_user="No, sbagli, il campione ICS era 15 chili e grado 25, hai invertito i valori.",
        rag_ctx_ids=[],
    )
    print(f"✓ Iniettato correction signal di test: {sid[:8]}")
    return sid


def main():
    import sys as _sys
    inject = "--inject" in _sys.argv

    print("\n══════════════════════════════════════════")
    print("  DREAM ENGINE — Ciclo completo forzato")
    if inject:
        print("  [modalità test: inietto correction signal]")
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

    if inject:
        _inject_test_correction(mem)
        print()

    print("── Stato PRIMA ─────────────────────────────")
    by_src_b, sup_b, cand_b, prom_b, af_b, pf_b, corr_b = snapshot(r)
    _print_state(by_src_b, sup_b, cand_b, prom_b, af_b, pf_b, corr_b)

    print("\n── Esecuzione ciclo completo ───────────────")
    t0 = time.time()
    engine._run_dream_cycle()
    elapsed = time.time() - t0
    print(f"\n✓ Ciclo completato in {elapsed:.1f}s")

    print("\n── Stato DOPO ──────────────────────────────")
    by_src_a, sup_a, cand_a, prom_a, af_a, pf_a, corr_a = snapshot(r)
    all_sources = sorted(set(by_src_b) | set(by_src_a),
                         key=lambda s: -by_src_a.get(s, 0))
    for src in all_sources:
        b = by_src_b.get(src, 0)
        a = by_src_a.get(src, 0)
        print(f"  {src:<18} {a:>4}  ({_delta(a, b)})")
    print(f"  superseded_by:     {sup_a}  ({_delta(sup_a, sup_b)})")
    print(f"  audit_flagged:     {af_a}  ({_delta(af_a, af_b)})")
    print(f"  plausibility_flag: {pf_a}  ({_delta(pf_a, pf_b)})")
    print(f"  insight candidate: {cand_a}  ({_delta(cand_a, cand_b)})")
    print(f"  insight promoted:  {prom_a}  ({_delta(prom_a, prom_b)})")
    if corr_a or corr_b:
        print(f"  correction signals: {corr_b} → {corr_a}")


if __name__ == "__main__":
    main()
