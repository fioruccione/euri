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

    # Traduzione italiana automatica delle SOLE conversazioni selezionate.
    hl = subparsers.add_parser("heldout-localize")
    hl.add_argument("--selection-manifest", type=Path, required=True)
    hl.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    hl.add_argument("--output", type=Path, required=True)
    hl.add_argument("--model", default=None)
    hl.add_argument("--model-version", default=None)
    hl.add_argument("--dry-run", action="store_true")
    hl.add_argument("--seconds-per-call", type=float, default=2.0)

    # Manifest finale derivato: lega selezione + protocollo + localization SHA.
    hf = subparsers.add_parser("heldout-finalize")
    hf.add_argument("--selection-manifest", type=Path, required=True)
    hf.add_argument("--localization", type=Path, required=True)
    hf.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    hf.add_argument("--output", type=Path, required=True)

    hr = subparsers.add_parser("heldout-run")
    hr.add_argument("--manifest", type=Path, required=True)
    hr.add_argument("--output-dir", type=Path, required=True)
    hr.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    hr.add_argument("--localization", type=Path)
    hr.add_argument("--dry-run", action="store_true")
    hr.add_argument("--seconds-per-call", type=float, default=3.0)
    hr.add_argument("--per-pair-timeout", type=int, default=21_600)

    ha = subparsers.add_parser("heldout-analyze")
    ha.add_argument("--results-dir", type=Path, required=True)
    # --manifest OBBLIGATORIO: l'analisi held-out è sempre legata al manifest.
    ha.add_argument("--manifest", type=Path, required=True)
    ha.add_argument("--output", type=Path, required=True)

    # Dual-channel: census + forecast + invarianti, senza LLM né Redis.
    dd = subparsers.add_parser("dual-dry-run")
    dd.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    dd.add_argument("--output", type=Path, required=True)

    dm = subparsers.add_parser("dual-manifest")
    # --seed OBBLIGATORIO: non seleziona domande (census invariato), fissa in modo
    # riproducibile gli answer_seed e il braccio iniziale.
    dm.add_argument("--seed", type=int, required=True)
    dm.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    dm.add_argument("--replicas", type=int, default=2)
    dm.add_argument("--output", type=Path, required=True)

    dl = subparsers.add_parser("dual-localize")
    dl.add_argument("--selection-manifest", type=Path, required=True)
    dl.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    dl.add_argument("--output", type=Path, required=True)
    dl.add_argument("--model", default=None)
    dl.add_argument("--model-version", default=None)
    dl.add_argument("--dry-run", action="store_true")

    df = subparsers.add_parser("dual-finalize")
    df.add_argument("--selection-manifest", type=Path, required=True)
    df.add_argument("--localization", type=Path, required=True)
    df.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    df.add_argument("--output", type=Path, required=True)

    dr = subparsers.add_parser("dual-run")
    dr.add_argument("--manifest", type=Path, required=True)
    dr.add_argument("--localization", type=Path)
    dr.add_argument("--output-dir", type=Path, required=True)
    dr.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    dr.add_argument("--dry-run", action="store_true")

    da = subparsers.add_parser("dual-analyze")
    da.add_argument("--results-dir", type=Path, required=True)
    da.add_argument("--manifest", type=Path, required=True)
    da.add_argument("--output", type=Path, required=True)

    # --- Prompt ablation v2 (development) ---
    _VAL = ROOT.parents[1] / "audit_output" / "dual_channel_validation_v1_seed396895560"
    _CASE_MANIFEST = ROOT / "prompt_ablation_v2_manifest.json"
    adr = subparsers.add_parser("ablation-dry-run")
    adr.add_argument("--case-manifest", type=Path, default=_CASE_MANIFEST)
    adr.add_argument("--validation-root", type=Path, default=_VAL / "run")
    adr.add_argument("--output", type=Path, required=True)

    aem = subparsers.add_parser("ablation-exec-manifest")
    aem.add_argument("--case-manifest", type=Path, default=_CASE_MANIFEST)
    aem.add_argument("--corpus", type=Path, default=DEFAULT_SOURCE)
    aem.add_argument("--localization", type=Path, default=_VAL / "localization_it.json")
    aem.add_argument("--validation-root", type=Path, default=_VAL / "run")
    aem.add_argument("--output", type=Path, required=True)

    arn = subparsers.add_parser("ablation-run")
    arn.add_argument("--execution-manifest", type=Path, required=True)
    arn.add_argument("--validation-root", type=Path, default=_VAL / "run")
    arn.add_argument("--output-dir", type=Path, required=True)
    arn.add_argument("--capture-dir", type=Path, required=True)
    arn.add_argument("--execute", action="store_true")
    arn.add_argument("--model", default="gemma4:26b")
    arn.add_argument("--model-digest", default=None)

    aan = subparsers.add_parser("ablation-analyze")
    aan.add_argument("--output-runs", type=Path, required=True)
    aan.add_argument("--execution-manifest", type=Path, required=True)
    aan.add_argument("--localization", type=Path, default=_VAL / "localization_it.json")
    aan.add_argument("--corpus", type=Path, default=DEFAULT_SOURCE)
    aan.add_argument("--validation-root", type=Path, default=_VAL / "run")
    aan.add_argument("--output", type=Path, required=True)

    aau = subparsers.add_parser("ablation-audit")
    aau.add_argument("--output-runs", type=Path, required=True)
    aau.add_argument("--localization", type=Path, default=_VAL / "localization_it.json")
    aau.add_argument("--corpus", type=Path, default=DEFAULT_SOURCE)
    aau.add_argument("--output-rows", type=Path, required=True)
    aau.add_argument("--output-key", type=Path, required=True)

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
    if args.command == "heldout-localize":
        import json as _json

        from benchmarks.euri_memory.heldout import verify_manifest
        from benchmarks.euri_memory.heldout_localization import (
            LocalizationError,
            build_selected_localization,
            checkpointed_translator,
            localization_forecast,
            ollama_translator,
            translation_protocol,
            verify_selected_localization,
        )

        selection_manifest = json.loads(
            args.selection_manifest.read_text(encoding="utf-8")
        )
        verify_manifest(selection_manifest)
        if args.dry_run:
            forecast = localization_forecast(
                selection_manifest, args.source, seconds_per_call=args.seconds_per_call
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                _json.dumps(forecast, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            print(_json.dumps(forecast, sort_keys=True))
            return 0
        import config

        model = args.model or config.OLLAMA_MODEL
        try:
            # Idempotenza: un artefatto finale già valido viene riusato senza
            # rigenerare built_at/SHA. Durante la prima generazione, invece, un
            # checkpoint incrementale evita di perdere ore su errori transitori.
            if args.output.is_file():
                localization = json.loads(args.output.read_text(encoding="utf-8"))
                verify_selected_localization(
                    localization, args.source, selection_manifest
                )
            else:
                cache_path = args.output.with_name(
                    args.output.name + ".translation-checkpoint.json"
                )
                translator = checkpointed_translator(
                    ollama_translator(model),
                    checkpoint_path=cache_path,
                    identity={
                        "selection_manifest_sha256": selection_manifest[
                            "manifest_sha256"
                        ],
                        "corpus_sha256": selection_manifest["corpus"]["sha256"],
                        "translation_protocol": translation_protocol(),
                        "model": model,
                        "model_version": args.model_version,
                    },
                )
                localization = build_selected_localization(
                    corpus_path=args.source,
                    selection_manifest=selection_manifest,
                    translate_fn=translator,
                    model=model,
                    model_version=args.model_version,
                )
                verify_selected_localization(
                    localization, args.source, selection_manifest
                )
        except LocalizationError as exc:
            print(json.dumps({"event": "localization_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            _json.dumps(localization, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "localization_sha256": localization["localization_sha256"],
                    "language": localization["language"],
                    "conversations": localization["selected_sample_ids"],
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "heldout-finalize":
        from benchmarks.euri_memory.heldout import build_final_manifest, write_manifest
        from benchmarks.euri_memory.heldout_localization import LocalizationError

        selection_manifest = json.loads(
            args.selection_manifest.read_text(encoding="utf-8")
        )
        localization = json.loads(args.localization.read_text(encoding="utf-8"))
        try:
            final = build_final_manifest(
                selection_manifest, localization, corpus_path=args.source
            )
        except LocalizationError as exc:
            print(json.dumps({"event": "finalize_error", "detail": str(exc)}), flush=True)
            return 4
        write_manifest(final, args.output)
        print(
            json.dumps(
                {
                    "manifest_sha256": final["manifest_sha256"],
                    "stage": final["stage"],
                    "language": final["language"],
                    "localization_sha256": final["localization"]["localization_sha256"],
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
                localization_path=args.localization,
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
    if args.command == "dual-dry-run":
        from benchmarks.euri_memory.dual_channel_worker import structural_dry_run

        result = structural_dry_run(source=args.source, output=args.output)
        print(
            json.dumps(
                {
                    "all_invariants_ok": result["all_invariants_ok"],
                    "census_totals": result["census_totals"],
                    "estimated_llm_calls": result["forecast"]["estimated_llm_calls"],
                    "estimated_hours": result["forecast"]["estimated_hours"],
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "dual-manifest":
        from benchmarks.euri_memory.dual_channel_pipeline import (
            DualPipelineError,
            build_census_manifest,
        )

        try:
            manifest = build_census_manifest(
                seed=args.seed, corpus_path=args.source, replicas=args.replicas
            )
        except DualPipelineError as exc:
            print(json.dumps({"event": "dual_manifest_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({
            "manifest_sha256": manifest["manifest_sha256"],
            "conversations": [c["sample_id"] for c in manifest["conversations"]],
            "eligible": sum(c["question_count"] for c in manifest["conversations"]),
            "output": str(args.output),
        }, sort_keys=True))
        return 0
    if args.command == "dual-localize":
        from benchmarks.euri_memory.heldout import verify_manifest
        from benchmarks.euri_memory.heldout_localization import (
            LocalizationError,
            build_selected_localization,
            localization_forecast,
            ollama_translator,
            verify_selected_localization,
        )

        selection_manifest = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        verify_manifest(selection_manifest)
        if args.dry_run:
            fc = localization_forecast(selection_manifest, args.source)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(fc, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(fc, sort_keys=True))
            return 0
        import config

        model = args.model or config.OLLAMA_MODEL
        try:
            localization = build_selected_localization(
                corpus_path=args.source, selection_manifest=selection_manifest,
                translate_fn=ollama_translator(model), model=model, model_version=args.model_version,
            )
            verify_selected_localization(localization, args.source, selection_manifest)
        except LocalizationError as exc:
            print(json.dumps({"event": "localization_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(localization, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"localization_sha256": localization["localization_sha256"],
                          "output": str(args.output)}, sort_keys=True))
        return 0
    if args.command == "dual-finalize":
        from benchmarks.euri_memory.dual_channel_pipeline import build_dual_final_manifest
        from benchmarks.euri_memory.heldout_localization import LocalizationError

        selection_manifest = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        localization = json.loads(args.localization.read_text(encoding="utf-8"))
        try:
            final = build_dual_final_manifest(selection_manifest, localization, corpus_path=args.source)
        except LocalizationError as exc:
            print(json.dumps({"event": "finalize_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(final, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"manifest_sha256": final["manifest_sha256"], "stage": final["stage"],
                          "output": str(args.output)}, sort_keys=True))
        return 0
    if args.command == "dual-run":
        from benchmarks.euri_memory.dual_channel_pipeline import DualPipelineError, run_all
        from benchmarks.euri_memory.integrity import IntegrityError

        try:
            result = run_all(
                manifest_path=args.manifest, localization_path=args.localization,
                output_dir=args.output_dir, corpus_path=args.source, dry_run=args.dry_run,
            )
        except (DualPipelineError, IntegrityError) as exc:
            print(json.dumps({"event": "dual_run_error", "detail": str(exc)}), flush=True)
            return 4
        if args.dry_run:
            print(json.dumps(result["forecast"]["estimated_llm_calls"], sort_keys=True))
        else:
            print(json.dumps({"pairs_completed": result["pairs_completed"],
                              "pairs_planned": result["pairs_planned"]}, sort_keys=True))
        return 0
    if args.command == "dual-analyze":
        from benchmarks.euri_memory.dual_channel_pipeline import DualPipelineError, analyze

        try:
            report = analyze(args.results_dir, args.manifest)
        except DualPipelineError as exc:
            print(json.dumps({"event": "dual_analysis_error", "detail": str(exc)}), flush=True)
            return 4
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({"n_conversations": report["n_conversations"],
                          "underpowered": report["power"]["underpowered"],
                          "output": str(args.output)}, sort_keys=True))
        return 0
    if args.command in {"ablation-dry-run", "ablation-exec-manifest", "ablation-run",
                        "ablation-analyze", "ablation-audit"}:
        from benchmarks.euri_memory import prompt_ablation_v2 as PA

        if args.command == "ablation-dry-run":
            m = json.loads(args.case_manifest.read_text(encoding="utf-8"))
            res = PA.dry_run_materialize(case_manifest=m, validation_root=args.validation_root)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            print(json.dumps({"materialized": res["materialized_and_verified"],
                              "non_reconstructible": res["non_reconstructible_count"],
                              "byte_exact_ok": res["byte_exact_ok"]}, sort_keys=True))
            return 0 if res["byte_exact_ok"] else 5  # non-zero se non tutti byte-esatti
        if args.command == "ablation-exec-manifest":
            m = json.loads(args.case_manifest.read_text(encoding="utf-8"))
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            ex = PA.build_execution_manifest(m, experimental_code_commit=head, corpus_path=args.corpus,
                                             localization_path=args.localization,
                                             validation_runs_dir=args.validation_root / "runs")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(ex, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            print(json.dumps({"manifest_sha256": ex["manifest_sha256"], "git_commit": ex["git_commit"],
                              "output": str(args.output)}, sort_keys=True))
            return 0
        if args.command == "ablation-run":
            ex = json.loads(args.execution_manifest.read_text(encoding="utf-8"))
            try:
                res = PA.run_ablation(execution_manifest=ex, validation_root=args.validation_root,
                                      output_dir=args.output_dir, capture_dir=args.capture_dir,
                                      execute=args.execute, model=args.model, model_digest=args.model_digest)
            except PA.AblationError as exc:
                print(json.dumps({"event": "ablation_error", "detail": str(exc)}), flush=True)
                return 4
            print(json.dumps(res, sort_keys=True))
            return 0
        if args.command == "ablation-analyze":
            ex = json.loads(args.execution_manifest.read_text(encoding="utf-8"))
            gold = PA.build_gold_lookup(args.localization, args.corpus)
            rep = PA.analyze(output_runs=args.output_runs, gold_lookup=gold,
                             validation_root=args.validation_root, execution_manifest=ex)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(rep, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            print(json.dumps({"output": str(args.output)}, sort_keys=True))
            return 0
        if args.command == "ablation-audit":
            gold = PA.build_gold_lookup(args.localization, args.corpus)
            au = PA.blind_audit_export(output_runs=args.output_runs, gold_lookup=gold)
            args.output_rows.parent.mkdir(parents=True, exist_ok=True)
            args.output_rows.write_text(json.dumps(au["rows"], indent=2, ensure_ascii=False), encoding="utf-8")
            args.output_key.write_text(json.dumps(au["key"], indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"rows": len(au["rows"]), "rows_out": str(args.output_rows),
                              "key_out": str(args.output_key)}, sort_keys=True))
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
