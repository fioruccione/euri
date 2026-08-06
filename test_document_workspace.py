#!/usr/bin/env python3
"""Regressioni pure del workspace condiviso UI/voce."""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from agent.executor import Executor, ToolCall, ToolResult, ToolSpec
from core.brain import Brain
from core.document_workspace import DocumentWorkspace


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.calls = []

    def __getattr__(self, name):
        def queued(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return queued

    def execute(self):
        return [getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in self.calls]


class _Redis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.lists.pop(key, None)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start:end + 1]

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    def expire(self, key, ttl):
        return True

    def pipeline(self, transaction=True):
        return _Pipeline(self)


def test_two_processes_share_selection_and_receipt():
    redis = _Redis()
    ui = DocumentWorkspace(redis)
    voice = DocumentWorkspace(redis)
    manifest = ui.publish_documents([
        {"filename": "uno.docx", "content": "UNO", "sha256": "1" * 64},
        {"filename": "due.pdf", "content": "DUE", "sha256": "2" * 64},
    ], source_channel="silent_chat")
    assert manifest["active_artifact_id"] == ""
    assert voice.get_active() is None
    second_id = manifest["documents"][1]["id"]
    assert voice.select(second_id) is True
    assert ui.get_active()["filename"] == "due.pdf"
    updated = ui.publish_documents([
        {"filename": "due.pdf", "content": "DUE aggiornato", "sha256": "3" * 64},
    ])
    assert updated["documents"][0]["id"] == second_id
    assert updated["documents"][0]["version"] == 2
    voice.record_receipt({
        "filepath": "/tmp/revisionato.docx",
        "filename": "revisionato.docx",
        "format": "docx",
        "sha256": "a" * 64,
    })
    assert ui.snapshot()["receipts"][0]["filename"] == "revisionato.docx"


def test_conversation_receipt_is_visible_without_source_manifest():
    redis = _Redis()
    workspace = DocumentWorkspace(redis)
    workspace.record_receipt({
        "filepath": "/tmp/analisi_conversazione.docx",
        "filename": "analisi_conversazione.docx",
        "format": "docx",
        "source_kind": "recent_conversation",
        "source_scope": "current_thread",
        "source_turn_refs": ["conversation:1", "conversation:2"],
        "preview_text": "Sintesi verificabile della conversazione.",
    })
    snapshot = workspace.snapshot()
    assert snapshot["documents"] == []
    assert snapshot["active_artifact_id"] == ""
    assert snapshot["receipts"][0]["source_kind"] == "recent_conversation"
    assert snapshot["receipts"][0]["source_turn_refs"] == [
        "conversation:1", "conversation:2",
    ]
    assert "Sintesi verificabile" in snapshot["receipts"][0]["preview_text"]


def test_two_processes_share_document_operation_lifecycle():
    redis = _Redis()
    ui = DocumentWorkspace(redis)
    voice = DocumentWorkspace(redis)
    operation = ui.start_operation(
        "document_analysis",
        source_channel="silent_chat",
        filename="setup.pdf",
    )
    observed = voice.get_operation()
    assert observed["status"] == "running"
    assert observed["source_channel"] == "silent_chat"
    assert voice.claim_operation(
        operation["id"], tool_name="read_document"
    )["tool_name"] == "read_document"
    finished = ui.finish_operation(
        operation["id"], success=True, message="Documento letto."
    )
    assert finished["status"] == "completed"
    assert voice.snapshot()["operation"]["message"] == "Documento letto."


def test_executor_claims_ui_operation_and_publishes_real_outcome():
    redis = _Redis()
    ui = DocumentWorkspace(redis)
    voice = DocumentWorkspace(redis)
    operation = ui.start_operation(
        "document_analysis",
        source_channel="silent_chat",
        filename="setup.pdf",
    )
    entered = threading.Event()
    release = threading.Event()

    def _read(_params, **_kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return ToolResult(True, "Analisi completata.")

    executor = Executor()
    executor.document_workspace = voice
    executor.operation_channel = "silent_chat"
    executor._registry["read_document"] = ToolSpec(
        "read_document", "legge", {}, _read, timeout_seconds=3,
        effect="read_only", contextual=True,
    )
    result_box = []
    worker = threading.Thread(
        target=lambda: result_box.append(
            executor.execute(ToolCall("read_document", {}))
        )
    )
    worker.start()
    assert entered.wait(timeout=2)
    running = ui.get_operation()
    assert running["id"] == operation["id"]
    assert running["tool_name"] == "read_document"
    assert running["status"] == "running"
    shared_context = executor.document_action_state_context()
    assert "IN_CORSO sul canale silent_chat" in shared_context
    assert "non affermare di averlo già completato" in shared_context
    release.set()
    worker.join(timeout=3)
    assert result_box[0].success is True
    completed = ui.get_operation()
    assert completed["status"] == "completed"
    assert completed["message"] == "Analisi completata."


def test_executor_artifact_crosses_process_boundary():
    redis = _Redis()
    first = Executor.__new__(Executor)
    first._session_artifact_lock = threading.Lock()
    first._session_artifact = None
    first.document_workspace = DocumentWorkspace(redis)
    second = Executor.__new__(Executor)
    second._session_artifact_lock = threading.Lock()
    second._session_artifact = None
    second.document_workspace = DocumentWorkspace(redis)
    first._capture_session_artifact("read_document", ToolResult(
        True,
        "letto",
        raw_data={
            "artifact_documents": [{
                "filename": "conformita.docx",
                "source_path": "/tmp/conformita.docx",
                "content": "testo completo",
            }],
            "artifact_active_filename": "conformita.docx",
            "artifact_source_channel": "silent_chat",
        },
    ))
    assert second.get_session_artifact()["content"] == "testo completo"
    assert second.get_session_artifact()["filename"] == "conformita.docx"


def test_streamlit_upload_queue_keeps_latest_active():
    redis = _Redis()
    workspace = DocumentWorkspace(redis)
    first = workspace.publish_documents([
        {
            "filename": "prima.docx",
            "source_path": "/tmp/prima.docx",
            "content": "PRIMA",
            "kind": "uploaded_document",
        },
    ], active_filename="prima.docx", source_channel="silent_chat")
    first_id = first["active_artifact_id"]
    second = workspace.publish_documents([
        {
            "filename": "seconda.docx",
            "source_path": "/tmp/seconda.docx",
            "content": "SECONDA",
            "kind": "uploaded_document",
        },
    ], active_filename="seconda.docx", source_channel="silent_chat",
       preserve_existing=True)
    assert [item["filename"] for item in second["documents"]] == [
        "prima.docx", "seconda.docx",
    ]
    assert second["documents"][0]["id"] == first_id
    assert workspace.get_active()["filename"] == "seconda.docx"


def test_new_ui_registry_drops_legacy_workspace_noise():
    redis = _Redis()
    workspace = DocumentWorkspace(redis)
    workspace.publish_documents([
        {
            "filename": ".silent_chat_uploads.json",
            "source_path": "/tmp/.silent_chat_uploads.json",
            "content": "registro interno",
            "kind": "uploaded_document",
        },
        {
            "filename": "estraneo.docx",
            "source_path": "/tmp/estraneo.docx",
            "content": "vecchia scansione cartella",
            "kind": "uploaded_document",
        },
    ])
    manifest = workspace.publish_documents([
        {
            "filename": "corrente.docx",
            "source_path": "/tmp/corrente.docx",
            "content": "upload corrente",
            "kind": "uploaded_document",
        },
    ], active_filename="corrente.docx", preserve_existing=True,
       allowed_existing_paths=["/tmp/corrente.docx"])
    assert [item["filename"] for item in manifest["documents"]] == [
        "corrente.docx"
    ]


def test_only_registered_streamlit_uploads_enter_precedence_queue():
    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        old = data_dir / "vecchio.docx"
        current = data_dir / "corrente.docx"
        unrelated = data_dir / "non_caricato.docx"
        for path in (old, current, unrelated):
            path.write_text(path.stem, encoding="utf-8")
        (data_dir / ".silent_chat_uploads.json").write_text(json.dumps([
            {"path": str(current), "uploaded_at": 20},
            {"path": str(old), "uploaded_at": 10},
            {"path": str(data_dir / ".silent_chat_uploads.json"), "uploaded_at": 30},
            {"path": "/tmp/fuori.docx", "uploaded_at": 40},
        ]), encoding="utf-8")
        queue = Executor._streamlit_upload_paths(data_dir)
        assert [path.name for path in queue] == ["vecchio.docx", "corrente.docx"]
        assert unrelated not in queue


def test_read_document_uses_current_ui_upload_not_directory_contents():
    class _ReadBrain:
        def read_and_extract(self, documents, question):
            assert list(documents) == ["corrente.docx"]
            return "Documento corrente letto."

    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        current = data_dir / "corrente.docx"
        unrelated = data_dir / "non_caricato.docx"
        current.write_text("corrente", encoding="utf-8")
        unrelated.write_text("estraneo", encoding="utf-8")
        (data_dir / ".silent_chat_uploads.json").write_text(json.dumps([
            {"path": str(current), "uploaded_at": 20},
        ]), encoding="utf-8")

        redis = _Redis()
        executor = Executor()
        executor.document_workspace = DocumentWorkspace(redis)
        executor._code_runner._input_dir = data_dir
        seen_paths = []
        executor._code_runner._preextract_files = lambda _brain, paths=None: (
            seen_paths.extend(paths or [])
            or {path.name: path.read_text(encoding="utf-8") for path in (paths or [])}
        )
        had_shared = hasattr(Brain, "_shared_instance")
        old_shared = getattr(Brain, "_shared_instance", None)
        Brain._shared_instance = _ReadBrain()
        try:
            result = executor.execute(ToolCall(
                "read_document",
                {"question": "Leggi il file appena caricato: corrente.docx"},
            ))
        finally:
            if had_shared:
                Brain._shared_instance = old_shared
            else:
                delattr(Brain, "_shared_instance")
        assert result.success is True
        assert seen_paths == [current]
        assert executor.document_workspace.get_active()["filename"] == "corrente.docx"
        assert unrelated.name not in [
            item["filename"]
            for item in executor.document_workspace.snapshot()["documents"]
        ]


def test_live_continuity_merge_is_idempotent_and_not_passive():
    brain = Brain()
    docs = [
        {
            "turn_ref": "turn:voice:1",
            "conversation_id": "voice",
            "seq": 1,
            "role": "user",
            "content": "Modifica il documento caricato dalla UI.",
            "trusted": True,
            "observed_at": 10.0,
            "memory_scope": "personal",
        },
        {
            "turn_ref": "turn:voice:2",
            "conversation_id": "voice",
            "seq": 2,
            "role": "assistant",
            "content": "Va bene, userò quello attivo.",
            "trusted": True,
            "observed_at": 11.0,
            "memory_scope": "personal",
        },
    ]
    assert brain.sync_continuity(docs, memory_scope="personal") == 2
    assert brain.sync_continuity(docs, memory_scope="personal") == 0
    assert len(brain._conversation_history) == 2
    assert all(item["restored_context"] for item in brain._conversation_history)
    assert list(brain._passive_journal) == []


if __name__ == "__main__":
    test_two_processes_share_selection_and_receipt()
    test_conversation_receipt_is_visible_without_source_manifest()
    test_two_processes_share_document_operation_lifecycle()
    test_executor_claims_ui_operation_and_publishes_real_outcome()
    test_executor_artifact_crosses_process_boundary()
    test_streamlit_upload_queue_keeps_latest_active()
    test_new_ui_registry_drops_legacy_workspace_noise()
    test_only_registered_streamlit_uploads_enter_precedence_queue()
    test_read_document_uses_current_ui_upload_not_directory_contents()
    test_live_continuity_merge_is_idempotent_and_not_passive()
    print("test_document_workspace: OK")
