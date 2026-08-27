#!/usr/bin/env python3
"""Misure read-only della velocita' di Euri e del backend Ollama.

Due percorsi distinti:

* ``voice`` ricostruisce dai log il tempo fra fine del parlato e prima sillaba;
* ``decode`` misura token/s usando le durate native restituite da Ollama.

Il comando ``decode`` rifiuta di partire se ``voice_daemon.py`` e' attivo, per
non rallentare Euri e non contaminare il campione. ``--force`` esiste solo per
esperimenti intenzionalmente concorrenti.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "logs" / "voice_daemon.log"
DEFAULT_PROMPT = (
    "Scrivi una lista numerata continua di brevi osservazioni tecniche "
    "indipendenti sulla progettazione di sistemi software affidabili. "
    "Non inserire conclusioni e continua fino al limite di generazione."
)


@dataclass
class VoiceTurn:
    timestamp: datetime
    transcript: str = ""
    stt_ms: float | None = None
    semantic_ms: float | None = None
    rag_ms: float | None = None
    brain_ms: float | None = None
    tts_first_ms: float | None = None
    handler: str = ""

    @property
    def first_voice_ms(self) -> float | None:
        values = (
            self.stt_ms,
            self.semantic_ms,
            self.rag_ms,
            self.brain_ms,
            self.tts_first_ms,
        )
        if any(value is None for value in values):
            return None
        return float(sum(value for value in values if value is not None))

    def record(self) -> dict:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        result["first_voice_ms"] = self.first_voice_ms
        return result


def _timestamp(line: str) -> datetime | None:
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f")
    except (ValueError, TypeError):
        return None


def _number_after(line: str, marker: str, suffix: str = "ms") -> float | None:
    start = line.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = line.find(suffix, start)
    if end < 0:
        return None
    try:
        return float(line[start:end].strip())
    except ValueError:
        return None


def parse_voice_log(lines: Iterable[str]) -> list[VoiceTurn]:
    """Ricostruisce i turni senza dipendere dai logger Python di produzione."""
    turns: list[VoiceTurn] = []
    current: VoiceTurn | None = None
    pending_transcript = ""

    def finish() -> None:
        nonlocal current
        if current is not None:
            turns.append(current)
        current = None

    for line in lines:
        if "voice.stt:transcribe" in line and "STT: '" in line:
            body = line.split("STT: '", 1)[1]
            pending_transcript = body.rsplit("' (lang=", 1)[0]
            continue

        if "[TIMING] STT:" in line:
            finish()
            stamp = _timestamp(line)
            value = _number_after(line, "[TIMING] STT:")
            if stamp is not None:
                current = VoiceTurn(
                    timestamp=stamp,
                    transcript=pending_transcript,
                    stt_ms=value,
                )
            pending_transcript = ""
            continue

        if current is None:
            continue

        if "[TIMING] Turno semantico:" in line:
            current.semantic_ms = _number_after(line, "[TIMING] Turno semantico:")
        elif "[TIMING] RAG dual:" in line:
            current.rag_ms = _number_after(line, "total=")
        elif "[TIMING] brain.respond() Ollama:" in line:
            current.brain_ms = _number_after(line, "[TIMING] brain.respond() Ollama:")
        elif "[TIMING] TTS first-ready:" in line:
            current.tts_first_ms = _number_after(line, "[TIMING] TTS first-ready:")
        elif "[TIMING] Handler " in line:
            body = line.split("[TIMING] Handler ", 1)[1]
            current.handler = body.split(":", 1)[0].strip()

    finish()
    return turns


def _percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(values: Iterable[float]) -> dict:
    samples = [float(value) for value in values]
    if not samples:
        return {"n": 0}
    return {
        "n": len(samples),
        "min": min(samples),
        "median": statistics.median(samples),
        "p95": _percentile(samples, 0.95),
        "max": max(samples),
        "mean": statistics.mean(samples),
    }


def _parse_local_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def voice_report(
    log_path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> dict:
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        turns = parse_voice_log(handle)

    complete = []
    for turn in turns:
        if turn.first_voice_ms is None or turn.handler not in {"CHAT", "SEARCH"}:
            continue
        if since is not None and turn.timestamp < since:
            continue
        if until is not None and turn.timestamp > until:
            continue
        complete.append(turn)
    if limit is not None:
        complete = complete[-limit:]

    stages = {
        "first_voice_ms": [turn.first_voice_ms for turn in complete],
        "stt_ms": [turn.stt_ms for turn in complete],
        "semantic_ms": [turn.semantic_ms for turn in complete],
        "rag_ms": [turn.rag_ms for turn in complete],
        "brain_ms": [turn.brain_ms for turn in complete],
        "tts_first_ms": [turn.tts_first_ms for turn in complete],
    }
    return {
        "kind": "voice_log",
        "log": str(log_path),
        "filters": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "limit": limit,
            "handlers": ["CHAT", "SEARCH"],
        },
        "turns": [turn.record() for turn in complete],
        "summary": {name: summarize(values) for name, values in stages.items()},
    }


def _voice_daemon_pids() -> list[int]:
    found = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
        except (OSError, PermissionError):
            continue
        if "voice_daemon.py" in command:
            found.append(int(entry.name))
    return sorted(found)


def _ollama_generate(
    *,
    url: str,
    model: str,
    prompt: str,
    tokens: int,
    num_ctx: int,
    timeout_s: float,
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": tokens,
            "temperature": 0,
            "seed": 42,
        },
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    wall_started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        result = json.load(response)
    wall_s = time.perf_counter() - wall_started

    eval_count = int(result.get("eval_count") or 0)
    eval_s = float(result.get("eval_duration") or 0) / 1e9
    return {
        "eval_count": eval_count,
        "eval_s": eval_s,
        "tokens_per_second": eval_count / eval_s if eval_s else 0.0,
        "wall_s": wall_s,
        "prompt_eval_count": int(result.get("prompt_eval_count") or 0),
        "prompt_eval_s": float(result.get("prompt_eval_duration") or 0) / 1e9,
        "load_s": float(result.get("load_duration") or 0) / 1e9,
        "done_reason": result.get("done_reason"),
    }


def decode_report(args: argparse.Namespace) -> dict:
    pids = _voice_daemon_pids()
    if pids and not args.force:
        joined = ", ".join(str(pid) for pid in pids)
        raise RuntimeError(
            f"voice_daemon.py attivo (PID {joined}): benchmark annullato per evitare contesa"
        )

    warmup = _ollama_generate(
        url=args.url,
        model=args.model,
        prompt=args.prompt,
        tokens=args.warmup_tokens,
        num_ctx=args.num_ctx,
        timeout_s=args.timeout,
    )
    runs = []
    for _ in range(args.repetitions):
        runs.append(
            _ollama_generate(
                url=args.url,
                model=args.model,
                prompt=args.prompt,
                tokens=args.tokens,
                num_ctx=args.num_ctx,
                timeout_s=args.timeout,
            )
        )

    speeds = [run["tokens_per_second"] for run in runs]
    report = {
        "kind": "ollama_decode",
        "model": args.model,
        "url": args.url,
        "configuration": {
            "repetitions": args.repetitions,
            "tokens": args.tokens,
            "warmup_tokens": args.warmup_tokens,
            "num_ctx": args.num_ctx,
            "thinking": False,
            "temperature": 0,
            "seed": 42,
            "voice_daemon_pids": pids,
            "forced_while_busy": bool(pids and args.force),
        },
        "warmup": warmup,
        "runs": runs,
        "summary_tokens_per_second": summarize(speeds),
    }
    if args.baseline_tps is not None:
        median = report["summary_tokens_per_second"]["median"]
        report["comparison"] = {
            "baseline_tokens_per_second": args.baseline_tps,
            "delta_tokens_per_second": median - args.baseline_tps,
            "delta_percent": (median / args.baseline_tps - 1.0) * 100.0,
        }
    return report


def _print_voice(report: dict) -> None:
    turns = report["turns"]
    print(f"Turni vocali completi: {len(turns)}")
    for turn in turns:
        print(
            f"- {turn['timestamp']} | {turn['handler']:<6} | "
            f"prima voce={turn['first_voice_ms'] / 1000:.3f}s | "
            f"STT={turn['stt_ms']:.0f} sem={turn['semantic_ms']:.0f} "
            f"RAG={turn['rag_ms']:.0f} LLM={turn['brain_ms']:.0f} "
            f"TTS={turn['tts_first_ms']:.0f} ms | {turn['transcript'][:90]}"
        )
    stats = report["summary"]["first_voice_ms"]
    if stats["n"]:
        print(
            "Sintesi prima voce: "
            f"n={stats['n']} min={stats['min'] / 1000:.3f}s "
            f"mediana={stats['median'] / 1000:.3f}s "
            f"p95={stats['p95'] / 1000:.3f}s max={stats['max'] / 1000:.3f}s"
        )


def _print_decode(report: dict) -> None:
    print(f"Modello: {report['model']}")
    for index, run in enumerate(report["runs"], 1):
        print(
            f"- replica {index}: {run['tokens_per_second']:.3f} token/s "
            f"({run['eval_count']} token in {run['eval_s']:.3f}s)"
        )
    stats = report["summary_tokens_per_second"]
    print(
        f"Sintesi: mediana={stats['median']:.3f} token/s "
        f"media={stats['mean']:.3f} min={stats['min']:.3f} max={stats['max']:.3f}"
    )
    if "comparison" in report:
        comparison = report["comparison"]
        print(
            f"Confronto baseline {comparison['baseline_tokens_per_second']:.3f}: "
            f"{comparison['delta_percent']:+.2f}%"
        )


def _write_json(path: Path | None, report: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    voice = subparsers.add_parser("voice", help="analizza la pipeline vocale dai log")
    voice.add_argument("--log", type=Path, default=DEFAULT_LOG)
    voice.add_argument("--since", help="timestamp locale ISO, incluso")
    voice.add_argument("--until", help="timestamp locale ISO, incluso")
    voice.add_argument("--limit", type=int)
    voice.add_argument("--json-out", type=Path)

    decode = subparsers.add_parser("decode", help="misura il decode reale di Ollama")
    decode.add_argument("--model", default="gemma4:26b")
    decode.add_argument("--url", default="http://127.0.0.1:11434")
    decode.add_argument("--prompt", default=DEFAULT_PROMPT)
    decode.add_argument("--repetitions", type=int, default=3)
    decode.add_argument("--tokens", type=int, default=512)
    decode.add_argument("--warmup-tokens", type=int, default=64)
    decode.add_argument("--num-ctx", type=int, default=32768)
    decode.add_argument("--timeout", type=float, default=300.0)
    decode.add_argument("--baseline-tps", type=float)
    decode.add_argument("--force", action="store_true")
    decode.add_argument("--json-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "voice":
            report = voice_report(
                args.log,
                since=_parse_local_datetime(args.since),
                until=_parse_local_datetime(args.until),
                limit=args.limit,
            )
            _print_voice(report)
        else:
            report = decode_report(args)
            _print_decode(report)
        _write_json(args.json_out, report)
        return 0
    except (OSError, RuntimeError, urllib.error.URLError, ValueError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
