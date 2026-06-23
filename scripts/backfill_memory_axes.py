#!/usr/bin/env python3
"""
Backfill agnostico degli assi strutturali delle memorie.

Annota i nodi euri:memory:* con `memory_axes`, senza cancellare o riscrivere il
contenuto. In modalita' --mark-acephalous alza anche requires_verification=True
per i frammenti con soggetto acefalo, cosi' non entrano in consolidamenti ciechi.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_axes import analyze_memory_axes
from utils.redis_client import get_client


def _memory_docs(r, *, source: str | None = None, limit: int | None = None):
    seen = 0
    for key in r.scan_iter("euri:memory:*"):
        raw = r.json().get(key, "$")
        if not raw:
            continue
        doc = raw[0]
        if source and doc.get("source") != source:
            continue
        yield key, doc
        seen += 1
        if limit and seen >= limit:
            break


def _backup(docs: list[dict[str, Any]]) -> Path:
    out_dir = Path("backups")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"memory_axes_backfill_{stamp}.json"
    path.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="scrive $.memory_axes su Redis")
    ap.add_argument("--mark-acephalous", action="store_true",
                    help="con --apply, alza requires_verification=True sui soggetti acefali")
    ap.add_argument("--source", help="limita a una source specifica")
    ap.add_argument("--limit", type=int, help="massimo numero di memorie da esaminare")
    ap.add_argument("--max-samples", type=int, default=12)
    ap.add_argument("--no-backup", action="store_true", help="non esporta backup prima di --apply")
    args = ap.parse_args()

    r = get_client()
    docs = list(_memory_docs(r, source=args.source, limit=args.limit))

    status_counts: Counter[str] = Counter()
    fact_counts: Counter[str] = Counter()
    temporal_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    audit_counts: Counter[str] = Counter()
    samples: dict[str, list[tuple[str, str]]] = {"acephalous": [], "unknown": []}

    analyzed: list[tuple[Any, dict, dict]] = []
    for key, doc in docs:
        axes = analyze_memory_axes(
            doc.get("content", ""),
            source=doc.get("source"),
            created_at=doc.get("created_at"),
        )
        analyzed.append((key, doc, axes))
        status = axes.get("subject_status", "unknown")
        status_counts[status] += 1
        source_counts[doc.get("source") or "?"] += 1
        fact_counts.update(axes.get("fact_types", []))
        temporal_counts.update(axes.get("temporal_markers", []))
        audit_counts.update(axes.get("audit_reasons", []))

        if status in samples and len(samples[status]) < args.max_samples:
            samples[status].append((doc.get("id", ""), (doc.get("content") or "")[:180]))

    backup_path = None
    if args.apply and not args.no_backup:
        backup_payload = [{"key": key, "doc": doc} for key, doc, _axes in analyzed]
        backup_path = _backup(backup_payload)

    changed = 0
    marked = 0
    if args.apply:
        for key, doc, axes in analyzed:
            r.json().set(key, "$.memory_axes", axes)
            changed += 1
            if args.mark_acephalous and axes.get("subject_status") == "acephalous":
                if not doc.get("requires_verification"):
                    r.json().set(key, "$.requires_verification", True)
                marked += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"memory_axes backfill — {mode}")
    print(f"memorie esaminate: {len(analyzed)}")
    if backup_path:
        print(f"backup: {backup_path}")
    if args.apply:
        print(f"memory_axes scritti: {changed}")
        if args.mark_acephalous:
            print(f"acefale marcate requires_verification: {marked}")

    print("\nsubject_status:")
    for k, v in status_counts.most_common():
        print(f"  {k}: {v}")

    print("\nfact_types:")
    for k, v in fact_counts.most_common():
        print(f"  {k}: {v}")

    print("\ntemporal_markers:")
    for k, v in temporal_counts.most_common():
        print(f"  {k}: {v}")

    print("\naudit_reasons:")
    for k, v in audit_counts.most_common():
        print(f"  {k}: {v}")

    print("\nsources:")
    for k, v in source_counts.most_common():
        print(f"  {k}: {v}")

    for status, rows in samples.items():
        if not rows:
            continue
        print(f"\nEsempi {status}:")
        for mid, content in rows:
            print(f"  {mid[:8]}  {content}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
