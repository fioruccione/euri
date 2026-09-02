#!/usr/bin/env python3
"""Regressioni preregistrate CORR-01 sul caso organico ICMA2."""

import json
from unittest.mock import patch

import config
from core.correction_resolver import (
    build_correction_evidence,
    select_correction_target,
)
from core.correction_review import (
    claim_next_review,
    classify_review_answer,
    defer_review,
    preview_signal_review,
    resolve_review,
)
from core.memory_manager import MemoryManager
from core.save_service import (
    _resolve_content,
    _save_or_merge,
    resolve_pending_correction,
    save_memory_command,
)
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
        owner_contract_version,
    ):
        self.eval_calls.append((script, numkeys, old_key, new_key))
        if self.link_result[0] in (b"1", "1", 1):
            sid = self.docs[old_key].get("correction_signal_id")
            owner_sid = self.docs[new_key].get("owner_correction_signal_id")
            owner_signal = (
                self.docs.get(f"{signal_prefix}{owner_sid}") if owner_sid else None
            )
            if owner_sid:
                if not owner_signal or owner_signal.get("status") != "proposed":
                    return [b"0", b"owner_signal_not_proposed"]
                if owner_signal.get("requires_owner_confirmation") is not True:
                    return [b"0", b"owner_confirmation_not_required"]
                if owner_signal.get("owner_review_contract_version") != int(
                    owner_contract_version
                ):
                    return [b"0", b"owner_contract_mismatch"]
            self.docs[old_key]["superseded_by"] = json.loads(new_id_json)
            self.docs[old_key]["correction_pending"] = False
            self.docs[new_key]["correction_of"] = json.loads(old_id_json)
            self.docs[new_key]["correction_pending"] = False
            self.docs[new_key]["correction_resolved_at"] = float(resolved_at)
            if owner_sid:
                owner_signal["status"] = "resolved"
                owner_signal["verdict"] = "owner_confirmed_memory_correction"
                owner_signal["requires_owner_confirmation"] = False
                owner_signal["resolved_old_memory_id"] = json.loads(old_id_json)
                owner_signal["resolved_new_memory_id"] = json.loads(new_id_json)
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


def test_corr03_atomic_link_closes_versioned_owner_signal_in_same_eval():
    docs = {
        f"euri:memory:{OLD_ID}": {
            "id": OLD_ID,
            "superseded_by": None,
        },
        "euri:memory:new": {
            "id": "new",
            "correction_of": OLD_ID,
            "correction_pending": True,
            "owner_correction_signal_id": "signal-owner",
        },
        "euri:correction:signal-owner": {
            "id": "signal-owner",
            "status": "proposed",
            "requires_owner_confirmation": True,
            "owner_review_contract_version": 1,
        },
    }
    redis = _Redis(docs)
    memory = MemoryManager(redis, embedder=None)

    assert memory.link_correction(OLD_ID, "new") is True
    signal = docs["euri:correction:signal-owner"]
    assert len(redis.eval_calls) == 1
    assert signal["status"] == "resolved"
    assert signal["verdict"] == "owner_confirmed_memory_correction"
    assert signal["requires_owner_confirmation"] is False
    assert signal["resolved_old_memory_id"] == OLD_ID
    assert signal["resolved_new_memory_id"] == "new"


def test_corr03_atomic_link_rejects_stale_owner_signal_before_memory_mutation():
    docs = {
        f"euri:memory:{OLD_ID}": {
            "id": OLD_ID,
            "superseded_by": None,
        },
        "euri:memory:new": {
            "id": "new",
            "correction_of": OLD_ID,
            "correction_pending": True,
            "owner_correction_signal_id": "signal-owner",
        },
        "euri:correction:signal-owner": {
            "id": "signal-owner",
            "status": "dismissed",
            "requires_owner_confirmation": False,
            "owner_review_contract_version": 1,
        },
    }
    redis = _Redis(docs)
    memory = MemoryManager(redis, embedder=None)
    memory._record_integrity_failure = lambda *_args: None

    assert memory.link_correction(OLD_ID, "new") is False
    assert docs[f"euri:memory:{OLD_ID}"]["superseded_by"] is None
    assert docs["euri:memory:new"]["correction_pending"] is True


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


def test_explicit_last_memory_contract_forces_atomic_correction_not_add():
    old_id = "press-offer-old"
    old_content = (
        "Yizumi e Chen Hsong sono entrambe dotate di vite bimetallica; "
        "la Yizumi resta preferibile per la capacità volumetrica."
    )
    corrected_fact = (
        "La vite bimetallica è di serie solo sulla Yizumi, mentre sulla "
        "Chen Hsong è un extra da 27.000 euro."
    )

    class _Memory:
        def __init__(self):
            self.saved = []
            self.linked = []

        def find_correction_target(self, content, correction_text):
            assert content == corrected_fact
            assert "ultima memoria" in correction_text
            return _candidate(old_id, old_content, 0.94, source="conversation")

        def save_memory(self, content, **kwargs):
            self.saved.append((content, kwargs))
            return "press-offer-new"

        def link_correction(self, old_memory_id, new_memory_id):
            self.linked.append((old_memory_id, new_memory_id))
            return True

    class _Brain:
        def resolve_save_intent(self, _text, _history):
            # Riproduce il fallimento da contenere: il modello capisce il fatto,
            # ma propone add invece di correct.
            return {
                "mode": "direct",
                "operation": "add",
                "memory": corrected_fact,
                "confidence": 1.0,
            }

        def apply_correction_to_memory(self, existing, correction):
            assert existing == old_content
            assert correction == corrected_fact
            return (
                "La Yizumi resta preferibile per capacità volumetrica e ha la "
                "vite bimetallica di serie; sulla Chen Hsong è un extra da "
                "27.000 euro."
            )

    frame = {
        "status": "interpreted",
        "confidence": 1.0,
        "requires_clarification": False,
        "primary_intent": "CHAT",
        "speech_acts": ["CORRECT_FACT"],
        "raw_text": (
            "Euri, una precisazione: sull'ultima memoria la vite bimetallica "
            "è di serie solo sulla Yizumi, mentre sulla Chen Hsong è un extra."
        ),
        "memory_disposition": "candidate",
        "facts": [{
            "claim": corrected_fact,
            "modality": "asserted",
            "durability": "reusable",
        }],
    }
    memory = _Memory()
    result = save_memory_command(
        frame["raw_text"],
        memory,
        _Brain(),
        recent_history=[{"role": "assistant", "content": old_content}],
        semantic_frame=frame,
    )

    assert result["corrected"] is True
    assert result["correction_of"] == old_id
    assert memory.linked == [(old_id, "press-offer-new")]
    assert memory.saved[0][1]["final_fields"]["correction_pending"] is True


class _ReviewRedis:
    def __init__(self, docs):
        self.docs = docs
        self.strings = {}
        self.eval_calls = []
        self.j = _JSON(docs)

    def json(self):
        return self.j

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return iter(sorted(key for key in self.docs if key.startswith(prefix)))

    def set(self, key, value, nx=False, ex=None, **_kwargs):
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        return True

    def get(self, key):
        return self.strings.get(key)

    def delete(self, key):
        return 1 if self.strings.pop(key, None) is not None else 0

    def eval(self, script, _numkeys, key, *args):
        self.eval_calls.append((script, key, args))
        if "JSON.SET" in script:
            if key not in self.docs:
                return 0
            for index in range(0, len(args), 2):
                self.docs[key][args[index]] = json.loads(args[index + 1])
            return 1
        token = args[0]
        if self.strings.get(key) != token:
            return 0
        return self.delete(key)


class _ReviewMemory:
    def __init__(self, redis, target=None):
        self.r = redis
        self.target = target
        self.saved = []
        self.linked = []
        self.link_ok = True

    def find_correction_target(self, content, correction_text, **_kwargs):
        assert content == correction_text
        return dict(self.target) if self.target else None

    def save_memory(self, content, **kwargs):
        mid = f"review-new-{len(self.saved) + 1}"
        self.saved.append((mid, content, kwargs))
        self.r.docs[f"euri:memory:{mid}"] = {
            "id": mid,
            "content": content,
            "source": kwargs.get("source"),
            "memory_scope": "personal",
            **(kwargs.get("final_fields") or {}),
        }
        return mid

    def link_correction(self, old_id, new_id):
        self.linked.append((old_id, new_id))
        if not self.link_ok:
            return False
        self.r.docs[f"euri:memory:{old_id}"]["superseded_by"] = new_id
        new_doc = self.r.docs[f"euri:memory:{new_id}"]
        new_doc["correction_pending"] = False
        signal_id = new_doc.get("owner_correction_signal_id")
        if signal_id:
            signal = self.r.docs[f"euri:correction:{signal_id}"]
            signal.update({
                "status": "resolved",
                "verdict": "owner_confirmed_memory_correction",
                "requires_owner_confirmation": False,
                "resolved_old_memory_id": old_id,
                "resolved_new_memory_id": new_id,
            })
        return True


class _ReviewBrain:
    def __init__(self, rewritten):
        self.rewritten = rewritten

    def apply_correction_to_memory(self, existing, correction):
        assert existing
        assert correction
        return self.rewritten


def _review_docs():
    return {
        "euri:correction:organic": {
            "id": "organic",
            "status": "proposed",
            "proposed_verdict": "bad_memory",
            "requires_owner_confirmation": True,
            "owner_review_contract_version": 1,
            "memory_scope": "personal",
            "created_at": 10.0,
            "correzione_user": "La ICMA 2 è una bivite, non una monovite.",
            "resolution_rag_ctx_ids": ["icma"],
        },
        "euri:memory:icma": {
            "id": "icma",
            "content": "La ICMA 2 è una monovite con filtro RAS500.",
            "source": "user",
            "memory_scope": "personal",
            "superseded_by": None,
        },
    }


def test_corr03_claim_is_single_channel_and_does_not_mutate_memory():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    target = _candidate("icma", docs["euri:memory:icma"]["content"], 0.95)
    memory = _ReviewMemory(redis, target=target)
    before = dict(docs["euri:memory:icma"])

    voice = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )
    ui = claim_next_review(
        redis, memory, memory_scope="personal", channel="silent_chat",
        now=21.0, token="ui-token",
    )

    assert voice is not None
    assert ui is None
    assert docs["euri:memory:icma"] == before
    assert "La ICMA 2 è una bivite" in voice["question"]
    assert "La ICMA 2 è una monovite" in voice["question"]
    assert voice["target_id"] == "icma"
    assert voice["question_id"] == voice["signal_id"] == "organic"


def test_corr03_read_only_preview_and_timeout_backoff():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    target = _candidate("icma", docs["euri:memory:icma"]["content"], 0.95)
    memory = _ReviewMemory(redis, target=target)
    before_docs = {key: dict(value) for key, value in docs.items()}

    preview = preview_signal_review(
        redis,
        memory,
        signal_key="organic",
        memory_scope="personal",
        now=20.0,
    )

    assert preview is not None
    assert preview["channel"] == "read_only_preview"
    assert redis.strings == {}
    assert docs == before_docs

    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=21.0, token="voice-token",
    )
    assert defer_review(redis, review, reason="voice_timeout", now=22.0)
    assert docs["euri:correction:organic"]["status"] == "proposed"
    assert docs["euri:correction:organic"]["review_after"] > 22.0
    assert redis.strings == {}


def test_corr03_apply_links_exact_snapshot_then_resolves_signal():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    target = _candidate("icma", docs["euri:memory:icma"]["content"], 0.95)
    memory = _ReviewMemory(redis, target=target)
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )

    result = resolve_review(
        redis, memory, _ReviewBrain("La ICMA 2 è una bivite con filtro RAS500."),
        review, "A, applicala a quella memoria.", now=30.0,
    )

    signal = docs["euri:correction:organic"]
    assert result["corrected"] is True
    assert memory.linked == [("icma", "review-new-1")]
    assert signal["status"] == "resolved"
    assert signal["verdict"] == "owner_confirmed_memory_correction"
    assert signal["resolved_old_memory_id"] == "icma"
    assert signal["resolved_new_memory_id"] == "review-new-1"
    assert any("JSON.SET" in script for script, _key, _args in redis.eval_calls)


def test_corr03_stale_target_never_writes_and_reopens_proposal():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    target = _candidate("icma", docs["euri:memory:icma"]["content"], 0.95)
    memory = _ReviewMemory(redis, target=target)
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )
    docs["euri:memory:icma"]["content"] = "Il nodo è cambiato nel frattempo."

    result = resolve_review(
        redis, memory, _ReviewBrain("non deve essere usato"), review,
        "A", now=30.0,
    )

    assert result["action"] == "stale"
    assert memory.saved == []
    assert memory.linked == []
    assert docs["euri:correction:organic"]["status"] == "proposed"


def test_corr03_changed_signal_never_uses_the_answer_to_the_old_question():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    target = _candidate("icma", docs["euri:memory:icma"]["content"], 0.95)
    memory = _ReviewMemory(redis, target=target)
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )
    docs["euri:correction:organic"]["correzione_user"] = (
        "La correzione è stata sostituita da un contenuto diverso."
    )

    result = resolve_review(
        redis, memory, _ReviewBrain("non deve essere usato"),
        review, "A", now=30.0,
    )

    assert result["action"] == "signal_changed"
    assert memory.saved == []
    assert memory.linked == []


def test_corr03_separate_dismiss_later_and_unknown_are_fail_closed():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    memory = _ReviewMemory(redis, target=None)
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="silent_chat",
        now=20.0, token="ui-token",
    )
    assert classify_review_answer(review, "forse ne parliamo") == "unknown"
    unknown = resolve_review(
        redis, memory, _ReviewBrain("x"), review, "forse ne parliamo", now=21.0,
    )
    assert unknown["needs_clarification"] is True
    assert memory.saved == []

    separate_prompt = resolve_review(
        redis, memory, _ReviewBrain("x"), review,
        "A, registrala separatamente", now=22.0,
    )
    assert separate_prompt["needs_clarification"] is True
    assert memory.saved == []
    uncertain_fact = resolve_review(
        redis, memory, _ReviewBrain("x"), review,
        "Non me lo ricordo con sicurezza", now=22.5,
    )
    assert uncertain_fact["needs_clarification"] is True
    assert memory.saved == []
    separate = resolve_review(
        redis, memory, _ReviewBrain("x"), review,
        "La ICMA 2 è una bivite, non una monovite.", now=23.0,
    )
    assert separate["separate"] is True
    assert memory.linked == []
    assert docs["euri:correction:organic"]["status"] == "resolved"

    docs2 = _review_docs()
    redis2 = _ReviewRedis(docs2)
    memory2 = _ReviewMemory(redis2, target=None)
    review2 = claim_next_review(
        redis2, memory2, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )
    later = resolve_review(
        redis2, memory2, _ReviewBrain("x"), review2,
        "più tardi", now=25.0,
    )
    assert later["action"] == "later"
    assert docs2["euri:correction:organic"]["status"] == "proposed"
    assert docs2["euri:correction:organic"]["review_after"] > 25.0

    review3 = claim_next_review(
        redis2, memory2, memory_scope="personal", channel="voice",
        now=4000.0, token="voice-token-2",
    )
    dismissed = resolve_review(
        redis2, memory2, _ReviewBrain("x"), review3,
        "B, era solo per quella conversazione", now=4001.0,
    )
    assert dismissed["action"] == "dismiss"
    assert docs2["euri:correction:organic"]["status"] == "dismissed"
    assert memory2.saved == []


def test_corr03_target_outside_signal_provenance_is_not_offered():
    docs = _review_docs()
    docs["euri:correction:organic"]["resolution_rag_ctx_ids"] = ["other"]
    redis = _ReviewRedis(docs)
    memory = _ReviewMemory(
        redis,
        target=_candidate("icma", docs["euri:memory:icma"]["content"], 0.99),
    )

    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )

    assert review["mode"] == "unresolved"
    assert review["target_id"] == ""
    assert "non trovo un antecedente sicuro" in review["question"]


def test_corr03_already_present_creates_no_duplicate():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    old = docs["euri:memory:icma"]["content"]
    memory = _ReviewMemory(redis, target=_candidate("icma", old, 0.95))
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )
    result = resolve_review(
        redis, memory, _ReviewBrain(old), review, "A", now=30.0,
    )

    assert result["action"] == "already_present"
    assert memory.saved == []
    assert docs["euri:correction:organic"]["status"] == "resolved"
    assert docs["euri:correction:organic"]["verdict"] == "already_present"


def test_corr03_link_failure_quarantines_new_version_without_retrying_proposal():
    docs = _review_docs()
    redis = _ReviewRedis(docs)
    memory = _ReviewMemory(
        redis,
        target=_candidate("icma", docs["euri:memory:icma"]["content"], 0.95),
    )
    memory.link_ok = False
    review = claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="voice-token",
    )

    result = resolve_review(
        redis,
        memory,
        _ReviewBrain("La ICMA 2 è una bivite con filtro RAS500."),
        review,
        "A",
        now=30.0,
    )

    signal = docs["euri:correction:organic"]
    assert result["action"] == "link_failed"
    assert signal["status"] == "repair_required"
    assert signal["requires_owner_confirmation"] is False
    assert docs["euri:memory:icma"].get("superseded_by") is None
    assert docs["euri:memory:review-new-1"]["correction_pending"] is True
    assert any(
        "JSON.SET" in script and "PERSIST" in script
        for script, _key, _args in redis.eval_calls
    )


def test_corr03_non_proposed_or_other_scope_is_not_claimed():
    for status in ("pending", "dismissed", "analyzed", "resolved"):
        docs = _review_docs()
        docs["euri:correction:organic"]["status"] = status
        redis = _ReviewRedis(docs)
        assert claim_next_review(
            redis, _ReviewMemory(redis), memory_scope="personal",
            channel="voice", now=20.0, token="x",
        ) is None

    docs = _review_docs()
    docs["euri:correction:organic"]["memory_scope"] = "experiment:other"
    redis = _ReviewRedis(docs)
    assert claim_next_review(
        redis, _ReviewMemory(redis), memory_scope="personal",
        channel="voice", now=20.0, token="x",
    ) is None


def test_corr03_legacy_proposals_are_replayable_but_never_claimed_runtime():
    docs = _review_docs()
    docs["euri:correction:organic"].pop("owner_review_contract_version")
    redis = _ReviewRedis(docs)
    memory = _ReviewMemory(redis)

    assert claim_next_review(
        redis, memory, memory_scope="personal", channel="voice",
        now=20.0, token="runtime",
    ) is None
    assert preview_signal_review(
        redis,
        memory,
        signal_key="organic",
        memory_scope="personal",
        now=20.0,
    ) is None
    replay = preview_signal_review(
        redis,
        memory,
        signal_key="organic",
        memory_scope="personal",
        now=20.0,
        include_legacy=True,
    )
    assert replay is not None
    assert replay["channel"] == "read_only_preview"
    assert redis.strings == {}


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
    test_corr03_atomic_link_closes_versioned_owner_signal_in_same_eval()
    test_corr03_atomic_link_rejects_stale_owner_signal_before_memory_mutation()
    test_c5_failed_link_leaves_new_version_pending()
    test_c6_signal_enrichment_preserves_original_context_and_quarantines_candidate()
    test_explicit_last_memory_contract_forces_atomic_correction_not_add()
    test_corr03_claim_is_single_channel_and_does_not_mutate_memory()
    test_corr03_read_only_preview_and_timeout_backoff()
    test_corr03_apply_links_exact_snapshot_then_resolves_signal()
    test_corr03_stale_target_never_writes_and_reopens_proposal()
    test_corr03_changed_signal_never_uses_the_answer_to_the_old_question()
    test_corr03_separate_dismiss_later_and_unknown_are_fail_closed()
    test_corr03_target_outside_signal_provenance_is_not_offered()
    test_corr03_already_present_creates_no_duplicate()
    test_corr03_link_failure_quarantines_new_version_without_retrying_proposal()
    test_corr03_non_proposed_or_other_scope_is_not_claimed()
    test_corr03_legacy_proposals_are_replayable_but_never_claimed_runtime()
    print("test_correction_resolver: OK")
