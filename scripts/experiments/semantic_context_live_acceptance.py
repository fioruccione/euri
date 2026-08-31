#!/usr/bin/env python3
"""Accettazione paired read-only del compilatore semantico.

Protocollo congelato nel codice prima dell'esecuzione:
- Redis reale letto con touch=False;
- un solo frame semantico per domanda, condiviso fra i due bracci;
- baseline (compilatore off) e trattamento (on), ordine alternato;
- Brain nuovo e scope experiment per ogni risposta;
- nessuna scrittura di memorie, turni, Dream o Obsidian.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from core.brain import Brain
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.rag_context import build_rag_context
from core.semantic_turn import SemanticTurnService
from utils.redis_client import get_client


CASES = (
    {
        "id": "topology",
        "question": (
            "Ricostruisci la linea ICMA2 dall'uscita della bivite fino al taglio. "
            "Distingui configurazione attuale e modifica FIMIC FPP20 proposta."
        ),
        "criterion": "attuale RAS500 prima della pompa; proposta FPP20 prima del RAS500",
    },
    {
        "id": "causal_constraint",
        "question": "Perche' la pompa a ingranaggi attuale della ICMA2 si trova dopo il RAS500?",
        "criterion": "materiale contaminato e posizione dopo RAS500",
    },
    {
        "id": "proposal_state",
        "question": "La pompa FIMIC FPP20 sulla ICMA2 e' gia' installata oppure e' ancora una proposta?",
        "criterion": "proposta/ipotesi, non installazione attuale",
    },
    {
        "id": "cross_project_isolation",
        "question": (
            "Che cosa ricordi del banco Orione 31 e della sigla BX17? "
            "Segnala eventuali dati mancanti senza collegarlo ad altri progetti."
        ),
        "criterion": "nessuna contaminazione ICMA2/RAS500/FPP20",
    },
    {
        "id": "missing_focus",
        "question": "La pompa e' prima o dopo il filtro?",
        "criterion": "chiarimento o astensione; nessuna topologia inventata",
    },
)


def _normal(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _before(text: str, first: str, second: str) -> bool:
    a, b = text.find(first), text.find(second)
    return a >= 0 and b >= 0 and a < b


def _section(text: str, start: str, end: str) -> str:
    low = text.lower()
    a = low.find(start)
    if a < 0:
        return ""
    a += len(start)
    b = low.find(end, a)
    return low[a:] if b < 0 else low[a:b]


def _section_until_any(text: str, start: str, ends: tuple[str, ...]) -> str:
    low = text.lower()
    a = low.find(start)
    if a < 0:
        return ""
    a += len(start)
    positions = [low.find(end, a) for end in ends]
    positions = [position for position in positions if position >= 0]
    return low[a:] if not positions else low[a:min(positions)]


def _section_from_any(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    low = text.lower()
    positions = [(low.find(start), start) for start in starts]
    positions = [(position, start) for position, start in positions if position >= 0]
    if not positions:
        return ""
    position, start = min(positions)
    position += len(start)
    end_positions = [low.find(end, position) for end in ends]
    end_positions = [end for end in end_positions if end >= 0]
    return low[position:] if not end_positions else low[position:min(end_positions)]


def score(case_id: str, answer: str) -> dict:
    low = _normal(answer)
    if case_id == "topology":
        current = _section_until_any(
            answer,
            "configurazione attuale",
            ("modifica proposta", "configurazione proposta", "effetti attesi"),
        )
        proposed = _section_from_any(
            answer,
            ("modifica proposta", "configurazione proposta"),
            ("effetti attesi", "aspetti da verificare"),
        )
        proposal_fpp_before_ras = _before(proposed, "fpp", "ras") or any(
            marker in proposed
            for marker in (
                "fpp20 prima del passaggio nel filtro",
                "fpp 20 prima del passaggio nel filtro",
                "fpp20 prima del filtro",
                "fpp 20 prima del filtro",
                "fpp20 a monte del filtro",
                "fpp 20 a monte del filtro",
            )
        )
        return {
            "pass": _before(current, "ras", "pomp") and proposal_fpp_before_ras,
            "current_ras_before_pump": _before(current, "ras", "pomp"),
            "proposal_fpp_before_ras": proposal_fpp_before_ras,
        }
    if case_id == "causal_constraint":
        contamination = any(x in low for x in ("contaminat", "inquinat", "impurit"))
        after = any(x in low for x in (
            "dopo il ras", "a valle del ras", "successivamente al ras",
            "filtro faccia da barriera prima", "filtro fa da barriera prima",
        ))
        return {"pass": contamination and after, "contamination": contamination, "after_ras": after}
    if case_id == "proposal_state":
        proposed = any(x in low for x in ("proposta", "ipotetic", "non è ancora", "non e' ancora", "da installare"))
        installed = bool(re.search(r"\b(?:è|e')\s+(?:già\s+)?installata\b", low)) and "non è" not in low and "non e'" not in low
        return {"pass": proposed and not installed, "proposed": proposed, "claims_installed": installed}
    if case_id == "cross_project_isolation":
        leaked = any(x in low for x in ("icma", "ras500", "ras 500", "fpp20", "fpp 20"))
        return {"pass": not leaked, "cross_project_leak": leaked}
    if case_id == "missing_focus":
        clarifies = any(x in low for x in (
            "puoi specificare", "puoi indicare", "a quale progetto ti riferisci",
            "di quale progetto parli", "non posso determinar", "mi manca il contesto",
        ))
        asserts_order = any(x in low for x in (
            "prima del filtro", "dopo il filtro", "a monte del filtro", "a valle del filtro",
        )) and not clarifies
        return {"pass": clarifies and not asserts_order, "clarifies": clarifies, "asserts_order": asserts_order}
    return {"pass": False, "reason": "unknown_case"}


def run(output: Path) -> dict:
    redis_client = get_client()
    memory = MemoryManager(redis_client, embedder=Embedder())
    semantic_turn = SemanticTurnService(redis_client)
    records = []
    original_flag = config.SEMANTIC_CONTEXT_ENABLED
    try:
        for case_index, case in enumerate(CASES):
            frame_started = time.perf_counter()
            frame = semantic_turn.interpret(
                case["question"],
                recent_history=[],
                memory_scope="personal",
                session_bootstrap=True,
                persist_corrections=False,
            )
            frame_ms = round((time.perf_counter() - frame_started) * 1000, 1)
            arms = (False, True) if case_index % 2 == 0 else (True, False)
            for enabled in arms:
                config.SEMANTIC_CONTEXT_ENABLED = enabled
                rag_started = time.perf_counter()
                rag = build_rag_context(
                    case["question"],
                    memory,
                    mode="search",
                    recent_history=[],
                    semantic_frame=frame,
                    touch=False,
                )
                rag_ms = round((time.perf_counter() - rag_started) * 1000, 1)
                brain = Brain()
                answer_started = time.perf_counter()
                answer = brain.respond(
                    case["question"],
                    context=rag.text,
                    memory_scope=f"experiment_semantic_acceptance_{case['id']}_{'on' if enabled else 'off'}",
                    semantic_frame=frame,
                    thinking=False,
                )
                answer_ms = round((time.perf_counter() - answer_started) * 1000, 1)
                records.append({
                    "case_id": case["id"],
                    "criterion": case["criterion"],
                    "arm": "semantic_on" if enabled else "baseline_off",
                    "frame_ms": frame_ms,
                    "rag_ms": rag_ms,
                    "answer_ms": answer_ms,
                    "rag_chars": len(rag.text),
                    "rag_sha256": hashlib.sha256(rag.text.encode()).hexdigest(),
                    "rag_ids": list(rag.ids),
                    "semantic_diagnostics": rag.diagnostics.get("semantic_context"),
                    "score": score(case["id"], answer),
                    "answer": answer,
                })
    finally:
        config.SEMANTIC_CONTEXT_ENABLED = original_flag

    pairs = []
    for case in CASES:
        rows = [item for item in records if item["case_id"] == case["id"]]
        pairs.append({
            "case_id": case["id"],
            "baseline_pass": next(item["score"]["pass"] for item in rows if item["arm"] == "baseline_off"),
            "semantic_pass": next(item["score"]["pass"] for item in rows if item["arm"] == "semantic_on"),
            "baseline_answer_ms": next(item["answer_ms"] for item in rows if item["arm"] == "baseline_off"),
            "semantic_answer_ms": next(item["answer_ms"] for item in rows if item["arm"] == "semantic_on"),
        })
    result = {
        "protocol": "semantic_context_live_acceptance_v1",
        "created_at": time.time(),
        "redis_mutation": False,
        "dream": False,
        "semantic_frame_shared_within_pair": True,
        "temperature": 0.7,
        "cases": list(CASES),
        "pairs": pairs,
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"output": str(args.output), "pairs": result["pairs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
