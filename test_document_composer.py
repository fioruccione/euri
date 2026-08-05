#!/usr/bin/env python3
"""Regressioni pure per artefatti di sessione e documenti verificati."""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from agent.executor import Executor, ToolResult
from agent.tools import document_composer, text_writer
from core.action_controller import (
    ActionAuthority, ActionDecision, ActionDisposition, ActionProposal,
)


PLAN = {
    "title": "Relazione di prova",
    "subtitle": "Versione revisionata",
    "blocks": [
        {"type": "heading", "level": 1, "text": "Sintesi"},
        {"type": "paragraph", "text": "Il dato verificato è 42 °C."},
        {"type": "bullet_list", "items": ["Primo punto", "Secondo punto"]},
        {
            "type": "table",
            "headers": ["Parametro", "Valore"],
            "rows": [["Temperatura", "42 °C"]],
        },
    ],
}


class _JsonChat:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(PLAN, ensure_ascii=False))
        )


class _Brain:
    def __init__(self):
        self.history_lock = threading.Lock()
        self._conversation_history = [
            {"role": "assistant", "content": "Renderei più chiara la sintesi."}
        ]
        self.injected = []

    def inject_tool_result(self, *args):
        self.injected.append(args)


class _DirectController:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, *_args, **_kwargs):
        return self.proposal

    def decide(self, proposal, _capabilities):
        return ActionDecision(ActionDisposition.EXECUTE, proposal, "test")


class _AbstainController:
    def propose(self, *_args, **_kwargs):
        return None

    def decide(self, proposal, _capabilities):
        return ActionDecision(ActionDisposition.ABSTAIN, proposal, "no_proposal")


def test_plan_is_structured_and_grounded_in_full_source():
    chat = _JsonChat()
    source = "Bozza completa con il dato 42 °C e il nome Gio Style."
    plan = document_composer.build_document_plan(
        source,
        "Riorganizza senza aggiungere fatti.",
        recent_context="ASSISTANT: usa sezioni più chiare",
        chat=chat,
        model="fake",
    )
    assert plan == PLAN
    prompt = chat.calls[0]["messages"][0]["content"]
    assert source in prompt
    assert "usa sezioni più chiare" in prompt
    assert chat.calls[0]["format"] == "json"


def test_txt_docx_pdf_are_reopened_and_verified():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for fmt in ("txt", "docx", "pdf"):
            receipt = document_composer.render_document(
                PLAN, root, fmt=fmt, filename="relazione"
            )
            path = Path(receipt["filepath"])
            assert path.exists() and path.stat().st_size == receipt["bytes"]
            assert receipt["format"] == fmt
            assert len(receipt["sha256"]) == 64
            assert receipt["validation"]["nonempty"] is True
        assert (root / "relazione.txt").read_text(encoding="utf-8").count("42 °C") == 2


def test_existing_output_is_never_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = document_composer.render_document(PLAN, root, fmt="txt", filename="bozza")
        second = document_composer.render_document(PLAN, root, fmt="txt", filename="bozza")
        assert first["filepath"] != second["filepath"]
        assert Path(first["filepath"]).exists() and Path(second["filepath"]).exists()


def test_clipboard_preview_keeps_complete_operational_artifact():
    original = text_writer._read_clipboard_text
    full = "A" * 3792
    try:
        text_writer._read_clipboard_text = lambda: full
        result = text_writer.tool_clipboard_read({})
    finally:
        text_writer._read_clipboard_text = original
    assert result.success is True
    assert len(result.output) < 400
    assert result.raw_data["artifact_content"] == full
    executor = Executor.__new__(Executor)
    executor._session_artifact_lock = threading.Lock()
    executor._session_artifact = None
    executor._capture_session_artifact("clipboard_read", result)
    assert executor.get_session_artifact()["content"] == full
    executor._capture_session_artifact(
        "clipboard_read", ToolResult(False, "appunti non disponibili")
    )
    assert executor.get_session_artifact() is None


def test_semantic_dispatch_uses_exact_request_and_returns_real_receipt():
    with tempfile.TemporaryDirectory() as tmp:
        executor = Executor()
        executor.brain = _Brain()
        executor._session_artifact = {
            "id": "artifact:test",
            "kind": "uploaded_documents",
            "source": "ui",
            "filenames": ["bozza.docx"],
            "content": "Contenuto completo della bozza: 42 °C.",
            "captured_at": __import__("time").time(),
        }
        received = {}
        original_builder = document_composer.build_document_plan
        original_handler = executor._registry["compose_document"].handler
        try:
            document_composer.build_document_plan = lambda source, instruction, **kwargs: (
                received.update({"source": source, "instruction": instruction}) or PLAN
            )
            executor._registry["compose_document"].handler = lambda params, **kwargs: (
                document_composer.compose_document_tool(
                    params,
                    artifact=executor.get_session_artifact(),
                    recent_context=executor._recent_document_context(),
                    output_dir=Path(tmp),
                )
            )
            proposal = ActionProposal(
                capability="executor.compose_document",
                args={"instruction": "parafrasi non autorizzata", "format": "docx"},
                target_id=None,
                authority=ActionAuthority.USER_EXPLICIT,
                confidence=0.99,
            )
            utterance = "Genera direttamente un documento Word con le modifiche suggerite."
            result = executor.dispatch_contextual_action(
                utterance,
                previous_euri_turn="Ho suggerito una sintesi più chiara.",
                controller=_DirectController(proposal),
            )
        finally:
            document_composer.build_document_plan = original_builder
            executor._registry["compose_document"].handler = original_handler
        assert result["success"] is True
        assert received["instruction"] == utterance
        assert "42 °C" in received["source"]
        assert Path(result["raw_data"]["filepath"]).exists()
        assert result["raw_data"]["validation"]["nonempty"] is True
        assert executor.brain.injected


def test_semantic_abstain_is_fail_closed_not_chat():
    executor = Executor()
    result = executor.dispatch_contextual_action(
        "Preparamelo e salvalo.", controller=_AbstainController()
    )
    assert result["success"] is False
    assert result["fail_closed"] is True
    assert "Non ho eseguito nulla" in result["output"]


if __name__ == "__main__":
    test_plan_is_structured_and_grounded_in_full_source()
    test_txt_docx_pdf_are_reopened_and_verified()
    test_existing_output_is_never_overwritten()
    test_clipboard_preview_keeps_complete_operational_artifact()
    test_semantic_dispatch_uses_exact_request_and_returns_real_receipt()
    test_semantic_abstain_is_fail_closed_not_chat()
    print("test_document_composer: OK")
