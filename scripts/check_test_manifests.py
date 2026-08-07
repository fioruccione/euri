#!/usr/bin/env python3
"""Fallisce se un test in tests/ manca dai manifest o compare in piu' livelli."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from run_test_manifest import ROOT, load_manifest


def main() -> int:
    manifest_dir = ROOT / "tests" / "manifests"
    manifests = sorted(manifest_dir.glob("*.txt"))
    entries = [path.relative_to(ROOT).as_posix() for manifest in manifests for path in load_manifest(manifest)]
    counts = Counter(entries)
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    }
    listed = set(entries)
    duplicates = sorted(path for path, count in counts.items() if count > 1)
    missing = sorted(actual - listed)
    unknown = sorted(listed - actual)

    if duplicates or missing or unknown:
        if duplicates:
            print("Duplicati:", *duplicates, sep="\n- ")
        if missing:
            print("Non classificati:", *missing, sep="\n- ")
        if unknown:
            print("Entry inesistenti:", *unknown, sep="\n- ")
        return 1
    print(f"Manifest test completi: {len(actual)} file in {len(manifests)} livelli")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
