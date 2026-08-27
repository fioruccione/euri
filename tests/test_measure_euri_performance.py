#!/usr/bin/env python3
"""Regressioni pure per lo strumento di misura P620."""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.measure_euri_performance import parse_voice_log, summarize


def test_parse_complete_voice_turn_and_critical_path():
    lines = [
        "2026-08-24 17:52:54.755 | INFO | voice.stt:transcribe - STT: 'Ciao Euri' (lang=it)\n",
        "2026-08-24 17:52:54.755 | INFO | main:run - [TIMING] STT: 667ms\n",
        "2026-08-24 17:53:03.397 | INFO | semantic - [TIMING] Turno semantico: 8638ms -> SEARCH\n",
        "2026-08-24 17:53:03.449 | INFO | rag - [TIMING] RAG dual: base=4ms total=6ms\n",
        "2026-08-24 17:53:07.292 | INFO | brain - [TIMING] brain.respond() Ollama: 3837ms | think=False\n",
        "2026-08-24 17:53:08.004 | INFO | main - [TIMING] TTS first-ready: 708ms (chunk 1/1)\n",
        "2026-08-24 17:53:21.881 | INFO | main - [TIMING] Handler SEARCH: 18456ms\n",
    ]

    turns = parse_voice_log(lines)

    assert len(turns) == 1
    assert turns[0].timestamp == datetime(2026, 8, 24, 17, 52, 54, 755000)
    assert turns[0].transcript == "Ciao Euri"
    assert turns[0].handler == "SEARCH"
    assert turns[0].first_voice_ms == 13856


def test_new_stt_closes_incomplete_turn_without_mixing_metrics():
    lines = [
        "2026-08-24 10:00:00.000 | INFO | main - [TIMING] STT: 100ms\n",
        "2026-08-24 10:01:00.000 | INFO | main - [TIMING] STT: 200ms\n",
        "2026-08-24 10:01:01.000 | INFO | semantic - [TIMING] Turno semantico: 300ms -> CHAT\n",
    ]

    turns = parse_voice_log(lines)

    assert len(turns) == 2
    assert turns[0].stt_ms == 100
    assert turns[0].semantic_ms is None
    assert turns[1].stt_ms == 200
    assert turns[1].semantic_ms == 300


def test_summary_uses_interpolated_p95():
    report = summarize([1, 2, 3, 4, 5])

    assert report["n"] == 5
    assert report["median"] == 3
    assert report["p95"] == 4.8


if __name__ == "__main__":
    test_parse_complete_voice_turn_and_critical_path()
    test_new_stt_closes_incomplete_turn_without_mixing_metrics()
    test_summary_uses_interpolated_p95()
    print("test_measure_euri_performance: OK")
