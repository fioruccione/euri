#!/usr/bin/env python3
"""
Diagnostico READ-ONLY della cascata di reflection.

Misura volume, ridondanza e auto-amplificazione delle memorie source=reflection.
Non scrive nulla in Redis: usa solo scan_iter, JSON.GET e TTL. Non tocca recalled_count,
TTL, superseded_by, embedding o contenuti. Le similarita' usano gli embedding gia' salvati.

Uso:
  venv/bin/python diag_reflection_cascade.py
  venv/bin/python diag_reflection_cascade.py --md
  venv/bin/python diag_reflection_cascade.py --days 14 --threshold 0.90
"""
from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import redis as redis_lib

import config


LIVED_SOURCES = {"user", "passive", "episode", "teach", "loop2e"}
DEFAULT_DAYS = 14
DEFAULT_THRESHOLD = 0.90
DIRECT_DEDUP_MAX_DIST = 0.10


def _ts_to_dt(ts) -> datetime | None:
    try:
        if ts is None:
            return None
        return datetime.fromtimestamp(float(ts))
    except Exception:
        return None


def _day(ts) -> str:
    dt = _ts_to_dt(ts)
    return dt.strftime("%Y-%m-%d") if dt else "unknown"


def _fmt_pct(part: int | float, total: int | float) -> str:
    if not total:
        return "0.0%"
    return f"{(float(part) / float(total) * 100):.1f}%"


def _quantiles(values: list[int | float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "min": 0, "p50": 0, "p90": 0, "max": 0, "avg": 0}
    arr = np.array(values, dtype=float)
    return {
        "n": int(arr.size),
        "min": float(np.min(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
        "avg": float(np.mean(arr)),
    }


def _subtype(doc: dict) -> str:
    tags = set(doc.get("tags") or [])
    content = (doc.get("content") or "").strip().lower()
    if "loop2h" in tags or "self_observation" in tags:
        return "2h self-observation"
    if content.startswith("[confronto]"):
        return "2f confronto"
    return "2a reflection"


def _ttl_bucket(ttl: int) -> str:
    if ttl == -2:
        return "missing key"
    if ttl == -1:
        return "permanent/no TTL"
    days = ttl / 86400
    if days < 1:
        return "<1 giorno"
    if days < 7:
        return "1-7 giorni"
    if days < 30:
        return "7-30 giorni"
    if days < 90:
        return "30-90 giorni"
    return ">90 giorni"


def _embedding(doc: dict) -> np.ndarray | None:
    emb = doc.get("embedding")
    if not emb:
        return None
    try:
        arr = np.asarray(emb, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if not math.isfinite(norm) or norm <= 0:
            return None
        return arr / norm
    except Exception:
        return None


def _components_for_domain(items: list[tuple[str, np.ndarray]], threshold: float) -> list[list[str]]:
    """
    Componenti connesse sul grafo cosine >= threshold.
    items: [(memory_id, normalized_embedding)]
    """
    n = len(items)
    if n < 2:
        return []

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # O(n^2) per dominio. Sufficiente per diagnostico locale; nessun re-encode.
    mat = np.stack([emb for _mid, emb in items])
    sim = mat @ mat.T
    for i in range(n):
        for j in range(i + 1, n):
            if float(sim[i, j]) >= threshold:
                union(i, j)

    comps = defaultdict(list)
    for idx, (mid, _emb) in enumerate(items):
        comps[find(idx)].append(mid)
    return [members for members in comps.values() if len(members) >= 2]


def _nearest_neighbor_distances(items: list[tuple[str, np.ndarray]]) -> list[float]:
    """
    Distanza al vicino piu' prossimo, NON transitiva.
    items: [(memory_id, normalized_embedding)] dentro lo stesso dominio.
    """
    n = len(items)
    if n < 2:
        return []
    mat = np.stack([emb for _mid, emb in items])
    sim = mat @ mat.T
    np.fill_diagonal(sim, -1.0)
    nearest_sim = np.max(sim, axis=1)
    return [float(1.0 - s) for s in nearest_sim]


def _nn_bucket(dist: float) -> str:
    if dist <= 0.05:
        return "<=0.05"
    if dist <= 0.10:
        return "<=0.10"
    if dist <= 0.15:
        return "<=0.15"
    if dist <= 0.20:
        return "<=0.20"
    return ">0.20"


def _table(rows: list[list[str]], headers: list[str]) -> str:
    all_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    out = []
    out.append(" | ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))))
    out.append("-+-".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        out.append(" | ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit read-only cascata reflection")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="giorni per tasso generazione")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="soglia cosine near-duplicate")
    ap.add_argument("--md", action="store_true", help="scrive anche reflection_cascade_audit.md")
    args = ap.parse_args()

    r = redis_lib.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )
    r.ping()

    docs = []
    for key in r.scan_iter("euri:memory:*"):
        data = r.json().get(key, "$")
        if not data:
            continue
        doc = data[0]
        doc["_redis_key"] = key
        doc["_ttl"] = r.ttl(key)
        docs.append(doc)

    total = len(docs)
    by_source = Counter(doc.get("source") or "?" for doc in docs)
    reflections = [doc for doc in docs if doc.get("source") == "reflection"]
    lived = [doc for doc in docs if doc.get("source") in LIVED_SOURCES]

    lines: list[str] = []
    lines.append("# Reflection Cascade Audit")
    lines.append("")
    lines.append(f"Generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Near-duplicate threshold: cosine >= {args.threshold:.2f}")
    lines.append("")

    # 1. Volume
    lines.append("## 1. Volume")
    rows = [[src, str(n), _fmt_pct(n, total)] for src, n in by_source.most_common()]
    lines.append(_table(rows, ["source", "count", "% totale"]))
    lines.append(f"\nReflection: {len(reflections)} / {total} = {_fmt_pct(len(reflections), total)}")
    lines.append("")

    # 2. Sottotipi
    lines.append("## 2. Sottotipi reflection")
    subtype_counts = Counter(_subtype(doc) for doc in reflections)
    rows = [[sub, str(n), _fmt_pct(n, len(reflections))] for sub, n in subtype_counts.most_common()]
    lines.append(_table(rows, ["sottotipo", "count", "% reflection"]))
    lines.append("")

    # 3. Tasso generazione
    lines.append(f"## 3. Tasso di generazione reflection ultimi {args.days} giorni")
    cutoff = datetime.now() - timedelta(days=args.days)
    by_day = Counter()
    for doc in reflections:
        dt = _ts_to_dt(doc.get("created_at"))
        if dt and dt >= cutoff:
            by_day[_day(doc.get("created_at"))] += 1
    day_rows = []
    for i in range(args.days - 1, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_rows.append([d, str(by_day.get(d, 0))])
    lines.append(_table(day_rows, ["giorno", "reflection create"]))
    lines.append("")

    # 4. Ridondanza transitiva
    lines.append("## 4. Cluster transitivi (union-find, cosine >= soglia)")
    by_domain = defaultdict(list)
    missing_emb = 0
    for doc in reflections:
        emb = _embedding(doc)
        if emb is None:
            missing_emb += 1
            continue
        by_domain[doc.get("domain") or "generale"].append((doc.get("id") or doc["_redis_key"], emb))

    all_clusters: list[tuple[str, list[str]]] = []
    domain_rows = []
    absorbed = set()
    for domain, items in sorted(by_domain.items(), key=lambda kv: len(kv[1]), reverse=True):
        comps = _components_for_domain(items, args.threshold)
        for comp in comps:
            all_clusters.append((domain, comp))
            absorbed.update(comp)
        sizes = [len(c) for c in comps]
        domain_rows.append([
            domain,
            str(len(items)),
            str(len(comps)),
            f"{(sum(sizes) / len(sizes)):.2f}" if sizes else "0.00",
            str(max(sizes) if sizes else 0),
            _fmt_pct(len({mid for c in comps for mid in c}), len(items)),
        ])

    cluster_sizes = [len(c) for _d, c in all_clusters]
    q = _quantiles(cluster_sizes)
    lines.append(f"Reflection senza embedding: {missing_emb}")
    lines.append(
        f"Cluster totali: {len(all_clusters)} | dimensione media: {q['avg']:.2f} | "
        f"max: {q['max']:.0f} | reflection in cluster>=2: {len(absorbed)} / "
        f"{len(reflections) - missing_emb} = {_fmt_pct(len(absorbed), len(reflections) - missing_emb)}"
    )
    lines.append("")
    lines.append(_table(domain_rows[:20], ["domain", "refl con emb", "cluster", "avg size", "max", "% assorbite"]))
    lines.append("")

    # 4b. Duplicati diretti / nearest-neighbor
    lines.append("## 4b. Duplicati diretti (nearest-neighbor, stesso dominio, no superseded/no confronto)")
    direct_by_domain = defaultdict(list)
    direct_missing_emb = 0
    direct_skipped = 0
    for doc in reflections:
        content = (doc.get("content") or "").strip().lower()
        if doc.get("superseded_by") or content.startswith("[confronto]"):
            direct_skipped += 1
            continue
        emb = _embedding(doc)
        if emb is None:
            direct_missing_emb += 1
            continue
        direct_by_domain[doc.get("domain") or "generale"].append((doc.get("id") or doc["_redis_key"], emb))

    all_nn_distances = []
    direct_domain_rows = []
    direct_dedupable = 0
    direct_total = 0
    for domain, items in sorted(direct_by_domain.items(), key=lambda kv: len(kv[1]), reverse=True):
        nn = _nearest_neighbor_distances(items)
        direct_total += len(nn)
        dedupable = sum(1 for d in nn if d <= DIRECT_DEDUP_MAX_DIST)
        direct_dedupable += dedupable
        all_nn_distances.extend(nn)
        qnn = _quantiles(nn)
        direct_domain_rows.append([
            domain,
            str(len(nn)),
            f"{qnn['p50']:.3f}",
            f"{qnn['p90']:.3f}",
            str(dedupable),
            _fmt_pct(dedupable, len(nn)),
        ])

    bucket_order = ["<=0.05", "<=0.10", "<=0.15", "<=0.20", ">0.20"]
    bucket_counts = Counter(_nn_bucket(d) for d in all_nn_distances)
    lines.append(f"Reflection escluse da metrica diretta (superseded o [confronto]): {direct_skipped}")
    lines.append(f"Reflection senza embedding nella metrica diretta: {direct_missing_emb}")
    rows = [[bucket, str(bucket_counts.get(bucket, 0)), _fmt_pct(bucket_counts.get(bucket, 0), len(all_nn_distances))]
            for bucket in bucket_order]
    lines.append(_table(rows, ["NN-distance", "count", "% dirette"]))
    lines.append(
        f"\nCon vicino diretto <= {DIRECT_DEDUP_MAX_DIST:.2f} (dedup-abili): "
        f"{direct_dedupable} / {direct_total} = {_fmt_pct(direct_dedupable, direct_total)}"
    )
    lines.append("")
    lines.append(_table(
        direct_domain_rows[:5],
        ["domain", "n NN", "mediana NN", "p90 NN", "dedup-abili", "% dedup-abili"],
    ))
    lines.append("")

    # 5. Auto-amplificazione
    lines.append("## 5. Auto-amplificazione recalled_count")
    refl_recalls = [int(doc.get("recalled_count") or 0) for doc in reflections]
    lived_recalls = [int(doc.get("recalled_count") or 0) for doc in lived]
    rows = []
    for label, vals in [("reflection", refl_recalls), ("vissute", lived_recalls)]:
        qq = _quantiles(vals)
        rows.append([
            label,
            str(qq["n"]),
            f"{qq['avg']:.2f}",
            f"{qq['p50']:.0f}",
            f"{qq['p90']:.0f}",
            f"{qq['max']:.0f}",
            str(sum(1 for v in vals if v > 0)),
            _fmt_pct(sum(1 for v in vals if v > 0), len(vals)),
        ])
    lines.append(_table(rows, ["gruppo", "n", "avg", "p50", "p90", "max", "toccate", "% toccate"]))
    lines.append("")

    # 6. TTL/lifecycle
    lines.append("## 6. TTL / lifecycle reflection")
    ttl_counts = Counter(_ttl_bucket(int(doc.get("_ttl"))) for doc in reflections)
    rows = [[bucket, str(n), _fmt_pct(n, len(reflections))] for bucket, n in ttl_counts.most_common()]
    lines.append(_table(rows, ["TTL bucket", "count", "% reflection"]))
    exp_values = []
    for doc in reflections:
        exp = _ts_to_dt(doc.get("expires_at"))
        if exp:
            exp_values.append((exp - datetime.now()).total_seconds() / 86400)
    exp_q = _quantiles(exp_values)
    lines.append(
        f"\nScadenze JSON expires_at presenti: {len(exp_values)} | "
        f"giorni residui avg={exp_q['avg']:.1f}, p50={exp_q['p50']:.1f}, p90={exp_q['p90']:.1f}, max={exp_q['max']:.1f}"
    )
    lines.append("")

    # 7. Concentrazione per dominio
    lines.append("## 7. Concentrazione per dominio")
    domain_counts = Counter(doc.get("domain") or "generale" for doc in reflections)
    rows = [[dom, str(n), _fmt_pct(n, len(reflections))] for dom, n in domain_counts.most_common(20)]
    lines.append(_table(rows, ["domain", "reflection", "% reflection"]))
    lines.append("")

    # Riepilogo neutro
    lines.append("## Riepilogo numerico neutro")
    lines.append(
        f"- Reflection totali: {len(reflections)} ({_fmt_pct(len(reflections), total)} del totale memorie)."
    )
    lines.append(
        f"- Cluster transitivi: {len(all_clusters)} cluster; {len(absorbed)} reflection assorbite "
        f"({_fmt_pct(len(absorbed), len(reflections) - missing_emb)} delle reflection con embedding)."
    )
    lines.append(
        f"- Duplicati diretti dedup-abili (NN distance <= {DIRECT_DEDUP_MAX_DIST:.2f}): "
        f"{direct_dedupable} / {direct_total} = {_fmt_pct(direct_dedupable, direct_total)}."
    )
    refl_q = _quantiles(refl_recalls)
    lived_q = _quantiles(lived_recalls)
    lines.append(
        f"- recalled_count medio: reflection {refl_q['avg']:.2f}, vissute {lived_q['avg']:.2f}."
    )
    if domain_counts:
        top_dom, top_n = domain_counts.most_common(1)[0]
        lines.append(f"- Dominio reflection principale: {top_dom} ({top_n}).")
    lines.append("")

    report = "\n".join(lines)
    print(report)

    if args.md:
        out = Path("reflection_cascade_audit.md")
        out.write_text(report + "\n", encoding="utf-8")
        print(f"\nReport scritto su {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
