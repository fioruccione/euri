"""
diag_convergence_sim.py — SIMULAZIONE READ-ONLY (non tocca il Dream Engine).

Controfattuale: se la convergenza usasse CLAIM-embedding + soglia RELATIVA invece del
testo-intero + cutoff assoluto 0.85, quante delle 172 promozioni reggerebbero?

Rigioca la logica di promozione (base=1, servono ≥2 vicini convergenti su KNN top-3,
soglia MIN_CONVERGENCES=3) sul pool insight attuale — vicini = altri promossi (proxy
della geometria dei candidate). Poi:
  - sweep di δ per la soglia relativa μ_claim+δ → curva del volume;
  - incrocio survivor/dropped col recalled_count (validazione-dall'uso): il taglio è SANO
    se droppa soprattutto i mai-usati e tiene gli usati.

Nessuna scrittura. base=1 = risveglio da zero sotto la nuova regola (onesto: non riusa il
convergence_count storico, accumulato sotto la regola rotta).
"""
import json
import numpy as np
import redis
import config

ABS = 0.85
NEED = config.DREAM_INSIGHT_MIN_CONVERGENCES - 1   # base=1 → servono NEED vicini
MARKERS = ["la connessione operativa non ovvia è:", "la connessione operativa non ovvia e:",
           "la connessione operativa è:"]


def claim_of(c):
    low = c.lower()
    for m in MARKERS:
        k = low.find(m)
        if k >= 0:
            return c[k + len(m):].strip()
    ls = [l for l in c.splitlines() if l.strip()]
    return ls[-1].strip() if ls else c.strip()


def main():
    from core.embedder import Embedder
    emb = Embedder(); emb.load()
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    ids, EF, claims, rc = [], [], [], []
    for k in r.scan_iter(match="euri:insight:*"):
        e = r.execute_command("JSON.GET", k, "$.embedding")
        st = json.loads(r.execute_command("JSON.GET", k, "$.status") or "[null]")[0]
        c = json.loads(r.execute_command("JSON.GET", k, "$.content") or "[null]")[0]
        if not e or st != "promoted" or not c:
            continue
        rcv = json.loads(r.execute_command("JSON.GET", k, "$.recalled_count") or "[0]")[0] or 0
        ids.append(k[-8:]); EF.append(np.asarray(json.loads(e)[0], dtype=np.float32))
        claims.append(claim_of(c)); rc.append(int(rcv))
    n = len(ids)
    rc = np.array(rc)
    EF = np.vstack(EF); EF /= np.linalg.norm(EF, axis=1, keepdims=True)
    EC = np.vstack([emb.encode(cl, mode="passage") for cl in claims])
    EC /= np.linalg.norm(EC, axis=1, keepdims=True)
    SF, SC = EF @ EF.T, EC @ EC.T
    muC = float(SC[~np.eye(n, dtype=bool)].mean())

    def survivors(S, thr):
        """seed con ≥NEED vicini (KNN top-3 non-self) sopra soglia thr."""
        out = np.zeros(n, dtype=bool)
        for i in range(n):
            order = [j for j in np.argsort(-S[i]) if j != i][:3]
            cnt = sum(1 for j in order if S[i, j] > thr)
            out[i] = cnt >= NEED
        return out

    used = rc > 0
    print(f"Pool: {n} promossi | usati (recalled_count>0): {int(used.sum())} "
          f"| mai usati: {int((~used).sum())}")
    print(f"μ_claim = {muC:.4f}   (regola: base=1, servono ≥{NEED} vicini convergenti)\n")

    old = survivors(SF, ABS)
    print(f"=== REGOLA VECCHIA (testo intero, cutoff assoluto {ABS}) ===")
    print(f"  promuoverebbero: {int(old.sum())}/{n}")
    print(f"  di cui usati: {int((old & used).sum())} | mai usati: {int((old & ~used).sum())}\n")

    print(f"=== REGOLA NUOVA (claim-embedding, soglia RELATIVA μ_claim+δ) — sweep δ ===")
    print(f"  {'δ':>5} {'soglia':>8} {'promuovono':>11} {'di cui usati':>13} {'droppati usati':>15} {'droppati mai-usati':>19}")
    for d in (0.04, 0.05, 0.07, 0.10, 0.13):
        thr = muC + d
        new = survivors(SC, thr)
        dropped = old & ~new           # promuovevano prima, non più
        print(f"  {d:>5.2f} {thr:>8.4f} {int(new.sum()):>11}"
              f" {int((new & used).sum()):>13}"
              f" {int((dropped & used).sum()):>15}"
              f" {int((dropped & ~used).sum()):>19}")

    print("\n  Lettura: 'droppati usati' alti = taglio BRUTALE (perde insight validati dall'uso);"
          "\n  'droppati mai-usati' alti con 'droppati usati' ~0 = taglio SANO (toglie solo il timbro).")


if __name__ == "__main__":
    main()
