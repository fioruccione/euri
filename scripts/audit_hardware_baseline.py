#!/usr/bin/env python3
"""Stampa il checkpoint read-only della baseline hardware di Euri."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from core.hardware_baseline import summarize_hardware_baseline
from core.hardware_interoception import BASELINE_STREAM, EVENT_STREAM
from utils.redis_client import get_client


def _stream_payloads(entries):
    for _entry_id, fields in entries:
        yield fields.get("payload", "{}")


def _when(timestamp):
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(float(timestamp), tz=config.TIMEZONE).strftime("%d/%m/%Y %H:%M")


def main() -> int:
    r = get_client()
    samples = list(_stream_payloads(r.xrange(BASELINE_STREAM, min="-", max="+")))
    events = list(_stream_payloads(r.xrange(EVENT_STREAM, min="-", max="+")))
    report = summarize_hardware_baseline(
        samples,
        events,
        expected_interval_s=config.HARDWARE_INTEROCEPTION_BASELINE_INTERVAL_S,
        review_after_s=config.HARDWARE_INTEROCEPTION_REVIEW_AFTER_S,
        min_coverage=config.HARDWARE_INTEROCEPTION_MIN_COVERAGE,
    )

    print("Audit interocezione hardware (read-only)")
    print(f"Stato: {report['status']}")
    print(
        f"Finestra: {_when(report.get('started_at'))} -> {_when(report.get('ended_at'))} | "
        f"{report['duration_s'] / 3600:.1f}h"
    )
    print(
        f"Campioni: {report['sample_count']}/{report.get('expected_samples', 0)} | "
        f"copertura {report['coverage'] * 100:.1f}%"
    )
    print(
        f"Fault sample: {report['fault_samples']} | eventi: {json.dumps(report['events'], ensure_ascii=False)} | "
        f"carico rappresentativo: {'si' if report['representative_load'] else 'non ancora'}"
    )
    for sensor, stats in report["sensors"].items():
        print(
            f"- {sensor}: min={stats['min']:g} p50={stats['p50']:g} "
            f"p95={stats['p95']:g} max={stats['max']:g} (n={stats['count']})"
        )
    print(f"Review dopo 72h: {'PRONTA' if report['ready_for_review'] else 'NON ANCORA'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
