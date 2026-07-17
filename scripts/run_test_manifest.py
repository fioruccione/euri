#!/usr/bin/env python3
"""Esegue una lista esplicita di test-script con timeout e isolamento per processo."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_manifest(path: Path) -> list[Path]:
    manifest = path.resolve()
    if ROOT not in manifest.parents:
        raise ValueError(f"manifest fuori repository: {manifest}")

    tests = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        test_path = (ROOT / entry).resolve()
        if ROOT not in test_path.parents or not test_path.is_file():
            raise ValueError(f"test non valido nel manifest: {entry}")
        tests.append(test_path)
    return tests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--timeout", type=float, default=180.0, help="secondi per test")
    parser.add_argument("--list", action="store_true", help="mostra i test senza eseguirli")
    args = parser.parse_args()

    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    tests = load_manifest(manifest)
    if args.list:
        for test in tests:
            print(test.relative_to(ROOT))
        return 0

    tier = manifest.stem
    env = os.environ.copy()
    env["EURI_TEST_TIER"] = tier
    failures = []
    started_suite = time.monotonic()

    for index, test in enumerate(tests, start=1):
        relative = test.relative_to(ROOT)
        print(f"\n[{index}/{len(tests)}] {relative}", flush=True)
        started = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(test)],
                cwd=ROOT,
                env=env,
                timeout=args.timeout,
                check=False,
            )
            elapsed = time.monotonic() - started
            if result.returncode:
                failures.append(f"{relative} (exit {result.returncode})")
                print(f"FAIL {relative} [{elapsed:.1f}s]", flush=True)
            else:
                print(f"PASS {relative} [{elapsed:.1f}s]", flush=True)
        except subprocess.TimeoutExpired:
            failures.append(f"{relative} (timeout {args.timeout:g}s)")
            print(f"TIMEOUT {relative}", flush=True)

    elapsed_suite = time.monotonic() - started_suite
    print(f"\n{len(tests) - len(failures)}/{len(tests)} test passati in {elapsed_suite:.1f}s")
    if failures:
        print("Fallimenti:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
