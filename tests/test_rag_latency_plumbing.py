#!/usr/bin/env python3
"""Regressioni pure per il plumbing RAG a bassa latenza."""
from __future__ import annotations

import numpy as np

import core.domain_gater as domain_gater
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


class _VectorDoc:
    def __init__(self, key, score, domain):
        self.id = key
        self.score = score
        self.domain = domain


class _VectorFt:
    def __init__(self, owner):
        self.owner = owner

    def search(self, _query, query_params=None):
        assert query_params and isinstance(query_params["vec"], bytes)
        self.owner.search_calls += 1
        return _Result([
            _VectorDoc("euri:memory:a", 0.10, "lavoro"),
            _VectorDoc("euri:memory:b", 0.12, "generale"),
        ])


class _FlakyVectorFt(_VectorFt):
    def search(self, _query, query_params=None):
        self.owner.search_calls += 1
        if self.owner.search_calls == 1:
            raise RuntimeError("redis temporaneamente non disponibile")
        return _Result([
            _VectorDoc("euri:memory:a", 0.10, "lavoro"),
            _VectorDoc("euri:memory:b", 0.12, "generale"),
        ])


class _VectorRedis:
    def __init__(self):
        self.search_calls = 0
        self.docs = {
            "euri:memory:a": [{
                "id": "a", "content": "A", "source": "user",
                "domain": "lavoro", "memory_scope": "personal",
            }],
            "euri:memory:b": [{
                "id": "b", "content": "B", "source": "passive",
                "domain": "generale", "memory_scope": "personal",
            }],
        }

    def ft(self, index):
        assert index == "idx:memories"
        return _VectorFt(self)

    def pipeline(self, transaction=False):
        assert transaction is False
        return _Pipeline(self.docs)

    def json(self):
        return _Json(self.docs)


class _FlakyVectorRedis(_VectorRedis):
    def ft(self, index):
        assert index == "idx:memories"
        return _FlakyVectorFt(self)


class _Embedder:
    def __init__(self):
        self.calls = 0

    def encode(self, text, mode):
        assert text == "progetto ICMA"
        assert mode == "query"
        self.calls += 1
        return np.array([0.25, 0.75], dtype=np.float32)


def test_cpu_prefetch_preserves_results_and_reuses_vector_pool():
    original_assign = domain_gater.assign_domain
    domain_gater.assign_domain = lambda _query: "lavoro"
    try:
        serial_redis = _VectorRedis()
        serial_embedder = _Embedder()
        serial = domain_gater.domain_aware_search(
            "progetto ICMA",
            serial_embedder,
            serial_redis,
            limit=5,
            memory_scope="personal",
        )

        prefetched_redis = _VectorRedis()
        prefetched_embedder = _Embedder()
        cache = domain_gater.prefetch_domain_search(
            "progetto ICMA",
            prefetched_embedder,
            prefetched_redis,
            [{"limit": 5, "memory_scope": "personal"}],
        )
        prefetched = domain_gater.domain_aware_search(
            "progetto ICMA",
            prefetched_embedder,
            prefetched_redis,
            limit=5,
            memory_scope="personal",
            query_feature_cache=cache,
        )
    finally:
        domain_gater.assign_domain = original_assign

    assert prefetched == serial
    assert prefetched_embedder.calls == 1
    assert prefetched_redis.search_calls == 1
    assert cache["hits"] == 1
    assert cache["pool_hits"] == 1


def test_cpu_prefetch_redis_failure_retries_on_serial_join():
    original_assign = domain_gater.assign_domain
    domain_gater.assign_domain = lambda _query: "lavoro"
    try:
        redis = _FlakyVectorRedis()
        embedder = _Embedder()
        cache = domain_gater.prefetch_domain_search(
            "progetto ICMA",
            embedder,
            redis,
            [{"limit": 5, "memory_scope": "personal"}],
        )
        result = domain_gater.domain_aware_search(
            "progetto ICMA",
            embedder,
            redis,
            limit=5,
            memory_scope="personal",
            query_feature_cache=cache,
        )
    finally:
        domain_gater.assign_domain = original_assign

    assert [item["id"] for item in result] == ["a", "b"]
    assert embedder.calls == 1
    assert redis.search_calls == 2
    assert cache.get("pool_hits", 0) == 0


if __name__ == "__main__":
    test_json_pipeline_preserves_request_order()
    test_active_domains_uses_index_and_excludes_superseded()
    test_cpu_prefetch_preserves_results_and_reuses_vector_pool()
    test_cpu_prefetch_redis_failure_retries_on_serial_join()
    print("test_rag_latency_plumbing: OK")
