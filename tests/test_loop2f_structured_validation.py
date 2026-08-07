#!/usr/bin/env python3
"""Regressioni pure del runner appaiato Loop 2f structured v2."""

import json
import tempfile
from pathlib import Path

from benchmarks.euri_memory.loop2f_structured_validation import (
    Loop2FV2Error,
    analyze,
    dry_run,
    execute,
    load_cases,
)


def _relation_for(case):
    return "contradiction" if case["expected_action"] == "supersede_a" else "none"


def test_dry_run_freezes_counts_and_orders():
    report = dry_run()
    assert report["protocol"]["cases"] == {
        "v1_open": 36,
        "v2_challenge": 30,
        "independent_total": 66,
        "observations": 76,
        "classifier_calls": 152,
    }
    assert sum(report["orders"].values()) == 76
    assert set(report["orders"]) == {"legacy→structured", "structured→legacy"}


def test_fake_paired_run_is_complete_resumable_and_go():
    cases = load_cases()
    by_pair = {
        (case["memory_a"], case["memory_b"]): _relation_for(case)
        for case in cases
    }
    calls = {"legacy": 0, "structured": 0}

    def legacy(a, b):
        calls["legacy"] += 1
        return by_pair[(a, b)]

    def structured(a, b):
        calls["structured"] += 1
        relation = by_pair[(a, b)]
        assessment = None
        if relation == "contradiction":
            assessment = {
                "policy_version": "loop2f-structured-affirmative-v2",
                "entity_relation": "same",
                "claim_relation": "same",
                "assertion_kind_a": "current_state",
                "assertion_kind_b": "current_state",
                "mutually_exclusive": "yes",
                "explicit_replacement": "yes",
                "useful_comparison": "no",
            }
        return {
            "relation": relation,
            "contract_ok": True,
            "diagnostic": "",
            "raw_sha256": None,
            "assessment": assessment,
        }

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "run"
        kwargs = {
            "output_dir": output,
            "execute_models": True,
            "legacy_fn": legacy,
            "structured_fn": structured,
            "model": "fake",
            "model_digest": "sha256:fake",
            "digest_resolver": lambda _model: "sha256:fake",
            "enforce_git": False,
            "enforce_output_guard": False,
        }
        result = execute(**kwargs)
        assert result["classifier_calls"] == 152
        assert calls == {"legacy": 76, "structured": 76}
        execute(**kwargs)
        assert calls == {"legacy": 76, "structured": 76}
        report = analyze(results_path=output / "results.json")

    assert report["verdict"] == "GO_DEV"
    assert all(report["gates"].values())
    assert report["suites"]["v1_open"]["structured"]["correct"] == 36
    assert report["suites"]["v2_challenge"]["structured"]["correct"] == 30


def test_execute_gate_and_analysis_tamper_fail_closed():
    try:
        execute(output_dir=Path("/tmp/not-used"), execute_models=False)
    except Loop2FV2Error as exc:
        assert "bloccata" in str(exc)
    else:
        raise AssertionError("--execute mancante doveva essere rifiutato")

    cases = load_cases()
    by_pair = {
        (case["memory_a"], case["memory_b"]): _relation_for(case)
        for case in cases
    }
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "run"
        execute(
            output_dir=output,
            execute_models=True,
            legacy_fn=lambda a, b: by_pair[(a, b)],
            structured_fn=lambda a, b: by_pair[(a, b)],
            model="fake",
            model_digest="sha256:fake",
            digest_resolver=lambda _model: "sha256:fake",
            enforce_git=False,
            enforce_output_guard=False,
        )
        payload = json.loads((output / "results.json").read_text(encoding="utf-8"))
        payload["identity"]["protocol_sha256"] = "wrong"
        tampered = output / "tampered.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        try:
            analyze(results_path=tampered)
        except Loop2FV2Error as exc:
            assert "protocollo diverso" in str(exc)
        else:
            raise AssertionError("identity manomessa doveva essere rifiutata")


if __name__ == "__main__":
    test_dry_run_freezes_counts_and_orders()
    test_fake_paired_run_is_complete_resumable_and_go()
    test_execute_gate_and_analysis_tamper_fail_closed()
    print("test_loop2f_structured_validation: 3/3 OK")
