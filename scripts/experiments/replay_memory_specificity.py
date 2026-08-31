#!/usr/bin/env python3
"""Replay controllato: sostituisce solo la memoria di flusso ambigua.

Legge Redis in sola lettura, crea una copia in memoria della memoria passiva con
identita', posizione e stato espliciti, quindi esegue gli stessi bracci del
replay dell'ordine. Nessuna memoria reale viene scritta e Dream non viene
avviato.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from replay_memory_order import (
    CORE_ORDER,
    IDS,
    QUESTION,
    _doc,
    _render,
    _score,
    _variants,
)
from core.brain import Brain
from utils.redis_client import get_client


SPECIFIC_FLOW = (
    "Configurazione attuale: dall'uscita della bivite il fuso passa prima "
    "attraverso il filtro continuo FIMIC RAS500 e poi alla pompa a ingranaggi "
    "attuale, collocata dopo il RAS500. La pompa a ingranaggi non accetta "
    "materiale contaminato, quindi non e' installata a monte del filtro. "
    "Configurazione proposta: la pompa a pistoni FIMIC FPP20 verrebbe "
    "installata tra l'uscita della bivite e il RAS500; questa e' una modifica "
    "ipotetica e non descrive la posizione della pompa attuale."
)


def run(output: Path) -> dict:
    client = get_client()
    docs = {name: _doc(client, memory_id) for name, memory_id in IDS.items()}
    original_flow = docs["flow_passive"]
    shadow_flow = dict(original_flow)
    shadow_flow["id"] = "shadow-flow-specificity"
    shadow_flow["content"] = SPECIFIC_FLOW
    docs["flow_passive"] = shadow_flow

    records = []
    for label, selected in _variants(docs).items():
        brain = Brain()
        started = time.perf_counter()
        try:
            reply = brain.respond(
                QUESTION,
                context=_render(label + "/specific_flow", selected),
                memory_scope="experiment_memory_specificity_icma2",
                thinking=False,
            )
            error = ""
        except Exception as exc:
            reply = ""
            error = f"{type(exc).__name__}: {exc}"
        records.append({
            "variant": label,
            "memory_ids": [str(d.get("id") or "") for d in selected],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
            "score": _score(reply),
            "reply": reply,
        })

    result = {
        "schema_version": 1,
        "protocol": "memory_specificity_ablation_v1",
        "created_at": time.time(),
        "model_path": "core.brain.Brain.respond",
        "question": QUESTION,
        "shadow_memory_only": True,
        "redis_mutation": False,
        "dream": False,
        "original_flow_memory_id": original_flow.get("id"),
        "shadow_flow_content": SPECIFIC_FLOW,
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
            {"variant": item["variant"], "elapsed_ms": item["elapsed_ms"],
             "score": item["score"], "error": item["error"]}
            for item in result["variants"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
