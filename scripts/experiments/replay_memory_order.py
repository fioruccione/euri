#!/usr/bin/env python3
"""Ablation offline dell'ordine e della composizione delle memorie.

La sonda legge una fotografia read-only di Redis e interroga il Brain reale con
contesti controllati. Non usa il daemon, non salva memorie e non attiva il Dream.
ICMA2 e' il primo caso di riferimento; il corpus e' separato dalla logica della
sonda per poter aggiungere altri episodi senza cambiare il protocollo.

Le varianti cambiano soltanto il materiale/ordine del contesto. La risposta
completa viene conservata per verifica manuale; gli score automatici sono segnali
diagnostici, non un giudice di correttezza tecnica.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.brain import Brain  # noqa: E402
from utils.redis_client import get_client  # noqa: E402


QUESTION = (
    "Ricostruisci la linea ICMA2 dall'uscita della bivite fino al taglio. "
    "Distingui chiaramente: (1) configurazione attuale, (2) modifica proposta, "
    "(3) effetti attesi, (4) aspetti da verificare. Non inventare; se trovi "
    "versioni discordanti, segnalale."
)

# Memorie selezionate prima del replay. Le versioni 82333/bc8f/8696 sono tenute
# soltanto nel braccio stale_conflict per misurare il comportamento davanti a
# documenti superati; non sono la fonte canonica del braccio principale.
IDS = {
    "topology_teach": "04f03e34-3584-4bd2-8489-a1937728c9e9",
    "operations_teach": "65763d0e-87e4-481d-b7c2-bda82334aaaa",
    "historical_drop": "e58b926a-37ac-4a8d-aec3-92902f26da5d",
    "current_drop": "9c0c83f7-daae-40ae-93a1-afdf0853f88d",
    "canonical_project": "ccc77585-f400-4f1b-b26a-ddce977c212c",
    "pressure_quality": "848e61ac-50dd-4ddd-a636-af6042a3db2b",
    "maintenance": "f08ea52c-2ba1-4219-adb1-87b2977f0658",
    "flow_passive": "7f96e75b-a617-4b3b-a6e8-c75a5f0f016f",
    "stale_las_old": "82333b25-1edb-4dd8-9dbe-37a0ddfef7b7",
    "stale_las_full": "bc8f7583-b331-4eff-a606-c6d3afed7bbf",
    "stale_ras_project": "8696bc28-ec1c-4f68-b99c-24db1052b5d5",
}

CORE_ORDER = (
    "topology_teach",
    "operations_teach",
    "historical_drop",
    "current_drop",
    "flow_passive",
    "canonical_project",
    "pressure_quality",
    "maintenance",
)


def _doc(client, memory_id: str) -> dict[str, Any]:
    raw = client.json().get(f"euri:memory:{memory_id}", "$")
    if not raw:
        raise RuntimeError(f"memoria non trovata: {memory_id}")
    return dict(raw[0])


def _render(label: str, docs: list[dict[str, Any]]) -> str:
    lines = [
        f"MEMORIE DI PROVA — braccio {label}",
        "Le memorie sono materiale fornito dal sistema: rispettane fonte e ordine.",
    ]
    for n, doc in enumerate(docs, 1):
        source = doc.get("source") or "?"
        domain = doc.get("domain") or "generale"
        created = doc.get("created_at") or "?"
        content = str(doc.get("content") or "").strip()
        lines.append(
            f"\n[{n}] fonte={source} dominio={domain} created_at={created}\n{content}"
        )
    return "\n".join(lines)


def _between(text: str, start: str, end: str) -> str:
    low = text.lower()
    a = low.find(start.lower())
    if a < 0:
        return ""
    a += len(start)
    b = low.find(end.lower(), a)
    return text[a:] if b < 0 else text[a:b]


def _score(reply: str) -> dict[str, Any]:
    current = _between(reply, "configurazione attuale", "modifica proposta").lower()
    future = _between(reply, "modifica proposta", "effetti attesi").lower()
    expected = _between(reply, "effetti attesi", "aspetti da verificare").lower()
    if not expected:
        expected = _between(reply, "effetti attesi", "elementi critici").lower()

    def before(block: str, first: str, second: str) -> bool:
        a, b = block.find(first), block.find(second)
        return a >= 0 and b >= 0 and a < b

    return {
        "current_ras_before_pump": before(current, "ras", "pomp"),
        "current_wrong_pump_before_ras": before(current, "pomp", "ras"),
        "future_fpp_before_ras": before(future, "fpp", "ras"),
        "mentions_1300": "1300" in reply,
        "mentions_1500": "1500" in reply,
        "mentions_quality": any(w in reply.lower() for w in ("qualità", "qualita", "filtr")),
        "mentions_maintenance": any(w in reply.lower() for w in ("manutenz", "usura", "costi")),
        "mentions_verification": any(w in reply.lower() for w in ("verific", "test", "ipotesi")),
        "mentions_las": bool(re.search(r"\blas\s*500\b", reply, re.IGNORECASE)),
        "mentions_conflict": any(w in reply.lower() for w in ("conflitto", "discord", "precedente", "superat")),
        "current_chars": len(current),
        "future_chars": len(future),
        "expected_chars": len(expected),
    }


def _variants(docs: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ordered = [docs[name] for name in CORE_ORDER]
    shuffled = ordered[:]
    random.Random(20260831).shuffle(shuffled)
    return {
        "ordered_core": ordered,
        "authority_first": [
            docs[n] for n in (
                "canonical_project", "current_drop", "topology_teach",
                "operations_teach", "flow_passive", "pressure_quality", "maintenance",
            )
        ],
        "source_layered": [
            docs[n] for n in (
                "canonical_project", "current_drop", "topology_teach",
                "operations_teach", "pressure_quality", "maintenance", "flow_passive",
            )
        ],
        "without_flow_ambiguity": [
            docs[n] for n in CORE_ORDER if n != "flow_passive"
        ],
        "user_teach_without_flow": [
            docs[n] for n in (
                "canonical_project", "current_drop", "topology_teach",
                "operations_teach", "pressure_quality", "maintenance",
            )
        ],
        "reversed_core": list(reversed(ordered)),
        "shuffled_core": shuffled,
        "semantic_only": [docs[n] for n in ("flow_passive", "canonical_project", "pressure_quality", "maintenance")],
        "teach_and_history": [docs[n] for n in ("topology_teach", "operations_teach", "historical_drop", "current_drop")],
        "stale_conflict": ordered + [docs[n] for n in ("stale_las_old", "stale_las_full", "stale_ras_project")],
    }


def run(output: Path) -> dict[str, Any]:
    client = get_client()
    docs = {name: _doc(client, memory_id) for name, memory_id in IDS.items()}
    variants = _variants(docs)
    records: list[dict[str, Any]] = []
    for label, selected in variants.items():
        brain = Brain()
        context = _render(label, selected)
        started = time.perf_counter()
        try:
            reply = brain.respond(
                QUESTION,
                context=context,
                memory_scope="experiment_memory_order_icma2",
                thinking=False,
            )
            error = ""
        except Exception as exc:  # conserviamo il fallimento come dato di replay
            reply = ""
            error = f"{type(exc).__name__}: {exc}"
        records.append({
            "variant": label,
            "memory_ids": [str(d.get("id") or "") for d in selected],
            "memory_sources": [str(d.get("source") or "?") for d in selected],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
            "score": _score(reply),
            "reply": reply,
        })

    result = {
        "schema_version": 1,
        "protocol": "memory_order_ablation_v1",
        "created_at": time.time(),
        "model_path": "core.brain.Brain.respond",
        "question": QUESTION,
        "redis_mutation": False,
        "dream": False,
        "variants": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({
        "output": str(args.output),
        "variants": [
            {"variant": item["variant"], "elapsed_ms": item["elapsed_ms"], "score": item["score"], "error": item["error"]}
            for item in result["variants"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
