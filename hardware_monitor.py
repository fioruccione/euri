#!/usr/bin/env python3
"""Demone indipendente per l'interocezione hardware di Euri."""

from __future__ import annotations

import argparse
import fcntl
import json
import signal
import sys
import threading
from pathlib import Path

from loguru import logger

import config
from core.hardware_interoception import (
    HardwareCollector,
    HardwareInteroceptor,
    InteroceptiveStateMachine,
    RedisInteroceptionPublisher,
)
from utils.redis_client import get_client


LOCK_PATH = Path("/tmp/euri-hardware-monitor.lock")


def _acquire_singleton_lock():
    handle = LOCK_PATH.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(Path("/proc/self").resolve().name))
    handle.flush()
    return handle


def build_interoceptor() -> HardwareInteroceptor:
    return HardwareInteroceptor(
        collector=HardwareCollector(),
        state_machine=InteroceptiveStateMachine(
            cooldown_s=config.HARDWARE_INTEROCEPTION_EVENT_COOLDOWN_S,
        ),
        publisher=RedisInteroceptionPublisher(
            get_client(),
            latest_ttl_s=config.HARDWARE_INTEROCEPTION_LATEST_TTL_S,
        ),
        interval_s=config.HARDWARE_INTEROCEPTION_INTERVAL_S,
        baseline_interval_s=config.HARDWARE_INTEROCEPTION_BASELINE_INTERVAL_S,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Interocezione hardware osservativa di Euri")
    parser.add_argument("--once", action="store_true", help="campiona una volta e stampa lo snapshot")
    args = parser.parse_args()

    if not config.HARDWARE_INTEROCEPTION_ENABLED:
        logger.info("Hardware interoception disabilitata da config")
        return 0

    lock_handle = _acquire_singleton_lock()
    if lock_handle is None:
        logger.info("Hardware interoception: un recettore e' gia' attivo")
        return 0

    stop_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: stop_event.set())

    interoceptor = build_interoceptor()
    if args.once:
        sample = interoceptor.sample_once()
        interoceptor.collector.close()
        print(json.dumps(sample.metrics, ensure_ascii=False, indent=2, default=str))
        return 0

    logger.info(
        f"Hardware interoception avviata — campionamento ogni "
        f"{config.HARDWARE_INTEROCEPTION_INTERVAL_S:g}s (osservazione, nessun riflesso attivo)"
    )
    interoceptor.run(stop_event)
    logger.info("Hardware interoception arrestata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
