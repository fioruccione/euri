"""Audit puro e read-only della baseline interocettiva hardware."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any, Iterable


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_hardware_baseline(
    samples: Iterable[Any],
    events: Iterable[Any] = (),
    *,
    expected_interval_s: float = 60.0,
    review_after_s: float = 72 * 3600,
    min_coverage: float = 0.70,
) -> dict[str, Any]:
    """Restituisce un report serializzabile; non decide alcun riflesso."""
    docs = [_payload(item) for item in samples]
    docs = [doc for doc in docs if doc.get("timestamp") is not None]
    docs.sort(key=lambda doc: float(doc["timestamp"]))
    if not docs:
        return {
            "status": "no_data",
            "ready_for_review": False,
            "sample_count": 0,
            "duration_s": 0.0,
            "coverage": 0.0,
            "sensors": {},
            "level_counts": {},
            "fault_samples": 0,
            "events": {},
            "representative_load": False,
        }

    started_at = float(docs[0]["timestamp"])
    ended_at = float(docs[-1]["timestamp"])
    duration_s = max(0.0, ended_at - started_at)
    interval = max(1.0, float(expected_interval_s))
    expected = max(1, int(duration_s / interval) + 1)
    coverage = min(1.0, len(docs) / expected)

    values: dict[str, list[float]] = defaultdict(list)
    levels = Counter()
    fault_samples = 0
    gpu_busy_peak = 0.0
    vram_peak = 0.0
    for doc in docs:
        for sensor, value in (doc.get("readings") or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values[str(sensor)].append(number)
                if str(sensor).endswith("_vram"):
                    vram_peak = max(vram_peak, number)
        for level in (doc.get("levels") or {}).values():
            levels[str(level)] += 1
        if doc.get("faults"):
            fault_samples += 1
        for gpu in ((doc.get("metrics") or {}).get("gpus") or []):
            try:
                gpu_busy_peak = max(gpu_busy_peak, float(gpu.get("util_percent") or 0))
            except (TypeError, ValueError):
                pass

    sensor_stats = {
        sensor: {
            "count": len(series),
            "min": round(min(series), 3),
            "p50": round(_percentile(series, 0.50), 3),
            "p95": round(_percentile(series, 0.95), 3),
            "max": round(max(series), 3),
        }
        for sensor, series in sorted(values.items())
        if series
    }
    event_counts = Counter()
    for item in events:
        event = _payload(item)
        event_counts[str(event.get("kind") or event.get("level") or "unknown")] += 1

    enough_time = duration_s >= max(0.0, float(review_after_s))
    enough_coverage = coverage >= min(1.0, max(0.0, float(min_coverage)))
    ready = enough_time and enough_coverage
    status = "ready_for_review" if ready else ("insufficient_coverage" if enough_time else "collecting")

    return {
        "status": status,
        "ready_for_review": ready,
        "started_at": started_at,
        "ended_at": ended_at,
        "sample_count": len(docs),
        "expected_samples": expected,
        "duration_s": round(duration_s, 3),
        "coverage": round(coverage, 4),
        "sensors": sensor_stats,
        "level_counts": dict(levels),
        "fault_samples": fault_samples,
        "events": dict(event_counts),
        "gpu_util_peak": round(gpu_busy_peak, 3),
        "vram_peak": round(vram_peak, 3),
        # Non e' un gate di sicurezza: indica solo che la finestra non e' stata tutta idle.
        "representative_load": gpu_busy_peak >= 50.0 and vram_peak >= 80.0,
    }
