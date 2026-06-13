#!/usr/bin/env python3
"""
Diagnostico READ-ONLY del Loop 2e same-subject gate (Gradino 0).
Ricostruisce i cluster come _consolidation_pass, fa girare il VERO _same_subject_gate
e mostra, per i cluster che il gate sfoltisce, seed + TENUTI vs ESCLUSI col contenuto.
Non salva, non consolida, non tocca Redis. Richiede Ollama (dream model) acceso.

Uso: venv/bin/python diag_gate.py [N]   (N = quanti cluster sfoltiti mostrare, default 3)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from redis.commands.search.query import Query
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.dream_engine import DreamEngine

SHOW = int(sys.argv[1]) if len(sys.argv) > 1 else 3
MIN_RECALLED, MIN_CLUSTER = 3, 3
SKIP_SOURCES = {"loop2e", "campus", "web", "reflection"}

r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
r.ping()
emb = Embedder(); emb.load()
mem = MemoryManager(r, embedder=emb)
engine = DreamEngine(r, emb, memory=mem)
print("✓ Redis + embedder + engine pronti\n")

# 1. Candidati (come _consolidation_pass)
candidates = []
for key in r.scan_iter("euri:memory:*"):
    try:
        d = r.json().get(key, "$")
    except Exception:
        continue
    if not d:
        continue
    doc = d[0]
    if doc.get("source") in SKIP_SOURCES or doc.get("requires_verification"):
        continue
    if doc.get("recalled_count", 0) < MIN_RECALLED:
        continue
    candidates.append(doc)
qualified_by_id = {doc.get("id", ""): doc for doc in candidates}
print(f"Candidati qualificati: {len(candidates)}\n")

_raw = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                       db=config.REDIS_DB, decode_responses=False)

def _dec(v):
    if v is None: return ""
    return v.decode() if isinstance(v, bytes) else str(v)

shown = 0
seen_fp = set()
for seed in candidates:
    if shown >= SHOW:
        break
    seed_id = seed.get("id", "")
    seed_domain = seed.get("domain", "generale")
    if not seed.get("embedding") or seed_domain == "generale":
        continue
    try:
        vec = emb.encode(seed.get("content", ""), mode="query")
        if vec is None:
            continue
        safe_domain = seed_domain.replace(" ", "\\ ")
        q = (Query(f"(@domain:{{{safe_domain}}})=>[KNN 6 @embedding $vec AS score]")
             .sort_by("score")
             .return_fields("id", "content").dialect(2))
        res = _raw.ft("idx:memories").search(q, query_params={"vec": vec.astype("float32").tobytes()})
    except Exception:
        continue

    cluster = []
    for doc in res.docs:
        did = _dec(doc.id).replace("euri:memory:", "")
        if did in qualified_by_id:
            cluster.append({"id": did, "content": qualified_by_id[did].get("content", "")})
    if len(cluster) < MIN_CLUSTER:
        continue

    # ordine come nel gate (seed in testa), primi 5
    ordered = sorted(cluster, key=lambda dd: dd.get("id") != seed_id)
    items = ordered[:5]
    fp = "|".join(sorted(it["id"] for it in items))
    if fp in seen_fp:
        continue
    seen_fp.add(fp)

    kept = engine._same_subject_gate(list(cluster), seed_domain, seed_id)
    kept_ids = {k["id"] for k in kept}
    dropped = [it for it in items if it["id"] not in kept_ids]
    if not dropped:
        continue  # interessano solo i cluster sfoltiti

    shown += 1
    print("═" * 70)
    print(f"CLUSTER #{shown}  dominio={seed_domain!r}  (KNN={len(items)} valutati)")
    print("═" * 70)
    print(f"SEED [{seed_id[:8]}]:\n   {(seed.get('content') or '').strip()[:260]}\n")
    print("TENUTI (stesso soggetto):")
    for it in items:
        if it["id"] in kept_ids and it["id"] != seed_id:
            print(f"  ✓ [{it['id'][:8]}] {it['content'].strip()[:200]}")
    print("\nESCLUSI dal gate (giudicati soggetto diverso):")
    for it in dropped:
        print(f"  ✗ [{it['id'][:8]}] {it['content'].strip()[:200]}")
    print()

if shown == 0:
    print("Nessun cluster sfoltito dal gate in questo passaggio (tutti coerenti o sotto soglia).")
