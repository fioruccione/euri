#!/usr/bin/env python3
"""Regressioni pure per il richiamo cronologico dei turni verbatim."""
from __future__ import annotations

import fnmatch
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.conversation_turns import (
    LEGACY_VOICE_BACKFILL_KEY,
    ConversationTurnStore,
    backfill_legacy_voice_turns,
    iter_accepted_voice_turns,
)
from core.rag_context import RagContext
from core.retrieval_strategy import (
    _maybe_chronological,
    augment_context_with_ids,
    build_chronological_recall,
    choose_strategy,
)


class FakeJson:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, path, value):
        assert path == "$"
        self.docs[key] = dict(value)
        return True

    def get(self, key, path):
        assert path == "$"
        value = self.docs.get(key)
        return [dict(value)] if value is not None else None


class FakeSearch:
    def __init__(self, redis):
        self.redis = redis

    def search(self, query):
        self.redis.last_query = str(query.query_string())
        self.redis.last_sort = tuple(
            getattr(query._sortby, "args", ()) or ()
        )
        terms = [
            term
            for term in ("leonardo", "collega", "ubq", "2026")
            if term in self.redis.last_query.casefold()
        ]
        docs = []
        for document in self.redis.docs.values():
            content = str(document.get("content") or "").casefold()
            if (
                document.get("role") == "user"
                and document.get("memory_scope") == "personal"
                and all(term in content for term in terms)
            ):
                docs.append(document)
        asc = bool(
            self.redis.last_sort
            and str(self.redis.last_sort[-1]).upper() == "ASC"
        )
        docs.sort(key=lambda item: item["observed_at"], reverse=not asc)
        # Come RediSearch: `total` è il numero di match, non quanti ne torniamo.
        # paging(0, 0) è la forma del conteggio puro.
        rows = [] if query._num == 0 else docs
        return SimpleNamespace(
            total=len(docs),
            docs=[
                SimpleNamespace(
                    turn_ref=document["turn_ref"],
                    observed_at=document["observed_at"],
                )
                for document in rows
            ],
        )


class FakeRedis:
    def __init__(self):
        self.docs = {}
        self.values = {}
        self._json = FakeJson(self.docs)
        self.last_query = ""
        self.last_sort = ()

    def json(self):
        return self._json

    def ft(self, index):
        assert index == "idx:turns"
        return FakeSearch(self)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value
        return True

    def scan_iter(self, match):
        for key in self.docs:
            if fnmatch.fnmatch(key, match):
                yield key


def _persist(
    store,
    conversation,
    seq,
    content,
    observed_at,
    *,
    trusted=True,
    scope="personal",
):
    store.persist({
        "conversation_id": conversation,
        "seq": seq,
        "role": "user",
        "content": content,
        "trusted": trusted,
        "observed_at": observed_at,
        "segment_id": 1,
        "memory_scope": scope,
    })


def test_lexical_gate_only_opens_semantic_classifier():
    assert _maybe_chronological(
        "In che data ti ho parlato per la prima volta di Leonardo?"
    )
    assert _maybe_chronological(
        "Qual è l'ultima volta che abbiamo nominato il Compound UBQ?"
    )
    assert not _maybe_chronological("Quando scade la commessa?")

    class SemanticBrain:
        def __init__(self):
            self.calls = 0

        def classify_retrieval_strategy(self, query, recent_history):
            self.calls += 1
            assert "Leonardo" in query
            return {
                "strategy": "chronological_first",
                "subject": "Leonardo collega",
                "confidence": 0.98,
            }

    brain = SemanticBrain()
    assert choose_strategy(
        "Quando ti ho parlato per la prima volta di Leonardo?",
        brain,
        [{"role": "user", "content": "Leonardo è un collega."}],
    ) == ("chronological_first", "Leonardo collega")
    assert brain.calls == 1

    class FastSemanticBrain:
        def __init__(self):
            self.fast_calls = 0

        def classify_chronological_query(self, query, recent_history):
            self.fast_calls += 1
            return {
                "kind": "first",
                "subject": "Leonardo collega",
                "confidence": 0.97,
            }

        def classify_retrieval_strategy(self, *_args):
            raise AssertionError("il classificatore generale non deve essere chiamato")

    fast = FastSemanticBrain()
    assert choose_strategy(
        "Quando ti ho parlato per la prima volta di Leonardo?",
        fast,
        [],
    ) == ("chronological_first", "Leonardo collega")
    assert fast.fast_calls == 1


def test_first_occurrence_uses_trusted_user_turn_and_scope_before_paging():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    _persist(
        store,
        "school",
        1,
        "Abbiamo parlato della scuola Leonardo da Vinci.",
        10.0,
    )
    _persist(
        store,
        "ambient",
        1,
        "Leonardo è un collega presente qui.",
        15.0,
        trusted=False,
    )
    _persist(
        store,
        "experiment",
        1,
        "Leonardo è un collega nello scenario di test.",
        18.0,
        scope="experiment_demo",
    )
    _persist(
        store,
        "real",
        1,
        "Ho qui Leonardo, un mio collega.",
        20.0,
    )
    _persist(
        store,
        "later",
        1,
        "Leonardo è un collega della produzione.",
        30.0,
    )

    turns = store.search_chronological(
        "Leonardo collega", order="first", limit=1
    )

    assert [turn.turn_ref for turn in turns] == ["real:1"]
    assert "@memory_scope:{personal}" in redis.last_query
    assert "@role:{user}" in redis.last_query
    assert "@content:(leonardo collega)" in redis.last_query


def test_context_labels_turn_date_and_never_event_or_memory_date():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    _persist(
        store,
        "real",
        1,
        "Ho qui Leonardo, un mio collega.",
        1785340800.0,
    )

    text, turns = build_chronological_recall(
        store, "Leonardo collega", "chronological_first"
    )

    assert len(turns) == 1
    assert "PRIMA OCCORRENZA TROVATA" in text
    assert "DATA DEL TURNO ORIGINALE" in text
    assert "non data dell'evento" in text
    assert "non data di creazione di una memoria" in text
    assert "Ho qui Leonardo, un mio collega." in text
    assert "archivio disponibile" in text


def test_shown_limit_is_never_verbalised_as_uniqueness():
    """Il caso reale del 29/07: una riga mostrata, 29 occorrenze in archivio.

    `limit=1` produce un solo turno perché ne è stato chiesto uno. Senza il
    censimento il modello lo legge come «l'unica occorrenza che trovo», cioè
    trasforma un parametro di query in un'affermazione sui dati.
    """
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    for index, (ref, when) in enumerate(
        (("primo", 10.0), ("secondo", 20.0), ("terzo", 30.0)), start=1
    ):
        _persist(store, ref, index, f"Ho parlato di Leonardo collega ({ref}).", when)

    text, turns = build_chronological_recall(
        store, "Leonardo collega", "chronological_first"
    )

    assert len(turns) == 1, "il gate mostra una riga sola: è il caso da coprire"
    assert "Occorrenze totali nell'archivio: 3" in text
    assert "NON dire «l'unica»" in text
    assert store.count_chronological("Leonardo collega") == 3


def test_full_result_is_declared_as_complete():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    _persist(store, "solo", 1, "Ho qui Leonardo, un mio collega.", 10.0)

    text, turns = build_chronological_recall(
        store, "Leonardo collega", "chronological_first"
    )

    assert len(turns) == 1
    assert "Occorrenze totali nell'archivio: 1, tutte mostrate qui." in text
    assert "NON dire" not in text


def test_unknown_total_is_not_reported_as_zero_or_as_uniqueness():
    """Conteggio indisponibile ≠ nessuna altra occorrenza: fail-safe sul linguaggio."""
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    _persist(store, "real", 1, "Ho qui Leonardo, un mio collega.", 10.0)

    original = store.count_chronological
    store.count_chronological = lambda *a, **k: None
    try:
        text, turns = build_chronological_recall(
            store, "Leonardo collega", "chronological_first"
        )
    finally:
        store.count_chronological = original

    assert len(turns) == 1
    assert "non verificate" in text
    assert "Non dire né che questa è l'unica" in text


def test_augment_prepends_chronology_and_records_turn_lineage():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    _persist(
        store,
        "real",
        1,
        "Ho qui Leonardo, un mio collega.",
        20.0,
    )

    class Brain:
        def classify_retrieval_strategy(self, *_args):
            return {
                "strategy": "chronological_first",
                "subject": "Leonardo collega",
                "confidence": 0.99,
            }

    rag = RagContext(
        text="Ricordi/note rilevanti:\n- una sintesi senza data affidabile",
        ids=["memory-1"],
        mode="search",
    )
    context, note, memory_ids = augment_context_with_ids(
        "Quando ti ho parlato per la prima volta di Leonardo?",
        rag.text,
        object(),
        Brain(),
        [],
        turn_store=store,
        rag_context=rag,
    )

    assert context.startswith("[RISULTATO CRONOLOGICO VERIFICATO")
    assert context.index("Ho qui Leonardo") < context.index("Ricordi/note rilevanti")
    assert "chronological_first" in note
    assert memory_ids == []
    assert rag.turn_ids == ["real:1"]
    assert rag.nodes[0]["retrieval_path"] == "chronological_first"
    assert rag.diagnostics["chronological_query"] == {
        "strategy": "chronological_first",
        "subject": "Leonardo collega",
        "terms": ["leonardo", "collega"],
        "matches": ["real:1"],
        "found": True,
        "date_semantics": "turn_observed_at",
    }


def test_no_match_is_explicit_and_does_not_infer_from_memories():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)

    text, turns = build_chronological_recall(
        store, "Leonardo collega", "chronological_first"
    )

    assert turns == []
    assert "nessuna occorrenza verificabile" in text
    assert "non ricavarla da sintesi" in text


def test_legacy_backfill_imports_only_stt_that_reached_an_intent():
    log = """\
2026-04-29 17:42:34.723 | INFO | voice.stt - STT: 'Ho qui Leonardo, un mio collega.' (lang=it)
2026-04-29 17:42:35.437 | INFO | daemon - Intent: CHAT — 'Ho qui Leonardo, un mio collega.'
2026-04-29 17:43:00.000 | INFO | voice.stt - STT: 'Leonardo da Vinci è una scuola.' (lang=it)
2026-04-29 17:43:00.100 | DEBUG | daemon - Wake word assente — ignorato
2026-04-29 17:44:00.000 | INFO | voice.stt - STT: 'Il Compound UBQ 2026 è pronto.' (lang=it)
2026-04-29 17:44:01.000 | INFO | daemon - Intent: CHAT — 'Il Compound UBQ 2026 è pronto.'
"""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "voice_daemon.legacy.log"
        path.write_text(log, encoding="utf-8")
        parsed = list(iter_accepted_voice_turns(path))
        assert [item["content"] for item in parsed] == [
            "Ho qui Leonardo, un mio collega.",
            "Il Compound UBQ 2026 è pronto.",
        ]

        redis = FakeRedis()
        report = backfill_legacy_voice_turns(redis, log_paths=[path])
        assert report["imported"] == 2
        assert report["parsed"] == 2
        assert redis.get(LEGACY_VOICE_BACKFILL_KEY)
        assert len(redis.docs) == 2
        assert all(
            doc["archive_origin"] == "legacy_voice_log"
            for doc in redis.docs.values()
        )

        # Marker + riferimenti hash rendono la migrazione idempotente.
        again = backfill_legacy_voice_turns(redis, log_paths=[path])
        assert again["imported"] == 2
        assert len(redis.docs) == 2


if __name__ == "__main__":
    tests = [
        globals()[name]
        for name in sorted(globals())
        if name.startswith("test_")
    ]
    for test in tests:
        test()
    print(f"test_chronological_recall: OK ({len(tests)} casi)")
