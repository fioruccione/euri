#!/usr/bin/env python3
"""Estrae la parte derivabile di D1 dai payload HTTP realmente catturati."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _events(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _last_user(payload: dict) -> str:
    for message in reversed(payload.get("messages") or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _relative(location: dict | None, message_count: int) -> dict | None:
    if not location:
        return None
    index = int(location["message_index"])
    return {
        "message_index_zero_based": index,
        "ordinal_in_array": index + 1,
        "message_count": message_count,
        "messages_after": message_count - index - 1,
        "role": location.get("role"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--replay", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    # L'ultimo replay fornito vince per la stessa etichetta: così B/C validate
    # sostituiscono le prime repliche alterate dallo STT, mentre A/D restano.
    replay_by_episode: dict[str, dict] = {}
    for path in args.replay:
        for episode in _load(path).get("episodes") or []:
            replay_by_episode[str(episode["episode"])] = episode

    all_events = _events(args.capture)
    requests = [row for row in all_events if row.get("event") == "request"]
    completions = {
        row.get("request_id"): row
        for row in all_events
        if row.get("event") == "completion"
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_episodes: list[dict] = []

    for label in ("A", "B", "C", "D"):
        replay = replay_by_episode[label]
        start = float(replay["sent_at"])
        end = float(replay["received_at"])
        candidates = [
            row for row in requests
            if start <= float(row.get("captured_at") or 0) <= end
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"episodio {label}: attesa una richiesta fra {start} e {end}, "
                f"trovate {len(candidates)}"
            )
        request = candidates[0]
        body_text = request["http"]["body_utf8"]
        payload = json.loads(body_text)
        analysis = request["analysis"]
        completion = completions.get(request["request_id"], {})
        payload_path = args.output_dir / f"d1_episode_{label}_ollama_payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rag = analysis["rag_context"]
        report_episodes.append({
            "episode": label,
            "request_id": request["request_id"],
            "captured_at": request["captured_at"],
            "payload_file": str(payload_path),
            "payload_sha256": request["http"]["body_sha256"],
            "payload_bytes": request["http"]["body_bytes"],
            "model": analysis.get("model"),
            "prompt_eval_count": completion.get("prompt_eval_count"),
            "stt_text": replay["stt_text"],
            "last_user_message": _last_user(payload),
            "identity": {
                "present": analysis["identity_block"]["present"],
                "relative_message_position": _relative(
                    analysis["identity_block"].get("location"),
                    analysis["message_count"],
                ),
            },
            "rag": {
                "present": rag["present"],
                "relative_message_position": _relative(
                    rag.get("location"), analysis["message_count"]
                ),
                "block_count": len(rag.get("blocks") or []),
                "blocks": [
                    {
                        "ordinal": block["ordinal"],
                        "label": block["label"],
                        "message_index_zero_based": block.get("message_index"),
                        "char_offset_in_message": block.get("char_offset_in_message"),
                        "char_offset_in_rag_context": block["char_offset_in_rag_context"],
                        "char_offset_from_rag_context_end": block[
                            "char_offset_from_rag_context_end"
                        ],
                    }
                    for block in rag.get("blocks") or []
                ],
            },
            "renderer_boundary": {
                "compiled_prompt": "genuinely_unavailable_ollama_renderer_internal",
                "subblock_token_offsets": "genuinely_unavailable_ollama_renderer_internal",
                "server_truncation_signal": "genuinely_unavailable_in_ollama_chat_response",
            },
        })

    report = {
        "schema_version": 1,
        "scope": "D1 derivabile dal payload client->Ollama realmente catturato",
        "episodes": report_episodes,
        "genuinely_unavailable": [
            "stringa compilata dal template gemma4 dentro Ollama",
            "offset token dei sottoblocchi nel prompt compilato",
            "segnale preciso di troncamento lato server",
        ],
        "derivable_and_reported": [
            "presenza del blocco identitario nell'array messages",
            "posizione relativa del blocco identitario nell'array messages",
            "presenza e posizione relativa del contesto e dei blocchi RAG",
            "token totali valutati da Ollama tramite prompt_eval_count",
        ],
    }
    report_path = args.output_dir / "d1_transport_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
