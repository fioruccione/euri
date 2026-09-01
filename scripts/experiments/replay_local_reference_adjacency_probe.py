#!/usr/bin/env python3
"""Fase 3: collocazione del vincolo referenziale vicino alla query."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.ollama_client import chat_client  # noqa: E402
from replay_local_reference_precedence import (  # noqa: E402
    LOCAL_PRECEDENCE_CONTRACT,
    TARGET,
    _clean,
    _load_request,
    _score,
)
from replay_local_reference_window_probe import STRUCTURED_ANCHOR  # noqa: E402


ARM_ORDER = (
    ("adjacent_generic_contract", "adjacent_structured_anchor", "interpreted_query_anchor"),
    ("interpreted_query_anchor", "adjacent_generic_contract", "adjacent_structured_anchor"),
)


def _messages_for(source: dict, arm: str) -> list[dict]:
    messages = copy.deepcopy(source["body"]["messages"])
    target = messages.pop()
    if arm == "adjacent_generic_contract":
        messages.append({"role": "system", "content": LOCAL_PRECEDENCE_CONTRACT})
    elif arm == "adjacent_structured_anchor":
        messages.append({"role": "system", "content": STRUCTURED_ANCHOR})
    elif arm == "interpreted_query_anchor":
        target["content"] = STRUCTURED_ANCHOR + "\n\n[FORMULAZIONE UTENTE]\n" + target["content"]
    else:
        raise ValueError(f"braccio non valido: {arm}")
    messages.append(target)
    return messages


def run(capture: Path, output: Path) -> dict:
    source = _load_request(capture)
    records = []
    for repetition, arms in enumerate(ARM_ORDER, 1):
        for ordinal, arm in enumerate(arms, 1):
            messages = _messages_for(source, arm)
            started = time.perf_counter()
            response = chat_client.chat(
                model=source["body"].get("model") or config.OLLAMA_MODEL,
                messages=messages,
                options=copy.deepcopy(source["body"].get("options") or {}),
                think=bool(source["body"].get("think", False)),
            )
            answer = _clean(response.message.content or "")
            records.append({
                "repetition": repetition,
                "ordinal": ordinal,
                "arm": arm,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "messages_sha256": hashlib.sha256(
                    json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest(),
                "score": _score(answer),
                "answer": answer,
            })
    summary = {}
    for arm in ARM_ORDER[0]:
        selected = [record for record in records if record["arm"] == arm]
        summary[arm] = {
            "passes": sum(bool(record["score"]["pass"]) for record in selected),
            "total": len(selected),
            "answers": [record["answer"] for record in selected],
        }
    result = {
        "schema_version": 1,
        "protocol": "local_reference_adjacency_probe_v1",
        "created_at": time.time(),
        "phase_2_manual_result": {
            "last_pair_only": "2/2",
            "last_pair_plus_live_rag": "1/2",
            "full_history_structured_anchor": "0/2",
        },
        "capture": str(capture),
        "request_id": source["event"].get("request_id"),
        "target": TARGET,
        "model": source["body"].get("model"),
        "options": source["body"].get("options"),
        "rag_sha256": source["rag_sha256"],
        "redis_read": False,
        "redis_mutation": False,
        "dream": False,
        "arm_order": ARM_ORDER,
        "summary": summary,
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.capture, args.output)
    print(json.dumps({
        "output": str(args.output),
        "summary": {
            arm: {"passes": item["passes"], "total": item["total"]}
            for arm, item in result["summary"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
