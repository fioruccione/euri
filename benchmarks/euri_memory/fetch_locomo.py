"""Acquisisce LoCoMo dalla fonte ufficiale e registra la provenienza."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter


ROOT = Path(__file__).resolve().parent
DEFAULT_DESTINATION = ROOT / "data"
UPSTREAM_COMMIT = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
RAW_ROOT = f"https://raw.githubusercontent.com/snap-research/locomo/{UPSTREAM_COMMIT}"
FILES = {
    "locomo10.json": f"{RAW_ROOT}/data/locomo10.json",
    "LICENSE.txt": f"{RAW_ROOT}/LICENSE.txt",
}


def _download(url: str, destination: Path) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Euri-memory-benchmark/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    destination.write_bytes(content)
    return {
        "url": url,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def fetch(destination: Path = DEFAULT_DESTINATION) -> Path:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest_files = {}
    with tempfile.TemporaryDirectory(prefix="locomo-download-") as tmp:
        temporary = Path(tmp)
        for name, url in FILES.items():
            metadata = _download(url, temporary / name)
            manifest_files[name] = metadata
        # La validazione avviene prima di sostituire la copia locale.
        cases = LoCoMoAdapter().load(temporary / "locomo10.json")
        if not cases:
            raise RuntimeError("LoCoMo ufficiale non contiene casi")
        for name in FILES:
            (temporary / name).replace(destination / name)

    manifest = {
        "schema_version": 1,
        "dataset": "LoCoMo",
        "source_repository": "https://github.com/snap-research/locomo",
        "source_commit": UPSTREAM_COMMIT,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "cases": len(cases),
        "files": manifest_files,
    }
    manifest_path = destination / "source_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    manifest = fetch(args.destination)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
