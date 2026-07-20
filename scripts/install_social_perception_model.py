#!/usr/bin/env python3
"""Install the official MediaPipe Face Landmarker bundle into Euri models."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import requests


URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
TARGET = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> int:
    if TARGET.is_file() and digest(TARGET) == SHA256:
        print(f"Modello gia' presente e verificato: {TARGET}")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = TARGET.with_suffix(".task.part")
    try:
        with requests.get(URL, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for block in response.iter_content(1024 * 1024):
                    if block:
                        handle.write(block)
        actual = digest(temporary)
        if actual != SHA256:
            raise RuntimeError(f"SHA256 inatteso: {actual}")
        temporary.replace(TARGET)
        print(f"Modello installato e verificato: {TARGET}")
        return 0
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        print(f"Installazione fallita: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
