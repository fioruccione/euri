"""Gate semantico fail-closed per continuazioni vocali senza wake word."""
from __future__ import annotations

import json
import re
import time
from typing import Callable

from loguru import logger

import config
from core.ollama_client import chat_client


_ACCEPTED_RELATIONS = frozenset({"direct_followup", "answer_to_assistant"})


def _clean(text: str) -> str:
    value = str(text or "")
    if "<channel|>" in value:
        value = value.split("<channel|>", 1)[-1]
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL)
    return value.strip()


def recent_dialogue_text(history: list[dict], *, max_messages: int = 6) -> str:
    """Serializza solo il dialogo recente; nessun RAG o contesto sensoriale."""
    rows = []
    for message in list(history)[-max(1, int(max_messages)):]:
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        speaker = (
            config.OWNER_DISPLAY_NAME
            if role == "user"
            else config.ASSISTANT_DISPLAY_NAME
        )
        content = " ".join(str(message.get("content") or "").split())
        if content:
            rows.append(f"{speaker}: {content[:1200]}")
    return "\n".join(rows)


def classify_adaptive_followup(
    utterance: str,
    recent_dialogue: str,
    *,
    chat_fn: Callable | None = None,
    min_confidence: float | None = None,
) -> dict:
    """Riconosce una continuazione diretta; somiglianza tematica non basta."""
    threshold = float(
        config.CONVERSATION_ADAPTIVE_FOLLOWUP_MIN_CONFIDENCE
        if min_confidence is None
        else min_confidence
    )
    if not str(utterance or "").strip() or not str(recent_dialogue or "").strip():
        return {
            "accepted": False,
            "addressed": False,
            "relation": "unclear",
            "confidence": 0.0,
            "reason": "missing_dialogue",
        }

    prompt = f"""\
Devi decidere se l'ULTIMA FRASE e' rivolta direttamente all'assistente come
continuazione del dialogo recente, oppure e' parlato ambientale rivolto a una
persona vicina.

Regole:
- La semplice somiglianza di argomento NON basta.
- direct_followup: domanda o continuazione che dipende chiaramente dall'ultima
  risposta dell'assistente.
- answer_to_assistant: risposta chiara a una domanda appena fatta dall'assistente.
- ambient: frase autonoma, commento a un collega, dettatura non richiesta o frase
  che potrebbe stare fuori dal dialogo senza perdere significato.
- unclear: qualunque dubbio. Nel dubbio addressed=false.
- Non usare il fatto che il parlante sia il proprietario come prova che stia
  parlando all'assistente.

Dialogo recente:
{recent_dialogue}

ULTIMA FRASE:
{utterance}

Rispondi esclusivamente in JSON:
{{"addressed":true/false,"relation":"direct_followup|answer_to_assistant|ambient|unclear","confidence":0.0}}"""

    started = time.perf_counter()
    try:
        call = chat_fn or chat_client.chat
        response = call(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 250},
            format="json",
            think=False,
        )
        raw = _clean(response.message.content or "")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("output non-object")
        addressed = parsed.get("addressed") is True
        relation = str(parsed.get("relation") or "unclear").strip().lower()
        confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
        accepted = (
            addressed
            and relation in _ACCEPTED_RELATIONS
            and confidence >= threshold
        )
        result = {
            "accepted": accepted,
            "addressed": addressed,
            "relation": relation,
            "confidence": confidence,
            "reason": "accepted" if accepted else "below_gate",
        }
        logger.info(
            "Addressedness gate: {} relation={} conf={:.2f} ({:.0f}ms)",
            "ACCEPT" if accepted else "REJECT",
            relation,
            confidence,
            (time.perf_counter() - started) * 1000,
        )
        return result
    except Exception as exc:
        logger.debug(f"Addressedness gate: fail-closed ({exc})")
        return {
            "accepted": False,
            "addressed": False,
            "relation": "unclear",
            "confidence": 0.0,
            "reason": "classifier_error",
        }
