"""Supervisione minima per worker thread di lunga durata."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from loguru import logger


class WorkerSupervisor:
    def __init__(self):
        self.stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._health: dict[str, dict] = {}
        self._lock = threading.Lock()

    def prepare(self) -> None:
        """Prepara un nuovo run; vieta il reset mentre un vecchio worker e' vivo."""
        with self._lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                raise RuntimeError("worker ancora attivi")
            self._threads.clear()
            self._health.clear()
            self.stop_event.clear()

    def _update(self, name: str, state: str, **fields) -> None:
        with self._lock:
            health = self._health.setdefault(name, {"failures": 0})
            health.update(fields)
            health["state"] = state
            health["updated_at"] = time.time()

    def heartbeat(self, name: str) -> None:
        self._update(name, "running", last_heartbeat=time.time())

    def _run(self, name: str, target: Callable[[], None]) -> None:
        failures = 0
        while not self.stop_event.is_set():
            self._update(name, "running", started_at=time.time(), failures=failures, error="")
            try:
                target()
                if self.stop_event.is_set():
                    break
                error = "worker exited unexpectedly"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.exception(f"Worker {name} terminato con errore")

            failures += 1
            self._update(name, "restarting", failures=failures, error=error)
            if self.stop_event.wait(min(30.0, 2.0 ** min(failures, 5))):
                break

        self._update(name, "stopped", stopped_at=time.time(), failures=failures)

    def start(self, name: str, target: Callable[[], None], *, enabled: bool = True) -> None:
        if not enabled:
            self._update(name, "disabled")
            return
        with self._lock:
            current = self._threads.get(name)
            if current and current.is_alive():
                return
            thread = threading.Thread(
                target=self._run,
                args=(name, target),
                daemon=True,
                name=f"euri-{name}",
            )
            self._threads[name] = thread
        thread.start()

    def health(self, *, stale_after_s: float | None = None) -> dict[str, dict]:
        """Snapshot di liveness; opzionalmente distingue ``alive`` da ``responsive``."""
        with self._lock:
            snapshot = {name: dict(value) for name, value in self._health.items()}
            threads = dict(self._threads)
        now_ts = time.time()
        for name, value in snapshot.items():
            thread = threads.get(name)
            value["alive"] = bool(thread and thread.is_alive())
            heartbeat = value.get("last_heartbeat")
            if heartbeat is not None:
                value["heartbeat_age_s"] = max(0.0, now_ts - float(heartbeat))
            stale = bool(
                value["alive"]
                and stale_after_s is not None
                and heartbeat is not None
                and value["heartbeat_age_s"] > max(1.0, float(stale_after_s))
            )
            value["responsive"] = bool(value["alive"] and not stale)
            if stale:
                value["state"] = "stalled"
        return snapshot

    def shutdown(self, timeout: float = 8.0) -> list[str]:
        """Segnala stop e attende tutti i worker entro una deadline complessiva."""
        self.stop_event.set()
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            threads = list(self._threads.items())
        current = threading.current_thread()
        for _name, thread in threads:
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        return [name for name, thread in threads if thread is not current and thread.is_alive()]
