#!/usr/bin/env python3
"""Read-only checkpoint for the Phase-0 visual social receptor."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from core.pulse import PULSE_STREAM
from utils.redis_client import get_client


LATEST_KEY = "euri:social:latest"
BASELINE_STREAM = "euri:social:baseline"


def _decode(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _when(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp, tz=config.TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")


def _metric_report(samples, field):
    values: dict[str, list[float]] = {}
    for sample in samples:
        for key, value in _decode(sample.get(field), {}).items():
            try:
                values.setdefault(key, []).append(float(value))
            except (TypeError, ValueError):
                pass
    for key, observed in sorted(values.items()):
        data = np.asarray(observed, dtype=float)
        print(
            f"- {key}: min={data.min():.3f} p50={np.percentile(data, 50):.3f} "
            f"p95={np.percentile(data, 95):.3f} max={data.max():.3f} (n={len(data)})"
        )


def main() -> int:
    r = get_client()
    raw_latest = r.get(LATEST_KEY)
    latest = _decode(raw_latest, {})
    samples = [fields for _entry_id, fields in r.xrange(BASELINE_STREAM, min="-", max="+")]
    transitions = []
    for _entry_id, fields in r.xrevrange(PULSE_STREAM, count=5000):
        if fields.get("sense") == "social" and fields.get("kind") == "movement_transition":
            transitions.append(_decode(fields.get("payload"), {}))

    print("Audit percezione sociale Fase 0 (read-only)")
    if latest:
        observed_at = float(latest.get("observed_at", 0.0) or 0.0)
        age = max(0.0, time.time() - observed_at) if observed_at else float("inf")
        live = age <= getattr(config, "SOCIAL_PERCEPTION_LATEST_TTL_S", 30)
        print(
            f"Recettore: {'vivo' if live else 'dato scaduto'} | ultimo {_when(observed_at)} | "
            f"eta' {age:.1f}s | calibrato: {'si' if latest.get('calibrated') else 'no'}"
        )
        print(f"Stato attuale: {json.dumps(latest.get('states', {}), ensure_ascii=False)}")
    else:
        print("Recettore: nessun dato corrente (non avviato, volto owner assente o backend non disponibile)")

    print(f"Baseline numerica: {len(samples)} campioni")
    if samples:
        started = float(samples[0].get("ts", 0.0) or 0.0)
        ended = float(samples[-1].get("ts", 0.0) or 0.0)
        print(f"Finestra: {_when(started)} -> {_when(ended)}")
        _metric_report(samples, "metrics")
        _metric_report(samples, "auxiliary_metrics")
        state_counts = Counter()
        for sample in samples:
            for feature, state in _decode(sample.get("states"), {}).items():
                state_counts[f"{feature}={state}"] += 1
        print(f"Stati: {json.dumps(dict(sorted(state_counts.items())), ensure_ascii=False)}")

    print(f"Transizioni sociali nel Pulse consultato: {len(transitions)}")
    for item in reversed(transitions[:10]):
        print(
            f"- {_when(float(item.get('observed_at', 0.0) or 0.0))}: "
            f"{item.get('feature', '?')} {item.get('previous', '?')}->{item.get('current', '?')} "
            f"(valore={item.get('value', '?')}, conf={item.get('confidence', '?')})"
        )
    print("Nota: sono movimenti osservati, non emozioni o intenzioni accertate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
