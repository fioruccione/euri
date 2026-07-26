"""Comandi ripetibili del banco prova memoria."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.profiles import get_profile
from benchmarks.euri_memory.runners import run_smoke
from benchmarks.euri_memory.runtime import IsolatedRuntime
from benchmarks.euri_memory.selection import BenchmarkSelection


ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURE = ROOT / "fixtures" / "locomo_smoke.json"
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
DEFAULT_SELECTION = ROOT / "fixtures" / "locomo_reduced_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--source", type=Path, default=DEFAULT_FIXTURE)
    smoke.add_argument("--profile", default="rag_only")
    smoke.add_argument("--limit", type=int)
    smoke.add_argument("--output", type=Path)
    ab = subparsers.add_parser("ab")
    ab.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ab.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    ab.add_argument("--localization", type=Path)
    ab.add_argument("--output", type=Path, required=True)
    ab.add_argument("--timeout", type=int, default=7200)

    # --- Valutazione indipendente held-out (preregistrata) ---
    hs = subparsers.add_parser("heldout-select")
    # --seed è OBBLIGATORIO e senza default: il campione non esiste finché non
    # viene fornito un seed dopo il commit del protocollo.
    hs.add_argument("--seed", type=int, required=True)
    hs.add_argument("--budget", required=True)
    hs.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    hs.add_argument("--output", type=Path, required=True)

    hr = subparsers.add_parser("heldout-run")
    hr.add_argument("--manifest", type=Path, required=True)
    hr.add_argument("--output-dir", type=Path, required=True)
    hr.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    hr.add_argument("--dry-run", action="store_true")
    hr.add_argument("--seconds-per-call", type=float, default=3.0)
    hr.add_argument("--per-pair-timeout", type=int, default=21_600)

    ha = subparsers.add_parser("heldout-analyze")
    ha.add_argument("--results-dir", type=Path, required=True)
    ha.add_argument("--manifest", type=Path)
    ha.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()

    if args.command == "smoke":
        cases = tuple(LoCoMoAdapter().load(args.source))
        if args.limit is not None:
            if args.limit < 1:
                parser.error("--limit deve essere positivo")
            cases = cases[: args.limit]
        with IsolatedRuntime() as runtime:
            report_path = run_smoke(runtime, cases, profile=get_profile(args.profile))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(report_path, args.output)
        print(json.dumps(report["summary"], sort_keys=True))
        return 0
    if args.command == "ab":
        if args.timeout < 1:
            parser.error("--timeout deve essere positivo")
        cases = tuple(LoCoMoAdapter().load(args.source))
        selection = BenchmarkSelection.load(args.selection)
        case = selection.apply(cases)
        owner_name, assistant_name = case.speakers
        with IsolatedRuntime() as runtime:
            assert runtime.report_dir is not None
            worker_report = runtime.report_dir / "locomo-reduced-ab.json"
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
                    str(args.source.resolve()),
                    "--selection",
                    str(args.selection.resolve()),
                    *(
                        ["--localization", str(args.localization.resolve())]
                        if args.localization
                        else []
                    ),
                    "--output",
                    str(worker_report),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                check=True,
                timeout=args.timeout,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(worker_report, args.output)
            report = json.loads(worker_report.read_text(encoding="utf-8"))
        print(json.dumps(report["comparison"]["metric_delta"], sort_keys=True))
        return 0
    if args.command == "heldout-select":
        from benchmarks.euri_memory.heldout import (
            HeldoutError,
            build_manifest,
            write_manifest,
        )

        try:
            manifest = build_manifest(
                seed=args.seed,
                budget_name=args.budget,
                corpus_path=args.source,
            )
        except HeldoutError as exc:
            parser.error(str(exc))
        write_manifest(manifest, args.output)
        print(
            json.dumps(
                {
                    "manifest_sha256": manifest["manifest_sha256"],
                    "budget": manifest["budget"]["name"],
                    "seed": manifest["seed"],
                    "conversations": [c["sample_id"] for c in manifest["conversations"]],
                    "n_independent": manifest["n_independent"],
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "heldout-run":
        from benchmarks.euri_memory.heldout_runner import BudgetExceeded, RunnerError, run_all
        from benchmarks.euri_memory.integrity import IntegrityError

        try:
            result = run_all(
                manifest_path=args.manifest,
                output_dir=args.output_dir,
                corpus_path=args.source,
                dry_run=args.dry_run,
                seconds_per_call=args.seconds_per_call,
                per_pair_timeout=args.per_pair_timeout,
            )
        except BudgetExceeded as exc:
            print(json.dumps({"event": "budget_exceeded", "detail": str(exc)}), flush=True)
            return 3
        except (RunnerError, IntegrityError) as exc:
            print(json.dumps({"event": "integrity_error", "detail": str(exc)}), flush=True)
            return 4
        if args.dry_run:
            print(json.dumps(result["forecast"]["estimated_llm_calls"], sort_keys=True))
        else:
            print(
                json.dumps(
                    {
                        "pairs_completed": result["pairs_completed"],
                        "pairs_planned": result["pairs_planned"],
                        "cumulative_llm_calls": result["cumulative_llm_calls"],
                    },
                    sort_keys=True,
                )
            )
        return 0
    if args.command == "heldout-analyze":
        from benchmarks.euri_memory.analysis import AnalysisError, analyze

        try:
            report = analyze(args.results_dir, args.manifest)
        except AnalysisError as exc:
            print(json.dumps({"event": "analysis_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "n_conversations": report["n_conversations"],
                    "pairs_complete": report["pairs_complete"],
                    "underpowered": report["power"]["underpowered"],
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
