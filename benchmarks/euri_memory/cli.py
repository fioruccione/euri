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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
