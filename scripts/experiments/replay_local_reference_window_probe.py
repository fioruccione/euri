#!/usr/bin/env python3
"""Fase 2: finestra locale contro ancoraggio strutturato del referente."""
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
    TARGET,
    _clean,
    _load_request,
    _score,
)


ARM_ORDER = (
    ("last_pair_only", "last_pair_plus_live_rag", "full_history_structured_anchor"),
    ("full_history_structured_anchor", "last_pair_only", "last_pair_plus_live_rag"),
)
STRUCTURED_ANCHOR = """[PROIEZIONE DEL REFERENTE LOCALE — NON FONTE FATTUALE]
expression: "quella configurazione che hai appena descritto"
resolution: assistant_turn_immediately_before_current_user
resolved_state: configurazione_attuale
selection_basis: adjacency_in_conversation_history
rule: usa questa proiezione solo per scegliere lo stato; ricava la topologia
dalle evidenze disponibili e non dalla proiezione."""


def _static_prefix(source: dict, *, include_rag: bool) -> list[dict]:
    original = source["body"]["messages"]
    # I primi due messaggi sono identita'/policy e contesto operativo. Il terzo
    # contiene data + RAG + continuity: nella sonda locale conserviamo soltanto
    # il prefisso fino al RAG e, nel braccio relativo, il RAG stesso.
    messages = copy.deepcopy(original[:2])
    context_message = str(original[source["message_index"]]["content"])
    prefix = context_message[:source["offset"]]
    if include_rag:
        prefix += source["rag"]
    messages.append({"role": "system", "content": prefix.rstrip()})
    return messages


def _messages_for(source: dict, arm: str) -> list[dict]:
    original = source["body"]["messages"]
    if arm in {"last_pair_only", "last_pair_plus_live_rag"}:
        messages = _static_prefix(
            source,
            include_rag=arm == "last_pair_plus_live_rag",
        )
        # Ultima domanda+risposta prima del target, quindi il target stesso.
        messages.extend(copy.deepcopy(original[-3:]))
        return messages
    if arm == "full_history_structured_anchor":
        messages = copy.deepcopy(original)
        index = source["message_index"]
        content = str(messages[index]["content"])
        insert_at = source["offset"] + source["length"]
        messages[index]["content"] = (
            content[:insert_at]
            + "\n\n"
            + STRUCTURED_ANCHOR
            + content[insert_at:]
        )
        return messages
    raise ValueError(f"braccio non valido: {arm}")


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
        "protocol": "local_reference_window_probe_v1",
        "created_at": time.time(),
        "phase_1_known_result": "all_arms_0_of_3",
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
