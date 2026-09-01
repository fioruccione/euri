#!/usr/bin/env python3
"""Fase 4: collisione few-shot nello storico e riscrittura naturale."""
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
from core.ollama_client import RealtimeClient  # noqa: E402
from replay_local_reference_precedence import (  # noqa: E402
    TARGET,
    _clean,
    _load_request,
    _score,
)


INTERPRETED_TARGET = (
    "Euri, restiamo sulla configurazione attuale che hai descritto nella "
    "risposta immediatamente precedente. In questa configurazione la pompa è "
    "prima o dopo il filtro RAS 500?"
)
ARM_ORDER = (
    ("history_without_near_duplicate", "full_live_natural_rewrite", "history_only_natural_rewrite"),
    ("history_only_natural_rewrite", "history_without_near_duplicate", "full_live_natural_rewrite"),
)
REPLAY_CLIENT = RealtimeClient(host=config.CHAT_OLLAMA_HOST, timeout=600.0)


def _without_rag(source: dict, messages: list[dict]) -> list[dict]:
    index = source["message_index"]
    content = str(messages[index]["content"])
    start = source["offset"]
    end = start + source["length"]
    messages[index]["content"] = content[:start] + content[end:]
    return messages


def _messages_for(source: dict, arm: str) -> list[dict]:
    messages = copy.deepcopy(source["body"]["messages"])
    if arm == "history_without_near_duplicate":
        messages = _without_rag(source, messages)
        messages = [
            message for message in messages
            if not (
                str(message.get("content") or "").startswith(
                    "Restiamo sulla configurazione che ho appena descritto"
                )
                or str(message.get("content") or "").startswith(
                    "Nella nuova configurazione che hai appena descritto"
                )
            )
        ]
        return messages
    if arm in {"full_live_natural_rewrite", "history_only_natural_rewrite"}:
        if arm == "history_only_natural_rewrite":
            messages = _without_rag(source, messages)
        messages[-1]["content"] = INTERPRETED_TARGET
        return messages
    raise ValueError(f"braccio non valido: {arm}")


def run(capture: Path, output: Path) -> dict:
    source = _load_request(capture)
    records = []
    for repetition, arms in enumerate(ARM_ORDER, 1):
        for ordinal, arm in enumerate(arms, 1):
            messages = _messages_for(source, arm)
            started = time.perf_counter()
            response = REPLAY_CLIENT.chat(
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
        "protocol": "local_reference_pattern_probe_v1",
        "created_at": time.time(),
        "capture": str(capture),
        "request_id": source["event"].get("request_id"),
        "raw_target": TARGET,
        "interpreted_target": INTERPRETED_TARGET,
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
