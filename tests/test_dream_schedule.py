#!/usr/bin/env python3
"""Test puro della schedulazione idle del Dream Engine."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import core.dream_engine as dream_engine_module
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
        DREAM_ENGINE_IDLE_SECONDS=1,
        DREAM_LIGHT_CYCLE_INTERVAL_S=10,
        DREAM_CREATIVE_CYCLE_INTERVAL_S=100,
        DREAM_MAINTENANCE_CYCLE_INTERVAL_S=1000,
    )
    try:
        engine._last_activity = now_ts - 2
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
        DREAM_ENGINE_IDLE_SECONDS=1,
        DREAM_LIGHT_CYCLE_INTERVAL_S=1,
        DREAM_CREATIVE_CYCLE_INTERVAL_S=1,
        DREAM_MAINTENANCE_CYCLE_INTERVAL_S=1,
    )
    try:
        engine._last_activity = time.time() - 2
        engine._light_last_run = 0
        engine._creative_last_run = 0
        engine._maintenance_last_run = 0
        engine._run_due_idle_cycles()
        assert engine.calls == ["creative", "light", "maintenance"]
    finally:
        _restore_config(old)


def test_foreground_activity_keeps_interrupted_phase_due():
    engine = FakeDreamEngine()
    old = _patch_config(
        DREAM_ENGINE_IDLE_SECONDS=1,
        DREAM_LIGHT_CYCLE_INTERVAL_S=1,
        DREAM_CREATIVE_CYCLE_INTERVAL_S=10_000,
        DREAM_MAINTENANCE_CYCLE_INTERVAL_S=10_000,
    )
    try:
        engine._last_activity = time.time() - 2
        previous_light_run = time.time() - 10
        engine._light_last_run = previous_light_run
        engine._creative_last_run = time.time()
        engine._maintenance_last_run = time.time()

        def interrupted_light():
            engine.calls.append("light")
            engine.notify_activity()

        engine._light_cycle = interrupted_light
        engine._run_due_idle_cycles()

        assert engine.calls == ["light"]
        assert engine._light_last_run == previous_light_run
    finally:
        _restore_config(old)


def test_stop_interrupts_poll_and_joins_thread():
    engine = FakeDreamEngine()
    old = _patch_config(DREAM_ENGINE_ENABLED=True, DREAM_ENGINE_POLL_SECONDS=60)
    original_rebuild = dream_engine_module.rebuild_loop2e_candidate_index
    original_utility = (
        dream_engine_module.run_memory_utility_shadow_maintenance
    )
    utility_calls = []
    try:
        dream_engine_module.rebuild_loop2e_candidate_index = lambda _r: 0
        dream_engine_module.run_memory_utility_shadow_maintenance = (
            lambda r: utility_calls.append(r) or {
                "totals": {
                    "turns_responded": 0,
                    "used_nodes_supported_not_proven": 0,
                }
            }
        )
        engine.start()
        assert utility_calls == [None]
        assert engine._thread.is_alive()
        started = time.monotonic()
        engine.stop(timeout=1)
        assert not engine._thread.is_alive()
        assert time.monotonic() - started < 0.5
    finally:
        dream_engine_module.rebuild_loop2e_candidate_index = original_rebuild
        dream_engine_module.run_memory_utility_shadow_maintenance = (
            original_utility
        )
        _restore_config(old)


if __name__ == "__main__":
    test_idle_threshold_uses_short_seconds_when_present()
    test_due_idle_cycles_are_split_by_interval()
    test_all_due_cycles_keep_order()
    test_foreground_activity_keeps_interrupted_phase_due()
    test_stop_interrupts_poll_and_joins_thread()
    print("test_dream_schedule: OK")
