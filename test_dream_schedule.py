#!/usr/bin/env python3
"""Test puro della schedulazione idle del Dream Engine."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from core.dream_engine import DreamEngine


class FakeDreamEngine(DreamEngine):
    def __init__(self):
        super().__init__(r=None, embedder=None, memory=None)
        self.calls = []

    def _creative_cycle(self):
        self.calls.append("creative")

    def _light_cycle(self):
        self.calls.append("light")

    def _maintenance_cycle(self):
        self.calls.append("maintenance")


def _patch_config(**values):
    old = {}
    for k, v in values.items():
        old[k] = getattr(config, k, None)
        setattr(config, k, v)
    return old


def _restore_config(old):
    for k, v in old.items():
        setattr(config, k, v)


def test_idle_threshold_uses_short_seconds_when_present():
    engine = FakeDreamEngine()
    old = _patch_config(DREAM_ENGINE_IDLE_SECONDS=10, DREAM_ENGINE_IDLE_HOURS=99)
    try:
        engine._last_activity = time.time() - 11
        assert engine._is_idle()
        engine._last_activity = time.time() - 5
        assert not engine._is_idle()
    finally:
        _restore_config(old)


def test_due_idle_cycles_are_split_by_interval():
    engine = FakeDreamEngine()
    now_ts = time.time()
    old = _patch_config(
        DREAM_LIGHT_CYCLE_INTERVAL_S=10,
        DREAM_CREATIVE_CYCLE_INTERVAL_S=100,
        DREAM_MAINTENANCE_CYCLE_INTERVAL_S=1000,
    )
    try:
        engine._light_last_run = now_ts - 11
        engine._creative_last_run = now_ts - 50
        engine._maintenance_last_run = now_ts - 500
        engine._run_due_idle_cycles()
        assert engine.calls == ["light"]
    finally:
        _restore_config(old)


def test_all_due_cycles_keep_order():
    engine = FakeDreamEngine()
    old = _patch_config(
        DREAM_LIGHT_CYCLE_INTERVAL_S=1,
        DREAM_CREATIVE_CYCLE_INTERVAL_S=1,
        DREAM_MAINTENANCE_CYCLE_INTERVAL_S=1,
    )
    try:
        engine._light_last_run = 0
        engine._creative_last_run = 0
        engine._maintenance_last_run = 0
        engine._run_due_idle_cycles()
        assert engine.calls == ["creative", "light", "maintenance"]
    finally:
        _restore_config(old)


if __name__ == "__main__":
    test_idle_threshold_uses_short_seconds_when_present()
    test_due_idle_cycles_are_split_by_interval()
    test_all_due_cycles_keep_order()
    print("test_dream_schedule: OK")
