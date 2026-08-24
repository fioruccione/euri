"""Audit separato dei transcript OpenCode."""
from __future__ import annotations

import json
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config
from agent.coding_research_log import capture_coding_attempt


def test_capture_preserves_tool_ground_truth_outside_memory():
    with TemporaryDirectory() as tmp, patch.object(
        config, "CODE_AGENT_RESEARCH_LOG_ENABLED", True
    ), patch.object(
        config, "CODE_AGENT_RESEARCH_LOG_DIR", Path(tmp)
    ), patch.object(
        config, "CODE_AGENT_RESEARCH_LOG_RETENTION_DAYS", 7
    ):
        artifact = Path(tmp) / "main.py"
        artifact.write_text("print(42)\n", encoding="utf-8")
        transcript = [{
            "info": {"role": "assistant"},
            "parts": [{
                "type": "tool",
                "tool": "edit",
                "callID": "call-1",
                "state": {"status": "error", "error": "permission denied"},
            }],
        }]
        result = capture_coding_attempt(
            job_id="audit-job",
            attempt=1,
            model="gemma4:26b",
            request_prompt="crea main.py",
            reply_text="provo a scrivere",
            reply_raw={"parts": [{"type": "text", "text": "provo"}]},
            session_snapshot={"available": True, "messages": transcript},
            outcome="execution_failed",
            artifact_path=artifact,
            execution={"success": False, "error": "scanner"},
        )

        path = Path(result)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert path.parent == Path(tmp)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert payload["request_prompt"] == "crea main.py"
        assert payload["session_snapshot"]["messages"] == transcript
        assert payload["tool_events"] == [{
            "tool": "edit",
            "call_id": "call-1",
            "status": "error",
            "error": "permission denied",
        }]
        assert payload["artifact"]["present"] is True
        assert payload["execution"]["error"] == "scanner"


if __name__ == "__main__":
    test_capture_preserves_tool_ground_truth_outside_memory()
    print("test_coding_research_log: OK")
