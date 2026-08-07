#!/usr/bin/env python3
"""La convergenza interna non deve essere presentata come validazione esterna."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config
from core.rag_context import insight_requires_external_validation
from core.dream_engine import DreamEngine
from utils.obsidian_sync import write_insight


class _JSON:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key, _path="$"):
        doc = self.docs.get(key)
        return [doc] if doc is not None else None

    def set(self, key, path, value):
        self.docs[key][path.removeprefix("$.")] = value


class _Redis:
    def __init__(self, docs):
        self.docs = docs
        self._json = _JSON(docs)

    def get(self, _key):
        return None

    def scan_iter(self, pattern):
        assert pattern == "euri:insight:*"
        return iter(self.docs)

    def json(self):
        return self._json


def test_legacy_promoted_without_external_evidence_is_tentative():
    assert insight_requires_external_validation({"status": "promoted"}) is True


def test_internal_convergence_remains_tentative():
    assert insight_requires_external_validation({
        "status": "promoted",
        "requires_verification": True,
        "verification_status": "internally_convergent",
        "external_reaction": {},
    }) is True


def test_external_confirmation_crosses_the_boundary():
    assert insight_requires_external_validation({
        "status": "promoted",
        "requires_verification": False,
        "verification_status": "externally_confirmed_by_owner",
        "external_reaction": {"verdict": "CONFERMA"},
    }) is False


def test_confirmation_does_not_hide_later_uncertainty():
    assert insight_requires_external_validation({
        "status": "promoted",
        "requires_verification": True,
        "external_reaction": {"verdict": "CONFERMA"},
    }) is True


def test_boot_reconciliation_separates_legacy_internal_and_external_states():
    docs = {
        "euri:insight:internal": {"status": "promoted"},
        "euri:insight:confirmed": {
            "status": "promoted",
            "external_reaction": {"verdict": "CONFERMA"},
        },
        "euri:insight:partial": {
            "status": "promoted",
            "external_reaction": {"verdict": "PARZIALE"},
        },
        "euri:insight:candidate": {"status": "candidate"},
    }
    engine = DreamEngine(_Redis(docs), embedder=None)
    assert engine._reconcile_insight_epistemic_state() == (2, 1)
    assert docs["euri:insight:internal"]["requires_verification"] is True
    assert docs["euri:insight:internal"]["epistemic_status"] == "internally_convergent"
    assert docs["euri:insight:confirmed"]["requires_verification"] is False
    assert docs["euri:insight:confirmed"]["epistemic_status"] == "externally_confirmed"
    assert docs["euri:insight:partial"]["epistemic_status"] == "partially_refuted"
    assert "epistemic_status" not in docs["euri:insight:candidate"]


def test_obsidian_projection_names_internal_state_and_cannot_collide():
    with (
        TemporaryDirectory() as tmp,
        patch.object(config, "OBSIDIAN_SYNC_ENABLED", True),
        patch.object(config, "OBSIDIAN_VAULT_PATH", tmp),
        patch("utils.obsidian_sync._mark_ignored"),
    ):
        write_insight({
            "id": "abcdef12-one",
            "status": "promoted",
            "content": "Connessione A.",
            "domain_a": "a",
            "domain_b": "b",
            "created_at": 1784790000.0,
            "promoted_at": 1784790000.0,
            "requires_verification": True,
            "epistemic_status": "internally_convergent",
            "verification_status": "internally_convergent",
        })
        write_insight({
            "id": "12345678-two",
            "status": "promoted",
            "content": "Connessione B.",
            "domain_a": "a",
            "domain_b": "b",
            "created_at": 1784790000.0,
            "promoted_at": 1784790000.0,
            "requires_verification": True,
        })
        files = sorted((Path(tmp) / "Insights").glob("*.md"))
        assert len(files) == 2
        assert files[0].name != files[1].name
        text = next(p.read_text(encoding="utf-8") for p in files if "abcdef12" in p.name)
        assert "Emersa Internamente" in text
        assert "requires_verification: true" in text


if __name__ == "__main__":
    test_legacy_promoted_without_external_evidence_is_tentative()
    test_internal_convergence_remains_tentative()
    test_external_confirmation_crosses_the_boundary()
    test_confirmation_does_not_hide_later_uncertainty()
    test_boot_reconciliation_separates_legacy_internal_and_external_states()
    test_obsidian_projection_names_internal_state_and_cannot_collide()
    print("test_insight_epistemic_boundary: OK")
