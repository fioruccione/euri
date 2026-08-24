"""Contratti dell'adapter OpenCode senza avviare processi o rete."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent.opencode_adapter import OpenCodeAdapter, OpenCodeError, _response_text


class _JsonResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self._payload


def test_response_text_extracts_message_parts():
    payload = {
        "info": {"id": "m1"},
        "parts": [
            {"type": "tool", "name": "write"},
            {"type": "text", "text": "main.py creato"},
        ],
    }
    assert _response_text(payload) == "main.py creato"


def test_generated_config_is_local_and_fail_closed():
    with TemporaryDirectory() as tmp:
        adapter = OpenCodeAdapter(
            tmp,
            model="qwen-test",
            ollama_base_url="http://127.0.0.1:11434/v1",
            max_steps=4,
        )
        adapter._write_config()
        data = json.loads((Path(tmp) / "opencode.json").read_text(encoding="utf-8"))
        assert data["model"] == "ollama/qwen-test"
        assert data["provider"]["ollama"]["options"]["baseURL"].startswith(
            "http://127.0.0.1:"
        )
        agent = data["agent"][adapter.AGENT_NAME]
        assert agent["steps"] == 4
        assert agent["permission"]["bash"] == "deny"
        assert agent["permission"]["task"] == "deny"
        assert agent["permission"]["external_directory"] == "deny"
        assert agent["permission"]["webfetch"] == "deny"
        assert agent["permission"]["edit"] == "allow"


def test_model_preflight_accepts_only_tool_capable_ollama_models():
    with TemporaryDirectory() as tmp:
        adapter = OpenCodeAdapter(tmp, model="gemma4:26b")
        payload = {
            "models": [{
                "name": "gemma4:26b",
                "capabilities": ["completion", "tools", "thinking"],
            }]
        }
        with patch(
            "agent.opencode_adapter.urllib.request.urlopen",
            return_value=_JsonResponse(payload),
        ):
            adapter._check_local_model()

        payload["models"][0] = {
            "name": "gemma4:26b",
            "capabilities": ["completion", "vision"],
        }
        with patch(
            "agent.opencode_adapter.urllib.request.urlopen",
            return_value=_JsonResponse(payload),
        ):
            try:
                adapter._check_local_model()
            except OpenCodeError as exc:
                assert "non espone tool calling" in str(exc)
            else:
                raise AssertionError("modello senza tools accettato dal preflight")


def test_close_aborts_and_deletes_ephemeral_session():
    with TemporaryDirectory() as tmp:
        adapter = OpenCodeAdapter(tmp)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-sensitive"
        with patch.object(adapter, "_request", return_value=True) as request:
            adapter.close()
        calls = [(item.args[0], item.args[1]) for item in request.call_args_list]
        assert calls == [
            ("POST", "/session/session-sensitive/abort"),
            ("DELETE", "/session/session-sensitive"),
        ]
        assert adapter.session_id == ""


def test_prompt_stops_when_new_artifact_is_stable():
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        artifact = workspace / "main.py"
        adapter = OpenCodeAdapter(workspace, prompt_timeout=10)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-test"
        released = threading.Event()

        def slow_request(*_args, **_kwargs):
            # Simula OpenCode che ha gia' scritto il file ma continua a parlare.
            released.wait(timeout=5)
            return {"parts": [{"type": "text", "text": "fine tardiva"}]}

        def materialize():
            time.sleep(0.15)
            artifact.write_text("print(42)\n", encoding="utf-8")

        threading.Thread(target=materialize, daemon=True).start()
        with patch.object(adapter, "_request", side_effect=slow_request), patch.object(
            adapter, "abort", side_effect=lambda: released.set()
        ) as abort:
            started = time.monotonic()
            reply = adapter.prompt("crea main.py", completion_path=artifact)

        assert time.monotonic() - started < 3
        assert reply.raw["stopped_after_artifact"] is True
        assert artifact.read_text(encoding="utf-8") == "print(42)\n"
        abort.assert_called_once()


def test_prompt_stops_early_when_no_artifact_appears():
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        artifact = workspace / "main.py"
        adapter = OpenCodeAdapter(workspace, prompt_timeout=10)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-no-artifact"
        released = threading.Event()

        def slow_request(*_args, **_kwargs):
            released.wait(timeout=5)
            return {"parts": [{"type": "text", "text": "fine tardiva"}]}

        with patch.object(adapter, "_request", side_effect=slow_request), patch.object(
            adapter, "abort", side_effect=lambda: released.set()
        ) as abort:
            started = time.monotonic()
            reply = adapter.prompt(
                "crea main.py",
                completion_path=artifact,
                no_artifact_timeout=1,
            )

        elapsed = time.monotonic() - started
        assert 0.8 <= elapsed < 3
        assert reply.raw["stopped_no_artifact"] is True
        assert not artifact.exists()
        abort.assert_called_once()


def test_prompt_treats_unchanged_existing_artifact_as_no_artifact():
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        artifact = workspace / "main.py"
        artifact.write_text("print(undefined_name)\n", encoding="utf-8")
        adapter = OpenCodeAdapter(workspace, prompt_timeout=10)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-unchanged-artifact"
        released = threading.Event()

        def slow_request(*_args, **_kwargs):
            released.wait(timeout=5)
            return {"parts": [{"type": "text", "text": "nessuna modifica"}]}

        with patch.object(adapter, "_request", side_effect=slow_request), patch.object(
            adapter, "abort", side_effect=lambda: released.set()
        ):
            reply = adapter.prompt(
                "correggi main.py",
                completion_path=artifact,
                no_artifact_timeout=1,
            )

        assert reply.raw["stopped_no_artifact"] is True
        assert artifact.read_text(encoding="utf-8") == "print(undefined_name)\n"


def test_reset_session_discards_previous_transcript():
    with TemporaryDirectory() as tmp:
        adapter = OpenCodeAdapter(tmp)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-old"
        with patch.object(
            adapter,
            "_request",
            side_effect=[{}, {}, {"id": "session-new"}],
        ) as request:
            session_id = adapter.reset_session("retry pulito")

        calls = [(item.args[0], item.args[1]) for item in request.call_args_list]
        assert calls == [
            ("POST", "/session/session-old/abort"),
            ("DELETE", "/session/session-old"),
            ("POST", "/session"),
        ]
        assert session_id == "session-new"
        assert adapter.session_id == "session-new"


def test_session_snapshot_reads_messages_before_cleanup():
    with TemporaryDirectory() as tmp:
        adapter = OpenCodeAdapter(tmp)
        adapter.base_url = "http://127.0.0.1:12345"
        adapter.session_id = "session-audit"
        transcript = [{
            "info": {"role": "assistant"},
            "parts": [{
                "type": "tool",
                "tool": "edit",
                "state": {"status": "completed"},
            }],
        }]
        with patch.object(
            adapter,
            "_request",
            side_effect=[{"id": "session-audit"}, transcript],
        ) as request:
            snapshot = adapter.session_snapshot()

        calls = [(item.args[0], item.args[1]) for item in request.call_args_list]
        assert calls == [
            ("GET", "/session/session-audit"),
            ("GET", "/session/session-audit/message"),
        ]
        assert snapshot["available"] is True
        assert snapshot["messages"] == transcript


if __name__ == "__main__":
    test_response_text_extracts_message_parts()
    test_generated_config_is_local_and_fail_closed()
    test_model_preflight_accepts_only_tool_capable_ollama_models()
    test_close_aborts_and_deletes_ephemeral_session()
    test_prompt_stops_when_new_artifact_is_stable()
    test_prompt_stops_early_when_no_artifact_appears()
    test_prompt_treats_unchanged_existing_artifact_as_no_artifact()
    test_reset_session_discards_previous_transcript()
    test_session_snapshot_reads_messages_before_cleanup()
    print("TUTTI I TEST OPENCODE ADAPTER OK")
