#!/usr/bin/env python3
"""Esegue esplicitamente Loop 2k su un problema e un contesto forniti.

Il comando non interroga automaticamente la memoria di Euri: il file di
contesto e gli eventuali source-ref sono il perimetro evidenziale del run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import redis

import config
from core.dream_engine import DreamEngine
from core.embedder import Embedder


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loop 2k Ideation Arena")
    parser.add_argument("prompt", help="problema su cui generare alternative")
    parser.add_argument(
        "--context-file",
        type=Path,
        help="file UTF-8 contenente il pacchetto evidenziale",
    )
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="vincolo; ripetibile",
    )
    parser.add_argument(
        "--source-ref",
        action="append",
        default=[],
        help="riferimento di provenienza; ripetibile",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=config.IDEATION_ARENA_DEFAULT_CANDIDATES,
        choices=range(4, 9),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    context = ""
    if args.context_file:
        context = args.context_file.expanduser().resolve().read_text(
            encoding="utf-8"
        )

    client = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )
    client.ping()
    embedder = Embedder()
    embedder.load()
    engine = DreamEngine(client, embedder)
    result = engine.run_ideation_tournament(
        args.prompt,
        grounding_context=context,
        constraints=args.constraint,
        source_refs=args.source_ref,
        n_candidates=args.candidates,
    )

    print(json.dumps({
        "run_id": result.run_id,
        "status": result.status,
        "artifact_key": result.artifact_key,
        "ranking": result.ranking,
        "top_candidate_ids": result.top_candidate_ids,
        "top_candidate": (
            result.top_candidate.to_dict() if result.top_candidate else None
        ),
    }, ensure_ascii=False, indent=2))
    return 0 if result.status in {"completed", "contested"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
