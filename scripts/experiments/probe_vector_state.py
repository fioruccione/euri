"""Sonda offline: uno stato vettoriale decadente porta segnale predittivo reale?

Pre-registrazione: SONDA_STATO_VETTORIALE.md. Read-only su Redis, nessun daemon,
nessuna scrittura. Compito: dato lo stato S dopo l'evento t (sequenza cronologica
delle memorie con embedding), predire il dominio dell'evento t+1. Confronto con
baseline simboliche banali; valutazione anche sulle sole transizioni con CAMBIO di
dominio (i burst del passive learner gonfiano "ultimo dominio").

Ipotesi nulla da falsificare: nello spazio e5 anisotropo S collassa sul centroide
e non batte "il dominio degli ultimi eventi".
"""
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, "/home/fio/Euri")

import config
import redis

MIN_CLASS_EVENTS = 20
TAUS = {"1h": 3600.0, "6h": 6 * 3600.0, "24h": 24 * 3600.0, "7g": 7 * 86400.0}
EMAS = {"ema.1": 0.1, "ema.3": 0.3, "ema.5": 0.5}
RNG = np.random.default_rng(20260713)


def load_events():
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    events, seen_content = [], set()
    for k in r.scan_iter(match="euri:memory:*"):
        d = r.json().get(k, "$")
        if not d:
            continue
        d = d[0]
        emb, dom, ts = d.get("embedding"), d.get("domain"), d.get("created_at")
        content = (d.get("content") or "").strip().lower()
        if not emb or not dom or not ts or not content:
            continue
        if content in seen_content:  # dedup esatto (doppioni del passive)
            continue
        seen_content.add(content)
        events.append((float(ts), dom, np.asarray(emb, dtype=np.float32),
                       d.get("source", "?")))
    events.sort(key=lambda e: e[0])
    return events


def main():
    events = load_events()
    doms_all = Counter(d for _, d, _, _ in events)
    keep = {d for d, n in doms_all.items() if n >= MIN_CLASS_EVENTS}
    lab = lambda d: d if d in keep else "altro"
    labels = [lab(d) for _, d, _, _ in events]
    coverage = sum(n for d, n in doms_all.items() if d in keep) / len(events)
    classes = sorted(set(labels))
    print(f"eventi (dedup): {len(events)}  |  classi tenute: {len(keep)} (+'altro')  "
          f"|  copertura: {coverage:.0%}")

    dim = events[0][2].shape[0]
    E = np.vstack([e[2] for e in events])
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    ts_arr = np.array([e[0] for e in events])
    global_centroid = E.mean(axis=0)
    gc_n = global_centroid / (np.linalg.norm(global_centroid) + 1e-9)

    # ── stati (aggiornati online, predizione PRIMA di vedere t+1) ──────────
    state_defs = list(TAUS.items()) + list(EMAS.items())
    n = len(events)
    warmup = 100

    # running centroids per dominio (per il predittore nearest-centroid, online)
    cent_sum = defaultdict(lambda: np.zeros(dim, dtype=np.float32))
    cent_n = Counter()

    # predizioni: dict nome_predittore -> list (pred_top1, ranked_top3, truth, is_change)
    preds = defaultdict(list)
    drift = []  # cos(S1_24h_raw, centroide) al passo k
    sep_same, sep_diff = [], []
    S = {name: np.zeros(dim, dtype=np.float32) for name, _ in state_defs}
    S_onehot = defaultdict(float)  # S7: attivazione decadente per dominio (τ=24h)
    prev_ts = None
    snapshots = []  # (S_centered, dominio dominante) per separazione
    probe_X, probe_y = [], []  # feature per il probe lineare (S 24h centrato)
    t0 = time.time()

    for t in range(n - 1):
        ts, dom, emb, src = events[t]
        truth = labels[t + 1]
        is_change = labels[t + 1] != labels[t]
        dt = (ts - prev_ts) if prev_ts is not None else 0.0
        prev_ts = ts

        # update stati
        for name, p in state_defs:
            if name in TAUS:
                S[name] = S[name] * np.exp(-dt / p) + emb
            else:
                S[name] = (1 - p) * S[name] + p * emb
        decay24 = np.exp(-dt / TAUS["24h"])
        for d in list(S_onehot):
            S_onehot[d] *= decay24
            if S_onehot[d] < 1e-6:
                del S_onehot[d]
        S_onehot[labels[t]] = S_onehot.get(labels[t], 0.0) + 1.0

        # running centroid del dominio dell'evento corrente
        cent_sum[labels[t]] += emb
        cent_n[labels[t]] += 1

        if t < warmup:
            continue

        # diagnostiche
        s24 = S["24h"]
        drift.append(float(s24 @ gc_n / (np.linalg.norm(s24) + 1e-9)))
        dom_now = max(S_onehot, key=S_onehot.get)
        s24c = s24 / (np.linalg.norm(s24) + 1e-9) - gc_n * 0  # raw per snapshots
        snapshots.append((s24 / (np.linalg.norm(s24) + 1e-9), dom_now))

        probe_X.append((s24 - np.linalg.norm(s24) * gc_n).astype(np.float32))
        probe_y.append(truth)

        # ── predittori vettoriali: nearest running-centroid ────────────────
        cents = {d: cent_sum[d] / cent_n[d] for d in cent_sum if cent_n[d] >= 3}
        if len(cents) >= 3:
            C = np.vstack([c / (np.linalg.norm(c) + 1e-9) for c in cents.values()])
            names = list(cents)
            for sname, centered in (("S1_24h", False), ("S1_24h_cent", True),
                                    ("S1_1h", False), ("S1_7g", False),
                                    ("ema.3", False), ("Sdiff_1h-7g", None)):
                if sname == "Sdiff_1h-7g":
                    v = S["1h"] / (np.linalg.norm(S["1h"]) + 1e-9) \
                        - S["7g"] / (np.linalg.norm(S["7g"]) + 1e-9)
                else:
                    base = sname.replace("S1_", "") if sname.startswith("S1_") else sname
                    base = base.replace("_cent", "")
                    v = S[base].astype(np.float32)
                vn = v / (np.linalg.norm(v) + 1e-9)
                Cc = C - gc_n if centered else C
                vv = vn - gc_n if centered else vn
                scores = Cc @ vv
                order = np.argsort(-scores)
                ranked = [names[i] for i in order[:3]]
                preds[f"V:{sname}"].append((ranked[0], ranked, truth, is_change))

        # S7 one-hot simbolico (gemello del vettore, stessa dinamica)
        ranked7 = sorted(S_onehot, key=S_onehot.get, reverse=True)[:3]
        preds["V:S7_onehot"].append((ranked7[0], ranked7, truth, is_change))

        # ── baseline simboliche ─────────────────────────────────────────────
        recent = labels[max(0, t - 4):t + 1]
        b = {
            "B1_ultimo": [labels[t]],
            "B2_moda3": [d for d, _ in Counter(recent[-3:]).most_common(3)],
            "B3_moda5": [d for d, _ in Counter(recent).most_common(3)],
            "B4_prior": [d for d, _ in Counter(labels[:t + 1]).most_common(3)],
        }
        cur_src = [labels[i] for i in range(t + 1) if events[i][3] in ("user", "teach", "reflection")]
        b["B5_recency_curata"] = [cur_src[-1]] if cur_src else [labels[t]]
        for name, ranked in b.items():
            ranked = (ranked + ["altro"] * 3)[:3]
            preds[name].append((ranked[0], ranked, truth, is_change))

    elapsed = time.time() - t0
    per_update_ms = elapsed / max(1, n - 1 - warmup) * 1000

    # ── separazione: cos tra stati con dominio dominante uguale vs diverso ──
    idx = RNG.choice(len(snapshots), size=(2000, 2))
    for i, j in idx:
        c = float(snapshots[i][0] @ snapshots[j][0])
        (sep_same if snapshots[i][1] == snapshots[j][1] else sep_diff).append(c)

    # ── report ──────────────────────────────────────────────────────────────
    def scores_for(name, only_change):
        rows = [p for p in preds[name] if (p[3] if only_change else True)]
        if not rows:
            return None
        top1 = np.mean([p[0] == p[2] for p in rows])
        top3 = np.mean([p[2] in p[1] for p in rows])
        return top1, top3, len(rows)

    print(f"\nlatenza media per update (tutti gli stati): {per_update_ms:.2f} ms")
    print(f"deriva verso il centroide — cos(S_24h, centroide): "
          f"medio {np.mean(drift):.4f}, p5 {np.percentile(drift,5):.4f}, "
          f"p95 {np.percentile(drift,95):.4f}")
    print(f"separazione stati (dominio dominante uguale vs diverso): "
          f"{np.mean(sep_same):.4f} vs {np.mean(sep_diff):.4f} "
          f"(Δ={np.mean(sep_same)-np.mean(sep_diff):+.4f}, n={len(sep_same)}/{len(sep_diff)})")

    for scope, only_change in (("TUTTE le transizioni", False),
                               ("SOLO cambio-dominio", True)):
        print(f"\n=== {scope} ===")
        print(f"{'predittore':22s} {'top1':>7s} {'top3':>7s} {'n':>6s}")
        for name in sorted(preds):
            s = scores_for(name, only_change)
            if s:
                print(f"{name:22s} {s[0]:7.3f} {s[1]:7.3f} {s[2]:6d}")

    # ── probe lineare (quanta informazione c'è in S, split temporale) ───────
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import f1_score
        X = np.vstack(probe_X)
        y = np.array(probe_y)
        cut = int(len(y) * 0.7)
        clf = LogisticRegression(max_iter=2000, C=1.0)
        clf.fit(X[:cut], y[:cut])
        acc = clf.score(X[cut:], y[cut:])
        f1 = f1_score(y[cut:], clf.predict(X[cut:]), average="macro")
        # baseline B2 sullo stesso segmento di test
        test_rows = preds["B2_moda3"][cut:]
        b2 = np.mean([p[0] == p[2] for p in test_rows]) if test_rows else float("nan")
        print(f"\n=== probe lineare su S_24h centrato (split temporale 70/30) ===")
        print(f"  top1 test: {acc:.3f}  macro-F1: {f1:.3f}  |  B2_moda3 stesso segmento: {b2:.3f}")
    except Exception as e:
        print(f"probe lineare saltato: {e}")

    # ── bootstrap: migliore V vs migliore B su cambio-dominio ───────────────
    def best(prefix, only_change):
        cand = [(name, scores_for(name, only_change)) for name in preds if name.startswith(prefix)]
        cand = [(n_, s) for n_, s in cand if s]
        return max(cand, key=lambda x: x[1][0])

    bv, sv = best("V:", True)
    bb, sb = best("B", True)
    rows_v = [p for p in preds[bv] if p[3]]
    rows_b = [p for p in preds[bb] if p[3]]
    m = min(len(rows_v), len(rows_b))
    diffs = []
    for _ in range(2000):
        idx = RNG.integers(0, m, m)
        dv = np.mean([rows_v[i][0] == rows_v[i][2] for i in idx])
        db = np.mean([rows_b[i][0] == rows_b[i][2] for i in idx])
        diffs.append(dv - db)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    print(f"\n=== verdetto pre-registrato (cambio-dominio) ===")
    print(f"  miglior vettoriale: {bv} top1={sv[0]:.3f}  |  miglior baseline: {bb} top1={sb[0]:.3f}")
    print(f"  Δ(V−B) bootstrap 95%: [{lo*100:+.1f}pp, {hi*100:+.1f}pp]")
    go = sv[0] - sb[0] >= 0.05 and lo > 0
    print(f"  criterio GO (≥+5pp e CI>0): {'RAGGIUNTO' if go else 'NON raggiunto'}")


if __name__ == "__main__":
    main()
