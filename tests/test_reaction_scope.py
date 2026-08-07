#!/usr/bin/env python3
"""Il confine di scope deve valere sul BACINO CANDIDATO, non solo sull'output.

Un test che verifica soltanto "nessun documento sperimentale nella risposta" resta
verde anche se i documenti sperimentali hanno occupato posti nella finestra da 800
e ne hanno spinto fuori evidenza personale. Quel difetto non è un leak: è perdita
di richiamo, e si vede solo guardando la query inviata a RediSearch.
"""
from __future__ import annotations

from core.memory_scope import PERSONAL_SCOPE, scope_clause
from core.reaction import _GROUNDED_SRC, gather_grounded_evidence


class _Doc:
    def __init__(self, doc_id):
        self.id = doc_id


class _Result:
    def __init__(self, docs):
        self.docs = docs


class _Json:
    def __init__(self, docs):
        self._docs = docs

    def get(self, key, _path="$"):
        doc = self._docs.get(key)
        return dict(doc) if isinstance(doc, dict) else None


class _Index:
    """Indice finto che ONORA il filtro di scope e il paging, come RediSearch."""

    def __init__(self, docs, captured):
        self._docs = docs
        self._captured = captured

    def search(self, query, **_kwargs):
        self._captured.append(query)
        wanted = None
        if f"@memory_scope:{{{PERSONAL_SCOPE}}}" in query.query_string():
            wanted = PERSONAL_SCOPE
        matching = [
            key
            for key, doc in self._docs.items()
            if doc.get("source", "").split("|")[0] in _GROUNDED_SRC.split("|")
            and (wanted is None or doc.get("memory_scope") == wanted)
        ]
        window = matching[query._offset:query._offset + query._num]
        return _Result([_Doc(key) for key in window])


class _Redis:
    def __init__(self, docs):
        self.docs = docs
        self.captured = []
        self._json = _Json(docs)

    def ft(self, _index):
        return _Index(self.docs, self.captured)

    def json(self):
        return self._json


def _corpus(experimental: int, personal_content: str) -> dict:
    """Documenti sperimentali PRIMA di quello personale: se non filtrati, lo scavalcano."""
    docs = {}
    for i in range(experimental):
        docs[f"euri:memory:exp-{i}"] = {
            "id": f"exp-{i}",
            "content": f"pallet Poseidon simulato numero {i}",
            "source": "teach",
            "memory_scope": "experiment_prova",
        }
    docs["euri:memory:real"] = {
        "id": "real",
        "content": personal_content,
        "source": "teach",
        "memory_scope": PERSONAL_SCOPE,
    }
    return docs


def test_scope_is_in_the_query_not_only_in_hydration():
    r = _Redis(_corpus(0, "Il pallet Poseidon pesa 24 chili"))

    gather_grounded_evidence(r, "Poseidon")

    assert r.captured, "nessuna query inviata all'indice"
    query = r.captured[0]
    sent = query.query_string()
    assert scope_clause(PERSONAL_SCOPE) in sent, sent
    # Lo scope deve restringere il bacino PRIMA del limite, non dopo.
    assert query._num == 800, query._num


def test_experimental_documents_cannot_crowd_out_personal_evidence():
    """Il caso che un'asserzione sull'output non rileverebbe.

    La finestra vale 800 posti: con 800 documenti sperimentali davanti, l'unica
    memoria personale entra soltanto se il filtro agisce nella query.
    """
    r = _Redis(_corpus(800, "Il pallet Poseidon pesa 24 chili"))

    evidence = gather_grounded_evidence(r, "Poseidon")

    assert evidence, "l'evidenza personale è stata spinta fuori dalla finestra"
    assert any("24 chili" in snippet for snippet in evidence), evidence
    assert not any("simulato" in snippet for snippet in evidence), evidence


def test_hydration_filter_survives_as_second_gate():
    """Seconda validazione fail-closed: indice stale che ignora il filtro."""

    class _StaleIndex(_Index):
        def search(self, query, **_kwargs):
            self._captured.append(query)
            return _Result([_Doc(key) for key in self._docs])

    r = _Redis(_corpus(3, "Il pallet Poseidon pesa 24 chili"))
    r.ft = lambda _index: _StaleIndex(r.docs, r.captured)

    evidence = gather_grounded_evidence(r, "Poseidon")

    assert not any("simulato" in snippet for snippet in evidence), evidence


if __name__ == "__main__":
    test_scope_is_in_the_query_not_only_in_hydration()
    test_experimental_documents_cannot_crowd_out_personal_evidence()
    test_hydration_filter_survives_as_second_gate()
    print("test_reaction_scope: OK")
