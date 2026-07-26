#!/usr/bin/env python3
"""
Golden regression per il caso Giada: frammenti acefali non devono essere
consolidati dentro un soggetto nominato.
"""
from types import SimpleNamespace

from core.brain import Brain
from core.dream_engine import DreamEngine
from core.memory_manager import MemoryManager
from core.retrieval_strategy import build_subject_recall


class _FakeDreamEngine(DreamEngine):
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def _ollama_chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(message=SimpleNamespace(content=self._content))


class _FakeRedisJSON:
    def __init__(self, docs):
        self._docs = docs

    def get(self, key, path="$"):
        doc = self._docs.get(key)
        if doc is None:
            return None
        return [doc]


class _FakeRedis:
    def __init__(self, docs):
        self._docs = docs

    def scan_iter(self, pattern):
        return list(self._docs)

    def json(self):
        return _FakeRedisJSON(self._docs)


class _FakeMemory:
    def __init__(self, docs):
        self.r = _FakeRedis(docs)


class _FakeComparisonMemory:
    def __init__(self, *, duplicate: bool):
        self.duplicate = duplicate
        self.duplicate_checks = []
        self.saved = []

    def is_duplicate_memory(self, content, llm_probe_fn=None):
        self.duplicate_checks.append((content, llm_probe_fn))
        return self.duplicate

    def save_memory(self, content, **kwargs):
        self.saved.append((content, kwargs))
        return "comparison-id"


def test_same_subject_gate_excludes_unknown_fragments():
    cluster = [
        {"id": "giada", "content": "Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica in fase di apprendimento delle procedure operative."},
        {"id": "leonardo", "content": "Ha un collega di nome Leonardo."},
        {"id": "remote", "content": "Lavora da casa in modalità remota."},
        {"id": "team", "content": "Lavora presso la Lucy Plast spa e gestisce un team di collaboratori esperti."},
        {"id": "generic", "content": "Lavora presso la Lucy Plast spa di Umbertide (PG)."},
    ]
    engine = _FakeDreamEngine(
        '{"1":"SAME","2":"UNKNOWN","3":"UNKNOWN","4":"UNKNOWN","5":"UNKNOWN"}'
    )

    kept = engine._same_subject_gate(cluster, "lavoro", seed_id="giada")

    assert [d["id"] for d in kept] == ["giada"]


def test_same_subject_gate_fails_closed_on_bad_output():
    cluster = [
        {"id": "giada", "content": "Giada è una nuova collaboratrice di laboratorio."},
        {"id": "remote", "content": "Lavora da casa in modalità remota."},
        {"id": "team", "content": "Lavora presso l'azienda e gestisce un team."},
    ]
    engine = _FakeDreamEngine("1,2,3")

    kept = engine._same_subject_gate(cluster, "lavoro", seed_id="giada")

    assert [d["id"] for d in kept] == ["giada"]


def test_passive_filter_rejects_acephalous_facts():
    assert Brain._looks_acephalous_fact("Lavora da casa in modalità remota.")
    assert Brain._looks_acephalous_fact("Ha un collega di nome Leonardo.")
    assert Brain._looks_acephalous_fact("Gestisce un team di collaboratori esperti.")
    assert not Brain._looks_acephalous_fact("Giada lavora in laboratorio.")
    assert not Brain._looks_acephalous_fact("Stefano ha un collega di nome Leonardo.")


def test_correction_target_scoring_prefers_buggy_giada_node():
    correction = (
        "Aggiungo che nelle tue memorie su Giada hai fatto collegamenti sbagliati. "
        "Giada è solo una nuova collaboratrice di laboratorio in apprendimento. "
        "Nel laboratorio fa quello che hai detto e niente altro, da parte su Leonardo, "
        "sul team e sul lavoro da casa, è sbagliato."
    )
    clean = (
        "Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica "
        "in fase di apprendimento delle procedure operative."
    )
    buggy = (
        "Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica "
        "in fase di apprendimento delle procedure operative, che lavora presso la Lucy Plast "
        "spa di Umbertide (PG) gestendo un team di esperti e collaborando con il collega Leonardo; "
        "opera in modalità remota da casa."
    )

    tokens = MemoryManager.correction_target_tokens(correction)
    assert "giada" in tokens
    assert "leonardo" in tokens
    assert MemoryManager.correction_overlap_score(correction, buggy) > MemoryManager.correction_overlap_score(correction, clean)


def test_subject_recall_demotes_risky_giada_consolidation():
    docs = {
        "euri:memory:bad": {
            "id": "bad",
            "content": "Giada e' una nuova collaboratrice di laboratorio e collabora con Leonardo lavorando da casa.",
            "source": "loop2e",
            "domain": "lavoro",
            "recalled_count": 99,
            "requires_verification": True,
            "provenance_stale": True,
            "consolidation_risk": {"level": "high"},
        },
        "euri:memory:clean": {
            "id": "clean",
            "content": "Giada e' una nuova collaboratrice di laboratorio in apprendimento.",
            "source": "passive",
            "domain": "lavoro",
            "recalled_count": 0,
            "requires_verification": False,
        },
    }

    rows = build_subject_recall(_FakeMemory(docs), "Giada", include_ids=True)

    assert rows[0][0] == "clean"
    assert rows[1][0] == "bad"
    assert "DATO DA VERIFICARE" in rows[1][1]


def test_passive_fact_parser_marks_tacit_acceptance_as_weak():
    strong = Brain._parse_passive_fact_line(
        "FORTE: Stefano si occupa anche di architetture agentiche e analisi DSC."
    )
    weak = Brain._parse_passive_fact_line(
        "DEBOLE: Stefano si occupa anche di architetture agentiche e analisi DSC."
    )
    unmarked = Brain._parse_passive_fact_line(
        "Stefano si occupa anche di architetture agentiche e analisi DSC."
    )

    assert strong == {
        "content": "Stefano si occupa anche di architetture agentiche e analisi DSC.",
        "support": "strong",
    }
    assert weak["support"] == "weak"
    assert unmarked["support"] == "weak"


def test_loop2f_does_not_feed_on_comparisons_or_high_risk():
    comparison_by_prefix = {
        "content": "[confronto] Due voci descrivono entita' diverse ma confrontabili.",
        "source": "reflection",
        "requires_verification": True,
    }
    comparison_by_tags = {
        "content": "Due voci descrivono entita' diverse ma confrontabili.",
        "source": "reflection",
        "tags": ["confronto", "loop2f"],
        "requires_verification": True,
    }
    high_risk_fact = {
        "content": "Memoria consolidata fragile.",
        "source": "loop2e",
        "requires_verification": True,
        "consolidation_risk": {"level": "high"},
    }
    watch_fact = {
        "content": "Dato fattuale da verificare ma ancora confrontabile.",
        "source": "passive",
        "requires_verification": True,
        "consolidation_risk": {"level": "watch"},
    }

    assert not DreamEngine._loop2f_source_allowed(comparison_by_prefix)
    assert not DreamEngine._loop2f_source_allowed(comparison_by_tags)
    assert not DreamEngine._loop2f_source_allowed(high_risk_fact)
    assert DreamEngine._loop2f_source_allowed(watch_fact)


def test_loop2f_comparison_is_descriptive_and_deduplicated():
    duplicate_memory = _FakeComparisonMemory(duplicate=True)
    duplicate_engine = _FakeDreamEngine(
        "Il target è 1100 MPa; la misura è 1250 MPa."
    )
    duplicate_engine._memory_manager = duplicate_memory
    duplicate_engine._r = None

    duplicate_engine._make_comparison_memory(
        "Target: modulo 1100 MPa, IZOD 4, allungamento >10%.",
        "Misura UBQ: modulo 1250 MPa, IZOD 3,8, allungamento 9%.",
        "chimica polimeri",
        source_ids=["target", "measure"],
        requires_verification=True,
    )

    generation_prompt = duplicate_engine.calls[0][1]["messages"][0]["content"]
    assert "TARGET e un" in generation_prompt
    assert "RISULTATO MISURATO" in generation_prompt
    assert "NON raccomandare o preferire A/B" in generation_prompt
    assert len(duplicate_memory.duplicate_checks) == 1
    assert callable(duplicate_memory.duplicate_checks[0][1])
    assert duplicate_memory.saved == []

    new_memory = _FakeComparisonMemory(duplicate=False)
    new_engine = _FakeDreamEngine(
        "La voce A è il target; la voce B è la misura e supera il modulo "
        "di 150 MPa, con IZOD e allungamento sotto il target."
    )
    new_engine._memory_manager = new_memory
    new_engine._r = None

    new_engine._make_comparison_memory(
        "Target: modulo 1100 MPa, IZOD 4, allungamento >10%.",
        "Misura UBQ: modulo 1250 MPa, IZOD 3,8, allungamento 9%.",
        "chimica polimeri",
        source_ids=["target", "measure"],
        requires_verification=True,
    )

    assert len(new_memory.saved) == 1
    saved_content, saved_kwargs = new_memory.saved[0]
    assert saved_content.startswith("[confronto] La voce A è il target")
    assert saved_kwargs["final_fields"]["source_memory_ids"] == [
        "target",
        "measure",
    ]
    assert saved_kwargs["final_fields"]["requires_verification"] is True


if __name__ == "__main__":
    test_same_subject_gate_excludes_unknown_fragments()
    test_same_subject_gate_fails_closed_on_bad_output()
    test_passive_filter_rejects_acephalous_facts()
    test_correction_target_scoring_prefers_buggy_giada_node()
    test_subject_recall_demotes_risky_giada_consolidation()
    test_passive_fact_parser_marks_tacit_acceptance_as_weak()
    test_loop2f_does_not_feed_on_comparisons_or_high_risk()
    test_loop2f_comparison_is_descriptive_and_deduplicated()
    print("test_giada_consolidation: OK")
