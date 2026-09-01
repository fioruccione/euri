#!/usr/bin/env python3
"""Replay paired del referente locale sul payload Gemma catturato.

Non ricostruisce il prompt e non accede a Redis: seleziona la richiesta organica
dal prompt capture, rimuove o annota soltanto il segmento RAG registrato dagli
offset del capture hook e interroga il medesimo endpoint locale.
"""
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


TARGET = (
    "Euri, restiamo sulla configurazione che hai appena descritto. "
    "In quella configurazione la pompa è prima o dopo il filtro RAS 500?"
)
ARM_ORDER = (
    ("history_only", "history_plus_live_rag", "history_rag_local_precedence"),
    ("history_plus_live_rag", "history_rag_local_precedence", "history_only"),
    ("history_rag_local_precedence", "history_only", "history_plus_live_rag"),
)
LOCAL_PRECEDENCE_CONTRACT = """[PRECEDENZA DEL REFERENTE CONVERSAZIONALE]
Quando il turno corrente indica esplicitamente «quello/quella che hai appena
descritto», risolvi prima il referente sulla risposta locale immediatamente
precedente. Solo dopo usa le memorie durevoli per ricavarne i fatti. La
somiglianza semantica o la maggiore ricchezza di una memoria non possono
sostituire quel referente locale. Se due turni locali sono davvero equivalenti,
chiedi quale intende l'utente invece di scegliere."""


def _load_request(capture: Path) -> dict:
    matches = []
    for raw in capture.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "request":
            continue
        body_raw = ((event.get("http") or {}).get("body_utf8") or "")
        try:
            body = json.loads(body_raw)
        except (TypeError, json.JSONDecodeError):
            continue
        messages = body.get("messages") or []
        if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == TARGET:
            matches.append((event, body))
    if len(matches) != 1:
        raise RuntimeError(f"richiesta organica non univoca: trovate {len(matches)}")
    event, body = matches[0]
    location = (((event.get("analysis") or {}).get("rag_context") or {}).get("location") or {})
    message_index = int(location["message_index"])
    offset = int(location["char_offset_in_message"])
    length = int(((event.get("analysis") or {}).get("rag_context") or {})["chars"])
    content = str(body["messages"][message_index]["content"])
    rag = content[offset:offset + length]
    expected_sha = str(((event.get("analysis") or {}).get("rag_context") or {}).get("sha256") or "")
    actual_sha = hashlib.sha256(rag.encode()).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeError(f"segmento RAG non coincide: {actual_sha} != {expected_sha}")
    return {
        "event": event,
        "body": body,
        "message_index": message_index,
        "offset": offset,
        "length": length,
        "rag": rag,
        "rag_sha256": actual_sha,
    }


def _messages_for(source: dict, arm: str) -> list[dict]:
    messages = copy.deepcopy(source["body"]["messages"])
    index = source["message_index"]
    content = str(messages[index]["content"])
    start = source["offset"]
    end = start + source["length"]
    if arm == "history_only":
        replacement = ""
    elif arm == "history_plus_live_rag":
        replacement = source["rag"]
    elif arm == "history_rag_local_precedence":
        replacement = source["rag"] + "\n\n" + LOCAL_PRECEDENCE_CONTRACT
    else:
        raise ValueError(f"braccio non valido: {arm}")
    messages[index]["content"] = content[:start] + replacement + content[end:]
    return messages


def _clean(text: str) -> str:
    from core.brain import Brain
    return Brain._clean(text)


def _score(answer: str) -> dict:
    low = " ".join(str(answer or "").casefold().replace("*", "").split())
    current = any(marker in low for marker in ("configurazione attuale", "linea attuale"))
    after = any(marker in low for marker in ("dopo il filtro", "a valle del filtro", "successivamente al filtro"))
    proposed = any(marker in low for marker in ("modifica proposta", "configurazione proposta", "fpp20", "fpp 20"))
    before = any(marker in low for marker in ("prima del filtro", "a monte del filtro"))
    return {
        # Nel braccio con la sola coppia locale "dopo il filtro" risolve gia'
        # completamente la domanda; ripetere la label "attuale" non e' richiesto.
        "pass": after and not proposed and not before,
        "current": current,
        "after_filter": after,
        "proposed": proposed,
        "before_filter": before,
    }


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
        arm_records = [record for record in records if record["arm"] == arm]
        summary[arm] = {
            "passes": sum(bool(record["score"]["pass"]) for record in arm_records),
            "total": len(arm_records),
            "answers": [record["answer"] for record in arm_records],
        }
    result = {
        "schema_version": 1,
        "protocol": "local_reference_precedence_v1",
        "created_at": time.time(),
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
        "request_id": result["request_id"],
        "summary": {
            arm: {"passes": item["passes"], "total": item["total"]}
            for arm, item in result["summary"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
