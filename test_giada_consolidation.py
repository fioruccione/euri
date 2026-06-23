#!/usr/bin/env python3
"""
Golden regression per il caso Giada: frammenti acefali non devono essere
consolidati dentro un soggetto nominato.
"""
from types import SimpleNamespace

from core.brain import Brain
from core.dream_engine import DreamEngine


class _FakeDreamEngine(DreamEngine):
    def __init__(self, content: str):
        self._content = content

    def _ollama_chat(self, *args, **kwargs):
        return SimpleNamespace(message=SimpleNamespace(content=self._content))


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


if __name__ == "__main__":
    test_same_subject_gate_excludes_unknown_fragments()
    test_same_subject_gate_fails_closed_on_bad_output()
    test_passive_filter_rejects_acephalous_facts()
    print("test_giada_consolidation: OK")
