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


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "benchmarks" / "euri_memory" / "fixtures" / "locomo_smoke.json"


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


if __name__ == "__main__":
    test_runtime_smoke_uses_ephemeral_redis_and_vault()
    print("test_memory_benchmark_integration: OK")
