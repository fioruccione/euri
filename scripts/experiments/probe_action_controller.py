#!/usr/bin/env python3
"""Probe read-only del controller intenzione→azione sul modello e stato reali."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.executor import Executor
from core.action_controller import (
    ActionController,
    build_capability_snapshot,
    looks_actionable,
)
from core.memory_manager import MemoryManager
from utils.redis_client import get_client


CASES = [
    (
        "Hai un impegno scaduto: provare sul bancale Poseidon il blend a mescola fredda.",
        "Considero chiuso, lo rifacciamo più avanti ma decido la data io.",
    ),
    (
        "Ti ricordo il test Poseidon scaduto.",
        "Lascialo in sospeso senza data, deciderò io quando riprenderlo.",
    ),
    (
        "La GPU 1 aveva ancora memoria disponibile.",
        "Puoi controllarla adesso?",
    ),
    (
        "Stavamo parlando dei progetti aperti.",
        "Euri, ho dei todo in sospeso?",
    ),
    (
        "Il PP e il PEMD hanno finestre operative diverse.",
        "È interessante quello che dici sui polimeri.",
    ),
    (
        "Hai ancora aperto il test del blend Poseidon.",
        "Quello del Poseidon per me non è più da fare.",
    ),
    (
        "Il servizio Euri è attivo, ma non hai una capability di riavvio.",
        "Fallo ripartire e poi assicurati che sia sano.",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=int, action="append", dest="cases")
    parser.add_argument(
        "--route", action="store_true",
        help="esegue anche il gate semantico che decide se invocare il controller",
    )
    args = parser.parse_args()
    r = get_client()
    memory = MemoryManager(r)
    executor = Executor()
    capabilities, state, _todos = build_capability_snapshot(
        memory.get_pending_todos(), executor.get_contextual_capabilities()
    )
    controller = ActionController()

    selected = args.cases or list(range(1, len(CASES) + 1))
    for index in selected:
        if index < 1 or index > len(CASES):
            raise SystemExit(f"Caso non valido: {index}")
        previous, utterance = CASES[index - 1]
        proposal = controller.propose(
            utterance,
            previous_euri_turn=previous,
            capabilities=capabilities,
            state_context=state,
        )
        decision = controller.decide(
            proposal,
            capabilities,
            allow_euri_read_only=bool(proposal and proposal.alternative),
        )
        print(f"\nTESTO: {utterance}")
        print(f"PRE-GATE: {looks_actionable(utterance)}")
        print(f"PROPOSTA: {proposal}")
        print(f"DECISIONE: {decision.disposition.value} ({decision.reason})")
        if args.route:
            from core.llm_classifier import llm_fallback_classify
            print(f"GATE SEMANTICO: {llm_fallback_classify(utterance) or 'CHAT'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
