#!/usr/bin/env python3
"""Read-only audit of temporal/context failures visible in voice daemon logs.

The audit does not connect to Redis and does not write reports. It identifies
evidence that is useful before integrating CognitivePresent:

- authenticated STT follow-ups rejected because the old lease started too early;
- likely proactive speech overlapping a rejected user follow-up;
- absolute capability denials that conflate configuration with availability.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+\|\s+[^|]+\|\s+(?P<origin>.*?)\s+-\s+(?P<message>.*)$"
)
_REJECT_RE = re.compile(
    r"Wake word assente e fuori finestra \((?P<seconds>\d+)s\) .*?: '(?P<text>.*)'"
)
_CAPABILITY_DENIAL_RE = re.compile(
    r"\bnon ho accesso\b.{0,80}\bwebcam\b|\bnon posso monitorare\b.{0,80}\bambiente\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    origin: str
    message: str


@dataclass(frozen=True)
class RejectedFollowup:
    timestamp: str
    transcript: str
    reported_outside_window_s: int
    seconds_after_playback: float | None
    followed_playback_within_window: bool


@dataclass(frozen=True)
class ProactiveOverlap:
    rejected_at: str
    proactive_at: str
    delay_s: float
    rejected_transcript: str
    proactive_text: str


@dataclass(frozen=True)
class CapabilityConflation:
    timestamp: str
    claim: str
    capability_configured: bool
    currently_available: bool | None


@dataclass(frozen=True)
class AuditReport:
    log_path: str
    entries: int
    rejected_followups: tuple[RejectedFollowup, ...]
    proactive_overlaps: tuple[ProactiveOverlap, ...]
    capability_conflations: tuple[CapabilityConflation, ...]


def parse_log(path: Path) -> list[LogEntry]:
    entries = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LINE_RE.match(raw)
        if not match:
            continue
        entries.append(LogEntry(
            timestamp=datetime.fromisoformat(match.group("ts")),
            origin=match.group("origin"),
            message=match.group("message"),
        ))
    return entries


def audit_entries(
    entries: list[LogEntry],
    *,
    log_path: str = "",
    conversation_window_s: float = 45.0,
    overlap_window_s: float = 90.0,
) -> AuditReport:
    rejected = []
    overlaps = []
    capability_claims = []
    last_speech_start: datetime | None = None
    last_playback_end: datetime | None = None
    visual_configured = False
    camera_available: bool | None = None

    for index, entry in enumerate(entries):
        message = entry.message
        if "VisualGate avviato" in message:
            visual_configured = True
            camera_available = True
        elif "webcam non accessibile" in message:
            camera_available = False

        if message.startswith("Euri: "):
            last_speech_start = entry.timestamp
            last_playback_end = None
            claim = message[6:].strip()
            if _CAPABILITY_DENIAL_RE.search(claim) and visual_configured:
                capability_claims.append(CapabilityConflation(
                    timestamp=entry.timestamp.isoformat(sep=" "),
                    claim=claim,
                    capability_configured=True,
                    currently_available=camera_available,
                ))
        elif "Interrupt listener terminato" in message and last_speech_start:
            last_playback_end = entry.timestamp

        match = _REJECT_RE.search(message)
        if not match:
            continue

        after_playback = None
        within_window = False
        if last_playback_end is not None:
            after_playback = (entry.timestamp - last_playback_end).total_seconds()
            within_window = 0 <= after_playback <= conversation_window_s
        item = RejectedFollowup(
            timestamp=entry.timestamp.isoformat(sep=" "),
            transcript=match.group("text"),
            reported_outside_window_s=int(match.group("seconds")),
            seconds_after_playback=after_playback,
            followed_playback_within_window=within_window,
        )
        rejected.append(item)

        # Speech with no routed intent after a rejected utterance is a likely
        # initiative collision. It remains an audit flag, not a causal verdict.
        for future in entries[index + 1:]:
            delay = (future.timestamp - entry.timestamp).total_seconds()
            if delay > overlap_window_s:
                break
            if future.message.startswith("Intent: "):
                break
            if future.message.startswith("Euri: "):
                overlaps.append(ProactiveOverlap(
                    rejected_at=item.timestamp,
                    proactive_at=future.timestamp.isoformat(sep=" "),
                    delay_s=delay,
                    rejected_transcript=item.transcript,
                    proactive_text=future.message[6:].strip(),
                ))
                break

    return AuditReport(
        log_path=log_path,
        entries=len(entries),
        rejected_followups=tuple(rejected),
        proactive_overlaps=tuple(overlaps),
        capability_conflations=tuple(capability_claims),
    )


def audit_log(path: Path, *, conversation_window_s: float = 45.0) -> AuditReport:
    return audit_entries(
        parse_log(path),
        log_path=str(path),
        conversation_window_s=conversation_window_s,
    )


def render_text(report: AuditReport) -> str:
    likely_followups = sum(
        item.followed_playback_within_window for item in report.rejected_followups
    )
    lines = [
        f"Audit Cognitive Present: {report.log_path}",
        f"Voci log analizzate: {report.entries}",
        f"Turni fuori finestra rifiutati: {len(report.rejected_followups)}",
        f"Di cui follow-up entro la finestra dalla fine del playback: {likely_followups}",
        f"Possibili sovrapposizioni Initiative: {len(report.proactive_overlaps)}",
        f"Auto-descrizioni capability/availability confuse: {len(report.capability_conflations)}",
    ]
    for item in report.rejected_followups:
        if item.followed_playback_within_window:
            lines.append(
                f"- {item.timestamp}: rifiutato dopo {item.seconds_after_playback:.1f}s "
                f"dalla fine TTS: {item.transcript}"
            )
    for item in report.proactive_overlaps:
        lines.append(
            f"- {item.proactive_at}: possibile Initiative {item.delay_s:.1f}s dopo "
            f"il turno rifiutato: {item.proactive_text}"
        )
    for item in report.capability_conflations:
        availability = "sconosciuta" if item.currently_available is None else str(item.currently_available).lower()
        lines.append(
            f"- {item.timestamp}: capability configurata, disponibilita'={availability}: {item.claim}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path, nargs="?", default=Path("logs/voice_daemon.log"))
    parser.add_argument("--conversation-window", type=float, default=45.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit_log(args.log, conversation_window_s=args.conversation_window)
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
