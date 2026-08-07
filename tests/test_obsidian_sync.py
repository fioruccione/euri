#!/usr/bin/env python3
"""Regressioni pure per la sincronizzazione Obsidian cross-process."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from utils.obsidian_sync import (
    ObsidianSyncManager,
    canonical_memory_body,
    parse_vault_markdown,
    unwrap_generated_memory_content,
)


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, path="$"):
        doc = self.redis.docs.get(str(key))
        if doc is None:
            return None
        if path == "$":
            return [doc]
        return [doc.get(path.removeprefix("$."))]

    def set(self, key, path, value):
        field = path.removeprefix("$.")
        self.redis.docs[str(key)][field] = deepcopy(value)
        return True


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def json(self):
        return self

    def set(self, key, path, value):
        self.commands.append((key, path, deepcopy(value)))
        return self

    def execute(self):
        for key, path, value in self.commands:
            self.redis.json().set(key, path, value)
        self.redis.pipeline_executes += 1
        return [True] * len(self.commands)


class FakeRedis:
    def __init__(self, doc):
        self.docs = {f"euri:memory:{doc['id']}": deepcopy(doc)}
        self.pipeline_executes = 0
        self._json = FakeJson(self)

    def json(self):
        return self._json

    def pipeline(self, transaction=True):
        assert transaction is True
        return FakePipeline(self)


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, text):
        self.calls.append(text)
        return np.asarray([0.1, 0.2], dtype=np.float32)


def _generated_markdown(body: str) -> str:
    return f"""---
id: m1
type: memory
memory_kind: semantic_fact
domain: automotive
source: user
memory_scope: personal
created_at: '2026-08-04 21:33:36'
---
# Memoria (2026-08-04 21:33:36)

{body}
"""


def test_canonical_body_removes_only_the_generated_heading():
    frontmatter, body = parse_vault_markdown(_generated_markdown("Fatto pulito."))
    assert canonical_memory_body(frontmatter, body) == "Fatto pulito."
    assert canonical_memory_body(frontmatter, "# Titolo manuale\n\nFatto.") == (
        "# Titolo manuale\n\nFatto."
    )
    clean, changed = unwrap_generated_memory_content({
        "created_at": 1785872016.0,
        "content": "# Memoria (2026-08-04 21:33:36)\n\nFatto pulito.",
    })
    assert changed and clean == "Fatto pulito."


def test_cross_process_self_write_is_a_noop_without_external_pulse():
    redis = FakeRedis({"id": "m1", "content": "Fatto pulito.", "embedding": [0.0]})
    embedder = FakeEmbedder()
    manager = ObsidianSyncManager(redis, embedder)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Memory_m1.md"
        path.write_text(_generated_markdown("Fatto pulito."), encoding="utf-8")
        with (
            patch("utils.obsidian_sync.time.sleep"),
            patch("utils.obsidian_sync.pulse_emit") as pulse,
        ):
            manager._process_file(str(path))

    assert embedder.calls == []
    assert redis.pipeline_executes == 0
    pulse.assert_not_called()


def test_manual_edit_updates_once_and_duplicate_event_becomes_noop():
    redis = FakeRedis({"id": "m1", "content": "Fatto vecchio.", "embedding": [0.0]})
    embedder = FakeEmbedder()
    manager = ObsidianSyncManager(redis, embedder)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "Memory_m1.md"
        path.write_text(_generated_markdown("Fatto corretto."), encoding="utf-8")
        with (
            patch("utils.obsidian_sync.time.sleep"),
            patch("utils.obsidian_sync.pulse_emit") as pulse,
        ):
            manager._process_file(str(path))
            manager._process_file(str(path))

    doc = redis.docs["euri:memory:m1"]
    assert doc["content"] == "Fatto corretto."
    assert doc["embedding"] == [0.10000000149011612, 0.20000000298023224]
    assert embedder.calls == ["Fatto corretto."]
    assert redis.pipeline_executes == 1
    pulse.assert_called_once()


if __name__ == "__main__":
    test_canonical_body_removes_only_the_generated_heading()
    test_cross_process_self_write_is_a_noop_without_external_pulse()
    test_manual_edit_updates_once_and_duplicate_event_becomes_noop()
    print("test_obsidian_sync: OK")
