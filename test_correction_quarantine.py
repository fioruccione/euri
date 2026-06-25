#!/usr/bin/env python3
"""Regression per quarantena immediata delle correzioni nello stesso contesto."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.memory_manager import MemoryManager
from core.dream_engine import DreamEngine
from core.memory_risk import memory_verification_suffix


class FakeJSON:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key, _path="$"):
        doc = self.docs.get(key)
        return [doc] if doc is not None else None

    def set(self, key, path, value, **_kwargs):
        if path == "$":
            self.docs[key] = value
            return True
        field = path.removeprefix("$.")
        self.docs.setdefault(key, {})[field] = value
        return True


class FakeRedis:
    def __init__(self, docs):
        self.j = FakeJSON(docs)
        self.expired = []
        self.zremoved = []
        self.zsets = {}
        self.stream = []
        self.integrity_failures = []

    def json(self):
        return self.j

    def expire(self, key, seconds):
        self.expired.append((key, seconds))

    def zrem(self, key, member):
        self.zremoved.append((key, member))
        self.zsets.setdefault(key, {}).pop(member, None)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def xadd(self, *args, **kwargs):
        self.stream.append((args, kwargs))


def test_explicit_same_context_correction_quarantines_only_matching_memory():
    docs = {
        "euri:memory:food": {
            "id": "food",
            "content": "Stefano ha scoperto che l'abbinamento tra fragole e cipolla è di suo gradimento.",
            "requires_verification": False,
        },
        "euri:memory:other": {
            "id": "other",
            "content": "Il Progetto Poseidon riguarda un pallet aperto per sacconi.",
            "requires_verification": False,
        },
    }
    memory = MemoryManager(FakeRedis(docs), embedder=None)

    sid = memory.save_correction_signal(
        prompt_originale="Come lo memorizzeresti?",
        risposta_euri="Preferenza alimentare di Stefano: fragole e cipolla.",
        correzione_user=(
            "In realtà era una provocazione: non ho davvero scoperto che "
            "fragole e cipolla mi piacciono."
        ),
        rag_ctx_ids=["food", "other"],
    )

    signal = docs[f"euri:correction:{sid}"]
    assert signal["quarantined_memory_ids"] == ["food"]
    assert docs["euri:memory:food"]["requires_verification"] is True
    assert docs["euri:memory:food"]["correction_pending"] is True
    assert docs["euri:memory:food"]["correction_signal_id"] == sid
    assert docs["euri:memory:food"]["correction_pending_prev_requires_verification"] is False
    assert docs["euri:memory:other"].get("correction_pending") is None
    assert docs["euri:memory:other"]["requires_verification"] is False


def test_joke_clarification_is_detected_as_correction_signal():
    memory = MemoryManager(FakeRedis({}), embedder=None)

    assert memory.detect_correction(
        "No, stavo scherzando, ti prendevo in giro, dovrebbe essere una schifezza."
    )


def test_correction_pending_suffix_is_stronger_than_generic_verification():
    suffix = memory_verification_suffix({
        "requires_verification": True,
        "correction_pending": True,
    })

    assert "DATO DA VERIFICARE" in suffix
    assert "correzione in sospeso" in suffix


def test_soft_correction_does_not_quarantine_immediately():
    docs = {
        "euri:memory:food": {
            "id": "food",
            "content": "Stefano ha scoperto che l'abbinamento tra fragole e cipolla è di suo gradimento.",
            "requires_verification": False,
        },
    }
    memory = MemoryManager(FakeRedis(docs), embedder=None)

    sid = memory.save_correction_signal(
        prompt_originale="x",
        risposta_euri="x",
        correzione_user="Forse era solo un esempio da prendere con cautela.",
        rag_ctx_ids=["food"],
    )

    assert docs[f"euri:correction:{sid}"]["quarantined_memory_ids"] == []
    assert docs["euri:memory:food"].get("correction_pending") is None
    assert docs["euri:memory:food"]["requires_verification"] is False


def test_tied_max_score_quarantines_all_tied_targets():
    docs = {
        "euri:memory:a": {
            "id": "a",
            "content": "Stefano ha provato fragole e cipolla come preferenza alimentare.",
            "requires_verification": False,
        },
        "euri:memory:b": {
            "id": "b",
            "content": "Stefano ha citato fragole e cipolla come abbinamento personale.",
            "requires_verification": False,
        },
        "euri:memory:c": {
            "id": "c",
            "content": "Il Progetto Poseidon riguarda un pallet aperto.",
            "requires_verification": False,
        },
    }
    memory = MemoryManager(FakeRedis(docs), embedder=None)

    sid = memory.save_correction_signal(
        prompt_originale="x",
        risposta_euri="x",
        correzione_user="Era una provocazione: fragole e cipolla non mi piacciono davvero.",
        rag_ctx_ids=["a", "b", "c"],
    )

    assert set(docs[f"euri:correction:{sid}"]["quarantined_memory_ids"]) == {"a", "b"}
    assert docs["euri:memory:a"]["correction_pending"] is True
    assert docs["euri:memory:b"]["correction_pending"] is True
    assert docs["euri:memory:c"].get("correction_pending") is None


def _engine_with_docs(docs):
    engine = DreamEngine(r=FakeRedis(docs), embedder=None)
    engine._integrity_failure = lambda *args: engine._r.integrity_failures.append(args)
    return engine


def test_settle_not_a_correction_preserves_preexisting_requires_verification_true():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "content": "Memoria gia incerta.",
            "requires_verification": True,
            "correction_pending": True,
            "correction_signal_id": "s1",
            "correction_pending_prev_requires_verification": True,
            "audit_flag": 0,
        }
    }
    engine = _engine_with_docs(docs)

    engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, "not_a_correction")

    assert docs["euri:memory:m1"]["correction_pending"] is False
    assert docs["euri:memory:m1"]["requires_verification"] is True


def test_settle_skips_requires_restore_when_audit_flag_present_but_closes_pending():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "content": "Memoria auditata.",
            "requires_verification": True,
            "correction_pending": True,
            "correction_signal_id": "s1",
            "correction_pending_prev_requires_verification": False,
            "audit_flag": 1,
        }
    }
    engine = _engine_with_docs(docs)

    engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, "not_a_correction")

    assert docs["euri:memory:m1"]["correction_pending"] is False
    assert docs["euri:memory:m1"]["requires_verification"] is True


def test_settle_is_idempotent_for_same_signal():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "content": "Memoria sotto correzione.",
            "requires_verification": True,
            "correction_pending": True,
            "correction_signal_id": "s1",
            "correction_pending_prev_requires_verification": False,
            "audit_flag": 0,
        }
    }
    engine = _engine_with_docs(docs)
    doc = {"id": "s1", "quarantined_memory_ids": ["m1"]}

    engine._settle_correction_quarantine(doc, "not_a_correction")
    engine._settle_correction_quarantine(doc, "not_a_correction")

    assert docs["euri:memory:m1"]["correction_pending"] is False
    assert docs["euri:memory:m1"]["requires_verification"] is False
    assert engine._r.integrity_failures == []


def test_settle_bad_reasoning_restores_like_not_a_correction():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "content": "Memoria sotto correzione.",
            "requires_verification": True,
            "correction_pending": True,
            "correction_signal_id": "s1",
            "correction_pending_prev_requires_verification": False,
            "audit_flag": 0,
        }
    }
    engine = _engine_with_docs(docs)

    engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, "bad_reasoning")

    assert docs["euri:memory:m1"]["correction_pending"] is False
    assert docs["euri:memory:m1"]["requires_verification"] is False


def test_settle_bad_memory_or_ambiguous_keeps_requires_verification_true():
    for verdict in ("bad_memory", "ambiguous"):
        docs = {
            "euri:memory:m1": {
                "id": "m1",
                "content": f"Memoria sotto correzione {verdict}.",
                "requires_verification": True,
                "correction_pending": True,
                "correction_signal_id": "s1",
                "correction_pending_prev_requires_verification": False,
                "audit_flag": 0,
            }
        }
        engine = _engine_with_docs(docs)

        engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, verdict)

        assert docs["euri:memory:m1"]["correction_pending"] is False
        assert docs["euri:memory:m1"]["requires_verification"] is True


def test_settle_reindexes_loop2e_when_memory_becomes_candidate_again():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "source": "passive",
            "content": "Memoria consolidabile dopo falso positivo.",
            "requires_verification": True,
            "correction_pending": True,
            "correction_signal_id": "s1",
            "correction_pending_prev_requires_verification": False,
            "audit_flag": 0,
            "recalled_count": 3,
            "last_recalled_at": 1782390000.0,
            "created_at": 1782380000.0,
            "embedding": [0.1, 0.2],
            "memory_axes": {"subject_status": "explicit"},
        }
    }
    engine = _engine_with_docs(docs)

    engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, "not_a_correction")

    assert docs["euri:memory:m1"]["correction_pending"] is False
    assert docs["euri:memory:m1"]["requires_verification"] is False
    assert "m1" in engine._r.zsets.get("euri:idx:loop2e:candidates", {})


def test_loop2f_skips_correction_pending_candidates():
    pending = {
        "id": "m1",
        "source": "passive",
        "requires_verification": True,
        "correction_pending": True,
    }
    normal = {
        "id": "m2",
        "source": "passive",
        "requires_verification": True,
        "correction_pending": False,
    }

    assert not DreamEngine._loop2f_candidate_allowed(pending, skip_sources={"web"})
    assert DreamEngine._loop2f_candidate_allowed(normal, skip_sources={"web"})


def test_partial_crash_without_signal_link_is_not_settled():
    docs = {
        "euri:memory:m1": {
            "id": "m1",
            "content": "Memoria parzialmente scritta.",
            "requires_verification": True,
            "correction_pending": True,
            # Manca correction_signal_id e prev: settle non deve toccarla.
        }
    }
    engine = _engine_with_docs(docs)

    engine._settle_correction_quarantine({"id": "s1", "quarantined_memory_ids": ["m1"]}, "not_a_correction")

    assert docs["euri:memory:m1"]["correction_pending"] is True
    assert docs["euri:memory:m1"]["requires_verification"] is True


if __name__ == "__main__":
    test_explicit_same_context_correction_quarantines_only_matching_memory()
    test_joke_clarification_is_detected_as_correction_signal()
    test_correction_pending_suffix_is_stronger_than_generic_verification()
    test_soft_correction_does_not_quarantine_immediately()
    test_tied_max_score_quarantines_all_tied_targets()
    test_settle_not_a_correction_preserves_preexisting_requires_verification_true()
    test_settle_skips_requires_restore_when_audit_flag_present_but_closes_pending()
    test_settle_is_idempotent_for_same_signal()
    test_settle_bad_reasoning_restores_like_not_a_correction()
    test_settle_bad_memory_or_ambiguous_keeps_requires_verification_true()
    test_settle_reindexes_loop2e_when_memory_becomes_candidate_again()
    test_loop2f_skips_correction_pending_candidates()
    test_partial_crash_without_signal_link_is_not_settled()
    print("test_correction_quarantine: OK")
