#!/usr/bin/env python3
"""Regressioni pure per il plumbing RAG a bassa latenza."""
from __future__ import annotations

from core.memory_manager import MemoryManager


class _Json:
    def __init__(self, docs, queue=None):
        self.docs = docs
        self.queue = queue

    def get(self, key, path):
        assert path == "$"
        if self.queue is not None:
            self.queue.append(key)
            return self
        return self.docs.get(key)


class _Pipeline:
    def __init__(self, docs):
        self.docs = docs
        self.queue = []

    def json(self):
        return _Json(self.docs, self.queue)

    def execute(self):
        return [self.docs.get(key) for key in self.queue]


class _Doc:
    def __init__(self, domain, superseded_by=None):
        self.domain = domain
        if superseded_by is not None:
            self.superseded_by = superseded_by


class _Result:
    def __init__(self, docs):
        self.docs = docs


class _Ft:
    def __init__(self, docs):
        self.docs = docs

    def search(self, _query):
        return _Result(self.docs)


class _Redis:
    def __init__(self):
        self.docs = {"a": [{"id": "a"}], "b": [{"id": "b"}]}

    def pipeline(self, transaction=False):
        assert transaction is False
        return _Pipeline(self.docs)

    def json(self):
        return _Json(self.docs)

    def ft(self, index):
        assert index == "idx:memories"
        return _Ft([
            _Doc("automotive"),
            _Doc("chimica polimeri"),
            _Doc("dominio vecchio", superseded_by="new-id"),
        ])

    def scan_iter(self, _pattern):
        raise AssertionError("il fast path indicizzato non deve fare SCAN")


def test_json_pipeline_preserves_request_order():
    manager = MemoryManager(_Redis())
    assert manager._json_get_many(["b", "missing", "a"]) == [
        [{"id": "b"}],
        None,
        [{"id": "a"}],
    ]


def test_active_domains_uses_index_and_excludes_superseded():
    manager = MemoryManager(_Redis())
    assert manager._active_domains() == {"automotive", "chimica polimeri"}


if __name__ == "__main__":
    test_json_pipeline_preserves_request_order()
    test_active_domains_uses_index_and_excludes_superseded()
    print("test_rag_latency_plumbing: OK")
