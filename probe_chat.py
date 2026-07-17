#!/usr/bin/env python3
"""
Sonda di conversazione READ-ONLY — replica il path mobile/chat del daemon
(build_rag_context → brain.respond → pavimenti d'onestà) senza Whisper/TTS.

Read-only per davvero:
  - niente log_conversation (no scritture nel diario);
  - set_last_rag_ctx neutralizzato → NON pesta euri:last_rag_ctx, che il
    save-resolver del daemon vivo legge per "salva questo";
  - recalled_count non toccato (il contesto iniettato usa touch=False).
È sicura da lanciare mentre il daemon gira.

Uso: venv/bin/python probe_chat.py "la domanda"
     # multi-turno: passa più domande, vengono incatenate nella stessa history
     venv/bin/python probe_chat.py "domanda 1" "domanda 2" ...
"""
import sys

from core.memory_manager import MemoryManager
from core.brain import Brain
from core.embedder import Embedder
from core.rag_context import build_rag_context
from core.honesty import scrub_unbacked_save_claim
from core.act_word_check import scrub_unbacked_action_claim
from utils.redis_client import get_client


def main(questions: list[str]) -> int:
    r = get_client()
    embedder = Embedder()
    memory = MemoryManager(r, embedder=embedder)
    brain = Brain()

    # READ-ONLY: non sovrascrivere euri:last_rag_ctx (lo legge il save-resolver vivo).
    memory.set_last_rag_ctx = lambda *a, **k: None

    for q in questions:
        with brain.history_lock:
            recent = list(brain._conversation_history)
        rag = build_rag_context(q, memory, mode="chat", recent_history=recent)
        context = rag.text

        reply = brain.respond(q, context=context)
        reply = scrub_unbacked_save_claim(reply)
        reply = scrub_unbacked_action_claim(reply, set())

        print("=" * 78)
        print(f"DOMANDA: {q}")
        print("-" * 78)
        print(f"CONTESTO RECUPERATO ({len(rag.ids)} memorie):")
        ctx = context.strip() or "(vuoto)"
        for line in ctx.splitlines():
            print(f"  | {line}")
        print("-" * 78)
        print(f"EURI: {reply}")
        print()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: probe_chat.py \"domanda\" [\"domanda 2\" ...]")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
