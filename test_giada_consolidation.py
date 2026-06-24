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

    def _ollama_chat(self, *args, **kwargs):
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


if __name__ == "__main__":
    test_same_subject_gate_excludes_unknown_fragments()
    test_same_subject_gate_fails_closed_on_bad_output()
    test_passive_filter_rejects_acephalous_facts()
    test_correction_target_scoring_prefers_buggy_giada_node()
    test_subject_recall_demotes_risky_giada_consolidation()
    test_passive_fact_parser_marks_tacit_acceptance_as_weak()
    print("test_giada_consolidation: OK")
