"""
pulse_watch.py — osservatorio del bus afferente `euri:pulse` (Euri Pulse, Fase 0).

Non consuma per agire: SOLO guarda i segnali che arrivano, come si guarda il log dei
loop. Tre modi:

    python pulse_watch.py            # tail live: resta in ascolto sui nuovi eventi
    python pulse_watch.py --replay   # rilegge tutta la storia recente, dal più vecchio
    python pulse_watch.py --stats     # riepilogo: conteggi per senso/sorgente/kind + salience

Read-only: nessuna scrittura, nessun XACK, nessun consumer-group.
"""
import sys
import json
import time
from datetime import datetime
from collections import Counter, defaultdict

from utils.redis_client import get_client

STREAM = "euri:pulse"

# Colori ANSI per leggere a colpo d'occhio extero vs intero
_C = {
    "extero": "\033[36m",  # ciano = il mondo fuori
    "intero": "\033[35m",  # magenta = Euri che sente sé stessa
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _fmt(fields: dict) -> str:
    ts = float(fields.get("ts", 0) or 0)
    hhmmss = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--:--:--"
    sense = fields.get("sense", "?")
    source = fields.get("source", "?")
    kind = fields.get("kind", "?")
    sal = fields.get("salience", "?")
    payload = fields.get("payload", "")
    try:
        p = json.loads(payload)
        payload = " ".join(f"{k}={v}" for k, v in p.items()) if p else ""
    except Exception:
        pass
    col = _C.get(source, "")
    return (f"{_C['dim']}{hhmmss}{_C['reset']} | {col}[{source[:6]:6}/{sense:12}]{_C['reset']} "
            f"{kind:14} {_C['dim']}s={sal}{_C['reset']}  {payload}")


def tail_live(r):
    print(f"── pulse: in ascolto su {STREAM} (Ctrl+C per uscire) ──")
    last_id = "$"
    while True:
        msgs = r.xread({STREAM: last_id}, count=20, block=5000)
        if not msgs:
            continue
        for _stream, entries in msgs:
            for entry_id, fields in entries:
                last_id = entry_id
                print(_fmt(fields))


def replay(r):
    entries = r.xrange(STREAM)
    if not entries:
        print(f"(stream {STREAM} vuoto — il polso non ha ancora emesso nulla)")
        return
    print(f"── pulse replay: {len(entries)} eventi ──")
    for _id, fields in entries:
        print(_fmt(fields))


def stats(r):
    entries = r.xrange(STREAM)
    if not entries:
        print(f"(stream {STREAM} vuoto — il polso non ha ancora emesso nulla)")
        return
    by_source = Counter()
    by_sense = Counter()
    by_kind = Counter()
    sal_by_sense = defaultdict(list)
    first_ts = last_ts = None
    for _id, f in entries:
        by_source[f.get("source", "?")] += 1
        by_sense[f.get("sense", "?")] += 1
        by_kind[f.get("kind", "?")] += 1
        try:
            sal_by_sense[f.get("sense", "?")].append(float(f.get("salience", 0)))
        except Exception:
            pass
        ts = float(f.get("ts", 0) or 0)
        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

    span_h = (last_ts - first_ts) / 3600 if (first_ts and last_ts) else 0
    print(f"== pulse stats: {len(entries)} eventi su ~{span_h:.1f}h ==\n")
    print("sorgente   | n")
    print("-----------+----")
    for k, v in by_source.most_common():
        print(f"{k:10} | {v}")
    print("\nsenso        | n   | salience media (ipotesi grezza del senso)")
    print("-------------+-----+------------------------------------------")
    for k, v in by_sense.most_common():
        sals = sal_by_sense.get(k, [])
        avg = sum(sals) / len(sals) if sals else 0
        print(f"{k:12} | {v:<3} | {avg:.2f}")
    print("\nkind            | n")
    print("----------------+----")
    for k, v in by_kind.most_common():
        print(f"{k:15} | {v}")


def main():
    r = get_client()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--stats":
        stats(r)
    elif arg == "--replay":
        replay(r)
    else:
        try:
            tail_live(r)
        except KeyboardInterrupt:
            print("\n── pulse: fine ascolto ──")


if __name__ == "__main__":
    main()
