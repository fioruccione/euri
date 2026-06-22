#!/usr/bin/env python3
"""
tension_watch.py — osservatorio della tensione del sistema.

Legge `euri:pulse`, calcola uno score agnostico con `core.tension` e lo stampa.
Di default NON scrive nulla. Con `--write` copia gli score su `euri:tension`;
anche in quel caso non agisce, non parla, non notifica.

Uso:
    python tension_watch.py             # tail live, solo stampa
    python tension_watch.py --replay    # rilegge lo stream Pulse
    python tension_watch.py --stats     # riepilogo degli score calcolati al volo
    python tension_watch.py --write     # tail live + scrive euri:tension
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime

from utils.redis_client import get_client
from core.pulse import PULSE_STREAM
from core.tension import evaluate_tension, tension_emit


def _fmt(entry_id: str, fields: dict) -> str:
    score = evaluate_tension(fields)
    ts = float(fields.get("ts", 0) or 0)
    hhmmss = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--:--:--"
    sense = fields.get("sense", "?")
    kind = fields.get("kind", "?")
    return (
        f"{hhmmss} | [{sense:12}/{kind:14}] "
        f"T={score.tension:.2f} mode={score.recommended_mode:<6} "
        f"risk={score.risk:.2f} urg={score.urgency:.2f} "
        f"act={score.actionability:.2f} reason={score.reason} id={entry_id}"
    )


def replay(r, *, write: bool = False):
    entries = r.xrange(PULSE_STREAM)
    if not entries:
        print(f"(stream {PULSE_STREAM} vuoto)")
        return
    print(f"── tension replay: {len(entries)} eventi Pulse ──")
    for entry_id, fields in entries:
        score = evaluate_tension(fields)
        print(_fmt(entry_id, fields))
        if write:
            tension_emit(r, entry_id, fields, score)


def tail_live(r, *, write: bool = False):
    print(f"── tension: in ascolto su {PULSE_STREAM} (Ctrl+C per uscire) ──")
    last_id = "$"
    while True:
        msgs = r.xread({PULSE_STREAM: last_id}, count=20, block=5000)
        if not msgs:
            continue
        for _stream, entries in msgs:
            for entry_id, fields in entries:
                last_id = entry_id
                score = evaluate_tension(fields)
                print(_fmt(entry_id, fields))
                if write:
                    tension_emit(r, entry_id, fields, score)


def stats(r):
    entries = r.xrange(PULSE_STREAM)
    if not entries:
        print(f"(stream {PULSE_STREAM} vuoto)")
        return
    by_mode = Counter()
    by_sense = Counter()
    max_by_sense = defaultdict(float)
    top = []
    for entry_id, fields in entries:
        score = evaluate_tension(fields)
        by_mode[score.recommended_mode] += 1
        sense = fields.get("sense", "?")
        by_sense[sense] += 1
        max_by_sense[sense] = max(max_by_sense[sense], score.tension)
        top.append((score.tension, entry_id, fields, score))

    print(f"== tension stats: {len(entries)} eventi Pulse ==\n")
    print("mode     | n")
    print("---------+----")
    for k, v in by_mode.most_common():
        print(f"{k:8} | {v}")
    print("\nsense        | n   | max T")
    print("-------------+-----+------")
    for k, v in by_sense.most_common():
        print(f"{k:12} | {v:<3} | {max_by_sense[k]:.2f}")

    print("\nTop tension:")
    for _t, entry_id, fields, score in sorted(top, reverse=True)[:10]:
        print("  " + _fmt(entry_id, fields))


def main():
    args = set(sys.argv[1:])
    r = get_client()
    write = "--write" in args
    if "--stats" in args:
        stats(r)
    elif "--replay" in args:
        replay(r, write=write)
    else:
        try:
            tail_live(r, write=write)
        except KeyboardInterrupt:
            print("\n── tension: fine ascolto ──")


if __name__ == "__main__":
    main()
