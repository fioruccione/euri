"""Persistent, local calibration profile for visual social signals.

The profile contains only numeric thresholds and pose summaries. It never stores
camera frames and it is deliberately separate from FaceAuth: expression tuning
must not weaken the identity boundary.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = 1
REQUIRED_PHASES = (
    "relaxed_neutral",
    "upright_neutral",
    "relaxed_smile",
    "upright_smile",
)


@dataclass(frozen=True)
class SocialCalibrationProfile:
    actor_id: str
    created_at: float
    thresholds: dict[str, float]
    posture: dict[str, dict[str, float]]
    diagnostics: dict[str, float]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def profile_path(actor_id: str, directory: str | Path) -> Path:
    safe_actor = "".join(ch for ch in actor_id.lower() if ch.isalnum() or ch in "_-")
    if not safe_actor:
        raise ValueError("actor_id non valido")
    return Path(directory) / f"{safe_actor}.json"


def _numbers(
    samples: Sequence[Mapping[str, Any]], group: str, key: str
) -> np.ndarray:
    values = []
    for sample in samples:
        try:
            value = float(sample.get(group, {}).get(key))
        except (TypeError, ValueError, AttributeError):
            continue
        if np.isfinite(value):
            values.append(value)
    return np.asarray(values, dtype=float)


def _pose_summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in ("head_pitch_deg", "head_yaw_deg", "head_roll_deg"):
        values = _numbers(samples, "auxiliary_metrics", key)
        if values.size:
            result[key] = round(float(np.median(values)), 3)
    return result


def derive_profile(
    actor_id: str,
    phases: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    created_at: float | None = None,
) -> SocialCalibrationProfile:
    """Derive conservative smile thresholds from four labelled pose phases.

    The first unique snapshot of every phase is discarded because the runtime
    median still contains frames from the preceding pose. If the neutral and
    smile distributions overlap, calibration fails closed instead of inventing
    a threshold.
    """
    missing = [name for name in REQUIRED_PHASES if len(phases.get(name, ())) < 4]
    if missing:
        raise ValueError(f"campioni insufficienti: {', '.join(missing)}")

    trimmed = {name: list(phases[name])[1:] for name in REQUIRED_PHASES}
    neutral_samples = trimmed["relaxed_neutral"] + trimmed["upright_neutral"]
    smile_samples = trimmed["relaxed_smile"] + trimmed["upright_smile"]
    neutral = _numbers(neutral_samples, "metrics", "smile")
    smile = _numbers(smile_samples, "metrics", "smile")
    if neutral.size < 6 or smile.size < 6:
        raise ValueError("campioni sorriso insufficienti dopo la stabilizzazione")

    neutral_hi = float(np.percentile(neutral, 95))
    smile_lo = float(np.percentile(smile, 25))
    smile_mid = float(np.percentile(smile, 50))
    margin = smile_lo - neutral_hi
    if margin < 0.025:
        raise ValueError(
            "sorriso lieve e volto neutro non sono separabili; ripetere con luce e posa stabili"
        )

    entry = max(0.08, min(0.45, neutral_hi + margin * 0.55))
    stay = max(0.04, min(entry - 0.02, entry * 0.68))
    marked_entry = max(0.42, min(0.75, smile_mid + 0.24))
    marked_stay = max(entry, marked_entry - 0.12)

    thresholds = {
        "smile_entry": round(entry, 4),
        "smile_stay": round(stay, 4),
        "smile_marked_entry": round(marked_entry, 4),
        "smile_marked_stay": round(marked_stay, 4),
    }
    posture = {
        name: _pose_summary(trimmed[name])
        for name in REQUIRED_PHASES
    }
    diagnostics = {
        "neutral_p95": round(neutral_hi, 4),
        "smile_p25": round(smile_lo, 4),
        "smile_p50": round(smile_mid, 4),
        "separation_margin": round(margin, 4),
        "neutral_samples": int(neutral.size),
        "smile_samples": int(smile.size),
    }
    return SocialCalibrationProfile(
        actor_id=actor_id,
        created_at=float(time.time() if created_at is None else created_at),
        thresholds=thresholds,
        posture=posture,
        diagnostics=diagnostics,
    )


def save_profile(profile: SocialCalibrationProfile, path: str | Path) -> Path:
    """Atomically persist a numeric profile so the daemon never reads half a file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile.to_dict(), ensure_ascii=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return target


def load_profile(path: str | Path, *, actor_id: str | None = None) -> SocialCalibrationProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = SocialCalibrationProfile(
        actor_id=str(raw["actor_id"]),
        created_at=float(raw["created_at"]),
        thresholds={str(k): float(v) for k, v in raw["thresholds"].items()},
        posture={
            str(name): {str(k): float(v) for k, v in values.items()}
            for name, values in raw.get("posture", {}).items()
        },
        diagnostics={str(k): float(v) for k, v in raw.get("diagnostics", {}).items()},
        schema_version=int(raw.get("schema_version", 0)),
    )
    if profile.schema_version != SCHEMA_VERSION:
        raise ValueError("versione profilo sociale non supportata")
    if actor_id is not None and profile.actor_id != actor_id:
        raise ValueError("profilo sociale attribuito a un altro attore")
    required = {
        "smile_entry", "smile_stay", "smile_marked_entry", "smile_marked_stay"
    }
    if not required.issubset(profile.thresholds):
        raise ValueError("profilo sociale incompleto")
    if not (
        0.0 < profile.thresholds["smile_stay"]
        < profile.thresholds["smile_entry"]
        <= profile.thresholds["smile_marked_stay"]
        < profile.thresholds["smile_marked_entry"]
        <= 1.0
    ):
        raise ValueError("soglie del profilo sociale non valide")
    return profile
