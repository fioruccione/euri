#!/usr/bin/env python3
"""
Riparazione N3 — ripristina i 2 atomi fattuali soft-deletati per errore dal Loop 2f
(assorbimento dannoso: vincitore che NON contiene il fatto). Reversibile: rimuove solo
$.superseded_by (stato identico agli atomi vivi), logga il vecchio valore.

Fasi: verifica PRIMA (atomo assente dal richiamo, touch=False) → ripara → verifica DOPO.

Uso:
  venv/bin/python repair_n3_atoms.py            # dry-run: solo verifica PRIMA
  venv/bin/python repair_n3_atoms.py --apply    # ripara + verifica DOPO
"""
import argparse
import sys
from pathlib import Path

import redis as redis_lib

sys.path.insert(0, str(Path(__file__).parent))
import config
from core.embedder import Embedder
from core.memory_manager import MemoryManager

# (prefix_atomo, query_di_richiamo, descrizione)
ATOMS = [
    ("4f70b1cb", "qual è l'MFI del lotto 03 PR043", "MFI 11,2 lotto PR043"),
    ("0aa1089c", "com'è organizzata la struttura operativa di Lucy Plast, reparti estrusione e granulazione",
     "struttura operativa Lucy Plast"),
]


def _full_key(r, prefix):
    ks = list(r.scan_iter(f"euri:memory:{prefix}*"))
    return ks[0] if ks else None


def _in_recall(mm, query, prefix) -> tuple[bool, list[str]]:
    """True se l'atomo compare tra i risultati del richiamo reale (touch=False = no side effect)."""
    res = mm.search_memories(query, limit=6, touch=False)
    ids = [r.get("id", "").replace("euri:memory:", "")[:8] for r in res]
    return any(i.startswith(prefix) for i in ids), ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="esegue la riparazione (default: dry-run)")
    args = ap.parse_args()

    r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                        db=config.REDIS_DB, decode_responses=True)
    emb = Embedder()
    emb.load()
    mm = MemoryManager(r, embedder=emb)

    print("=" * 70)
    print("FASE 1 — verifica PRIMA (l'atomo dovrebbe essere ASSENTE dal richiamo)")
    print("=" * 70)
    before = {}
    for prefix, query, desc in ATOMS:
        present, ids = _in_recall(mm, query, prefix)
        before[prefix] = present
        print(f"\n  [{prefix}] {desc}")
        print(f"    query: «{query}»")
        print(f"    presente nel richiamo? {'SÌ' if present else 'NO'}   (top: {ids})")

    if not args.apply:
        print("\n(dry-run: niente riparazione. Rilancia con --apply per ripristinare.)")
        return

    print("\n" + "=" * 70)
    print("FASE 2 — riparazione (rimuovo $.superseded_by, reversibile)")
    print("=" * 70)
    for prefix, query, desc in ATOMS:
        k = _full_key(r, prefix)
        if not k:
            print(f"  [{prefix}] NON trovato, salto")
            continue
        old = r.json().get(k, "$.superseded_by")
        old = old[0] if isinstance(old, list) and old else old
        if not old:
            print(f"  [{prefix}] già vivo (nessun superseded_by), salto")
            continue
        r.json().delete(k, "$.superseded_by")
        print(f"  [{prefix}] {desc}: superseded_by rimosso (era → {str(old)[:8]})")

    print("\n" + "=" * 70)
    print("FASE 3 — verifica DOPO (l'atomo dovrebbe RICOMPARIRE nel richiamo)")
    print("=" * 70)
    all_ok = True
    for prefix, query, desc in ATOMS:
        present, ids = _in_recall(mm, query, prefix)
        ricomparso = present and not before[prefix]
        mark = "✓" if present else "✗"
        print(f"\n  {mark} [{prefix}] {desc}")
        print(f"    presente nel richiamo? {'SÌ' if present else 'NO'}   (top: {ids})")
        if present and not before[prefix]:
            print(f"    → RICOMPARSO (era assente prima)")
        elif present and before[prefix]:
            print(f"    → era già presente (verifica il livello keyword)")
        else:
            print(f"    → ANCORA ASSENTE — indagare")
            all_ok = False

    print("\n" + "=" * 70)
    print("OK: entrambi gli atomi ricompaiono nel richiamo." if all_ok
          else "ATTENZIONE: qualche atomo non ricompare — indagare.")
    print("=" * 70)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
