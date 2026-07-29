#!/usr/bin/env python3
"""Regressioni pure del protocollo appaiato Loop 2f / Loop 2h."""

import json
import tempfile
from pathlib import Path

from benchmarks.euri_memory.loop2fh_validation import (
    DEFAULT_FIXTURE,
    LABELS_2F,
    LABELS_2H,
    Loop2FHError,
    _identity,
    analyze,
    counterbalanced_order,
    dry_run,
    execute,
    load_fixture,
    observation_key,
    protocol_digest,
    repetitions,
)
from benchmarks.euri_memory.integrity import sha256_file


def _synthetic_results() -> dict:
    fixture = load_fixture(DEFAULT_FIXTURE)
    identity = _identity(
        DEFAULT_FIXTURE.resolve(),
        model="fake-model",
        model_digest="sha256:fake",
    )
    records = {}
    forced_false_supersessions = {"dist_01", "dist_04"}
    for case in fixture["cases"]:
        for replica in range(repetitions(case)):
            key = observation_key(case["case_id"], replica)
            expected = case.get("expected_action")
            label_2f = (
                "contradiction"
                if expected == "supersede_a"
                or case["case_id"] in forced_false_supersessions
                else "none"
            )
            records[key] = {
                "observation_key": key,
                "case_id": case["case_id"],
                "replica": replica,
                "order": list(counterbalanced_order(case["case_id"], replica)),
                "labels": {
                    "2f": {
                        "label": label_2f,
                        "latency_s": 0.1,
                        "completed_at": 1.0,
                    },
                    "2h": {
                        "label": case["expected_2h_relation"],
                        "latency_s": 0.2,
                        "completed_at": 1.0,
                    },
                },
            }
    return {"schema_version": 1, "identity": identity, "records": records}


def test_fixture_and_dry_run_are_frozen_and_complete():
    fixture = load_fixture(DEFAULT_FIXTURE)
    report = dry_run(DEFAULT_FIXTURE)

    assert len(fixture["cases"]) == 42
    assert report["protocol"]["fixture"]["primary_cases"] == 36
    assert report["protocol"]["fixture"]["diagnostic_ambiguous_cases"] == 6
    assert report["protocol"]["fixture"]["stability_cases"] == 9
    assert report["protocol"]["execution"]["observations"] == 60
    assert report["protocol"]["execution"]["classifier_calls"] == 120
    assert sum(report["orders"].values()) == 60
    assert set(report["orders"]) == {"2f→2h", "2h→2f"}
    assert all(report["invariants"].values())


def test_gold_is_not_part_of_classifier_contract():
    fixture = load_fixture(DEFAULT_FIXTURE)
    seen = []

    def fake_2f(a, b):
        seen.append(("2f", a, b))
        return "none"

    def fake_2h(a, b):
        seen.append(("2h", a, b))
        return "unknown"

    with tempfile.TemporaryDirectory() as tmp:
        result = execute(
            fixture_path=DEFAULT_FIXTURE,
            output_dir=Path(tmp) / "run",
            execute_models=True,
            classify_2f=fake_2f,
            classify_2h=fake_2h,
            model="fake-model",
            model_digest="sha256:fake",
            enforce_git=False,
            enforce_output_guard=False,
            model_digest_resolver=lambda _model: "sha256:fake",
        )

    assert result["classifier_calls"] == 120
    assert len(seen) == 120
    expected_pairs = {
        (case["memory_a"], case["memory_b"]) for case in fixture["cases"]
    }
    assert {(a, b) for _, a, b in seen} == expected_pairs


def test_execute_is_gated_and_resume_skips_completed_calls():
    try:
        execute(
            fixture_path=DEFAULT_FIXTURE,
            output_dir=Path("/tmp/not-used"),
            execute_models=False,
        )
    except Loop2FHError as exc:
        assert "bloccata" in str(exc)
    else:
        raise AssertionError("execute=False doveva essere rifiutato")

    calls = {"2f": 0, "2h": 0}

    def fake_2f(_a, _b):
        calls["2f"] += 1
        return "none"

    def fake_2h(_a, _b):
        calls["2h"] += 1
        return "unknown"

    with tempfile.TemporaryDirectory() as tmp:
        kwargs = {
            "fixture_path": DEFAULT_FIXTURE,
            "output_dir": Path(tmp) / "run",
            "execute_models": True,
            "classify_2f": fake_2f,
            "classify_2h": fake_2h,
            "model": "fake-model",
            "model_digest": "sha256:fake",
            "enforce_git": False,
            "enforce_output_guard": False,
            "model_digest_resolver": lambda _model: "sha256:fake",
        }
        execute(**kwargs)
        assert calls == {"2f": 60, "2h": 60}
        execute(**kwargs)
        assert calls == {"2f": 60, "2h": 60}


def test_analysis_detects_incremental_value_without_damage():
    results = _synthetic_results()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        report = analyze(results_path=path, fixture_path=DEFAULT_FIXTURE)

    assert report["verdict"] == {
        "loop2f": "GO_DEV",
        "loop2h_incremental": "GO_DEV",
    }
    assert report["arms"]["2f"]["false_supersessions"] == 2
    assert report["arms"]["2f_plus_2h"]["false_supersessions"] == 0
    assert report["loop2h_incremental"]["cross_entity_opportunities"] == 2
    assert report["loop2h_incremental"]["cross_entity_corrected"] == 2
    assert report["loop2h_incremental"]["true_supersessions_damaged"] == 0
    assert report["paired"]["improved"] == 2
    assert report["paired"]["worsened"] == 0


def test_analysis_fails_closed_on_identity_or_label_tampering():
    results = _synthetic_results()
    results["identity"]["fixture_sha256"] = "wrong"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        try:
            analyze(results_path=path, fixture_path=DEFAULT_FIXTURE)
        except Loop2FHError as exc:
            assert "fixture diversa" in str(exc)
        else:
            raise AssertionError("identity alterata doveva essere rifiutata")

    results = _synthetic_results()
    first = next(iter(results["records"].values()))
    first["labels"]["2f"]["label"] = "invented"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "results.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        try:
            analyze(results_path=path, fixture_path=DEFAULT_FIXTURE)
        except Loop2FHError as exc:
            assert "label 2f invalida" in str(exc)
        else:
            raise AssertionError("label alterata doveva essere rifiutata")


def test_protocol_identity_contains_source_and_fixture_hashes():
    identity = _identity(
        DEFAULT_FIXTURE.resolve(),
        model="fake-model",
        model_digest="sha256:fake",
    )
    assert identity["fixture_sha256"] == sha256_file(DEFAULT_FIXTURE)
    assert identity["protocol_sha256"] == protocol_digest(DEFAULT_FIXTURE)
    assert set(identity["source_sha256"]) == {
        "loop2f_classifier",
        "loop2f_ollama_wrapper",
        "loop2h_classifier",
    }
    assert identity["model"] == "fake-model"
    assert identity["model_digest"] == "sha256:fake"


if __name__ == "__main__":
    test_fixture_and_dry_run_are_frozen_and_complete()
    test_gold_is_not_part_of_classifier_contract()
    test_execute_is_gated_and_resume_skips_completed_calls()
    test_analysis_detects_incremental_value_without_damage()
    test_analysis_fails_closed_on_identity_or_label_tampering()
    test_protocol_identity_contains_source_and_fixture_hashes()
    print(
        "test_loop2fh_validation: 6/6 OK "
        f"(2f={sorted(LABELS_2F)}, 2h={sorted(LABELS_2H)})"
    )
