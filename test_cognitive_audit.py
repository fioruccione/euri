"""Unit test dell'audit log Cognitive Present."""
from datetime import datetime

from scripts.experiments.audit_cognitive_present import audit_entries, parse_log


def test_audit_detects_observed_failures(tmp_path=None):
    content = """2026-07-14 16:49:37.849 | INFO | voice.visual_gate:start:115 - VisualGate avviato - yunet
2026-07-14 16:49:37.850 | WARNING | voice.visual_gate:_loop:227 - VisualGate: webcam non accessibile - gate disabilitato
2026-07-14 16:53:49.807 | INFO | __main__:_speak:281 - Euri: Non ho accesso alla tua webcam.
2026-07-14 16:54:39.513 | DEBUG | __main__:_interrupt_listener:412 - Interrupt listener terminato
2026-07-14 16:55:07.351 | DEBUG | __main__:_accept_voice_transcript:2575 - Wake word assente e fuori finestra (78s) - ignorato: 'Sarebbe molto figo'
2026-07-14 16:55:28.778 | INFO | __main__:_speak:281 - Euri: Questa analogia regge?
"""
    if tmp_path is None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            path = Path(directory) / "voice.log"
            path.write_text(content, encoding="utf-8")
            entries = parse_log(path)
    else:
        path = tmp_path / "voice.log"
        path.write_text(content, encoding="utf-8")
        entries = parse_log(path)

    report = audit_entries(entries, log_path="fixture")
    assert len(report.rejected_followups) == 1
    assert report.rejected_followups[0].followed_playback_within_window
    assert report.rejected_followups[0].seconds_after_playback == 27.838
    assert len(report.proactive_overlaps) == 1
    assert len(report.capability_conflations) == 1
    claim = report.capability_conflations[0]
    assert claim.capability_configured and claim.currently_available is False


def test_routed_turn_is_not_overlap():
    from scripts.experiments.audit_cognitive_present import LogEntry

    def entry(value, message):
        return LogEntry(datetime.fromisoformat(value), "test", message)

    report = audit_entries([
        entry("2026-07-14 10:00:00", "Wake word assente e fuori finestra (50s) - ignorato: 'ciao'"),
        entry("2026-07-14 10:00:01", "Intent: CHAT - 'altro turno'"),
        entry("2026-07-14 10:00:02", "Euri: risposta"),
    ])
    assert not report.proactive_overlaps


if __name__ == "__main__":
    test_audit_detects_observed_failures()
    test_routed_turn_is_not_overlap()
    print("PASS")
