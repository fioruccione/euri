"""
diag_convergence_quality.py — SONDA READ-ONLY (non tocca il Dream Engine).

Domanda: la soglia di "convergenza certa" del Dream Engine (dream_engine.py:696,
score cosine-distance < 0.15  ⇔  cosine similarity > 0.85) discrimina davvero, o cade
sulla media anisotropa di e5-large e conta come "convergenza quasi-identica" del brodo-template?

Replica offline la geometria del conteggio convergenze sul pool insight attuale:
- KNN top-4 per seed (self escluso → 3 vicini), esattamente come il codice;
- classifica ogni link "certain" (cos>0.85) in GENUINO (Δ = cos-μ ≥ 0.07, regola relativa
  dell'anisotropia) vs BRODO (cos>0.85 ma Δ<0.07, cioè solo baseline);
- stima quante promozioni-per-convergenza poggerebbero su brodo.

NESSUNA scrittura su Redis. NESSUNA modifica al sogno.
"""
import json
import numpy as np
import redis
import config

CONV_COS = 0.85       # dist < 0.15  → "convergenza certa" (auto-count, no LLM)
GREY_COS_LO = 0.60    # dist < 0.40  → zona grigia (LLM judge)
REL_DELTA = 0.07      # regola relativa max-mean dell'anisotropia e5
KNN = 4               # come nel codice (include self, poi skip → 3 vicini non-self)
MIN_CONV = config.DREAM_INSIGHT_MIN_CONVERGENCES  # 3


def load_insights():
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    ids, doms, embs = [], [], []
    for k in r.scan_iter(match="euri:insight:*"):
        try:
            emb = r.execute_command("JSON.GET", k, "$.embedding")
            st = r.execute_command("JSON.GET", k, "$.status")
            emb = json.loads(emb)[0] if emb else None
            st = json.loads(st)[0] if st else None
            if not emb or st != "promoted":
                continue
            da = json.loads(r.execute_command("JSON.GET", k, "$.domain_a") or "[null]")[0]
            db = json.loads(r.execute_command("JSON.GET", k, "$.domain_b") or "[null]")[0]
        except Exception:
            continue
        ids.append(k.split(":")[-1][:8])
        doms.append(f"{da}×{db}")
        embs.append(np.asarray(emb, dtype=np.float32))
    M = np.vstack(embs)
    M = M / np.linalg.norm(M, axis=1, keepdims=True)  # difensivo
    return ids, doms, M


def main():
    ids, doms, M = load_insights()
    n = len(ids)
    S = M @ M.T
    off = S[~np.eye(n, dtype=bool)]
    mu, sd = float(off.mean()), float(off.std())
    rel_thresh = mu + REL_DELTA

    print(f"Pool insight promossi: {n}")
    print(f"\n=== GEOMETRIA nuvola e5 (coseno off-diagonale) ===")
    print(f"  media μ = {mu:.4f}   σ = {sd:.4f}")
    for p in (50, 75, 90, 95, 99):
        print(f"  p{p} = {np.percentile(off, p):.4f}")
    pct_at_thresh = float((off < CONV_COS).mean() * 100)
    print(f"  → la soglia 'certa' {CONV_COS} è al percentile {pct_at_thresh:.1f}"
          f" (quota di coppie SOTTO soglia). Se ~50%, il cutoff è sulla media = no-op.")
    print(f"  → soglia relativa proposta μ+{REL_DELTA} = {rel_thresh:.4f}")

    # Replica meccanismo: top-KNN per seed (self escluso), classifica i link 'certain'
    certain, grey = [], []
    seed_certain_neighbors = np.zeros(n, dtype=int)
    seed_genuine_neighbors = np.zeros(n, dtype=int)
    for i in range(n):
        order = np.argsort(-S[i])            # self in testa (cos=1)
        neigh = [j for j in order if j != i][:KNN - 1]  # 3 vicini non-self
        for j in neigh:
            c = float(S[i, j])
            if c > CONV_COS:
                certain.append((i, j, c))
                seed_certain_neighbors[i] += 1
                if c - mu >= REL_DELTA:
                    seed_genuine_neighbors[i] += 1
            elif c > GREY_COS_LO:
                grey.append((i, j, c))

    n_cert = len(certain)
    genuine = [p for p in certain if p[2] - mu >= REL_DELTA]
    soup = [p for p in certain if p[2] - mu < REL_DELTA]
    print(f"\n=== LINK di convergenza 'certa' (cos>{CONV_COS}, auto-contati, no LLM) ===")
    print(f"  totali: {n_cert}")
    if n_cert:
        print(f"  GENUINI (Δ≥{REL_DELTA}): {len(genuine)}  ({100*len(genuine)/n_cert:.1f}%)")
        print(f"  BRODO   (Δ<{REL_DELTA}): {len(soup)}  ({100*len(soup)/n_cert:.1f}%)")

    # Propensione a promuovere: base convergences=1, servono ≥2 vicini certi per arrivare a MIN_CONV=3
    need = MIN_CONV - 1
    promotable_abs = int((seed_certain_neighbors >= need).sum())
    promotable_rel = int((seed_genuine_neighbors >= need).sum())
    print(f"\n=== PROPENSIONE promozione-per-convergenza (base=1, serve ≥{need} vicini) ===")
    print(f"  seed che raggiungono la soglia con cutoff ASSOLUTO {CONV_COS}: {promotable_abs}/{n}")
    print(f"  seed che la raggiungono con cutoff RELATIVO μ+{REL_DELTA}:   {promotable_rel}/{n}")
    print(f"  → {promotable_abs - promotable_rel} promozioni poggiano su convergenza-brodo"
          f" (evaporano con la regola relativa)")

    # Esempi concreti di BRODO: coppie cos>0.85 ma domini diversi, Δ minimo
    print(f"\n=== esempi di BRODO (cos>{CONV_COS}, ma Δ sulla media minimo → 'convergenza' falsa) ===")
    soup_sorted = sorted(soup, key=lambda p: p[2] - mu)[:6]
    for i, j, c in soup_sorted:
        print(f"  {ids[i]} [{doms[i][:28]}] ~ {ids[j]} [{doms[j][:28]}]  cos={c:.3f} Δ={c-mu:+.3f}")


if __name__ == "__main__":
    main()
