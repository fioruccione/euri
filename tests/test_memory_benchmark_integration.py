#!/usr/bin/env python3
"""Smoke integration del runtime Redis effimero del benchmark memoria."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.profiles import get_profile
from benchmarks.euri_memory.runners import run_smoke
from benchmarks.euri_memory.runtime import IsolatedRuntime


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "euri_memory" / "fixtures" / "locomo_smoke.json"
OFFICIAL = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"


def test_runtime_smoke_uses_ephemeral_redis_and_vault():
    cases = LoCoMoAdapter().load(FIXTURE)
    with tempfile.TemporaryDirectory() as parent:
        runtime_root = None
        with IsolatedRuntime(base_dir=Path(parent)) as runtime:
            runtime_root = runtime.root
            assert runtime_root is not None and runtime_root.is_dir()
            assert runtime.port != 6379
            assert runtime.vault_dir is not None
            assert runtime.vault_dir.is_relative_to(runtime_root)

            runtime.client.set("euri:benchmark:test", "present")
            runtime.reset()
            assert runtime.client.get("euri:benchmark:test") is None

            report_path = run_smoke(runtime, cases, profile=get_profile("rag_only"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["runtime"]["isolated"] is True
            assert report["summary"] == {
                "cases": 1,
                "questions_run": 3,
                "trace_events": 7,
                "turns_ingested": 4,
            }
            assert runtime.client.dbsize() > 1

            env = os.environ.copy()
            env.update(runtime.environment())
            probe = subprocess.run(
                [
                    os.sys.executable,
                    "-c",
                    (
                        "import json, config; "
                        "print(json.dumps([config.REDIS_HOST, config.REDIS_PORT, "
                        "config.REDIS_DB, config.OBSIDIAN_VAULT_PATH]))"
                    ),
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            configured = json.loads(probe.stdout)
            assert configured == [
                "127.0.0.1",
                runtime.port,
                0,
                str(runtime.vault_dir),
            ]
        assert runtime_root is not None and not runtime_root.exists()


def test_heldout_dry_run_forecasts_without_models():
    if not OFFICIAL.is_file():
        return
    from benchmarks.euri_memory.heldout import build_manifest, write_manifest
    from benchmarks.euri_memory.heldout_runner import run_all

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        manifest = build_manifest(seed=99, budget_name="smoke", git_commit="dry")
        manifest_path = write_manifest(manifest, work / "manifest.json")
        result = run_all(
            manifest_path=manifest_path,
            output_dir=work / "out",
            corpus_path=OFFICIAL,
            dry_run=True,
        )
        assert result["mode"] == "dry_run"
        assert result["isolation_probe_ok"] is True
        assert len(result["plan"]) == len(manifest["conversations"]) * len(
            manifest["replicas"]
        )
        assert result["forecast"]["estimated_llm_calls"]["high"] >= 1
        # nessun report cognitivo generato in dry-run
        assert not (work / "out" / "runs").exists()
        assert (work / "out" / "forecast.json").is_file()


if __name__ == "__main__":
    test_runtime_smoke_uses_ephemeral_redis_and_vault()
    test_heldout_dry_run_forecasts_without_models()
    print("test_memory_benchmark_integration: OK")
