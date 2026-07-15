"""
diag_convergence_correlation.py — ANALIZZATORE OFFLINE (read-only, nessuna scrittura).

Unisce lo stream euri:convergence:trace (instrumentazione forward in dream_engine:
_trace_convergence) col recalled_count ATTUALE degli insight, per rispondere alla domanda
che il pool dei soli promossi non poteva (selection bias):

  1. La convergenza-al-momento-della-decisione predice l'USO reale (recall) tra i promossi?
  2. La convergenza ricalcolata a livello di CLAIM (claim-embedding + soglia relativa μ+δ,
     dagli stessi vicini loggati) predice l'uso MEGLIO del full-text usato dalla decisione?

Nota epistemica: solo i PROMOSSI possono essere richiamati (search_insights filtra
@status:{promoted}), quindi il legame convergenza↔uso è misurabile SOLO tra i promossi.
Lo stream registra anche gli scartati: servono per la DISTRIBUZIONE (quanti candidate a
quale convergenza) e per confrontare la selettività claim-vs-full-text su candidate reali.

Vuoto finché il daemon col nuovo codice non ha valutato candidate. Read-only.
"""
import json
import numpy as np
import redis
import config

REL_DELTA = 0.07
MIN_MATURITY_DAYS = 3  # un promosso troppo fresco non ha ancora avuto occasione di essere usato
MARKERS = ["la connessione operativa non ovvia è:", "la connessione operativa non ovvia e:",
           "la connessione operativa è:"]


def claim_of(c: str) -> str:
    low = (c or "").lower()
    for m in MARKERS:
        k = low.find(m)
        if k >= 0:
            return c[k + len(m):].strip()
    ls = [l for l in (c or "").splitlines() if l.strip()]
    return ls[-1].strip() if ls else (c or "").strip()


def _f(x, default=None):
    try:
        v = json.loads(x)[0] if x else None
        return v if v is not None else default
    except Exception:
        return default


def main():
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    entries = r.xrange("euri:convergence:trace")
    if not entries:
        print("Stream euri:convergence:trace VUOTO — nessun dato ancora.")
        print("Serve: restart del daemon (per caricare il nuovo codice dell'hook) + candidate")
        print("valutati dal ciclo insight (nascono da sogni creativi e conversazioni).")
        print("Poi rilanciare: la correlazione convergenza↔uso ha senso con qualche settimana di dati.")
        return

    from core.embedder import Embedder
    emb = Embedder(); emb.load()

    def enc(t):
        v = emb.encode(t, mode="passage")
        return v / (np.linalg.norm(v) or 1.0)

    # μ_claim di riferimento dal pool promosso attuale (soglia relativa stabile)
    pool = []
    for k in r.scan_iter(match="euri:insight:*"):
        if _f(r.execute_command("JSON.GET", k, "$.status")) == "promoted":
            c = _f(r.execute_command("JSON.GET", k, "$.content"))
            if c:
                pool.append(claim_of(c))
    PC = np.vstack([enc(x) for x in pool]) if pool else None
    muC = float((PC @ PC.T)[~np.eye(len(pool), dtype=bool)].mean()) if pool else 0.82
    thr = muC + REL_DELTA
    now = __import__("time").time()

    rows = []
    for eid, fdict in entries:
        try:
            ts = float(fdict.get("ts") or 0)
        except Exception:
            ts = 0.0
        seed = fdict.get("seed_id", "")
        outcome = fdict.get("outcome", "?")
        conv = int(fdict.get("convergences") or 0)
        ncert = int(fdict.get("n_certain") or 0)
        policy = fdict.get("promotion_policy") or "vector_auto_v1"
        njudge = int(fdict.get("n_judge_confirmed") or 0)
        neighbors = []
        try:
            neighbors = json.loads(fdict.get("neighbors") or "[]")
        except Exception:
            pass
        # ricalcolo convergenza CLAIM dagli stessi vicini loggati
        seed_claim_vec = enc(claim_of(fdict.get("seed_content", "")))
        claim_conv = 0
        for nb in neighbors:
            try:
                _, _, ncontent = nb
            except Exception:
                continue
            if float(seed_claim_vec @ enc(claim_of(ncontent))) >= thr:
                claim_conv += 1
        # recall attuale (solo i promossi sono richiamabili e ancora esistenti)
        cur_rc = None
        if outcome == "promoted":
            cur_rc = _f(r.execute_command("JSON.GET", seed, "$.recalled_count"))
        rows.append(dict(seed=seed, ts=ts, outcome=outcome, conv=conv, ncert=ncert,
                         policy=policy, njudge=njudge,
                         claim_conv=claim_conv, cur_rc=cur_rc,
                         age_days=(now - ts) / 86400 if ts else None))

    # dedup per seed: una sola decisione per candidate — preferisci la TERMINALE, poi la più
    # recente (lo stesso seed è ri-valutato/loggato più volte; robusto anche ai dati vecchi).
    TERMINAL = {"promoted", "denied_format", "denied_repromotion"}
    best = {}
    for r_ in rows:
        rank = (1 if r_["outcome"] in TERMINAL else 0, r_["ts"])
        prev = best.get(r_["seed"])
        if prev is None or rank > prev[0]:
            best[r_["seed"]] = (rank, r_)
    n_raw = len(rows)
    rows = [v[1] for v in best.values()]

    # ── Report ─────────────────────────────────────────────
    from collections import Counter
    print(f"Trace: {n_raw} entry grezze → {len(rows)} candidate distinti (dedup per seed)"
          f"  |  μ_claim={muC:.4f}  soglia claim={thr:.4f}\n")
    print("=== distribuzione esiti ===")
    for oc, n in Counter(r["outcome"] for r in rows).most_common():
        print(f"  {oc:22s} {n}")
    print("\n=== versioni policy ===")
    for policy, n in Counter(r["policy"] for r in rows).most_common():
        print(f"  {policy:22s} {n}")

    promoted = [r for r in rows if r["outcome"] == "promoted" and r["cur_rc"] is not None]
    mature = [r for r in promoted if (r["age_days"] or 0) >= MIN_MATURITY_DAYS]
    print(f"\n=== convergenza ↔ USO (solo promossi ancora vivi, maturi ≥{MIN_MATURITY_DAYS}g: {len(mature)}) ===")
    if len(mature) < 8:
        print("  campione ancora troppo piccolo per una correlazione — accumula dati.")
    else:
        used = [r for r in mature if (r["cur_rc"] or 0) > 0]
        unused = [r for r in mature if (r["cur_rc"] or 0) == 0]
        def avg(xs, key): return np.mean([x[key] for x in xs]) if xs else float("nan")
        print(f"  usati (recall>0): {len(used)} | mai usati: {len(unused)}")
        print(f"  convergenza FULL-TEXT media  — usati {avg(used,'conv'):.2f} vs mai-usati {avg(unused,'conv'):.2f}")
        print(f"  convergenza CLAIM media       — usati {avg(used,'claim_conv'):.2f} vs mai-usati {avg(unused,'claim_conv'):.2f}")
        print("  → se 'usati' hanno convergenza nettamente più alta, quella metrica PREDICE l'uso.")
        print("    Se full-text non separa ma claim sì, il claim-level è il segnale migliore.")

    # selettività claim vs full-text su TUTTI i candidate (anche scartati)
    print("\n=== selettività su candidate reali (full-text usato vs claim ricalcolato) ===")
    would_full = sum(1 for r in rows if r["ncert"] >= config.DREAM_INSIGHT_MIN_CONVERGENCES - 1)
    would_claim = sum(1 for r in rows if r["claim_conv"] >= config.DREAM_INSIGHT_MIN_CONVERGENCES - 1)
    print(f"  raggiungerebbero soglia con FULL-TEXT (n_certain): {would_full}/{len(rows)}")
    print(f"  raggiungerebbero soglia con CLAIM (relativo):      {would_claim}/{len(rows)}")
    judged = [r for r in rows if r["policy"] == "claim_judge_v2"]
    if judged:
        would_judge = sum(
            1 for r in judged
            if r["njudge"] >= config.DREAM_INSIGHT_MIN_CONVERGENCES - 1
        )
        print(f"  confermati dal JUDGE v2:                           "
              f"{would_judge}/{len(judged)}")


if __name__ == "__main__":
    main()
