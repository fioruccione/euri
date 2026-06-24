#!/usr/bin/env python3
"""
Baseline e verifica per l'indice ZSET dei candidati Loop 2e.

Lo scan completo resta il riferimento. Lo ZSET è utile solo se:
  1. contiene lo stesso set di candidati dello scan;
  2. riduce il costo della selezione quando è già mantenuto;
  3. può essere ricostruito senza ambiguità.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_attention import (  # noqa: E402
    LOOP2E_ZSET,
    rebuild_loop2e_candidate_index,
    scan_loop2e_candidates,
    zset_loop2e_candidates,
)
from utils.redis_client import get_client  # noqa: E402


def _timeit(fn, n: int) -> tuple[list[float], object]:
    times = []
    out = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t0) * 1000)
    return times, out


def _ids(rows) -> list[str]:
    return [str(d.get("id", "")) for d in rows if d.get("id")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--rebuild", action="store_true", help="ricostruisce lo ZSET prima del confronto")
    ap.add_argument("--assert-equal", action="store_true", help="exit 1 se scan e ZSET divergono")
    args = ap.parse_args()

    r = get_client()
    if args.rebuild:
        t0 = time.perf_counter()
        n = rebuild_loop2e_candidate_index(r)
        print(f"rebuild: {n} candidati in {(time.perf_counter() - t0) * 1000:.1f} ms")

    scan_times, scan_rows = _timeit(lambda: scan_loop2e_candidates(r), args.runs)
    zset_times, zset_result = _timeit(lambda: zset_loop2e_candidates(r), args.runs)
    zset_rows, used_index = zset_result

    scan_ids = _ids(scan_rows)
    zset_ids = _ids(zset_rows)
    missing = sorted(set(scan_ids) - set(zset_ids))
    extra = sorted(set(zset_ids) - set(scan_ids))
    same_order = scan_ids == zset_ids
    same_set = not missing and not extra

    print(f"zset_key: {LOOP2E_ZSET}")
    print(f"used_index: {used_index}")
    print(f"scan_count: {len(scan_ids)}")
    print(f"zset_count: {len(zset_ids)}")
    print(f"same_set: {same_set}")
    print(f"same_order: {same_order}")
    if missing:
        print(f"missing_from_zset: {', '.join(m[:8] for m in missing[:20])}")
    if extra:
        print(f"extra_in_zset: {', '.join(m[:8] for m in extra[:20])}")
    print(f"scan_ms: {[round(x, 1) for x in scan_times]} avg={statistics.mean(scan_times):.1f}")
    print(f"zset_ms: {[round(x, 1) for x in zset_times]} avg={statistics.mean(zset_times):.1f}")

    if args.assert_equal and (not same_set or not same_order):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
