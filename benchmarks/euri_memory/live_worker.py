"""Worker A/B reale, avviato solo dentro un runtime benchmark isolato.

Il processo separato è intenzionale: ``config`` e i client Redis/Ollama di Euri
leggono l'ambiente all'import. Il parent crea prima Redis e Vault temporanei,
poi avvia questo modulo con quell'ambiente già fissato.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

import dateparser

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.contracts import (
    BenchmarkCase,
    ConversationCorpus,
    MemoryProfile,
    QuestionResult,
)
from benchmarks.euri_memory.profiles import get_profile
from benchmarks.euri_memory.scorers import score_locomo_reduced
from benchmarks.euri_memory.selection import BenchmarkSelection
from benchmarks.euri_memory.localization import BenchmarkLocalization


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
DEFAULT_SELECTION = ROOT / "fixtures" / "locomo_reduced_v1.json"
_RUNTIME_MARKER_KEY = "euri:benchmark:runtime_id"
_ANSWER_SYSTEM_EN = """\
You answer a long-term conversation-memory benchmark.
Use only the supplied memory context. Do not use outside knowledge and do not
infer an answer from the wording of the question. If the context does not
support an answer, answer exactly: I don't know.
Answer in English, concisely, without an explanation."""
_ANSWER_SYSTEM_IT = """\
Stai rispondendo a un benchmark sulla memoria conversazionale a lungo termine.
Usa soltanto il contesto di memoria fornito. Non usare conoscenze esterne e non
dedurre la risposta dalla formulazione della domanda. Se il contesto non
supporta una risposta, rispondi esattamente: Non lo so.
Rispondi in italiano, in modo conciso e senza spiegazioni."""


def _require_isolated_environment() -> str:
    if os.environ.get("EURI_BENCHMARK_MODE") != "1":
        raise RuntimeError("worker rifiutato: EURI_BENCHMARK_MODE non attivo")
    runtime_id = os.environ.get("EURI_BENCHMARK_RUNTIME_ID", "").strip()
    if not runtime_id:
        raise RuntimeError("worker rifiutato: runtime id mancante")
    if os.environ.get("EURI_REDIS_HOST") != "127.0.0.1":
        raise RuntimeError("worker rifiutato: Redis non è loopback")
    if os.environ.get("EURI_REDIS_PORT") in {"", "6379", None}:
        raise RuntimeError("worker rifiutato: porta Redis personale o mancante")
    return runtime_id


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_metadata() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        tracked = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {
            "commit": commit,
            "worktree_dirty": bool(status.strip()),
            # Solo file tracciati: i report non tracciati/ignorati non sporcano.
            "worktree_tracked_dirty": bool(tracked.strip()),
            "worktree_status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "commit": None,
            "worktree_dirty": None,
            "worktree_tracked_dirty": None,
            "worktree_status_sha256": None,
        }


def _timestamp(value: str | None, *, offset: int = 0) -> float:
    parsed = dateparser.parse(
        value or "",
        languages=["en"],
        settings={
            "TIMEZONE": "Europe/Rome",
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        raise ValueError(f"timestamp LoCoMo non interpretabile: {value!r}")
    return (parsed + timedelta(seconds=offset)).timestamp()


def _response_content(response: Any) -> str:
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    text = str(content or "")
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _response_metric(response: Any, name: str) -> int:
    value = getattr(response, name, None)
    if value is None and isinstance(response, dict):
        value = response.get(name)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class LLMCallTracker:
    """Proxy trasparente che registra costi locali senza salvare i prompt."""

    def __init__(self, client: Any):
        self._client = client
        self._phase = "unclassified"
        self.calls: list[dict[str, Any]] = []

    @contextmanager
    def phase(self, name: str):
        previous = self._phase
        self._phase = name
        try:
            yield
        finally:
            self._phase = previous

    def chat(self, *args, **kwargs):
        messages = kwargs.get("messages")
        if messages is None and len(args) > 1:
            messages = args[1]
        prompt_text = "\n".join(
            str(item.get("content") or "")
            for item in (messages or [])
            if isinstance(item, dict)
        )
        started = time.perf_counter()
        response = self._client.chat(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.calls.append(
            {
                "phase": self._phase,
                "model": kwargs.get("model") or (args[0] if args else None),
                "elapsed_ms": round(elapsed_ms, 3),
                "prompt_chars": len(prompt_text),
                "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
                "prompt_eval_count": _response_metric(response, "prompt_eval_count"),
                "eval_count": _response_metric(response, "eval_count"),
            }
        )
        return response

    def summary(self, *, start_at: int = 0) -> dict[str, Any]:
        calls = self.calls[start_at:]
        return {
            "calls": len(calls),
            "elapsed_ms": round(sum(item["elapsed_ms"] for item in calls), 3),
            "prompt_eval_count": sum(item["prompt_eval_count"] for item in calls),
            "eval_count": sum(item["eval_count"] for item in calls),
            "by_phase": {
                phase: sum(1 for item in calls if item["phase"] == phase)
                for phase in sorted({item["phase"] for item in calls})
            },
            "trace": calls,
        }


def _patch_chat_clients(tracker: LLMCallTracker) -> None:
    import core.brain as brain_module
    import core.domain_gater as domain_module
    import core.ollama_client as client_module
    import core.validator as validator_module

    client_module.chat_client = tracker
    brain_module.chat_client = tracker
    domain_module.chat_client = tracker
    validator_module.chat_client = tracker


def _reset_database(redis_client: Any, runtime_id: str) -> None:
    from utils.redis_client import init_indexes

    redis_client.flushdb()
    redis_client.set(_RUNTIME_MARKER_KEY, runtime_id)
    if redis_client.get(_RUNTIME_MARKER_KEY) != runtime_id:
        raise RuntimeError("reset Redis senza marker valido")
    init_indexes(redis_client)


def _raw_turn_document(corpus: ConversationCorpus, turn: Any, embedding: list[float]) -> dict:
    turn_index = next(
        index for index, item in enumerate(corpus.turns) if item.turn_id == turn.turn_id
    )
    asserted_at = _timestamp(turn.session_timestamp, offset=turn_index)
    return {
        "id": f"raw-{corpus.sample_id}-{turn.turn_id.replace(':', '-')}",
        "content": f"{turn.speaker}: {turn.text}",
        "category": "benchmark_conversation",
        "source": "conversation",
        "domain": "conversation",
        "memory_kind": "conversation_turn",
        "created_at": asserted_at,
        "asserted_at": asserted_at,
        "event_start": None,
        "event_end": None,
        "due_at": None,
        "expires_at": None,
        "recalled_count": 0,
        "last_recalled_at": None,
        "requires_verification": False,
        "tags": ["benchmark", "locomo"],
        "embedding": embedding,
        "temporal_context": {
            "schema_version": 1,
            "asserted_at": asserted_at,
            "source_turn_ids": [turn.turn_id],
        },
        "memory_axes": {},
        "safety_flag": [],
        "benchmark_dataset": corpus.dataset,
        "benchmark_sample_id": corpus.sample_id,
        "benchmark_session_id": turn.session_id,
        "benchmark_turn_id": turn.turn_id,
        "benchmark_speaker": turn.speaker,
    }


def _ingest_raw_turns(redis_client: Any, embedder: Any, corpus: ConversationCorpus) -> dict:
    started = time.perf_counter()
    for turn in corpus.turns:
        content = f"{turn.speaker}: {turn.text}"
        vector = embedder.encode(content, mode="passage")
        if vector is None:
            raise RuntimeError(f"embedding fallito per {turn.turn_id}")
        doc = _raw_turn_document(corpus, turn, vector.tolist())
        redis_client.json().set(f"euri:memory:{doc['id']}", "$", doc)
    return {
        "turns": len(corpus.turns),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _session_messages(session: Any, start_seq: int) -> tuple[list[dict], dict[int, str], int]:
    messages = []
    turn_ids: dict[int, str] = {}
    seq = start_seq
    for offset, turn in enumerate(session.turns):
        seq += 1
        turn_ids[seq] = turn.turn_id
        messages.append(
            {
                "role": "user" if turn.speaker_role == "speaker_a" else "assistant",
                "speaker": turn.speaker,
                "content": turn.text,
                "seq": seq,
                "observed_at": _timestamp(session.timestamp, offset=offset),
                "conversation_id": session.session_id,
                "segment_id": int(session.session_id.rsplit("_", 1)[-1]),
            }
        )
    return messages, turn_ids, seq


def _ingest_passive(
    memory: Any,
    brain: Any,
    corpus: ConversationCorpus,
    tracker: LLMCallTracker,
    *,
    selection_id: str,
) -> dict:
    from core.memory_attention import remove_loop2e_candidate
    from core.temporal_context import derive_passive_memory_metadata
    from core.validator import validate_passive_payload

    stats = {
        "sessions": len(corpus.sessions),
        "extracted": 0,
        "validated": 0,
        "rejected": 0,
        "provenance_rejected": 0,
        "provenance_repaired": 0,
        "duplicates": 0,
        "saved": 0,
        "temporal_corrections": 0,
        "saved_ids": [],
        "saved_memories": [],
    }
    seq = 0
    started = time.perf_counter()
    for session in corpus.sessions:
        messages, source_ids, seq = _session_messages(session, seq)
        with tracker.phase("passive_extract"):
            items = brain.extract_passive_memories(messages) or []
        for item in items:
            stats["extracted"] += 1
            content = item.get("content", "") if isinstance(item, dict) else str(item)
            fact_item = item if isinstance(item, dict) else {"content": content}
            with tracker.phase("passive_validate"):
                clean = validate_passive_payload(content)
            if not clean:
                stats["rejected"] += 1
                continue
            audit_candidate = (
                dict(fact_item)
                if isinstance(fact_item, dict)
                else {
                    "content": clean,
                    "memory_kind": "semantic_fact",
                    "source_turn_ids": [],
                }
            )
            audit_candidate["content"] = clean
            with tracker.phase("passive_provenance"):
                audited_item = brain.audit_passive_memory_provenance(
                    audit_candidate,
                    messages,
                )
            if not audited_item:
                stats["rejected"] += 1
                stats["provenance_rejected"] += 1
                continue
            provenance_audit = dict(audited_item.get("provenance_audit") or {})
            if provenance_audit.get("repaired"):
                stats["provenance_repaired"] += 1
            metadata = derive_passive_memory_metadata(audited_item, messages)
            clean = metadata["canonical_content"]
            if metadata["temporal_context"].get("content_date_corrected"):
                stats["temporal_corrections"] += 1
            stats["validated"] += 1
            with tracker.phase("passive_dedup"):
                duplicate = memory.is_duplicate_memory(
                    clean,
                    llm_probe_fn=brain.probe_same_meaning,
                )
            if duplicate:
                stats["duplicates"] += 1
                continue
            evidence_ids = [
                source_ids[value]
                for value in metadata["temporal_context"].get("source_turn_ids", [])
                if value in source_ids
            ]
            weak = fact_item.get("support") == "weak"
            final_fields = {
                "benchmark_dataset": corpus.dataset,
                "benchmark_sample_id": corpus.sample_id,
                "benchmark_selection_id": selection_id,
                "benchmark_evidence_turn_ids": evidence_ids,
                "passive_provenance": provenance_audit,
            }
            if weak:
                final_fields.update(
                    {
                        "requires_verification": True,
                        "passive_support": "tacit_acceptance",
                    }
                )
            with tracker.phase("passive_save"):
                memory_id = memory.save_memory(
                    clean,
                    category="passivo",
                    source="passive",
                    idempotent=True,
                    memory_kind=metadata["memory_kind"],
                    temporal_context=metadata["temporal_context"],
                    final_fields=final_fields,
                )
            if memory_id:
                stats["saved"] += 1
                stats["saved_ids"].append(memory_id)
                raw = memory.r.json().get(f"euri:memory:{memory_id}", "$")
                saved_doc = raw[0] if raw else {}
                stats["saved_memories"].append(
                    {
                        "id": memory_id,
                        "content": saved_doc.get("content"),
                        "source_turn_ids": metadata["temporal_context"].get(
                            "source_turn_ids", []
                        ),
                        "evidence_turn_ids": evidence_ids,
                        "memory_kind": saved_doc.get("memory_kind"),
                        "domain": saved_doc.get("domain"),
                        "event_start": saved_doc.get("event_start"),
                        "event_end": saved_doc.get("event_end"),
                        "source_temporal_expression": (
                            saved_doc.get("temporal_context") or {}
                        ).get("source_temporal_expression"),
                        "content_original_date": (
                            saved_doc.get("temporal_context") or {}
                        ).get("content_original_date"),
                        "content_date_corrected": (
                            saved_doc.get("temporal_context") or {}
                        ).get("content_date_corrected"),
                        "passive_support": saved_doc.get("passive_support"),
                        "requires_verification": saved_doc.get("requires_verification"),
                        "passive_provenance": saved_doc.get("passive_provenance"),
                    }
                )
                if weak or metadata["memory_kind"] == "conversation_anchor":
                    remove_loop2e_candidate(memory.r, memory_id)
    stats["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return stats


def _load_memory_doc(redis_client: Any, node_id: str) -> dict:
    raw = redis_client.json().get(f"euri:memory:{node_id}", "$")
    return raw[0] if raw else {}


def _retrieval_trace(redis_client: Any, nodes: Iterable[dict]) -> tuple[list[dict], tuple[str, ...]]:
    trace = []
    recalled: list[str] = []
    for node in nodes:
        doc = _load_memory_doc(redis_client, node["id"]) if node.get("kind") == "memory" else {}
        evidence = []
        raw_turn = doc.get("benchmark_turn_id")
        if raw_turn:
            evidence.append(str(raw_turn))
        evidence.extend(str(item) for item in doc.get("benchmark_evidence_turn_ids") or [])
        recalled.extend(evidence)
        trace.append(
            {
                "id": node.get("id"),
                "kind": node.get("kind"),
                "source": node.get("source"),
                "domain": node.get("domain"),
                "position": node.get("position"),
                "retrieval_path": node.get("retrieval_path"),
                "evidence_turn_ids": list(dict.fromkeys(evidence)),
                "content_sha256": hashlib.sha256(
                    str(node.get("content") or "").encode()
                ).hexdigest(),
            }
        )
    return trace, tuple(dict.fromkeys(recalled))


def _answer_questions(
    corpus: ConversationCorpus,
    questions: Iterable[Any],
    memory: Any,
    redis_client: Any,
    tracker: LLMCallTracker,
    model: str,
    answer_system: str,
    answer_seed: int,
) -> list[QuestionResult]:
    from core.rag_context import build_rag_context

    results = []
    for prompt in questions:
        started = time.perf_counter()
        with tracker.phase("retrieval"):
            rag = build_rag_context(prompt.text, memory, mode="search")
        trace, recalled = _retrieval_trace(redis_client, rag.nodes)
        user_prompt = (
            f"Conversation participants: {corpus.speakers[0]} and {corpus.speakers[1]}.\n\n"
            f"Memory context:\n{rag.text or '(no relevant memory found)'}\n\n"
            f"Question: {prompt.text}"
        )
        with tracker.phase("answer"):
            response = tracker.chat(
                model=model,
                messages=[
                    {"role": "system", "content": answer_system},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0, "num_predict": 160, "seed": answer_seed},
                think=False,
            )
        answer = _response_content(response)
        results.append(
            QuestionResult(
                question_id=prompt.question_id,
                answer=answer,
                recalled_turn_ids=recalled,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                metadata={
                    "retrieval_nodes": trace,
                    "rag_chars": len(rag.text),
                    "rag_sha256": hashlib.sha256(rag.text.encode()).hexdigest(),
                },
            )
        )
        print(
            json.dumps(
                {
                    "event": "question_complete",
                    "question_id": prompt.question_id,
                    "answer": answer,
                    "recalled_turns": len(recalled),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return results


def _database_stats(redis_client: Any) -> dict:
    source_counts: dict[str, int] = {}
    memories = 0
    for key in redis_client.scan_iter("euri:memory:*"):
        raw = redis_client.json().get(key, "$")
        if not raw:
            continue
        memories += 1
        source = str(raw[0].get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "keys": redis_client.dbsize(),
        "memories": memories,
        "memory_sources": dict(sorted(source_counts.items())),
        "used_memory_bytes": int(redis_client.info("memory").get("used_memory", 0)),
    }


def _run_profile(
    case: BenchmarkCase,
    profile: MemoryProfile,
    redis_client: Any,
    embedder: Any,
    tracker: LLMCallTracker,
    runtime_id: str,
    model: str,
    answer_system: str,
    answer_seed: int,
) -> dict:
    from core.brain import Brain
    from core.memory_manager import MemoryManager

    _reset_database(redis_client, runtime_id)
    memory = MemoryManager(redis_client, embedder=embedder)
    brain = Brain()
    calls_start = len(tracker.calls)
    started = time.perf_counter()
    passive_stats = None
    if profile.passive_memory:
        passive_stats = _ingest_passive(
            memory,
            brain,
            case.corpus(),
            tracker,
            selection_id=str(case.metadata.get("selection_id") or ""),
        )
    raw_stats = _ingest_raw_turns(redis_client, embedder, case.corpus())
    before_questions = _database_stats(redis_client)
    # Da qui in avanti il generatore riceve solo prompt neutrali: answer gold ed
    # evidence restano nell'oggetto BenchmarkQuestion usato più sotto dal scorer.
    results = _answer_questions(
        case.corpus(),
        tuple(question.prompt() for question in case.questions),
        memory,
        redis_client,
        tracker,
        model,
        answer_system,
        answer_seed,
    )
    scoring = score_locomo_reduced(case.questions, results)
    after_questions = _database_stats(redis_client)
    return {
        "profile": asdict(profile),
        "ingest": {
            "raw": raw_stats,
            "passive": passive_stats,
        },
        "database_before_questions": before_questions,
        "database_after_questions": after_questions,
        "results": [asdict(result) for result in results],
        "scoring": scoring,
        "llm": tracker.summary(start_at=calls_start),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _comparison(profile_reports: list[dict]) -> dict:
    by_name = {item["profile"]["name"]: item for item in profile_reports}
    baseline = by_name["rag_only"]
    treatment = by_name["passive_memory"]
    metrics = ("mean_token_f1", "exact_match", "adversarial_accuracy", "evidence_recall")
    return {
        "direction": "passive_memory_minus_rag_only",
        "metric_delta": {
            name: treatment["scoring"][name] - baseline["scoring"][name]
            for name in metrics
        },
        "per_question": [
            {
                "question_id": left["question_id"],
                "rag_only_answer": left["answer"],
                "passive_memory_answer": right["answer"],
                "answer_changed": left["answer"] != right["answer"],
                "rag_only_recalled_turn_ids": left["recalled_turn_ids"],
                "passive_memory_recalled_turn_ids": right["recalled_turn_ids"],
            }
            for left, right in zip(baseline["results"], treatment["results"], strict=True)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--localization", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    # Flag additivi: i default riproducono esattamente il comportamento storico.
    parser.add_argument(
        "--branch-order",
        default="rag_only,passive_memory",
        help="ordine di esecuzione dei bracci, separati da virgola",
    )
    parser.add_argument("--answer-seed", type=int, default=42)
    parser.add_argument("--run-label", default=None)
    # Legame crittografico col manifest finale (opzionali per compatibilità con
    # smoke/ab storici; l'held-out italiano li fornisce sempre).
    parser.add_argument("--manifest-sha256", default=None)
    parser.add_argument("--selection-manifest-sha256", default=None)
    parser.add_argument("--localization-sha256", default=None)
    parser.add_argument("--localization-id", default=None)
    parser.add_argument("--expected-language", default=None)
    args = parser.parse_args()

    branch_order = tuple(
        name.strip() for name in args.branch_order.split(",") if name.strip()
    )
    if sorted(branch_order) != ["passive_memory", "rag_only"]:
        parser.error(
            "--branch-order deve contenere esattamente rag_only e passive_memory"
        )

    runtime_id = _require_isolated_environment()

    # Import Euri solo dopo il controllo dell'ambiente isolato.
    import config
    from core.embedder import Embedder, _MODEL_NAME
    from core.ollama_client import chat_client
    from utils.redis_client import get_client

    cases = LoCoMoAdapter().load(args.source)
    selection = BenchmarkSelection.load(args.selection)
    case = selection.apply(cases)
    localization = None
    if args.localization:
        localization = BenchmarkLocalization.load(args.localization)
        case = localization.apply(case)
    language = str(case.metadata.get("language") or "en")
    if args.expected_language and language != args.expected_language:
        raise RuntimeError(
            f"lingua applicata {language!r} diversa dall'attesa {args.expected_language!r}"
        )
    answer_system = _ANSWER_SYSTEM_IT if language == "it" else _ANSWER_SYSTEM_EN
    redis_client = get_client()
    if redis_client.get(_RUNTIME_MARKER_KEY) != runtime_id:
        raise RuntimeError("worker rifiutato: marker Redis non corrispondente")

    tracker = LLMCallTracker(chat_client)
    _patch_chat_clients(tracker)
    embedder = Embedder()
    embed_started = time.perf_counter()
    embedder.load()
    embed_elapsed_ms = round((time.perf_counter() - embed_started) * 1000, 3)

    reports = []
    for profile_name in branch_order:
        print(json.dumps({"event": "profile_start", "profile": profile_name}), flush=True)
        reports.append(
            _run_profile(
                case,
                get_profile(profile_name),
                redis_client,
                embedder,
                tracker,
                runtime_id,
                config.OLLAMA_MODEL,
                answer_system,
                args.answer_seed,
            )
        )
        print(json.dumps({"event": "profile_complete", "profile": profile_name}), flush=True)

    source_manifest_path = args.source.parent / "source_manifest.json"
    source_manifest = (
        json.loads(source_manifest_path.read_text(encoding="utf-8"))
        if source_manifest_path.is_file()
        else None
    )
    report = {
        "schema_version": 2,
        "benchmark": "euri_memory_locomo_reduced_ab",
        "created_at": time.time(),
        "git": _git_metadata(),
        "dataset": {
            "name": case.dataset,
            "sample_id": case.sample_id,
            "source_path": str(args.source.resolve()),
            "source_sha256": _sha256_file(args.source),
            "source_manifest": source_manifest,
            "license_use": "LoCoMo CC BY-NC 4.0; benchmark non commerciale",
        },
        "selection": {
            "selection_id": selection.selection_id,
            "selection_path": str(args.selection.resolve()),
            "selection_sha256": _sha256_file(args.selection),
            "sessions": len(case.sessions),
            "turns": len(case.turns),
            "questions": len(case.questions),
            "question_ids": [question.question_id for question in case.questions],
            "speaker_mapping": dict(selection.speaker_mapping),
        },
        "localization": (
            {
                "localization_id": localization.localization_id,
                "language": localization.language,
                "source_language": localization.source_language,
                "path": str(args.localization.resolve()),
                "sha256": _sha256_file(args.localization),
            }
            if localization and args.localization
            else {
                "localization_id": None,
                "language": language,
                "source_language": language,
                "path": None,
                "sha256": None,
            }
        ),
        "runtime": {
            "isolated": True,
            "redis_host": config.REDIS_HOST,
            "redis_port": config.REDIS_PORT,
            "redis_db": config.REDIS_DB,
            "external_actions_disabled": os.environ.get("EURI_DISABLE_EXTERNAL_ACTIONS") == "1",
        },
        "models": {
            "answer_and_cognition": config.OLLAMA_MODEL,
            "ollama_host": config.CHAT_OLLAMA_HOST,
            "embedder": _MODEL_NAME,
            "embedder_load_ms": embed_elapsed_ms,
            "answer_temperature": 0,
            "answer_seed": args.answer_seed,
            "answer_system_sha256": hashlib.sha256(answer_system.encode()).hexdigest(),
        },
        "run": {
            "run_label": args.run_label,
            "branch_order": list(branch_order),
            "answer_seed": args.answer_seed,
        },
        # Legame crittografico col manifest finale (punto 4 dell'hardening).
        "binding": {
            "manifest_sha256": args.manifest_sha256,
            "selection_manifest_sha256": args.selection_manifest_sha256,
            "localization_sha256": args.localization_sha256,
            "localization_id": args.localization_id,
            "language": language,
        },
        "gold_boundary": {
            "ingest_received_questions": False,
            "runner_received_expected_answers": False,
            "runner_received_evidence": False,
            "scorer_runs_after_generation": True,
        },
        "profiles": reports,
        "comparison": _comparison(reports),
        # Inserito soltanto dopo entrambe le generazioni: rende il file
        # autosufficiente senza contaminare ingestione, retrieval o risposta.
        "evaluation_gold": [
            {
                "question_id": question.question_id,
                "question": question.text,
                "category": question.category,
                "expected_answer": question.expected_answer,
                "evidence_turn_ids": list(question.evidence_turn_ids),
                "adversarial_answer": question.metadata.get("adversarial_answer"),
            }
            for question in case.questions
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({"event": "report_written", "path": str(args.output)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
