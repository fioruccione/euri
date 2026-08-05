#!/usr/bin/env python3
"""Rimuove in modo strettamente meccanico gli H1 Obsidian riassorbiti in Redis.

Default dry-run. Con ``--apply`` aggiorna atomicamente content ed embedding ma
non cambia fonte, TTL, provenienza o stato epistemico del nodo.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.embedder import Embedder
from core.pulse import COGNITIVE_EVENT, pulse_emit_once
from utils.obsidian_sync import unwrap_generated_memory_content
from utils.redis_client import get_client


REPAIR_VERSION = "obsidian-echo-header-v1"
AUDIT_EVENT_ID = "repair-obsidian-echo-headers-v1"


def _scan(redis) -> list[tuple[str, dict, str]]:
    rows = []
    for key in redis.scan_iter("euri:memory:*"):
        try:
            raw = redis.json().get(key, "$")
            if not raw:
                continue
            doc = raw[0]
            clean, changed = unwrap_generated_memory_content(doc)
            if changed and clean:
                rows.append((str(key), doc, clean))
        except Exception:
            continue
    return rows


def repair(*, apply: bool) -> None:
    redis = get_client()
    rows = _scan(redis)
    by_source = Counter(str(doc.get("source") or "unknown") for _, doc, _ in rows)
    print(f"H1 generati riassorbiti: {len(rows)}")
    for source, count in by_source.most_common():
        print(f"  {source:<18} {count:>5}")
    for key, doc, clean in rows[:20]:
        print(f"  {key[-36:][:8]} | {str(doc.get('source') or '?'):<12} | {clean[:90]}")
    if not apply:
        print("DRY-RUN: nessuna modifica applicata")
        return
    if not rows:
        print("APPLICATO: nulla da riparare")
        return

    embedder = Embedder()
    embedder.load()
    repaired_at = time.time()
    repaired_ids = []
    for key, doc, clean in rows:
        vec = embedder.encode(clean)
        if vec is None:
            raise RuntimeError(f"embedding non disponibile per {key}")
        original = str(doc.get("content") or "")
        pipe = redis.pipeline(transaction=True)
        pipe.json().set(key, "$.content", clean)
        pipe.json().set(key, "$.embedding", vec.tolist())
        pipe.json().set(key, "$.obsidian_echo_repaired_at", repaired_at)
        pipe.json().set(key, "$.obsidian_echo_repair_version", REPAIR_VERSION)
        pipe.json().set(
            key,
            "$.obsidian_echo_removed_heading",
            original.splitlines()[0].strip(),
        )
        pipe.json().set(
            key,
            "$.obsidian_echo_original_sha256",
            hashlib.sha256(original.encode("utf-8")).hexdigest(),
        )
        pipe.execute()
        repaired_ids.append(str(doc.get("id") or key.rsplit(":", 1)[-1]))

    pulse_emit_once(
        redis,
        AUDIT_EVENT_ID,
        "audit",
        "intero",
        "repaired",
        payload={
            "repair_version": REPAIR_VERSION,
            "count": len(repaired_ids),
            "by_source": dict(by_source),
        },
        salience=0.55,
        event_class=COGNITIVE_EVENT,
        producer="repair_obsidian_echo_headers",
        trace_id=f"repair:{AUDIT_EVENT_ID}",
        entity_refs=[
            {"type": "memory", "id": memory_id, "role": "format_repaired"}
            for memory_id in repaired_ids[:50]
        ],
        parent_refs=repaired_ids[:50],
        epistemic_before="content_wrapped_by_generated_markdown_heading",
        epistemic_after="canonical_plain_memory_content",
    )
    print(f"APPLICATO: {len(repaired_ids)} memorie ripulite")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repair(apply=args.apply)
