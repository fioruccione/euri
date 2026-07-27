"""Worker A/B dual-channel STADIATO, avviato solo in un runtime isolato.

rag_only vs dual_channel sullo stesso ambiente isolato, in stadi, per non tenere
due Redis simultanei:

  1. reset -> ingest RAW-only  -> per ogni domanda: base retrieval, base testuale,
     turni idratati dai doc Redis, base_sha256. Tutto CONSERVATO.
  2. reset -> ingest RAW+PASSIVE -> per ogni domanda: locator retrieval, note
     passive idratate dai doc Redis (primi due nodi source=passive).
  3. componi i contesti dual congelati usando la base testuale GIÀ SALVATA.
  4. genera A/B nell'ordine controbilanciato sui contesti ormai immutabili.

La base usata dai due bracci è letteralmente lo stesso oggetto testuale salvato,
non due retrieval rieseguiti; divergenza SHA -> fail-closed.

Blocker corretto: ``RagContext.nodes`` NON contiene evidence_turn_ids; ogni nodo
è idratato dal proprio documento Redis isolato (come ``_retrieval_trace``):
- raw:     benchmark_turn_id;
- passive: benchmark_evidence_turn_ids (fallback: source_turn_ids canonici del
  temporal_context, se presenti nel formato congelato).

Durante retrieval e generazione si usano solo ConversationCorpus e QuestionPrompt:
gold ed evidence restano fuori dalla vista del runner fino allo scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.contracts import QuestionResult
from benchmarks.euri_memory.dual_channel import FROZEN_POLICY, POLICY_ID, compose_dual_channel
from benchmarks.euri_memory.localization import BenchmarkLocalization
from benchmarks.euri_memory.selection import BenchmarkSelection


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
UNTOUCHED = ("conv-41", "conv-44", "conv-48", "conv-49", "conv-50")

_INGEST_CALLS_PER_TURN = (0.7, 1.8)
_ANSWER_CALLS_PER_QUESTION = (1.0, 2.0)
_DEFAULT_SECONDS_PER_CALL = 3.0
_WINDOW_SIZE, _WINDOW_OVERLAP = 12, 4

_ANSWER_SYSTEM_IT = """\
Stai rispondendo a un benchmark sulla memoria conversazionale a lungo termine.
Usa soltanto il contesto di memoria fornito. Non usare conoscenze esterne e non
dedurre la risposta dalla formulazione della domanda. Se il contesto non
supporta una risposta, rispondi esattamente: Non lo so.
Rispondi in italiano, in modo conciso e senza spiegazioni."""


# --------------------------------------------------------------------------- #
# Census delle domande eleggibili (correzione 8)
# --------------------------------------------------------------------------- #
def build_census(source: Path = DEFAULT_SOURCE) -> dict:
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
# Forecast (fasi LLM separate)
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
        ingest = (turns * _INGEST_CALLS_PER_TURN[0], turns * _INGEST_CALLS_PER_TURN[1])
        # per replica: 2 retrieval/domanda (base + locator) + 2 generazioni (A e B)
        answer = (
            q * (2 * _ANSWER_CALLS_PER_QUESTION[0] + 2),
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
        "llm_phases": ["passive_ingestion", "base_retrieval", "locator_retrieval", "answer_rag", "answer_dual"],
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
# Idratazione dei nodi dai documenti Redis (blocker 1)
# --------------------------------------------------------------------------- #
def _sorted_by_position(nodes: list[dict]) -> list[dict]:
    return sorted(nodes, key=lambda x: x.get("position", 0))


def hydrate_base_turn_ids(nodes: list[dict], load_doc: Callable[[str], dict]) -> list[str]:
    """Turni grezzi della base, risolti da benchmark_turn_id del doc Redis."""

    ids: list[str] = []
    for n in _sorted_by_position(nodes):
        if n.get("source") != "conversation":
            continue
        doc = load_doc(n["id"]) if n.get("kind") == "memory" else {}
        turn = doc.get("benchmark_turn_id")
        if turn:
            ids.append(str(turn))
    return list(dict.fromkeys(ids))


def hydrate_locator_notes(
    nodes: list[dict], load_doc: Callable[[str], dict], *, q: int = FROZEN_POLICY["Q_notes"]
) -> list[list[str]]:
    """Note passive (primi ``q`` nodi source=passive) idratate a source turns reali."""

    notes: list[list[str]] = []
    for n in _sorted_by_position(nodes):
        if n.get("source") != "passive":
            continue
        doc = load_doc(n["id"]) if n.get("kind") == "memory" else {}
        evidence = [str(t) for t in (doc.get("benchmark_evidence_turn_ids") or [])]
        if not evidence:
            temporal = doc.get("temporal_context") or {}
            evidence = [str(t) for t in (temporal.get("source_turn_ids") or [])]
        notes.append(evidence)
        if len(notes) >= q:
            break
    return notes


def locators_from_nodes(
    nodes: list[dict], *, q: int = FROZEN_POLICY["Q_notes"]
) -> list[list[str]]:
    """Primi ``q`` nodi source=passive che GIÀ portano evidence_turn_ids.

    Usato per i report salvati (dove ``_retrieval_trace`` ha già idratato i nodi)
    e per la riproduzione dev. Nel worker live i nodi grezzi non hanno gli
    evidence: si usa invece ``hydrate_locator_notes`` dai documenti Redis.
    """

    passive = [n for n in _sorted_by_position(nodes) if n.get("source") == "passive"]
    return [[str(t) for t in (n.get("evidence_turn_ids") or [])] for n in passive[:q]]


def build_turn_renderer(case) -> Callable[[str], str]:
    """turn_id -> "Speaker: testo italiano". Il testo delle NOTE non è mai usato."""

    rendered = {t.turn_id: f"{t.speaker}: {t.text}" for t in case.turns}

    def render(turn_id: str) -> str:
        if turn_id not in rendered:
            raise KeyError(f"turno sorgente assente dal corpus localizzato: {turn_id}")
        return rendered[turn_id]

    return render


# --------------------------------------------------------------------------- #
# Dry-run strutturale (nessun Redis, nessun LLM)
# --------------------------------------------------------------------------- #
def structural_dry_run(*, source: Path = DEFAULT_SOURCE, output: Path | None = None) -> dict:
    census = build_census(source)
    fc = forecast(census, source=source)
    base_text = "OwnerUser: ciao\nAssistant: salve"
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=2,
        base_turn_ids=["D1:1", "D1:2"],
        locator_notes=[["D5:3"], ["D6:1"]],
        render_turn=lambda t: f"Speaker: contenuto di {t}",
    )
    # smoke dell'idratazione con doc finti
    fake_docs = {
        "raw-1": {"benchmark_turn_id": "D1:1"},
        "pas-1": {"benchmark_evidence_turn_ids": ["D5:3"]},
    }
    nodes = [
        {"id": "raw-1", "kind": "memory", "source": "conversation", "position": 0},
        {"id": "pas-1", "kind": "memory", "source": "passive", "position": 1},
    ]
    hydrated_base = hydrate_base_turn_ids(nodes, lambda i: fake_docs.get(i, {}))
    hydrated_loc = hydrate_locator_notes(nodes, lambda i: fake_docs.get(i, {}))
    invariants = {
        "base_preserved_prefix": comp.final_context_text.startswith(base_text),
        "final_slots_le_base_plus_2": comp.final_slots <= comp.base_slots + 2,
        "final_chars_equals_len": comp.final_chars == len(comp.final_context_text),
        "hydration_reconstructs_base": hydrated_base == ["D1:1"],
        "hydration_reconstructs_locators": hydrated_loc == [["D5:3"]],
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
# Worker stadiato reale (subprocess, ambiente isolato) — NON invocato in audit
# --------------------------------------------------------------------------- #
class DualChannelError(RuntimeError):
    pass


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_doc_fn(redis_client):
    def load(node_id: str) -> dict:
        raw = redis_client.json().get(f"euri:memory:{node_id}", "$")
        return raw[0] if raw else {}
    return load


def _node_view(n: dict, load_doc) -> dict:
    doc = load_doc(n["id"]) if n.get("kind") == "memory" else {}
    return {
        "id": n.get("id"),
        "source": n.get("source"),
        "position": n.get("position"),
        "retrieval_path": n.get("retrieval_path"),
        "benchmark_turn_id": doc.get("benchmark_turn_id"),
        "benchmark_evidence_turn_ids": list(doc.get("benchmark_evidence_turn_ids") or []),
    }


def _worker_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--localization", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation-order", default="rag_only,dual_channel")
    parser.add_argument("--answer-seed", type=int, default=42)
    parser.add_argument("--run-label", default=None)
    parser.add_argument("--manifest-sha256", default=None)
    parser.add_argument("--selection-manifest-sha256", default=None)
    parser.add_argument("--localization-sha256", default=None)
    parser.add_argument("--localization-id", default=None)
    args = parser.parse_args()

    order = tuple(a.strip() for a in args.generation_order.split(",") if a.strip())
    if sorted(order) != ["dual_channel", "rag_only"]:
        parser.error("--generation-order deve contenere rag_only e dual_channel")

    from benchmarks.euri_memory.live_worker import (
        LLMCallTracker,
        _git_metadata,
        _ingest_passive,
        _ingest_raw_turns,
        _patch_chat_clients,
        _reset_database,
        _require_isolated_environment,
        _response_content,
        _sha256_file,
    )
    from benchmarks.euri_memory.scorers import score_locomo_reduced

    runtime_id = _require_isolated_environment()

    import config
    from core.brain import Brain
    from core.embedder import Embedder, _MODEL_NAME
    from core.memory_manager import MemoryManager
    from core.ollama_client import chat_client
    from core.rag_context import build_rag_context
    from utils.redis_client import get_client

    cases = LoCoMoAdapter().load(args.source)
    case = BenchmarkSelection.load(args.selection).apply(cases)
    case = BenchmarkLocalization.load(args.localization).apply(case)
    language = str(case.metadata.get("language") or "en")
    if language != "it":
        raise DualChannelError("worker dual-channel richiede localizzazione italiana")

    redis_client = get_client()
    tracker = LLMCallTracker(chat_client)
    _patch_chat_clients(tracker)
    embedder = Embedder()
    embedder.load()
    load_doc = _load_doc_fn(redis_client)
    render_turn = build_turn_renderer(case)
    prompts = [q.prompt() for q in case.questions]
    model = config.OLLAMA_MODEL

    # STADIO 1: raw-only -> base testuale + turni idratati + SHA (conservati).
    _reset_database(redis_client, runtime_id)
    memory = MemoryManager(redis_client, embedder=embedder)
    _ingest_raw_turns(redis_client, embedder, case.corpus())
    base_records: dict[str, dict] = {}
    for prompt in prompts:
        with tracker.phase("base_retrieval"):
            base = build_rag_context(prompt.text, memory, mode="search")
        base_text = base.text
        base_records[prompt.question_id] = {
            "text": base_text,
            "sha256": _sha256(base_text),
            "slots": len(base.nodes),
            "turn_ids": hydrate_base_turn_ids(base.nodes, load_doc),
            "nodes": [_node_view(n, load_doc) for n in base.nodes],
        }

    # STADIO 2: raw+passive -> locator idratati (conservati).
    _reset_database(redis_client, runtime_id)
    memory = MemoryManager(redis_client, embedder=embedder)
    brain = Brain()
    with tracker.phase("passive_ingestion"):
        passive_stats = _ingest_passive(
            memory, brain, case.corpus(), tracker,
            selection_id=str(case.metadata.get("selection_id") or ""),
        )
    _ingest_raw_turns(redis_client, embedder, case.corpus())
    locator_records: dict[str, dict] = {}
    for prompt in prompts:
        with tracker.phase("locator_retrieval"):
            mixed = build_rag_context(prompt.text, memory, mode="search")
        locator_records[prompt.question_id] = {
            "notes": hydrate_locator_notes(mixed.nodes, load_doc),
            "nodes": [_node_view(n, load_doc) for n in mixed.nodes if n.get("source") == "passive"],
        }

    # STADIO 3: composizione dei contesti dual sulla base GIÀ SALVATA.
    compositions: dict[str, Any] = {}
    for prompt in prompts:
        b = base_records[prompt.question_id]
        comp = compose_dual_channel(
            base_context_text=b["text"],
            base_slots=b["slots"],
            base_turn_ids=b["turn_ids"],
            locator_notes=locator_records[prompt.question_id]["notes"],
            render_turn=render_turn,
        )
        if comp.base_sha256 != b["sha256"] or not comp.final_context_text.startswith(b["text"]):
            raise DualChannelError(f"base divergente per {prompt.question_id}: fail-closed")
        compositions[prompt.question_id] = comp

    # STADIO 4: generazione A/B sull'ordine controbilanciato, contesti immutabili.
    rag_results, dual_results = [], []
    for prompt in prompts:
        b = base_records[prompt.question_id]
        comp = compositions[prompt.question_id]
        contexts = {"rag_only": b["text"], "dual_channel": comp.final_context_text}
        recalled = {"rag_only": b["turn_ids"], "dual_channel": comp.final_turn_ids}
        answers = {}
        for arm in order:
            phase = "answer_rag" if arm == "rag_only" else "answer_dual"
            with tracker.phase(phase):
                resp = tracker.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": _ANSWER_SYSTEM_IT},
                        {"role": "user", "content": _user_prompt(case, prompt, contexts[arm])},
                    ],
                    options={"temperature": 0, "num_predict": 160, "seed": args.answer_seed},
                    think=False,
                )
            answers[arm] = _response_content(resp)
        rag_results.append(QuestionResult(
            question_id=prompt.question_id, answer=answers["rag_only"],
            recalled_turn_ids=tuple(recalled["rag_only"]),
            metadata={"base_sha256": b["sha256"]},
        ))
        dual_results.append(QuestionResult(
            question_id=prompt.question_id, answer=answers["dual_channel"],
            recalled_turn_ids=tuple(recalled["dual_channel"]),
            metadata={"composition": comp.to_record()},
        ))

    # SCORING (qui, e solo qui, gold ed evidence tornano in vista).
    arms = [
        {"name": "rag_only", "results": rag_results},
        {"name": "dual_channel", "results": dual_results},
    ]
    arm_reports = []
    for arm in arms:
        scoring = score_locomo_reduced(case.questions, arm["results"])
        arm_reports.append({
            "arm": arm["name"],
            "results": [asdict(r) for r in arm["results"]],
            "scoring": scoring,
        })

    report = {
        "schema_version": 1,
        "benchmark": "euri_dual_channel_validation",
        "policy_id": POLICY_ID,
        "created_at": time.time(),
        "git": _git_metadata(),
        "dataset": {
            "name": case.dataset, "sample_id": case.sample_id,
            "source_sha256": _sha256_file(args.source),
        },
        "selection": {
            "selection_sha256": _sha256_file(args.selection),
            "question_ids": [q.question_id for q in case.questions],
        },
        "run": {"run_label": args.run_label, "generation_order": list(order), "answer_seed": args.answer_seed},
        "binding": {
            "manifest_sha256": args.manifest_sha256,
            "selection_manifest_sha256": args.selection_manifest_sha256,
            "localization_sha256": args.localization_sha256,
            "localization_id": args.localization_id,
            "language": language,
        },
        "models": {"answer_and_cognition": model, "embedder": _MODEL_NAME, "answer_seed": args.answer_seed},
        "gold_boundary": {
            "retrieval_used_only_prompts": True,
            "generation_used_only_prompts": True,
            "scorer_runs_after_generation": True,
        },
        "llm_by_phase": tracker.summary(),
        "ingest": {"passive": passive_stats},
        "base_nodes_by_question": {q: base_records[q]["nodes"] for q in base_records},
        "locator_nodes_by_question": {q: locator_records[q]["nodes"] for q in locator_records},
        "arms": arm_reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({"event": "dual_report_written", "path": str(args.output)}), flush=True)
    return 0


def _user_prompt(case, prompt, context_text: str) -> str:
    return (
        f"Partecipanti: {case.speakers[0]} e {case.speakers[1]}.\n\n"
        f"Contesto di memoria:\n{context_text or '(nessuna memoria rilevante)'}\n\n"
        f"Domanda: {prompt.text}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_worker_main())
