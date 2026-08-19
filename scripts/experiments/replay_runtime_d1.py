#!/usr/bin/env python3
"""Riproduce le quattro sonde D1 attraversando il processo Euri principale.

Il trasporto usa il canale mobile vocale: il testo viene sintetizzato in audio,
trascritto dal Whisper gia' caricato nel daemon e poi attraversa context builder,
RAG e ``Brain`` reali. I risultati sono ricerca diagnostica e vengono scritti
solo sotto ``research_logs/``.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
import uuid
from pathlib import Path

from utils.redis_client import get_client
from voice.tts import TTS


EPISODES = (
    (
        "A",
        "No, questo era sempre un test di memoria. In realtà ti avevo detto "
        "prima di venerdì scorso che sarei mancato per qualche giorno. Ti "
        "ricordi per quanti giorni? A che data più che mai saremmo ripartiti?",
    ),
    (
        "B",
        "Vedo che in questi giorni passati, o perlomeno questo fine settimana, "
        "hai fatto diversi sogni digitali, delle elaborazioni oniriche. Ti ricordi?",
    ),
    ("C", "Sì, fai una ricerca nei log."),
    ("D", "Euri, buongiorno, sono venuto a trovarti, come va?"),
)


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


def _field(data: dict, name: str) -> str:
    """Supporta client Redis con e senza ``decode_responses``."""
    return _decode(data.get(name, data.get(name.encode(), "")))


def run(output: Path, timeout_s: float, selected: set[str] | None = None) -> dict:
    redis_client = get_client()
    tts = TTS()
    tts.load()
    results: list[dict] = []

    for label, question in EPISODES:
        if selected and label not in selected:
            continue
        samples, sample_rate = tts.synthesize(question, lang="it")
        request_id = f"d1-{label.lower()}-{uuid.uuid4().hex[:12]}"
        last_out = redis_client.xrevrange("euri:mobile:out", count=1)
        cursor = _decode(last_out[0][0]) if last_out else "0-0"
        sent_at = time.time()
        redis_client.xadd(
            "euri:mobile:in",
            {
                "request_id": request_id,
                "audio_b64": base64.b64encode(samples.tobytes()).decode(),
                "sr": str(sample_rate),
            },
            maxlen=20,
        )

        deadline = time.monotonic() + timeout_s
        response_record = None
        while time.monotonic() < deadline:
            batches = redis_client.xread(
                {"euri:mobile:out": cursor}, count=10, block=3000
            )
            for _, messages in batches:
                for message_id, data in messages:
                    cursor = _decode(message_id)
                    if _field(data, "request_id") == request_id:
                        response_record = {
                            "stream_id": cursor,
                            "stt_text": _field(data, "text"),
                            "response": _field(data, "response"),
                            "sample_rate": _field(data, "sample_rate"),
                        }
                        break
                if response_record is not None:
                    break
            if response_record is not None:
                break
        if response_record is None:
            raise TimeoutError(f"nessuna risposta mobile per episodio {label}")
        results.append(
            {
                "episode": label,
                "question_synthesized": question,
                "request_id": request_id,
                "sent_at": sent_at,
                "received_at": time.time(),
                **response_record,
            }
        )

    result = {
        "schema_version": 1,
        "created_at": time.time(),
        "transport": "euri:mobile:in -> main voice_daemon -> euri:mobile:out",
        "episodes": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--episodes",
        default="",
        help="etichette separate da virgola (es. B,C); vuoto = tutte",
    )
    args = parser.parse_args()
    selected = {
        item.strip().upper() for item in args.episodes.split(",") if item.strip()
    }
    result = run(args.output, args.timeout, selected or None)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "episodes": [
                    {
                        "episode": item["episode"],
                        "stt_text": item["stt_text"],
                        "response": item["response"],
                    }
                    for item in result["episodes"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
