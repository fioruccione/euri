#!/usr/bin/env python3
"""Regressioni pure per Dream Trace Paired V2 (stesso seme, con/senza residuo)."""

from types import SimpleNamespace

import numpy as np

import config
from core.dream_engine import (
    DREAM_TRACE_PAIRED_RESIDUE_KEY,
    DREAM_TRACE_PAIRED_SEQUENCE_KEY,
    DREAM_TRACE_PAIRED_STREAM,
    DreamEngine,
)
from scripts.experiments.sample_dream_trace_paired import (
    _collect_pairs,
    _error_imbalance,
    _valid_side,
)


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def set(self, key, _path, value):
        self.redis.docs[key] = value


class FakeRedis:
    def __init__(self, residue=None):
        self.values = {}
        if residue is not None:
            self.values[DREAM_TRACE_PAIRED_RESIDUE_KEY] = residue
        self.docs = {}
        self.streams = []
        self.expired = []
        self._seq = 0
        self._json = FakeJson(self)

    def get(self, key):
        return self.values.get(key)

    def incr(self, _key):
        self._seq += 1
        return self._seq

    def setex(self, key, _ttl, value):
        self.values[key] = value

    def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0

    def json(self):
        return self._json

    def expire(self, key, ttl):
        self.expired.append((key, ttl))

    def xadd(self, key, fields, **kwargs):
        self.streams.append((key, fields, kwargs))
        return "1-0"


class FakeEmbedder:
    def encode(self, _content, mode=None):
        return np.array([1.0, 0.0])


def _seeds():
    return iter([
        ("dominio_a", {"id": "mem-a", "content": "fatto sorgente A"}),
        ("dominio_b", {"id": "mem-b", "content": "fatto sorgente B"}),
    ])


def _chat_factory(*, thinking_text="ragionamento esplorativo " * 10):
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        if kwargs.get("think") is False:
            return SimpleNamespace(message=SimpleNamespace(
                content="ho provato un ponte causale: debole perché mancava una misura",
                thinking="",
            ))
        return SimpleNamespace(message=SimpleNamespace(
            content=(
                "Nel dominio [dominio_a] succede: fatto sorgente A.\n"
                "Nel dominio [dominio_b] succede: fatto sorgente B.\n"
                "La connessione operativa non ovvia è: verificare insieme A e B."
            ),
            thinking=thinking_text,
        ))
    return chat, calls


def _run(*, residue, paired_enabled=True, legacy_enabled=False):
    redis_key = "euri:dream_trace:latest" if legacy_enabled else DREAM_TRACE_PAIRED_RESIDUE_KEY
    redis = FakeRedis()
    if residue is not None:
        redis.values[redis_key] = residue
    engine = DreamEngine(redis, FakeEmbedder())
    seeds = _seeds()
    engine._pick_dream_seed = lambda *_args, **_kwargs: next(seeds)
    chat, calls = _chat_factory()
    engine._ollama_chat = chat

    old = {
        "DREAM_TRACE_ENABLED": config.DREAM_TRACE_ENABLED,
        "DREAM_TRACE_PAIRED_ENABLED": config.DREAM_TRACE_PAIRED_ENABLED,
        "BRIDGE_VALIDITY_ENABLED": config.BRIDGE_VALIDITY_ENABLED,
    }
    config.DREAM_TRACE_ENABLED = legacy_enabled
    config.DREAM_TRACE_PAIRED_ENABLED = paired_enabled and not legacy_enabled
    config.BRIDGE_VALIDITY_ENABLED = False
    try:
        result = engine._generate_dream(["dominio_a", "dominio_b"])
    finally:
        for name, value in old.items():
            setattr(config, name, value)
    return result, redis, calls


def test_warmup_generates_once_and_seeds_residue_without_logging_a_pair():
    assert config.DREAM_TRACE_PAIRED_VERSION in DREAM_TRACE_PAIRED_RESIDUE_KEY
    assert config.DREAM_TRACE_PAIRED_VERSION in DREAM_TRACE_PAIRED_SEQUENCE_KEY
    result, redis, calls = _run(residue=None)
    assert result is not None and result["status"] == "candidate"
    # 1 generazione del sogno (think=True) + 1 distillazione del residuo (think=False).
    assert len(calls) == 2
    assert calls[0].get("think") is True
    assert calls[1].get("think") is False
    assert redis.streams == []  # nessuna coppia: niente da confrontare
    assert redis.values[DREAM_TRACE_PAIRED_RESIDUE_KEY].startswith("ho provato")


def test_paired_generation_uses_identical_seed_for_both_arms():
    result, redis, calls = _run(residue="strategia debole precedente")
    assert result is not None
    # baseline (think=True) + trattamento (think=True) + distillazione (think=False).
    assert len(calls) == 3
    assert len(redis.streams) == 2

    by_arm = {fields["arm"]: fields for _key, fields, _kw in redis.streams}
    assert set(by_arm) == {"baseline", "trattamento"}
    assert by_arm["baseline"]["pair_id"] == by_arm["trattamento"]["pair_id"]

    # Il seme (entrambe le memorie sorgente) e' identico nei due lati.
    assert by_arm["baseline"]["memory_a_content"] == by_arm["trattamento"]["memory_a_content"]
    assert by_arm["baseline"]["memory_b_content"] == by_arm["trattamento"]["memory_b_content"]

    baseline_prompt = calls[0]["messages"][0]["content"]
    treatment_prompt = calls[1]["messages"][0]["content"]
    assert "[TRACCIA DEL CICLO PRECEDENTE" not in baseline_prompt
    assert "[TRACCIA DEL CICLO PRECEDENTE" in treatment_prompt

    for fields in by_arm.values():
        assert _valid_side(fields) is True
        assert fields.get("duration_s")
        assert float(fields["duration_s"]) >= 0.0

    # Solo il baseline diventa un insight vivo: il trattamento resta strumentazione.
    assert by_arm["baseline"]["insight_persisted"] == "1"
    assert by_arm["trattamento"]["insight_persisted"] == "0"
    insight_docs = [doc for key, doc in redis.docs.items() if "insight" in key]
    assert len(insight_docs) == 1
    assert insight_docs[0]["trace_arm"] == "baseline"


def test_residue_metadata_is_per_arm_not_shared():
    """Bug segnalato in review: il record baseline non deve mostrare il residuo
    come se fosse stato iniettato, anche se il prompt correttamente non lo riceve."""
    _result, redis, _calls = _run(residue="strategia debole precedente")
    by_arm = {fields["arm"]: fields for _key, fields, _kw in redis.streams}
    assert by_arm["baseline"]["trace_residue"] == ""
    assert by_arm["trattamento"]["trace_residue"] == "strategia debole precedente"
    assert by_arm["baseline"]["trace_available"] == "1"
    assert by_arm["trattamento"]["trace_available"] == "1"


def test_residue_evolves_only_from_treatment_side():
    _result, redis, _calls = _run(residue="strategia debole precedente")
    # Il residuo per il ciclo successivo si distilla solo dal CoT del lato
    # trattamento (capture_cot=True); il baseline non contribuisce mai.
    assert redis.values[DREAM_TRACE_PAIRED_RESIDUE_KEY].startswith("ho provato")


def test_valid_side_checks_hash_even_on_discarded():
    _result, redis, _calls = _run(residue="strategia debole precedente")
    by_arm = {fields["arm"]: fields for _key, fields, _kw in redis.streams}
    discarded = dict(by_arm["baseline"])
    discarded["status"] = "discarded"
    discarded["model_output"] = "NESSUN INSIGHT"
    # chars/hash non aggiornati per corrispondere al nuovo output: deve fallire
    # anche se lo stato non e' "candidate".
    assert _valid_side(discarded) is False


def _distill(*, distillation_reply: str, existing_residue=None,
             previous_residue="", clear_on_invalid=True):
    """Chiama _update_dream_trace isolatamente con una risposta di distillazione
    controllata, per verificare cosa finisce (o non finisce) nel residuo."""
    redis = FakeRedis()
    if existing_residue is not None:
        redis.values[DREAM_TRACE_PAIRED_RESIDUE_KEY] = existing_residue
    engine = DreamEngine(redis, FakeEmbedder())

    def chat(**kwargs):
        assert kwargs.get("think") is False
        return SimpleNamespace(message=SimpleNamespace(content=distillation_reply, thinking=""))

    engine._ollama_chat = chat
    cot = "ragionamento esplorativo abbastanza lungo da superare la soglia minima " * 3
    engine._update_dream_trace(cot, "dominio_a", "dominio_b",
                                trace_key=DREAM_TRACE_PAIRED_RESIDUE_KEY,
                                clear_on_invalid=clear_on_invalid,
                                previous_residue=previous_residue)
    return redis.values.get(DREAM_TRACE_PAIRED_RESIDUE_KEY)


def test_distillation_rejects_the_wrong_sentinel_nessun_insight():
    """Bug trovato il 22/07 su dati reali: il modello a volte risponde con la
    sentinella dell'ALTRO prompt (NESSUN INSIGHT) invece di NIENTE DA SEGNALARE.
    Prima del fix questo veniva scritto come residuo vero."""
    assert _distill(distillation_reply="NESSUN INSIGHT") is None


def test_paired_invalid_distillation_clears_stale_residue():
    assert _distill(
        distillation_reply="NESSUN INSIGHT",
        existing_residue="vecchia strategia valida",
    ) is None


def test_legacy_invalid_distillation_can_keep_previous_residue():
    assert _distill(
        distillation_reply="NESSUN INSIGHT",
        existing_residue="vecchia strategia legacy",
        clear_on_invalid=False,
    ) == "vecchia strategia legacy"


def test_paired_distillation_exception_clears_stale_residue():
    redis = FakeRedis(residue="residuo da non riusare")
    engine = DreamEngine(redis, FakeEmbedder())

    def failing_chat(**_kwargs):
        raise RuntimeError("distillatore non disponibile")

    engine._ollama_chat = failing_chat
    cot = "ragionamento esplorativo abbastanza lungo da superare la soglia " * 3
    assert engine._update_dream_trace(
        cot, "dominio_a", "dominio_b",
        trace_key=DREAM_TRACE_PAIRED_RESIDUE_KEY,
        clear_on_invalid=True,
    ) is False
    assert DREAM_TRACE_PAIRED_RESIDUE_KEY not in redis.values


def test_distillation_accepts_well_formatted_lines():
    reply = (
        "ho provato un ponte diretto: debole perché mancava un dato verificabile.\n"
        "ho considerato un'analogia strutturale: debole perché troppo generica."
    )
    residue = _distill(distillation_reply=reply)
    assert residue is not None
    assert residue.count("\n") == 1  # entrambe le righe conformi mantenute


def test_distillation_drops_non_conforming_lines_keeps_the_rest():
    reply = (
        "ho provato un ponte diretto: debole perché mancava un dato verificabile.\n"
        "una riga senza il formato richiesto"
    )
    residue = _distill(distillation_reply=reply)
    assert residue == "ho provato un ponte diretto: debole perché mancava un dato verificabile."


def test_distillation_drops_thematic_echo_keeps_new_lines():
    previous = (
        "ho provato a collegare le vibrazioni meccaniche del blocco al disallineamento "
        "dell'antenna Yagi: debole perché richiede un effetto fisico non verificabile.\n"
        "ho ipotizzato che i picchi del motore generassero EMI sull'antenna: debole "
        "perché la schermatura è una variabile infrastrutturale fissa."
    )
    reply = (
        "ho provato un ponte infrastrutturale vibrazioni/EMI: debole perché basato "
        "su correlazioni fisiche non verificabili.\n"
        "ho cercato un vincolo geometrico sul filtro: debole perché manca una "
        "procedura di collaudo esplicita."
    )
    residue = _distill(distillation_reply=reply, previous_residue=previous)
    assert residue == (
        "ho cercato un vincolo geometrico sul filtro: debole perché manca una "
        "procedura di collaudo esplicita."
    )


def test_sampler_rejects_tampered_side():
    _result, redis, _calls = _run(residue="strategia debole precedente")
    _key, fields, _kw = redis.streams[0]
    assert _valid_side(fields) is True
    tampered = dict(fields)
    tampered["model_output"] = tampered.get("model_output", "") + " alterato"
    assert _valid_side(tampered) is False


def test_sampler_rejects_tampered_residue():
    _result, redis, _calls = _run(residue="strategia debole precedente")
    _key, fields, _kw = redis.streams[1]
    assert _valid_side(fields) is True
    tampered = dict(fields)
    tampered["trace_residue"] += " alterato"
    assert _valid_side(tampered) is False


def test_legacy_single_arm_path_still_generates_one_candidate_with_trace():
    result, redis, calls = _run(residue="strategia debole precedente", legacy_enabled=True)
    assert result is not None and result["status"] == "candidate"
    # 1 generazione (think=True) + 1 distillazione del residuo (think=False).
    assert len(calls) == 2
    assert redis.streams == []  # il percorso legacy non scrive lo stream paired
    assert DREAM_TRACE_PAIRED_RESIDUE_KEY not in redis.values
    prompt = calls[0]["messages"][0]["content"]
    assert "[TRACCIA DEL CICLO PRECEDENTE" in prompt
    insight_docs = [doc for key, doc in redis.docs.items() if "insight" in key]
    assert len(insight_docs) == 1
    assert insight_docs[0]["trace_injected"] is True
    assert "trace_pair_id" not in insight_docs[0]


# --- copertura del campionatore: raggruppamento, duplicati, errori per braccio ---

def _side(pair_id, arm, *, status="candidate", output="testo", duplicate_of=None, ts=1000.0):
    output_bytes = output.encode("utf-8")
    import hashlib
    source_a, source_b = "sorgente A", "sorgente B"
    return (f"entry-{pair_id}-{arm}" if duplicate_of is None else duplicate_of, {
        "experiment_version": config.DREAM_TRACE_PAIRED_VERSION,
        "ts": repr(ts),
        "pair_id": str(pair_id),
        "arm": arm,
        "trace_available": "1",
        "trace_residue": "" if arm == "baseline" else "residuo valido",
        "trace_residue_sha256": hashlib.sha256(
            ("" if arm == "baseline" else "residuo valido").encode("utf-8")
        ).hexdigest(),
        "status": status,
        "model_output": output,
        "model_output_chars": str(len(output)),
        "model_output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "memory_a_content": source_a,
        "memory_a_sha256": hashlib.sha256(source_a.encode("utf-8")).hexdigest(),
        "memory_b_content": source_b,
        "memory_b_sha256": hashlib.sha256(source_b.encode("utf-8")).hexdigest(),
        "duration_s": "1.0",
        "record_complete": "1",
    })


def test_collect_pairs_excludes_duplicate_side_entirely():
    entries = [
        _side(1, "baseline"),
        _side(1, "trattamento"),
        _side(2, "baseline"),
        _side(2, "baseline"),  # duplicato: stesso pair_id+arm
        _side(2, "trattamento"),
    ]
    complete, duplicates, invalid, errors, incomplete, excluded_pre_fix = _collect_pairs(
        entries, config.DREAM_TRACE_PAIRED_VERSION
    )
    assert set(complete.keys()) == {"1"}
    assert duplicates == {"2"}
    assert invalid == 0
    assert excluded_pre_fix == 0


def test_collect_pairs_counts_errors_per_arm_without_hiding_them():
    entries = [
        _side(1, "baseline"),
        _side(1, "trattamento", status="error", output=""),
        _side(2, "baseline"),
        _side(2, "trattamento"),
    ]
    complete, duplicates, invalid, errors, incomplete, excluded_pre_fix = _collect_pairs(
        entries, config.DREAM_TRACE_PAIRED_VERSION
    )
    # La coppia 1 non e' completa (il lato in errore non e' valido), ma l'errore
    # deve comunque comparire nel conteggio per braccio, non sparire silenziosamente.
    assert "1" not in complete
    assert errors["trattamento"] == 1
    assert errors["baseline"] == 0


def test_error_imbalance_detects_zero_vs_nonzero_arm():
    imbalanced, detail = _error_imbalance({"baseline": 0, "trattamento": 2})
    assert imbalanced is True
    assert "rapporto infinito" in detail

    imbalanced, _detail = _error_imbalance({"baseline": 0, "trattamento": 0})
    assert imbalanced is False


def test_collect_pairs_supports_optional_timestamp_cutoff():
    """Il cutoff resta disponibile come controllo diagnostico, anche se v2 usa
    experiment_version come separazione primaria."""
    entries = [
        _side(1, "baseline", ts=100.0),      # pre-fix
        _side(1, "trattamento", ts=100.0),   # pre-fix
        _side(2, "baseline", ts=500.0),      # post-fix
        _side(2, "trattamento", ts=500.0),   # post-fix
    ]
    complete, duplicates, invalid, errors, incomplete, excluded_pre_fix = _collect_pairs(
        entries, config.DREAM_TRACE_PAIRED_VERSION, valid_since_ts=300.0
    )
    assert set(complete.keys()) == {"2"}
    assert excluded_pre_fix == 2
    assert invalid == 0


def test_collect_pairs_ignores_previous_experiment_version():
    old = [
        _side(1, "baseline"),
        _side(1, "trattamento"),
    ]
    for _entry_id, fields in old:
        fields["experiment_version"] = "dream_trace_paired_v1"
    complete, duplicates, invalid, errors, incomplete, excluded = _collect_pairs(
        old, config.DREAM_TRACE_PAIRED_VERSION
    )
    assert complete == {}
    assert duplicates == set()
    assert invalid == 0
    assert incomplete == 0
    assert excluded == 0


if __name__ == "__main__":
    test_warmup_generates_once_and_seeds_residue_without_logging_a_pair()
    test_paired_generation_uses_identical_seed_for_both_arms()
    test_residue_metadata_is_per_arm_not_shared()
    test_residue_evolves_only_from_treatment_side()
    test_distillation_rejects_the_wrong_sentinel_nessun_insight()
    test_paired_invalid_distillation_clears_stale_residue()
    test_legacy_invalid_distillation_can_keep_previous_residue()
    test_paired_distillation_exception_clears_stale_residue()
    test_distillation_accepts_well_formatted_lines()
    test_distillation_drops_non_conforming_lines_keeps_the_rest()
    test_distillation_drops_thematic_echo_keeps_new_lines()
    test_valid_side_checks_hash_even_on_discarded()
    test_sampler_rejects_tampered_side()
    test_sampler_rejects_tampered_residue()
    test_legacy_single_arm_path_still_generates_one_candidate_with_trace()
    test_collect_pairs_excludes_duplicate_side_entirely()
    test_collect_pairs_counts_errors_per_arm_without_hiding_them()
    test_error_imbalance_detects_zero_vs_nonzero_arm()
    test_collect_pairs_supports_optional_timestamp_cutoff()
    test_collect_pairs_ignores_previous_experiment_version()
    print("test_dream_trace_paired: OK")
