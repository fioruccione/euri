#!/usr/bin/env python3
"""
Baseline N3 — piano-awareness del soft-delete (Loop 2f) e assorbimento-blob.

READ-ONLY. Enumera le memorie soft-deletate (`superseded_by` valorizzato), risale al
vincitore e misura la coppia (perdente → vincitore) con segnali OGGETTIVI dagli embedding
e dai metadati già salvati — niente LLM (la v1 con probe LLM era uno strumento sbilanciato:
chiamava "fusione" qualunque vincitore più lungo, 78% falso). Segmenta per MECCANISMO,
perché meccanismi diversi non vanno mescolati:

  refl-dedup     — perdente e vincitore entrambi source=reflection: è il reflection-dedup
                   latest-wins (commit 4bcb6cc), quasi-doppioni voluti. FUORI scopo N3.
  loop2f-contrad — entrambi requires_verification=True: la via contraddizione del Loop 2f.
                   BERSAGLIO N3.
  altro          — supersede cross-source (assorbimento di un atomo dentro un blob, ecc.).
  orfana         — il vincitore non esiste più: la memoria è stata cancellata in favore di
                   qualcosa di sparito → integrità, problema a parte.

Il caso canonico (atomo comportamentale `ae8df33a` → blob `51b9eca6`) ha cosine 0.972
(dist 0.028): la fusione-di-piano indebita È un quasi-doppione → la distanza cosine NON la
separa da un dedup legittimo. Il segnale che la distingue è l'ASSORBIMENTO: vincitore molto
più lungo e ad alta similarità (lo ingloba), perdente atomo corto e a piano unico. Per questo
il baseline riporta, dentro ogni segmento, la quota "vincitore ≥ FATTORE× più lungo".

Uso:
  venv/bin/python diag_plane_fusion.py
  venv/bin/python diag_plane_fusion.py --absorb-factor 1.3 --md
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import redis as redis_lib

import config


def _r() -> redis_lib.Redis:
    return redis_lib.Redis(
        host=config.REDIS_HOST, port=config.REDIS_PORT,
        db=config.REDIS_DB, decode_responses=True,
    )


def _get(r, key, path, default=None):
    try:
        v = r.json().get(key, path)
    except Exception:
        return default
    if isinstance(v, list):
        return v[0] if v else default
    return v if v is not None else default


def _cos(a, b) -> float | None:
    if not a or not b:
        return None
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    n = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / n) if n else None


def _short(s: str, n: int = 150) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)


def _is_real_id(wid: str) -> bool:
    """Un vincitore reale ha forma UUID. I sentinella (es. 'phantom_correction_n1',
    la lapide della bonifica N1) NON sono memorie: sono soft-delete intenzionali."""
    return bool(_UUID_RE.match(str(wid)))


def _segment(ls, ws, lrv, wrv) -> str:
    if ls == "reflection" and ws == "reflection":
        return "refl-dedup"
    if lrv and wrv:
        return "loop2f-contrad"
    return "altro"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--absorb-factor", type=float, default=1.3,
                    help="vincitore ≥ FATTORE× più lungo del perdente = segnale assorbimento")
    ap.add_argument("--near-dist", type=float, default=0.10,
                    help="dist cosine ≤ soglia = quasi-doppione")
    ap.add_argument("--md", action="store_true", help="scrive plane_fusion_audit.md")
    args = ap.parse_args()

    r = _r()
    rows = []
    total = 0
    for key in r.scan_iter("euri:memory:*"):
        total += 1
        sup = _get(r, key, "$.superseded_by")
        if not sup:
            continue
        wid = sup[0] if isinstance(sup, list) else sup
        if isinstance(wid, list):
            wid = wid[0] if wid else None
        if not wid:
            continue
        loser_id = key.replace("euri:memory:", "")
        wkey = f"euri:memory:{wid}"
        wc = _get(r, wkey, "$.content")
        lc = _get(r, key, "$.content", "") or ""
        if wc is None:
            # Vincitore assente: distingui la lapide intenzionale (sentinella non-UUID,
            # es. bonifica N1) dall'orfana vera (UUID reale ma chiave sparita).
            seg = "orfana-vera" if _is_real_id(wid) else "tombstone-intenz"
            rows.append({"seg": seg, "loser_id": loser_id[:8], "winner_id": str(wid)[:8],
                         "loser_src": _get(r, key, "$.source", "?"),
                         "loser_recalled": _get(r, key, "$.recalled_count", 0),
                         "loser_content": lc, "dist": None})
            continue
        le = _get(r, key, "$.embedding")
        we = _get(r, wkey, "$.embedding")
        cos = _cos(le, we)
        ls = _get(r, key, "$.source", "?")
        ws = _get(r, wkey, "$.source", "?")
        lrv = bool(_get(r, key, "$.requires_verification", False))
        wrv = bool(_get(r, wkey, "$.requires_verification", False))
        rows.append({
            "seg": _segment(ls, ws, lrv, wrv),
            "loser_id": loser_id[:8], "winner_id": str(wid)[:8],
            "loser_src": ls, "winner_src": ws, "domain": _get(r, key, "$.domain", "?"),
            "loser_recalled": _get(r, key, "$.recalled_count", 0),
            "dist": (1 - cos) if cos is not None else None,
            "llen": len(lc), "wlen": len(wc),
            "absorbed": len(wc) >= len(lc) * args.absorb_factor if lc else False,
            "loser_content": lc, "winner_content": wc,
        })

    segs = ("refl-dedup", "loop2f-contrad", "altro", "tombstone-intenz", "orfana-vera")
    by_seg = defaultdict(list)
    for x in rows:
        by_seg[x["seg"]].append(x)

    n = len(rows)
    print("=" * 68)
    print(f"Memorie totali: {total}   |   soft-deletate: {n}")
    print(f"Segmenti: " + ", ".join(f"{s}={len(by_seg[s])}" for s in segs))
    print("=" * 68)
    target_absorbed = 0
    for s in segs:
        sub = by_seg[s]
        if not sub:
            continue
        dvals = sorted(x["dist"] for x in sub if x["dist"] is not None)
        absorbed = [x for x in sub if x.get("absorbed")]
        print(f"\n[{s}] n={len(sub)}")
        if dvals:
            med = dvals[len(dvals) // 2]
            near = sum(1 for d in dvals if d <= args.near_dist)
            print(f"  dist: med={med:.3f} min={dvals[0]:.3f} max={dvals[-1]:.3f} | "
                  f"quasi-dup ≤{args.near_dist}: {near}/{len(dvals)}")
        if s not in ("orfana-vera", "tombstone-intenz"):
            print(f"  assorbimento (vincitore ≥{args.absorb_factor}× più lungo): "
                  f"{len(absorbed)}/{len(sub)}")
        if s in ("loop2f-contrad", "altro"):
            target_absorbed += len(absorbed)

    n_target = len(by_seg["loop2f-contrad"]) + len(by_seg["altro"])
    print("\n" + "-" * 68)
    print(f"BERSAGLIO N3 (loop2f-contrad + altro): {n_target} coppie")
    print(f"  di cui con segnale assorbimento-blob: {target_absorbed}  ← baseline da abbassare")
    print(f"FUORI SCOPO (refl-dedup, voluto): {len(by_seg['refl-dedup'])}")
    print(f"INTENZIONALE (lapidi sentinella, es. bonifica N1): {len(by_seg['tombstone-intenz'])}")
    print(f"INTEGRITA' (orfane vere, UUID sparito): {len(by_seg['orfana-vera'])}")
    print("-" * 68)

    if args.md:
        out = [
            f"# Baseline N3 — fusione/assorbimento nel soft-delete ({datetime.now():%Y-%m-%d %H:%M})",
            "",
            f"Memorie totali **{total}**, soft-deletate **{n}**. "
            f"Segnali oggettivi (cosine embedding + lunghezza), nessun LLM.",
            "",
            f"| segmento | n | dist med | quasi-dup ≤{args.near_dist} | assorbimento ≥{args.absorb_factor}× |",
            "|---|---|---|---|---|",
        ]
        for s in segs:
            sub = by_seg[s]
            if not sub:
                continue
            dvals = sorted(x["dist"] for x in sub if x["dist"] is not None)
            med = f"{dvals[len(dvals)//2]:.3f}" if dvals else "—"
            near = sum(1 for d in dvals if d <= args.near_dist)
            absorbed = sum(1 for x in sub if x.get("absorbed"))
            skip_absorb = s in ("orfana-vera", "tombstone-intenz")
            out.append(f"| {s} | {len(sub)} | {med} | {near}/{len(dvals) if dvals else 0} | "
                       f"{absorbed}/{0 if skip_absorb else len(sub)} |")
        out += ["",
                f"**Bersaglio N3** = loop2f-contrad + altro = {n_target} coppie, "
                f"assorbimento-blob su **{target_absorbed}**. "
                f"refl-dedup ({len(by_seg['refl-dedup'])}) fuori scopo; lapidi intenzionali "
                f"({len(by_seg['tombstone-intenz'])}, N1); orfane vere "
                f"({len(by_seg['orfana-vera'])}) = integrità.",
                "",
                "## Coppie bersaglio con assorbimento (atomo perso dentro un blob più lungo)",
                ""]
        for x in by_seg["loop2f-contrad"] + by_seg["altro"]:
            if not x.get("absorbed"):
                continue
            out.append(
                f"- `{x['loser_id']}`→`{x['winner_id']}` "
                f"({x['loser_src']}→{x['winner_src']}, dist={x['dist']:.3f}, "
                f"{x['llen']}→{x['wlen']} char, recalled={x['loser_recalled']})\n"
                f"  - perso : {_short(x['loser_content'])}\n"
                f"  - tenuto: {_short(x['winner_content'])}"
            )
        Path("plane_fusion_audit.md").write_text("\n".join(out), encoding="utf-8")
        print("→ scritto plane_fusion_audit.md")


if __name__ == "__main__":
    main()
