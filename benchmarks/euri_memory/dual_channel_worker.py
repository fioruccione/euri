"""Harness A/B dual-channel (rag_only vs dual-channel) — costruito, NON eseguito.

Regole (fino alla revisione di Codex): nessun campione generato, nessun LLM
eseguito, nessuna modifica alla produzione. Le parti senza LLM (census, forecast,
dry-run strutturale, composizione) sono verificabili ora; l'orchestrazione reale
``run_dual_channel_pair`` richiede l'ambiente isolato e i modelli e non viene
invocata qui.

Fedeltà alla simulazione dev (correzioni 3 e 6):
- base = retrieval normale su store RAW-ONLY (contesto rag_only intero, protetto);
- locator = retrieval normale su store RAW+PASSIVE, poi i primi due nodi con
  source=passive nell'ordine registrato (NON una nuova ricerca passive-only);
- A e B condividono la stessa base byte-per-byte (base_sha256 verificato);
- si controbilancia solo l'ordine di GENERAZIONE, non la definizione dei contesti.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.dual_channel import (
    FROZEN_POLICY,
    POLICY_ID,
    compose_dual_channel,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
UNTOUCHED = ("conv-41", "conv-44", "conv-48", "conv-49", "conv-50")

# Stima di costo (banda, come nell'held-out): ingestione passiva ~0,7–1,8
# chiamate/turno; retrieval + risposta ~1–2 chiamate/domanda per braccio.
_INGEST_CALLS_PER_TURN = (0.7, 1.8)
_ANSWER_CALLS_PER_QUESTION = (1.0, 2.0)
_DEFAULT_SECONDS_PER_CALL = 3.0
_WINDOW_SIZE, _WINDOW_OVERLAP = 12, 4


# --------------------------------------------------------------------------- #
# Census delle domande eleggibili (correzione 8)
# --------------------------------------------------------------------------- #
def build_census(source: Path = DEFAULT_SOURCE) -> dict:
    """Tutte le domande eleggibili delle 5 conversazioni, incluse le avversariali.

    Esclude solo item strutturalmente invalidi secondo i validatori congelati:
    una domanda ANSWERABLE la cui evidence gold non è presente nel corpus (difetto
    del rilascio LoCoMo) non è scorabile sull'evidenza. Le avversariali sono
    sempre incluse. Conteggio e motivo delle esclusioni sono riportati.
    """

    cases = {c.sample_id: c for c in LoCoMoAdapter().load(Path(source))}
    conversations = []
    for sample_id in UNTOUCHED:
        case = cases.get(sample_id)
        if case is None:
            raise ValueError(f"conversazione untouched assente dal corpus: {sample_id}")
        known = {t.turn_id for t in case.turns}
        eligible, excluded = [], []
        answerable = adversarial = 0
        for q in case.questions:
            if q.expected_answer is not None and (set(q.evidence_turn_ids) - known):
                excluded.append(
                    {"question_id": q.question_id, "reason": "evidence_gold_non_nel_corpus"}
                )
                continue
            eligible.append(q.question_id)
            if q.expected_answer is None:
                adversarial += 1
            else:
                answerable += 1
        conversations.append(
            {
                "sample_id": sample_id,
                "sessions": len(case.sessions),
                "turns": len(case.turns),
                "session_ids": [s.session_id for s in case.sessions],
                "eligible_question_ids": eligible,
                "eligible_count": len(eligible),
                "answerable": answerable,
                "adversarial": adversarial,
                "excluded": excluded,
                "excluded_count": len(excluded),
            }
        )
    return {
        "experiment": "euri_dual_channel_validation",
        "policy_id": POLICY_ID,
        "universe": list(UNTOUCHED),
        "selection_mode": "census_all_eligible",
        "conversations": conversations,
        "totals": {
            "eligible": sum(c["eligible_count"] for c in conversations),
            "answerable": sum(c["answerable"] for c in conversations),
            "adversarial": sum(c["adversarial"] for c in conversations),
            "excluded": sum(c["excluded_count"] for c in conversations),
        },
    }


# --------------------------------------------------------------------------- #
# Forecast di tempo/chiamate
# --------------------------------------------------------------------------- #
def _windows(turn_count: int) -> int:
    if turn_count <= _WINDOW_SIZE:
        return 1 if turn_count else 0
    step = _WINDOW_SIZE - _WINDOW_OVERLAP
    return (turn_count - _WINDOW_SIZE + step - 1) // step + 1


def forecast(
    census: dict,
    *,
    replicas: int = 2,
    seconds_per_call: float = _DEFAULT_SECONDS_PER_CALL,
    source: Path = DEFAULT_SOURCE,
) -> dict:
    cases = {c.sample_id: c for c in LoCoMoAdapter().load(Path(source))}
    per_conv = []
    calls_low = calls_high = 0.0
    for conv in census["conversations"]:
        case = cases[conv["sample_id"]]
        turns = conv["turns"]
        q = conv["eligible_count"]
        windows = sum(_windows(len(s.turns)) for s in case.sessions)
        # per replica: ingestione passiva (una volta) + 2 retrieval/domanda
        # (base raw-only + locator raw+passive) + 2 generazioni/domanda (A e B).
        ingest = (turns * _INGEST_CALLS_PER_TURN[0], turns * _INGEST_CALLS_PER_TURN[1])
        answer = (
            q * (2 * _ANSWER_CALLS_PER_QUESTION[0] + 2),  # 2 generazioni + ~2 retrieval-calls
            q * (2 * _ANSWER_CALLS_PER_QUESTION[1] + 2),
        )
        low = (ingest[0] + answer[0]) * replicas
        high = (ingest[1] + answer[1]) * replicas
        calls_low += low
        calls_high += high
        per_conv.append(
            {
                "sample_id": conv["sample_id"],
                "turns": turns,
                "eligible_questions": q,
                "extraction_windows_per_replica": windows,
                "estimated_calls_low": round(low),
                "estimated_calls_high": round(high),
            }
        )
    return {
        "note": "Stima strutturale, nessun modello avviato. Bande, non garanzie.",
        "replicas": replicas,
        "pairs_total": len(census["conversations"]) * replicas,
        "generations_total": census["totals"]["eligible"] * replicas * 2,
        "assumptions": {
            "ingest_calls_per_turn": list(_INGEST_CALLS_PER_TURN),
            "answer_calls_per_question": list(_ANSWER_CALLS_PER_QUESTION),
            "retrievals_per_question": 2,
            "generations_per_question": 2,
            "seconds_per_call": seconds_per_call,
        },
        "estimated_llm_calls": {"low": round(calls_low), "high": round(calls_high)},
        "estimated_hours": {
            "low": round(calls_low * seconds_per_call / 3600, 2),
            "high": round(calls_high * seconds_per_call / 3600, 2),
        },
        "per_conversation": per_conv,
    }


# --------------------------------------------------------------------------- #
# Rendering deterministico dei turni verbatim (correzione 4)
# --------------------------------------------------------------------------- #
def build_turn_renderer(case) -> Callable[[str], str]:
    """turn_id -> "Speaker: testo italiano". Il testo delle NOTE non è mai usato."""

    rendered = {t.turn_id: f"{t.speaker}: {t.text}" for t in case.turns}

    def render(turn_id: str) -> str:
        if turn_id not in rendered:
            raise KeyError(f"turno sorgente assente dal corpus localizzato: {turn_id}")
        return rendered[turn_id]

    return render


def locators_from_nodes(passive_retrieval_nodes: list[dict]) -> list[list[str]]:
    """I primi due nodi con source=passive, nell'ordine registrato (correzione 3)."""

    passive = [
        n for n in sorted(passive_retrieval_nodes, key=lambda x: x.get("position", 0))
        if n.get("source") == "passive"
    ]
    return [[str(t) for t in (n.get("evidence_turn_ids") or [])] for n in passive[: FROZEN_POLICY["Q_notes"]]]


# --------------------------------------------------------------------------- #
# Dry-run strutturale (nessun Redis, nessun LLM)
# --------------------------------------------------------------------------- #
def structural_dry_run(*, source: Path = DEFAULT_SOURCE, output: Path | None = None) -> dict:
    census = build_census(source)
    fc = forecast(census, source=source)
    # Smoke della composizione su input sintetico (nessun retrieval reale).
    base_text = "OwnerUser: ciao\nAssistant: salve"
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=2,
        base_turn_ids=["D1:1", "D1:2"],
        locator_notes=[["D5:3"], ["D6:1"]],
        render_turn=lambda t: f"Speaker: contenuto di {t}",
    )
    invariants = {
        "base_preserved_prefix": comp.final_context_text.startswith(base_text),
        "final_slots_le_base_plus_2": comp.final_slots <= comp.base_slots + 2,
        "final_chars_equals_len": comp.final_chars == len(comp.final_context_text),
        "no_synthetic_text_keys": all(
            set(a) == {"turn_id", "chars", "from_note_index"} for a in comp.to_record()["additions"]
        ),
    }
    result = {
        "mode": "structural_dry_run",
        "policy": FROZEN_POLICY,
        "census_totals": census["totals"],
        "census": census,
        "forecast": fc,
        "composition_smoke": comp.to_record(),
        "invariants": invariants,
        "all_invariants_ok": all(invariants.values()),
    }
    if output is not None:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# --------------------------------------------------------------------------- #
# Orchestrazione A/B reale — COSTRUITA, NON INVOCATA (richiede env isolato + LLM)
# --------------------------------------------------------------------------- #
class DualChannelError(RuntimeError):
    pass


def run_dual_channel_pair(
    *,
    case,
    eligible_question_ids: list[str],
    build_rag_context: Callable,
    memory_raw,
    memory_mixed,
    chat,
    model: str,
    answer_system: str,
    answer_seed: int,
    generation_order: tuple[str, str],
) -> list[dict]:
    """Esegue una coppia (conversazione, replica). NON invocata in questo turno.

    Precondizioni (predisposte dal chiamante nell'ambiente isolato):
    - ``memory_raw``: store con SOLO turni grezzi (per la base rag_only);
    - ``memory_mixed``: store con turni grezzi + memorie passive (per i locator);
    - la base è calcolata su ``memory_raw`` e riusata byte-per-byte dai due bracci.
    """

    render_turn = build_turn_renderer(case)
    results = []
    for question in case.questions:
        if question.question_id not in eligible_question_ids:
            continue
        base = build_rag_context(question.text, memory_raw, mode="search")
        base_text = base.text
        base_turn_ids = [
            str(t) for n in base.nodes for t in (n.get("evidence_turn_ids") or [])
            if n.get("source") == "conversation"
        ]
        base_slots = len(base.nodes)
        base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()

        mixed = build_rag_context(question.text, memory_mixed, mode="search")
        locator_notes = locators_from_nodes(mixed.nodes)

        comp = compose_dual_channel(
            base_context_text=base_text,
            base_slots=base_slots,
            base_turn_ids=base_turn_ids,
            locator_notes=locator_notes,
            render_turn=render_turn,
        )
        # Correzione 6: A e B DEVONO usare la stessa base byte-per-byte.
        if comp.base_sha256 != base_sha or not comp.final_context_text.startswith(base_text):
            raise DualChannelError(
                f"base divergente tra i bracci per {question.question_id}: fail-closed"
            )

        contexts = {"rag_only": base_text, "dual_channel": comp.final_context_text}
        answers = {}
        for arm in generation_order:  # controbilanciato solo l'ordine di generazione
            resp = chat(
                model=model,
                messages=[
                    {"role": "system", "content": answer_system},
                    {"role": "user", "content": _user_prompt(case, question, contexts[arm])},
                ],
                options={"temperature": 0, "num_predict": 160, "seed": answer_seed},
                think=False,
            )
            answers[arm] = _content(resp)

        results.append(
            {
                "question_id": question.question_id,
                "category": question.category,
                "base_sha256": base_sha,
                "generation_order": list(generation_order),
                "answers": answers,
                "composition": comp.to_record(),
                "base_nodes": [_node_view(n) for n in base.nodes],
                "locator_nodes": [_node_view(n) for n in mixed.nodes if n.get("source") == "passive"],
            }
        )
    return results


def _user_prompt(case, question, context_text: str) -> str:
    return (
        f"Partecipanti: {case.speakers[0]} e {case.speakers[1]}.\n\n"
        f"Contesto di memoria:\n{context_text or '(nessuna memoria rilevante)'}\n\n"
        f"Domanda: {question.text}"
    )


def _node_view(n: dict) -> dict:
    return {
        "id": n.get("id"),
        "source": n.get("source"),
        "position": n.get("position"),
        "retrieval_path": n.get("retrieval_path"),
        "evidence_turn_ids": list(n.get("evidence_turn_ids") or []),
    }


def _content(resp: Any) -> str:
    message = getattr(resp, "message", None)
    if message is None and isinstance(resp, dict):
        message = resp.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or "").strip()
