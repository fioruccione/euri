#!/usr/bin/env python3
"""Regressioni preregistrate CORR-01 sul caso organico ICMA2."""

import json
from unittest.mock import patch

import config
from core.correction_resolver import (
    build_correction_evidence,
    select_correction_target,
)
from core.memory_manager import MemoryManager
from core.save_service import _resolve_content, _save_or_merge, resolve_pending_correction
from core.semantic_turn import filter_passive_memory_history


OLD_ID = "bc8f7583-b331-4eff-a606-c6d3afed7bbf"
PASSIVE_ID = "78399f5d-6fe1-4581-8d16-a28f1f882401"
NEW_FACT = "La macchina ICMA 2 utilizza una pompa FIMIC FPP20 e un filtro FIMIC RAS500."
OLD_FACT = (
    "Progetto per l'estrusore ICMA 2: sostituzione della pompa a ingranaggi "
    "con la pompa a pistoni FIMIC FPP20 tra la bivite e il filtro FIMIC LAS 500. "
    "L'obiettivo è aumentare la produzione da 1300 a circa 1500 kg/h."
)
CORRECTION = (
    "Ti correggo: la macchina è ICMA 2, la pompa è FIMIC FPP20 e il filtro "
    "è FIMIC RAS500, non LAS500."
)


def _candidate(mid, content, similarity, source="user", created_at=1.0):
    return {
        "id": mid,
        "content": content,
        "similarity": similarity,
        "source": source,
        "created_at": created_at,
    }


def test_c1_icma_selects_complete_old_fact_not_new_passive_duplicate():
    candidates = [
        _candidate(PASSIVE_ID, NEW_FACT, 0.99, source="passive", created_at=3.0),
        _candidate(OLD_ID, OLD_FACT, 0.89, source="user", created_at=1.0),
        _candidate(
            "other",
            "La linea ICMA 1 usa un filtro RAS 300 per un'altra produzione.",
            0.84,
            source="user",
            created_at=2.0,
        ),
    ]

    result = select_correction_target(NEW_FACT, CORRECTION, candidates)

    assert result.target is not None
    assert result.target["id"] == OLD_ID
    assert result.reason == "resolved"
    assert PASSIVE_ID in result.excluded_exact_ids


def test_c2_equivalent_old_targets_are_ambiguous():
    candidates = [
        _candidate("old-a", OLD_FACT, 0.900, source="user"),
        _candidate("old-b", OLD_FACT, 0.895, source="user"),
    ]

    result = select_correction_target(NEW_FACT, CORRECTION, candidates)

    assert result.target is None
    assert result.reason == "ambiguous"
    assert set(result.ambiguous_ids) == {"old-a", "old-b"}


def test_c3_unrelated_candidates_do_not_get_superseded():
    candidates = [
        _candidate(
            "poseidon",
            "Il progetto Poseidon riguarda un pallet aperto per sacconi.",
            0.82,
            source="user",
        ),
    ]

    result = select_correction_target(NEW_FACT, CORRECTION, candidates)

    assert result.target is None
    assert result.reason == "no_supported_target"


def test_direct_correction_save_does_not_synthesize_from_recent_history():
    class _Brain:
        def resolve_save_intent(self, *_args):
            raise AssertionError("un comando diretto non deve usare la history per il payload")

    content, kind = _resolve_content(
        "Sì, registra la correzione per il banco Orione 31. "
        "Il contenitore è BX19 al posto di BX17. Mantieni BX17 come storia precedente.",
        _Brain(),
        "", "", True,
        recent_history=[{
            "role": "assistant",
            "content": "La modifica riguarda la pompa FIMIC sull'ICMA 2.",
        }],
    )

    assert kind == "correction"
    assert "Orione 31" in content
    assert "BX19" in content
    assert "ICMA" not in content
    assert "Mantieni" not in content


def test_cross_domain_candidate_must_overlap_correction_evidence():
    contaminated = (
        "Stefano ha precisato la funzione tecnica delle pompe ICMA 2 e FIMIC. "
        "Per il banco Orione 31, il contenitore è BX19."
    )
    result = select_correction_target(
        contaminated,
        "Sì, registra la correzione per il banco Orione 31: BX19 al posto di BX17.",
        [_candidate(
            "icma-passive",
            "Stefano ha precisato la funzione tecnica delle pompe ICMA 2 e FIMIC.",
            0.99,
            source="passive",
        )],
    )

    assert result.target is None
    assert result.reason == "no_supported_target"


def test_quarantined_antecedent_is_visible_only_to_the_correction_resolver():
    class _Embedder:
        available = True

    memory = MemoryManager(_Redis({}), embedder=_Embedder())
    calls = []

    def _search(query, limit, **kwargs):
        calls.append((query, limit, kwargs))
        return [{
            **_candidate(OLD_ID, OLD_FACT, 0.89, source="user"),
            "correction_pending": True,
        }]

    memory._search_semantic = _search

    target = memory.find_correction_target(NEW_FACT, CORRECTION)

    assert target is not None and target["id"] == OLD_ID
    assert calls[0][2]["include_correction_pending"] is True


def test_c4_reliable_correction_exchange_is_not_learned_passively():
    correction_frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "speech_acts": ["INFORM", "CORRECT_FACT", "REQUEST_SAVE"],
    }
    ordinary_frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "speech_acts": ["INFORM"],
        "facts": [{"claim": "dato", "durability": "reusable"}],
    }
    history = [
        {"role": "user", "content": CORRECTION, "semantic_frame": correction_frame},
        {"role": "assistant", "content": "Ricevuto, devo ancora applicarla."},
        {"role": "user", "content": "Il collaudo è previsto domani.", "semantic_frame": ordinary_frame},
        {"role": "assistant", "content": "Ricevuto."},
    ]

    eligible = filter_passive_memory_history(history)

    assert [item["content"] for item in eligible] == [
        "Il collaudo è previsto domani.",
        "Ricevuto.",
    ]


class _SaveMemory:
    def __init__(self):
        self.saved = []
        self.linked = []

    def find_similar_memory(self, _content):
        raise AssertionError("una correzione non deve usare il nearest neighbour singolo")

    def find_correction_target(self, content, correction_text):
        assert content == NEW_FACT
        assert "LAS500" in correction_text
        return _candidate(OLD_ID, OLD_FACT, 0.89, source="user")

    def save_memory(self, content, **kwargs):
        self.saved.append((content, kwargs))
        return "new-user"

    def link_correction(self, old_id, new_id):
        self.linked.append((old_id, new_id))
        return True


class _SaveBrain:
    def apply_correction_to_memory(self, existing, correction):
        assert existing == OLD_FACT
        assert correction == NEW_FACT
        return OLD_FACT.replace("LAS 500", "RAS500")

    def confirm_save(self, _kind, content, _due_at_str=""):
        return f"Memorizzato: {content}"


def test_c5_save_links_the_resolved_antecedent_before_claiming_completion():
    memory = _SaveMemory()

    result = _save_or_merge(
        NEW_FACT,
        memory,
        _SaveBrain(),
        operation="correct",
        correction_text=CORRECTION,
    )

    assert result["saved"] is True
    assert result["corrected"] is True
    assert result["correction_of"] == OLD_ID
    assert result["reply"].startswith("Ho corretto la memoria:")
    assert memory.linked == [(OLD_ID, "new-user")]
    saved_content, kwargs = memory.saved[0]
    assert "RAS500" in saved_content and "LAS 500" not in saved_content
    assert kwargs["final_fields"]["correction_of"] == OLD_ID
    assert kwargs["final_fields"]["correction_pending"] is True
    assert kwargs["idempotent"] is False


def test_unresolved_correction_abstains_and_requests_user_clarification():
    class _Memory:
        def find_correction_target(self, *_args):
            return None

        def save_memory(self, *_args, **_kwargs):
            raise AssertionError("la correzione ambigua non deve essere pubblicata")

    result = _save_or_merge(
        NEW_FACT,
        _Memory(),
        _SaveBrain(),
        operation="correct",
        correction_text=CORRECTION,
    )

    assert result["saved"] is False
    assert result["needs_clarification"] is True
    assert "collegato" in result["reply"]


def test_pending_correction_can_be_saved_as_separate_memory():
    class _Memory:
        def save_memory(self, content, **kwargs):
            assert content == NEW_FACT
            assert kwargs["idempotent"] is False
            return "separate-new"

    result = resolve_pending_correction(
        {"pending_content": NEW_FACT, "pending_correction_text": CORRECTION},
        "È un argomento separato, non c'entra con quello.",
        _Memory(),
        _SaveBrain(),
    )

    assert result["saved"] is True
    assert result["separate"] is True


class _LegacySaveMemory:
    def __init__(self):
        self.saved = []
        self.superseded = []

    def find_correction_target(self, *_args):
        raise AssertionError("il resolver deve essere spento")

    def find_similar_memory(self, _content):
        return _candidate(OLD_ID, OLD_FACT, 0.89, source="user")

    def save_memory(self, content, **kwargs):
        self.saved.append((content, kwargs))
        return "legacy-new"

    def supersede_memory(self, old_id, new_id):
        self.superseded.append((old_id, new_id))
        return True


def test_config_flag_restores_legacy_save_and_passive_paths():
    memory = _LegacySaveMemory()
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "speech_acts": ["INFORM", "CORRECT_FACT"],
    }

    with patch.object(config, "CORRECTION_RESOLVER_ENABLED", False):
        result = _save_or_merge(
            NEW_FACT,
            memory,
            _SaveBrain(),
            operation="correct",
            correction_text=CORRECTION,
        )
        from core.semantic_turn import frame_blocks_passive_memory
        assert frame_blocks_passive_memory(frame) is False

    assert result["corrected"] is True
    assert memory.superseded == [(OLD_ID, "legacy-new")]


def test_recent_correction_turn_is_part_of_bounded_resolution_evidence():
    history = [
        {"role": "user", "content": CORRECTION, "semantic_frame": {
            "status": "interpreted",
            "confidence": 0.99,
            "speech_acts": ["CORRECT_FACT"],
        }},
        {"role": "assistant", "content": "Vuoi che la registri?"},
    ]

    evidence = build_correction_evidence(
        "Sì, registra la versione corretta.", history
    )

    assert "LAS500" in evidence
    assert "registra la versione corretta" in evidence


class _JSON:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key, _path="$"):
        doc = self.docs.get(key)
        return [doc] if doc is not None else None

    def set(self, key, path, value, **_kwargs):
        if path == "$":
            self.docs[key] = value
        else:
            self.docs.setdefault(key, {})[path.removeprefix("$.")] = value
        return True


class _Redis:
    def __init__(self, docs, *, link_result=(b"1", b"linked")):
        self.docs = docs
        self.j = _JSON(docs)
        self.link_result = link_result
        self.eval_calls = []

    def json(self):
        return self.j

    def eval(
        self, script, numkeys, old_key, new_key,
        new_id_json, old_id_json, resolved_at, signal_prefix,
    ):
        self.eval_calls.append((script, numkeys, old_key, new_key))
        if self.link_result[0] in (b"1", "1", 1):
            self.docs[old_key]["superseded_by"] = json.loads(new_id_json)
            self.docs[old_key]["correction_pending"] = False
            self.docs[new_key]["correction_of"] = json.loads(old_id_json)
            self.docs[new_key]["correction_pending"] = False
            self.docs[new_key]["correction_resolved_at"] = float(resolved_at)
            sid = self.docs[old_key].get("correction_signal_id")
            signal = self.docs.get(f"{signal_prefix}{sid}") if sid else None
            if signal and signal.get("status") == "pending":
                signal["status"] = "resolved"
                signal["verdict"] = "explicit_fact_correction"
                signal["resolved_old_memory_id"] = json.loads(old_id_json)
                signal["resolved_new_memory_id"] = json.loads(new_id_json)
        return list(self.link_result)

    def zrem(self, *_args):
        return 1

    def expire(self, *_args):
        return True

    def xadd(self, *_args, **_kwargs):
        return "1-0"


def test_c5_atomic_link_updates_both_sides_in_one_eval():
    docs = {
        f"euri:memory:{OLD_ID}": {"id": OLD_ID, "superseded_by": None},
        "euri:memory:new": {
            "id": "new", "correction_of": OLD_ID, "correction_pending": True,
        },
    }
    redis = _Redis(docs)
    memory = MemoryManager(redis, embedder=None)

    assert memory.link_correction(OLD_ID, "new") is True
    assert len(redis.eval_calls) == 1
    assert docs[f"euri:memory:{OLD_ID}"]["superseded_by"] == "new"
    assert docs["euri:memory:new"]["correction_of"] == OLD_ID
    assert docs["euri:memory:new"]["correction_pending"] is False


def test_c5_atomic_link_closes_the_signal_that_quarantined_the_antecedent():
    docs = {
        f"euri:memory:{OLD_ID}": {
            "id": OLD_ID,
            "superseded_by": None,
            "correction_pending": True,
            "correction_signal_id": "signal-1",
        },
        "euri:memory:new": {
            "id": "new",
            "correction_of": OLD_ID,
            "correction_pending": True,
        },
        "euri:correction:signal-1": {"id": "signal-1", "status": "pending"},
    }
    memory = MemoryManager(_Redis(docs), embedder=None)

    assert memory.link_correction(OLD_ID, "new") is True
    signal = docs["euri:correction:signal-1"]
    assert signal["status"] == "resolved"
    assert signal["verdict"] == "explicit_fact_correction"
    assert signal["resolved_old_memory_id"] == OLD_ID
    assert signal["resolved_new_memory_id"] == "new"


def test_c5_failed_link_leaves_new_version_pending():
    docs = {
        f"euri:memory:{OLD_ID}": {"id": OLD_ID, "superseded_by": None},
        "euri:memory:new": {
            "id": "new", "correction_of": OLD_ID, "correction_pending": True,
        },
    }
    redis = _Redis(docs, link_result=(b"0", b"conflict"))
    memory = MemoryManager(redis, embedder=None)
    failures = []
    memory._record_integrity_failure = lambda *args: failures.append(args)

    assert memory.link_correction(OLD_ID, "new") is False
    assert docs[f"euri:memory:{OLD_ID}"]["superseded_by"] is None
    assert docs["euri:memory:new"]["correction_pending"] is True
    assert failures and failures[0][0] == "correction_link"


def test_c6_signal_enrichment_preserves_original_context_and_quarantines_candidate():
    docs = {
        f"euri:memory:{OLD_ID}": {
            "id": OLD_ID,
            "content": OLD_FACT,
            "source": "user",
            "memory_scope": "personal",
            "requires_verification": False,
        },
        "euri:memory:other": {
            "id": "other",
            "content": "Il progetto Poseidon riguarda un pallet.",
            "source": "user",
            "memory_scope": "personal",
            "requires_verification": False,
        },
    }
    memory = MemoryManager(_Redis(docs), embedder=None)
    sid = memory.save_correction_signal(
        prompt_originale="Da dove viene questo ricordo?",
        risposta_euri="Non trovo Hikma 2.",
        correzione_user=CORRECTION,
        rag_ctx_ids=["wrong-context"],
        rag_ctx_nodes=[
            {
                "kind": "memory",
                "id": "wrong-context",
                "source": "reflection",
                "retrieval_path": "base_rag",
            },
            {
                "kind": "insight",
                "id": "derived-gpu",
                "requires_verification": True,
                "epistemic_status": "internally_convergent",
                "retrieval_path": "insight_rag",
            },
        ],
    )

    quarantined = memory.extend_correction_signal_context(
        sid, [OLD_ID, "other"]
    )

    signal = docs[f"euri:correction:{sid}"]
    assert signal["rag_ctx_ids"] == ["wrong-context"]
    assert signal["candidate_derived_ids"] == ["derived-gpu"]
    assert signal["rag_ctx_nodes"][1]["kind"] == "insight"
    assert signal["resolution_rag_ctx_ids"] == [OLD_ID, "other"]
    assert quarantined == [OLD_ID]
    assert signal["quarantined_memory_ids"] == [OLD_ID]
    assert docs[f"euri:memory:{OLD_ID}"]["correction_pending"] is True
    assert docs["euri:memory:other"].get("correction_pending") is None


if __name__ == "__main__":
    test_c1_icma_selects_complete_old_fact_not_new_passive_duplicate()
    test_c2_equivalent_old_targets_are_ambiguous()
    test_c3_unrelated_candidates_do_not_get_superseded()
    test_quarantined_antecedent_is_visible_only_to_the_correction_resolver()
    test_c4_reliable_correction_exchange_is_not_learned_passively()
    test_c5_save_links_the_resolved_antecedent_before_claiming_completion()
    test_config_flag_restores_legacy_save_and_passive_paths()
    test_recent_correction_turn_is_part_of_bounded_resolution_evidence()
    test_c5_atomic_link_updates_both_sides_in_one_eval()
    test_c5_atomic_link_closes_the_signal_that_quarantined_the_antecedent()
    test_c5_failed_link_leaves_new_version_pending()
    test_c6_signal_enrichment_preserves_original_context_and_quarantines_candidate()
    print("test_correction_resolver: OK")
