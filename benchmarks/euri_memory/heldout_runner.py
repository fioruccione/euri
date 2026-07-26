"""Runner A/B appaiato sul campione held-out, con checkpoint/resume e forecast.

Unità atomica: la coppia ``(conversazione, replica)``. Un solo processo
``live_worker`` isolato esegue entrambi i bracci sulla stessa selezione, quindi
la coppia entra nell'analisi soltanto se il suo report contiene entrambi i
profili completi. Un fallimento non viene sostituito con un altro caso: la
coppia resta incompleta e si può riprendere.

L'arresto dipende solo da errori tecnici o dal superamento dei cap preregistrati
(chiamate LLM, tempo). Le metriche non influenzano mai l'arresto.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.heldout import (
    _SPEAKER_MAPPING,
    get_budget,
    verify_manifest,
)


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]

# Finestre dell'estrattore passivo: 12 messaggi, overlap 4 (passo 8).
_WINDOW_SIZE = 12
_WINDOW_OVERLAP = 4
# Banda storica (README): l'ingestione passiva è costata ~0,7–1,8 chiamate LLM
# per turno (36–92 chiamate su 51 turni). È una stima, non una garanzia.
_INGEST_CALLS_PER_TURN = (0.7, 1.8)
# Banda di chiamate per domanda (retrieval + eventuale gating + risposta).
_ANSWER_CALLS_PER_QUESTION = (1.0, 2.0)
_DEFAULT_SECONDS_PER_CALL = 3.0


class RunnerError(RuntimeError):
    pass


class BudgetExceeded(RunnerError):
    pass


@dataclass(frozen=True)
class RunSpec:
    sample_id: str
    replica_index: int
    branch_order: tuple[str, ...]
    answer_seed: int
    session_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    speakers: tuple[str, str]

    @property
    def key(self) -> str:
        return f"{self.sample_id}__r{self.replica_index}"


def load_and_verify_manifest(path: Path) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_manifest(manifest)
    return manifest


def _windows_for_session(turn_count: int) -> int:
    if turn_count <= _WINDOW_SIZE:
        return 1 if turn_count > 0 else 0
    step = _WINDOW_SIZE - _WINDOW_OVERLAP
    return math.ceil((turn_count - _WINDOW_SIZE) / step) + 1


def plan_runs(manifest: dict, corpus_path: Path) -> list[RunSpec]:
    """Espande il manifest nelle coppie (conversazione, replica) da eseguire."""

    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    specs: list[RunSpec] = []
    for conversation in manifest["conversations"]:
        sample_id = conversation["sample_id"]
        case = cases.get(sample_id)
        if case is None:
            raise RunnerError(f"conversazione del manifest assente dal corpus: {sample_id}")
        speakers = (case.speakers[0], case.speakers[1])
        for replica in manifest["replicas"]:
            specs.append(
                RunSpec(
                    sample_id=sample_id,
                    replica_index=int(replica["replica_index"]),
                    branch_order=tuple(replica["branch_order"]),
                    answer_seed=int(replica["answer_seed"]),
                    session_ids=tuple(conversation["session_ids"]),
                    question_ids=tuple(conversation["question_ids"]),
                    speakers=speakers,
                )
            )
    return specs


def cost_forecast(
    manifest: dict,
    corpus_path: Path,
    *,
    seconds_per_call: float = _DEFAULT_SECONDS_PER_CALL,
) -> dict:
    """Previsione di costo puramente strutturale: nessun risultato cognitivo.

    Legge solo conteggi (sessioni, turni per sessione, domande). Parte è esatta
    (sessioni, finestre di estrazione, domande); le chiamate LLM e il tempo sono
    stime con banda dichiarata.
    """

    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    replicas = len(manifest["replicas"])
    per_conversation = []
    total_windows = 0
    total_turns = 0
    total_questions_runs = 0
    calls_low = 0.0
    calls_high = 0.0
    for conversation in manifest["conversations"]:
        sample_id = conversation["sample_id"]
        case = cases[sample_id]
        turns = len(case.turns)
        windows = sum(_windows_for_session(len(s.turns)) for s in case.sessions)
        questions = conversation["question_count"]
        # Per replica: ingestione passiva (solo braccio passive) + risposte su
        # entrambi i bracci. Il braccio rag_only non ingerisce passivamente.
        ingest_low = turns * _INGEST_CALLS_PER_TURN[0]
        ingest_high = turns * _INGEST_CALLS_PER_TURN[1]
        answer_low = questions * _ANSWER_CALLS_PER_QUESTION[0] * 2
        answer_high = questions * _ANSWER_CALLS_PER_QUESTION[1] * 2
        conv_calls_low = (ingest_low + answer_low) * replicas
        conv_calls_high = (ingest_high + answer_high) * replicas
        per_conversation.append(
            {
                "sample_id": sample_id,
                "sessions": len(case.sessions),
                "turns": turns,
                "extraction_windows_per_replica": windows,
                "questions_per_replica": questions,
                "estimated_llm_calls_low": round(conv_calls_low),
                "estimated_llm_calls_high": round(conv_calls_high),
            }
        )
        total_windows += windows * replicas
        total_turns += turns * replicas
        total_questions_runs += questions * replicas * 2
        calls_low += conv_calls_low
        calls_high += conv_calls_high

    budget = manifest["budget"]
    return {
        "note": "Stima strutturale, nessun modello avviato. Le chiamate LLM e il "
        "tempo sono bande, non garanzie.",
        "assumptions": {
            "window_size": _WINDOW_SIZE,
            "window_overlap": _WINDOW_OVERLAP,
            "ingest_calls_per_turn": list(_INGEST_CALLS_PER_TURN),
            "answer_calls_per_question": list(_ANSWER_CALLS_PER_QUESTION),
            "seconds_per_call": seconds_per_call,
        },
        "replicas": replicas,
        "pairs_total": len(manifest["conversations"]) * replicas,
        "exact": {
            "total_extraction_windows": total_windows,
            "total_ingested_turns": total_turns,
            "total_question_runs": total_questions_runs,
        },
        "estimated_llm_calls": {
            "low": round(calls_low),
            "high": round(calls_high),
        },
        "estimated_seconds": {
            "low": round(calls_low * seconds_per_call),
            "high": round(calls_high * seconds_per_call),
        },
        "estimated_hours": {
            "low": round(calls_low * seconds_per_call / 3600, 2),
            "high": round(calls_high * seconds_per_call / 3600, 2),
        },
        "budget_caps": {
            "max_llm_calls": budget["max_llm_calls"],
            "max_seconds": budget["max_seconds"],
        },
        "forecast_exceeds_call_cap": calls_high > budget["max_llm_calls"],
        "per_conversation": per_conversation,
    }


def _write_selection(spec: RunSpec, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.sample_id}.json"
    if not path.exists():
        payload = {
            "schema_version": 1,
            "selection_id": f"heldout-{spec.sample_id}",
            "dataset": "locomo",
            "sample_id": spec.sample_id,
            "session_ids": list(spec.session_ids),
            "question_ids": list(spec.question_ids),
            "speaker_mapping": dict(_SPEAKER_MAPPING),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    return path


def _pair_report_is_complete(report: dict) -> bool:
    """Vera solo se entrambi i bracci hanno prodotto uno scoring."""

    profiles = {item.get("profile", {}).get("name"): item for item in report.get("profiles", [])}
    if {"rag_only", "passive_memory"} - set(profiles):
        return False
    return all("scoring" in profiles[name] for name in ("rag_only", "passive_memory"))


def _run_pair(
    spec: RunSpec,
    *,
    source: Path,
    selection_path: Path,
    runs_dir: Path,
    timeout: int,
) -> Path:
    from benchmarks.euri_memory.runtime import IsolatedRuntime

    report_path = runs_dir / f"{spec.key}.json"
    owner_name, assistant_name = spec.speakers
    with IsolatedRuntime() as runtime:
        assert runtime.report_dir is not None
        worker_report = runtime.report_dir / f"{spec.key}.json"
        environment = dict(os.environ)
        environment.update(runtime.environment())
        environment.update(
            {
                "EURI_OWNER_DISPLAY_NAME": owner_name,
                "EURI_ASSISTANT_DISPLAY_NAME": assistant_name,
                "EURI_OWNER_ACTOR_ID": f"locomo-{owner_name.lower()}",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
            }
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmarks.euri_memory.live_worker",
                "--source",
                str(source.resolve()),
                "--selection",
                str(selection_path.resolve()),
                "--branch-order",
                ",".join(spec.branch_order),
                "--answer-seed",
                str(spec.answer_seed),
                "--run-label",
                spec.key,
                "--output",
                str(worker_report),
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            timeout=timeout,
        )
        report = json.loads(worker_report.read_text(encoding="utf-8"))
        if not _pair_report_is_complete(report):
            raise RunnerError(f"coppia {spec.key}: report incompleto, non registrata")
        runs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(worker_report, report_path)
    return report_path


def _report_llm_calls(report: dict) -> int:
    return sum(
        int(item.get("llm", {}).get("calls", 0)) for item in report.get("profiles", [])
    )


def _report_elapsed_ms(report: dict) -> float:
    return sum(float(item.get("elapsed_ms", 0.0)) for item in report.get("profiles", []))


def _load_checkpoint(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"completed": {}, "cumulative_llm_calls": 0, "cumulative_elapsed_ms": 0.0}


def _save_checkpoint(path: Path, checkpoint: dict) -> None:
    path.write_text(
        json.dumps(checkpoint, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def run_all(
    *,
    manifest_path: Path,
    output_dir: Path,
    corpus_path: Path,
    dry_run: bool = False,
    seconds_per_call: float = _DEFAULT_SECONDS_PER_CALL,
    per_pair_timeout: int = 21_600,
) -> dict:
    manifest = load_and_verify_manifest(manifest_path)
    budget = get_budget(manifest["budget"]["name"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copia verificata del manifest + forecast, sempre (anche in dry-run).
    shutil.copy2(manifest_path, output_dir / "manifest.json")
    forecast = cost_forecast(manifest, corpus_path, seconds_per_call=seconds_per_call)
    (output_dir / "forecast.json").write_text(
        json.dumps(forecast, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    specs = plan_runs(manifest, corpus_path)
    selections_dir = output_dir / "selections"
    runs_dir = output_dir / "runs"
    checkpoint_path = output_dir / "checkpoint.json"

    plan = [
        {
            "key": spec.key,
            "sample_id": spec.sample_id,
            "replica_index": spec.replica_index,
            "branch_order": list(spec.branch_order),
            "answer_seed": spec.answer_seed,
            "questions": len(spec.question_ids),
            "sessions": len(spec.session_ids),
        }
        for spec in specs
    ]

    if dry_run:
        # Nessun modello: valida le selezioni, prova l'isolamento di UN ambiente
        # effimero, stampa piano e forecast.
        for spec in specs:
            _write_selection(spec, selections_dir)
        isolation_ok = _probe_isolation()
        result = {
            "mode": "dry_run",
            "manifest_sha256": manifest["manifest_sha256"],
            "budget": manifest["budget"],
            "isolation_probe_ok": isolation_ok,
            "plan": plan,
            "forecast": forecast,
        }
        (output_dir / "dry_run.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return result

    checkpoint = _load_checkpoint(checkpoint_path)
    completed: dict[str, Any] = dict(checkpoint.get("completed", {}))
    cumulative_calls = int(checkpoint.get("cumulative_llm_calls", 0))
    cumulative_ms = float(checkpoint.get("cumulative_elapsed_ms", 0.0))

    for spec in specs:
        if spec.key in completed and (runs_dir / f"{spec.key}.json").is_file():
            print(json.dumps({"event": "pair_skip_completed", "key": spec.key}), flush=True)
            continue
        # Cap preregistrati: arresto tecnico, mai guidato dalle metriche.
        if cumulative_calls >= budget.max_llm_calls:
            raise BudgetExceeded(
                f"cap chiamate LLM superato: {cumulative_calls} ≥ {budget.max_llm_calls}"
            )
        if cumulative_ms / 1000.0 >= budget.max_seconds:
            raise BudgetExceeded(
                f"cap tempo superato: {cumulative_ms / 1000.0:.0f}s ≥ {budget.max_seconds}s"
            )
        selection_path = _write_selection(spec, selections_dir)
        print(json.dumps({"event": "pair_start", "key": spec.key}), flush=True)
        report_path = _run_pair(
            spec,
            source=corpus_path,
            selection_path=selection_path,
            runs_dir=runs_dir,
            timeout=per_pair_timeout,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        cumulative_calls += _report_llm_calls(report)
        cumulative_ms += _report_elapsed_ms(report)
        completed[spec.key] = {
            "report": str(report_path.relative_to(output_dir)),
            "sample_id": spec.sample_id,
            "replica_index": spec.replica_index,
        }
        checkpoint = {
            "completed": completed,
            "cumulative_llm_calls": cumulative_calls,
            "cumulative_elapsed_ms": cumulative_ms,
        }
        _save_checkpoint(checkpoint_path, checkpoint)
        print(json.dumps({"event": "pair_complete", "key": spec.key}), flush=True)

    return {
        "mode": "run",
        "manifest_sha256": manifest["manifest_sha256"],
        "pairs_planned": len(specs),
        "pairs_completed": len(completed),
        "cumulative_llm_calls": cumulative_calls,
        "cumulative_elapsed_ms": cumulative_ms,
        "output_dir": str(output_dir),
    }


def _probe_isolation() -> bool:
    """Avvia e distrugge un runtime effimero per provare le guardie d'isolamento."""

    from benchmarks.euri_memory.runtime import IsolatedRuntime

    with IsolatedRuntime() as runtime:
        assert runtime.port is not None and runtime.port != 6379
        env = runtime.environment()
        assert env["EURI_REDIS_PORT"] != "6379"
        assert env["EURI_BENCHMARK_MODE"] == "1"
        runtime.client.set("euri:benchmark:probe", "1")
        runtime.reset()
        return runtime.client.get("euri:benchmark:probe") is None
