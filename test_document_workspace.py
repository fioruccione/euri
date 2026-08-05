#!/usr/bin/env python3
"""Regressioni pure del workspace condiviso UI/voce."""
from __future__ import annotations

import json
import threading

from agent.executor import Executor, ToolResult
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
    test_executor_artifact_crosses_process_boundary()
    test_live_continuity_merge_is_idempotent_and_not_passive()
    print("test_document_workspace: OK")
