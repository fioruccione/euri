#!/usr/bin/env python3
"""Regressioni: analizzare la clipboard non deve implicare memorizzarla."""

from agent.executor import Executor
from agent.tools import text_writer
from core import brain as brain_module
from core.brain import Brain
from types import SimpleNamespace
import tempfile
from pathlib import Path


class FakeBrain:
    pass


class FakeMemory:
    def __init__(self, memory_id="memory-1"):
        self.saved = []
        self.memory_id = memory_id

    def save_memory(self, **kwargs):
        self.saved.append(kwargs)
        return self.memory_id


def _run_text_analysis(*, persist: bool, memory_id="memory-1"):
    original_image = text_writer._clipboard_image
    original_read = text_writer._read_clipboard_text
    original_analyze = text_writer._analyze_text_full
    memory = FakeMemory(memory_id=memory_id)
    try:
        text_writer._clipboard_image = lambda: None
        text_writer._read_clipboard_text = lambda: "Documento temporaneo di terzi"
        text_writer._analyze_text_full = lambda _text, _cfg, _brain: "Sintesi controllata"
        tool = (
            text_writer.tool_clipboard_analyze_save
            if persist else text_writer.tool_clipboard_analyze
        )
        result = tool({}, brain=FakeBrain(), memory=memory)
        return result, memory
    finally:
        text_writer._clipboard_image = original_image
        text_writer._read_clipboard_text = original_read
        text_writer._analyze_text_full = original_analyze


def test_plain_analysis_is_session_only():
    result, memory = _run_text_analysis(persist=False)
    assert result.success is True
    assert result.raw_data["persisted"] is False
    assert result.raw_data["memory_id"] is None
    assert memory.saved == []
    assert "Non ho salvato nulla" in result.output


def test_explicit_analysis_and_save_persists_teach_memory():
    result, memory = _run_text_analysis(persist=True)
    assert result.success is True
    assert result.raw_data["persisted"] is True
    assert result.raw_data["memory_id"] == "memory-1"
    assert len(memory.saved) == 1
    assert memory.saved[0]["source"] == "teach"
    assert memory.saved[0]["tags"] == ["clipboard", "testo"]
    assert memory.saved[0]["memory_kind"] == "document_summary"
    assert "Ho salvato la sintesi" in result.output


def test_explicit_save_reports_rejection_truthfully():
    result, memory = _run_text_analysis(persist=True, memory_id=None)
    assert len(memory.saved) == 1
    assert result.success is False
    assert result.raw_data["persisted"] is False
    assert result.raw_data["memory_id"] is None
    assert "non sono riuscito a salvarla" in result.output


def test_clipboard_routing_requires_explicit_save_language():
    executor = Executor()
    cases = {
        "analizza gli appunti": "clipboard_analyze",
        "riassumi il testo nella clipboard": "clipboard_analyze",
        "salva il contenuto degli appunti": "clipboard_analyze_save",
        "analizza e salva gli appunti": "clipboard_analyze_save",
        "analizza gli appunti e memorizza il risultato": "clipboard_analyze_save",
        "analizza gli appunti senza salvare": "clipboard_analyze",
        "analizza gli appunti ma non salvarli": "clipboard_analyze",
    }
    for utterance, expected in cases.items():
        call = executor.select_tool_by_regex(utterance)
        assert call is not None, utterance
        assert call.tool_name == expected, (utterance, call.tool_name)

    assert executor.select_tool_by_regex("non analizzare gli appunti") is None


def test_clipboard_rejects_non_png_bytes_from_image_target():
    original_run = text_writer.subprocess.run
    try:
        text_writer.subprocess.run = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"testo o html dichiarato erroneamente come immagine",
        )
        assert text_writer._clipboard_image() is None
    finally:
        text_writer.subprocess.run = original_run


def test_failed_clipboard_image_analysis_is_not_reported_as_success():
    original_image = text_writer._clipboard_image

    class FailedVisionBrain:
        def analyze_image(self, *_args, **_kwargs):
            return ""

    with tempfile.TemporaryDirectory(prefix="euri-clipboard-test-") as temp_dir:
        image_path = Path(temp_dir) / "clipboard.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        try:
            text_writer._clipboard_image = lambda: str(image_path)
            result = text_writer.tool_clipboard_analyze(
                {},
                brain=FailedVisionBrain(),
                memory=FakeMemory(),
            )
        finally:
            text_writer._clipboard_image = original_image

    assert result.success is False
    assert result.error == "image analysis failed"
    assert "non sono riuscito" in result.output.lower()


def test_brain_image_failure_returns_no_fake_description():
    original_chat = brain_module.chat_client.chat
    try:
        def _fail(**_kwargs):
            raise RuntimeError("vision unavailable")

        brain_module.chat_client.chat = _fail
        brain = Brain.__new__(Brain)
        assert brain.analyze_image("/tmp/immagine-inesistente.png") == ""
    finally:
        brain_module.chat_client.chat = original_chat


def test_only_temporary_analysis_is_contextually_proposable():
    executor = Executor()
    capabilities = {
        item["name"]: item for item in executor.get_contextual_capabilities()
    }
    assert capabilities["clipboard_analyze"]["effect"] == "read_only"
    assert "clipboard_analyze_save" not in capabilities


if __name__ == "__main__":
    test_plain_analysis_is_session_only()
    test_explicit_analysis_and_save_persists_teach_memory()
    test_explicit_save_reports_rejection_truthfully()
    test_clipboard_routing_requires_explicit_save_language()
    test_clipboard_rejects_non_png_bytes_from_image_target()
    test_failed_clipboard_image_analysis_is_not_reported_as_success()
    test_brain_image_failure_returns_no_fake_description()
    test_only_temporary_analysis_is_contextually_proposable()
    print("test_clipboard_persistence: OK")
