#!/usr/bin/env python3
"""Regressioni pure per Loop 2j e l'espansione RAG a un solo arco."""
from __future__ import annotations

from core.memory_schema import (
    SCHEMA_CURRENT_KEY,
    SCHEMA_PROJECTION_PREFIX,
    build_schema_projection,
    build_schema_projection_from_documents,
    expand_memories_via_schema,
)
from core.rag_context import build_rag_context


def _doc(mid: str, entities: list[str], content: str | None = None, **extra) -> dict:
    base = {
        "id": mid,
        "content": content or f"Memoria verificabile su {entities[0]}",
        "source": "user",
        "memory_scope": "personal",
        "domain": "test",
        "created_at": 100.0,
        "recalled_count": 3,
        "memory_axes": {
            "subject_status": "explicit",
            "entity_mentions": entities,
        },
    }
    base.update(extra)
    return base


class _Json:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, _path="$"):
        value = self.redis.docs.get(str(key))
        return [value] if value is not None else None

    def set(self, key, _path, value):
        self.redis.docs[str(key)] = value
        return True


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.keys = []

    def json(self):
        return self

    def get(self, key, _path="$"):
        self.keys.append(str(key))
        return self

    def execute(self):
        return [
            [self.redis.docs[key]] if key in self.redis.docs else None
            for key in self.keys
        ]


class _Redis:
    def __init__(self, memories: list[dict] | None = None):
        self.docs = {
            f"euri:memory:{doc['id']}": doc for doc in (memories or [])
        }
        self.values = {}
        self.expirations = {}

    def json(self):
        return _Json(self)

    def scan_iter(self, pattern):
        assert pattern == "euri:memory:*"
        return iter(sorted(k for k in self.docs if k.startswith("euri:memory:")))

    def pipeline(self, transaction=False):
        assert transaction is False
        return _Pipeline(self)

    def set(self, key, value):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, seconds):
        self.expirations[key] = seconds


class _Memory:
    def __init__(self, redis, semantic):
        self.r = redis
        self.semantic = list(semantic)
        self.touched = []

    def get_recent_reflections(self, **_kwargs):
        return []

    def get_recent_memories(self, **_kwargs):
        return []

    def search_memories(self, *_args, **_kwargs):
        return list(self.semantic)

    def search_notes(self, *_args, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_insights(self, *_args, **_kwargs):
        return []

    def _touch_memories(self, docs):
        self.touched.extend(str(doc.get("id")) for doc in docs)


def _semantic_frame(*focus, needed=True, goal="overview") -> dict:
    return {
        "status": "interpreted",
        "confidence": 0.97,
        "memory_retrieval": {
            "needed": needed,
            "focus": [
                {"entity": entity, "role": role, "relevance": relevance}
                for entity, role, relevance in focus
            ],
            "relation": "relazione compresa da Gemma",
            "evidence_goal": goal,
            "confidence": 0.96,
        },
    }


def test_projection_merges_corporate_alias_and_excludes_ambient_or_unsafe_nodes():
    docs = [
        _doc("m1", ["Lucy Plast S.p.A.", "Stefano"]),
        _doc("m2", ["Lucy Plast"]),
        _doc("m3", ["Lucy Plast SPA"], requires_verification=True),
        _doc("old", ["Lucy Plast"], superseded_by="m3"),
    ]
    projection = build_schema_projection_from_documents(
        docs,
        generation="g1",
        min_members=3,
    )

    assert len(projection["schemas"]) == 1
    schema = next(iter(projection["schemas"].values()))
    assert schema["normalised_label"] == "lucy plast"
    assert schema["member_ids"] == ["m1", "m2", "m3"]
    assert "old" not in projection["membership"]
    # Il flag numerico/da-verificare resta sulla fonte, ma non impedisce il
    # collegamento strutturale: lo schema non lo trasforma in verita'.
    assert "m3" in projection["membership"]


def test_numeric_values_and_sentence_starters_do_not_become_schemas():
    docs = [
        _doc("m1", ["2026", "Test"], "Test eseguito nel 2026."),
        _doc("m2", ["2026", "Test"], "Test ripetuto nel 2026."),
        _doc("m3", ["2026", "Test"], "Test chiuso nel 2026."),
    ]
    projection = build_schema_projection_from_documents(
        docs,
        generation="g-noise",
        min_members=3,
    )
    assert projection["schemas"] == {}


def test_publication_switches_generation_only_after_projection_exists():
    docs = [
        _doc("m1", ["Lucy Plast"]),
        _doc("m2", ["Lucy Plast"]),
        _doc("m3", ["Lucy Plast"]),
    ]
    redis = _Redis(docs)
    result = build_schema_projection(redis)

    generation = redis.get(SCHEMA_CURRENT_KEY)
    assert result["status"] == "updated"
    assert generation == result["generation"]
    assert f"{SCHEMA_PROJECTION_PREFIX}{generation}" in redis.docs
    assert redis.expirations[f"{SCHEMA_PROJECTION_PREFIX}{generation}"] > 86400


def test_expansion_returns_original_sources_and_never_the_schema_as_evidence():
    seed = _doc("m1", ["Lucy Plast"], "Lucy Plast possiede un reparto produttivo.")
    extrusion = _doc(
        "m2", ["Lucy Plast"],
        "Lucy Plast usa ICMA2 nel reparto di estrusione.",
        domain="estrusione polimeri",
    )
    moulding = _doc(
        "m3", ["Lucy Plast"],
        "Lucy Plast dispone di presse per lo stampaggio.",
        domain="stampaggio plastica",
    )
    redis = _Redis([seed, extrusion, moulding])
    build_schema_projection(redis)
    memory = _Memory(redis, [seed])

    added, diagnostics = expand_memories_via_schema(
        memory, [seed], "Descrivi il lavoro di Lucy Plast", limit=2
    )

    assert {doc["id"] for doc in added} == {"m2", "m3"}
    assert all(doc.get("_schema_retrieval") for doc in added)
    assert diagnostics["activated_schema_ids"]
    assert all(not str(doc["id"]).startswith("euri:loop2j") for doc in added)


def test_repeated_property_never_crosses_distinct_anchors_by_itself():
    first = _doc("m1", ["Azienda Alfa", "MFI"], "Azienda Alfa misura MFI.")
    second = _doc("m2", ["Azienda Beta", "MFI"], "Azienda Beta misura MFI.")
    third = _doc("m3", ["Azienda Gamma", "MFI"], "Azienda Gamma misura MFI.")
    redis = _Redis([first, second, third])
    build_schema_projection(redis)
    memory = _Memory(redis, [first])

    projection_key = f"{SCHEMA_PROJECTION_PREFIX}{redis.get(SCHEMA_CURRENT_KEY)}"
    projection = redis.docs[projection_key]
    mfi_schema = next(
        schema for schema in projection["schemas"].values()
        if schema["normalised_label"] == "mfi"
    )
    assert mfi_schema["retrieval_policy"] == "contextual_only"

    added, _diagnostics = expand_memories_via_schema(
        memory, [first], "Confronta i valori MFI", limit=2
    )
    assert added == []


def test_compound_query_keeps_repeated_product_type_inside_named_brand():
    peroni = [
        _doc(
            f"p{i}", ["Peroni", "Birra Bionda"],
            f"La Birra Bionda Peroni ha la caratteristica P{i}.",
        )
        for i in range(1, 4)
    ]
    raffo = [
        _doc(
            f"r{i}", ["Raffo", "Birra Bionda"],
            f"La Birra Bionda Raffo ha la caratteristica R{i}.",
        )
        for i in range(1, 4)
    ]
    redis = _Redis(peroni + raffo)
    build_schema_projection(redis)
    memory = _Memory(redis, [peroni[0]])

    added, diagnostics = expand_memories_via_schema(
        memory,
        [peroni[0]],
        "Quali caratteristiche ha la Birra Bionda Peroni?",
        limit=4,
    )

    assert diagnostics["activated_schema_ids"]
    assert {doc["id"] for doc in added} == {"p2", "p3"}
    assert all(not doc["id"].startswith("r") for doc in added)


def test_rag_reserves_bounded_slots_and_marks_schema_lineage():
    seed = _doc("m1", ["Lucy Plast"], "Lucy Plast trasforma materie plastiche.")
    extrusion = _doc(
        "m2", ["Lucy Plast"],
        "Lucy Plast lavora anche nel reparto di estrusione con ICMA2.",
        domain="estrusione polimeri",
    )
    moulding = _doc(
        "m3", ["Lucy Plast"],
        "Lucy Plast produce articoli nel reparto di stampaggio.",
        domain="stampaggio plastica",
    )
    redis = _Redis([seed, extrusion, moulding])
    build_schema_projection(redis)
    memory = _Memory(redis, [seed])

    rag = build_rag_context(
        "Descrivimi il lavoro di Lucy Plast",
        memory,
        mode="search",
    )

    assert seed["content"] in rag.text
    assert extrusion["content"] in rag.text
    assert moulding["content"] in rag.text
    schema_nodes = [n for n in rag.nodes if n["retrieval_path"] == "schema_expansion"]
    assert {node["id"] for node in schema_nodes} == {"m2", "m3"}
    assert set(rag.diagnostics["schema_expansion"]["added_memory_ids"]) == {"m2", "m3"}
    assert {"m2", "m3"}.issubset(set(memory.touched))


def test_semantic_focus_opens_named_schema_when_base_retrieval_misses_it():
    lucy = [
        _doc("l1", ["Lucy Plast"], "Lucy Plast trasforma materie plastiche."),
        _doc("l2", ["Lucy Plast"], "Lucy Plast usa linee di estrusione."),
        _doc("l3", ["Lucy Plast"], "Lucy Plast dispone di presse di stampaggio."),
    ]
    unrelated = _doc("x1", ["Altro Progetto"], "Nota su un altro progetto.")
    redis = _Redis([*lucy, unrelated])
    build_schema_projection(redis)
    memory = _Memory(redis, [unrelated])

    rag = build_rag_context(
        "Cosa ricordi di Lucy Plast?",
        memory,
        mode="search",
        semantic_frame=_semantic_frame(("Lucy Plast", "focus", 0.99)),
    )

    schema_nodes = [n for n in rag.nodes if n["retrieval_path"] == "schema_expansion"]
    # Le panoramiche usano il bundle entity-first largo (max 4) invece del
    # tetto puntuale a due fonti.
    assert len(schema_nodes) == 3
    assert all(node["id"].startswith("l") for node in schema_nodes)
    assert rag.diagnostics["schema_expansion"]["activation_mode"] == "semantic"


def test_semantic_incidental_mention_does_not_expand_schema():
    lucy = [
        _doc("l1", ["Lucy Plast"], "Lucy Plast trasforma materie plastiche."),
        _doc("l2", ["Lucy Plast"], "Lucy Plast usa linee di estrusione."),
        _doc("l3", ["Lucy Plast"], "Lucy Plast dispone di presse di stampaggio."),
    ]
    redis = _Redis(lucy)
    build_schema_projection(redis)
    memory = _Memory(redis, [lucy[0]])

    rag = build_rag_context(
        "Noi di Lucy Plast siamo rientrati presto.",
        memory,
        mode="chat",
        semantic_frame=_semantic_frame(
            ("Lucy Plast", "context", 0.2), needed=False, goal="other"
        ),
    )

    assert not [n for n in rag.nodes if n["retrieval_path"] == "schema_expansion"]
    assert rag.diagnostics["semantic_memory_plan"]["needed"] is False


def test_semantic_comparison_keeps_one_source_bucket_per_entity():
    peroni = [
        _doc(f"p{i}", ["Peroni"], f"Dato {i} sulla Peroni.") for i in range(1, 4)
    ]
    raffo = [
        _doc(f"r{i}", ["Raffo"], f"Dato {i} sulla Raffo.") for i in range(1, 4)
    ]
    redis = _Redis([*peroni, *raffo])
    build_schema_projection(redis)
    memory = _Memory(redis, [])

    rag = build_rag_context(
        "Confronta Peroni e Raffo.",
        memory,
        mode="search",
        semantic_frame=_semantic_frame(
            ("Peroni", "focus", 0.95),
            ("Raffo", "comparison", 0.94),
            goal="comparison",
        ),
    )

    schema_ids = {
        node["id"] for node in rag.nodes
        if node["retrieval_path"] == "schema_expansion"
    }
    assert len(schema_ids) == 2
    assert any(mid.startswith("p") for mid in schema_ids)
    assert any(mid.startswith("r") for mid in schema_ids)


def test_provenance_goal_exposes_source_metadata_without_inventing_origin():
    docs = [
        _doc("i1", ["ICMA2"], "ICMA2 produce attualmente 1300 kg/h.", source="user"),
        _doc("i2", ["ICMA2"], "ICMA2 e' una linea bivite.", source="teach"),
        _doc("i3", ["ICMA2"], "ICMA2 appartiene al reparto estrusione.", source="passive"),
    ]
    redis = _Redis(docs)
    build_schema_projection(redis)
    memory = _Memory(redis, [])

    rag = build_rag_context(
        "Da dove viene la memoria su ICMA2?",
        memory,
        mode="search",
        semantic_frame=_semantic_frame(("ICMA2", "focus", 0.99), goal="provenance"),
    )

    assert "Vincolo di provenienza richiesto" in rag.text
    assert "source=user" in rag.text
    assert "non inventare deduzioni" in rag.text


def test_overview_opens_compound_entity_schema_and_reserves_wide_bundle():
    project = [
        _doc(
            f"z{i}",
            ["Alpha", "ZX"],
            f"Il progetto Alpha ZX contiene il dettaglio tecnico {i}.",
            domain=f"area-{i % 2}",
        )
        for i in range(1, 6)
    ]
    unrelated = _doc("other", ["Altro Tema"], "Nota su Altro Tema.")
    redis = _Redis([*project, unrelated])
    build_schema_projection(redis)
    memory = _Memory(redis, [unrelated])

    rag = build_rag_context(
        "Fammi una panoramica completa di AlphaZX.",
        memory,
        mode="search",
        semantic_frame=_semantic_frame(("AlphaZX", "focus", 0.99), goal="overview"),
    )

    schema_nodes = [
        node for node in rag.nodes
        if node["retrieval_path"] == "schema_expansion"
    ]
    assert len(schema_nodes) == 4
    assert all(node["id"].startswith("z") for node in schema_nodes)
    assert len(rag.diagnostics["schema_expansion"]["focused_schema_ids"]) == 2


if __name__ == "__main__":
    test_projection_merges_corporate_alias_and_excludes_ambient_or_unsafe_nodes()
    test_numeric_values_and_sentence_starters_do_not_become_schemas()
    test_publication_switches_generation_only_after_projection_exists()
    test_expansion_returns_original_sources_and_never_the_schema_as_evidence()
    test_repeated_property_never_crosses_distinct_anchors_by_itself()
    test_compound_query_keeps_repeated_product_type_inside_named_brand()
    test_rag_reserves_bounded_slots_and_marks_schema_lineage()
    test_semantic_focus_opens_named_schema_when_base_retrieval_misses_it()
    test_semantic_incidental_mention_does_not_expand_schema()
    test_semantic_comparison_keeps_one_source_bucket_per_entity()
    test_provenance_goal_exposes_source_metadata_without_inventing_origin()
    test_overview_opens_compound_entity_schema_and_reserves_wide_bundle()
    print("test_memory_schema: OK")
