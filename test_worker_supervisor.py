#!/usr/bin/env python3
"""Regressioni pure per restart, health e shutdown dei worker."""

import threading
import time

from core.worker_supervisor import WorkerSupervisor


def test_failed_worker_restarts_and_reports_health():
    supervisor = WorkerSupervisor()
    calls = 0
    restarted = threading.Event()

    def worker():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first boot fails")
        restarted.set()
        supervisor.stop_event.wait()

    supervisor.start("probe", worker)
    assert restarted.wait(4)
    health = supervisor.health()["probe"]
    assert health["alive"] is True
    assert health["failures"] == 1
    assert supervisor.shutdown(timeout=2) == []
    assert supervisor.health()["probe"]["state"] == "stopped"


def test_shutdown_interrupts_wait_and_joins_all_workers():
    supervisor = WorkerSupervisor()
    entered = threading.Event()

    def worker():
        entered.set()
        supervisor.stop_event.wait(60)

    supervisor.start("sleeper", worker)
    assert entered.wait(1)
    started = time.monotonic()
    alive = supervisor.shutdown(timeout=1)

    assert alive == []
    assert time.monotonic() - started < 0.5


def test_disabled_worker_is_visible_but_not_started():
    supervisor = WorkerSupervisor()
    supervisor.start("optional", lambda: None, enabled=False)

    health = supervisor.health()["optional"]
    assert health["state"] == "disabled"
    assert health["alive"] is False
    assert health["failures"] == 0


if __name__ == "__main__":
    test_failed_worker_restarts_and_reports_health()
    test_shutdown_interrupts_wait_and_joins_all_workers()
    test_disabled_worker_is_visible_but_not_started()
    print("test_worker_supervisor: OK")
