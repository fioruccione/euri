"""Confronto preregistrato fra Loop 2f legacy e structured v2."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import config
from benchmarks.euri_memory.integrity import (
    assert_worktree_clean,
    git_head,
    sha256_file,
)
from benchmarks.euri_memory.loop2fh_validation import (
    AUDIT_ROOT,
    DEFAULT_FIXTURE as V1_FIXTURE,
    REPO_ROOT,
    _exact_mcnemar,
    _model_digest,
    _sha_text,
)
from core.dream_engine import DreamEngine
from core.loop2f_policy import (
    POLICY_VERSION,
    audit_basis,
    relation_from_assessment,
)


CHALLENGE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "loop2f_structured_v2_challenge.json"
)
PREREGISTRATION = REPO_ROOT / "docs" / "EURI_LOOP2F_STRUCTURED_V2_PREREGISTRATION.md"
PROTOCOL_VERSION = "loop2f-structured-paired-v2"
ARMS = ("legacy", "structured")
RELATIONS = frozenset({"contradiction", "comparison", "none"})


class Loop2FV2Error(RuntimeError):
    pass


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise Loop2FV2Error(f"fixture non valida: {path}")
    return payload


def load_cases() -> list[dict]:
    v1 = _load_json(V1_FIXTURE)
    challenge = _load_json(CHALLENGE_FIXTURE)
    rows = []
    seen = set()
    for suite, fixture in (("v1_open", v1), ("v2_challenge", challenge)):
        for source in fixture["cases"]:
            if suite == "v1_open" and source.get("expected_action") is None:
                continue
            case = {
                "suite": suite,
                "case_id": str(source.get("case_id") or ""),
                "stratum": str(source.get("stratum") or ""),
                "memory_a": str(source.get("memory_a") or ""),
                "memory_b": str(source.get("memory_b") or ""),
                "expected_action": source.get("expected_action"),
                "stability_probe": bool(
                    source.get("stability_probe") and suite == "v2_challenge"
                ),
            }
            key = f"{suite}:{case['case_id']}"
            if (
                not case["case_id"]
                or key in seen
                or not case["memory_a"]
                or not case["memory_b"]
                or case["expected_action"] not in {"supersede_a", "keep_both"}
            ):
                raise Loop2FV2Error(f"caso non valido o duplicato: {key}")
            seen.add(key)
            rows.append(case)
    if len([row for row in rows if row["suite"] == "v1_open"]) != 36:
        raise Loop2FV2Error("regressione v1 diversa da 36 casi primari")
    if len([row for row in rows if row["suite"] == "v2_challenge"]) != 30:
        raise Loop2FV2Error("challenge v2 diverso da 30 casi")
    return rows


def repetitions(case: dict) -> int:
    return 3 if case["stability_probe"] else 1


def observation_key(case: dict, replica: int) -> str:
    return f"{case['suite']}__{case['case_id']}__r{replica}"


def arm_order(key: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{PROTOCOL_VERSION}:{key}".encode()).digest()
    return ARMS if digest[0] % 2 == 0 else tuple(reversed(ARMS))


def _source_hashes() -> dict[str, str]:
    return {
        "legacy_classifier": _sha_text(
            inspect.getsource(DreamEngine._llm_classify_pair_legacy)
        ),
        "structured_classifier": _sha_text(
            inspect.getsource(DreamEngine._llm_assess_pair)
        ),
        "structured_policy": _sha_text(inspect.getsource(relation_from_assessment)),
    }


def protocol_payload() -> dict:
    cases = load_cases()
    observations = sum(repetitions(case) for case in cases)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "policy_version": POLICY_VERSION,
        "fixtures": {
            "v1_sha256": sha256_file(V1_FIXTURE),
            "challenge_sha256": sha256_file(CHALLENGE_FIXTURE),
        },
        "cases": {
            "v1_open": 36,
            "v2_challenge": 30,
            "independent_total": 66,
            "observations": observations,
            "classifier_calls": observations * 2,
        },
        "source_sha256": _source_hashes(),
        "redis_access": False,
        "early_stop_on_metrics": False,
    }


def protocol_digest() -> str:
    return _sha_text(_canonical(protocol_payload()))


def dry_run() -> dict:
    cases = load_cases()
    orders = Counter()
    for case in cases:
        for replica in range(repetitions(case)):
            key = observation_key(case, replica)
            orders["→".join(arm_order(key))] += 1
    return {
        "protocol": protocol_payload(),
        "protocol_sha256": protocol_digest(),
        "orders": dict(orders),
        "challenge_strata": dict(
            Counter(
                case["stratum"]
                for case in cases
                if case["suite"] == "v2_challenge"
            )
        ),
    }


def _assert_committed() -> None:
    for path in (Path(__file__).resolve(), V1_FIXTURE, CHALLENGE_FIXTURE, PREREGISTRATION):
        relative = path.resolve().relative_to(REPO_ROOT)
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(relative)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise Loop2FV2Error(f"protocollo non committato: {relative}")


def _assert_output(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(AUDIT_ROOT.resolve())
    except ValueError as exc:
        raise Loop2FV2Error("output-dir deve stare sotto audit_output") from exc
    if resolved == AUDIT_ROOT.resolve():
        raise Loop2FV2Error("output-dir non può essere audit_output")
    return resolved


def _identity(model: str, digest: str) -> dict:
    head = git_head(REPO_ROOT)
    if not head:
        raise Loop2FV2Error("HEAD Git non disponibile")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "protocol_sha256": protocol_digest(),
        "git_commit": head,
        "model": model,
        "model_digest": digest,
        "source_sha256": _source_hashes(),
        "fixtures": protocol_payload()["fixtures"],
    }


def classify_legacy(memory_a: str, memory_b: str) -> dict:
    engine = DreamEngine.__new__(DreamEngine)
    captured = {}

    def audited_chat(**kwargs):
        try:
            response = DreamEngine._ollama_chat(engine, **kwargs)
            captured["raw"] = response.message.content or ""
            return response
        except Exception as exc:
            captured["error"] = repr(exc)
            raise

    engine._ollama_chat = audited_chat
    label = DreamEngine._llm_classify_pair_legacy(engine, memory_a, memory_b)
    if captured.get("error"):
        raise Loop2FV2Error(f"legacy ha mascherato errore modello: {captured['error']}")
    raw = str(captured.get("raw") or "").strip().upper()
    contract_ok = bool(raw) and any(
        token in raw for token in ("CONTRADD", "CONFRONT", "NESSUNA")
    )
    return {
        "relation": label,
        "contract_ok": contract_ok,
        "diagnostic": "" if contract_ok else "unrecognized_output",
        "raw_sha256": _sha_text(raw),
        "assessment": None,
    }


def classify_structured(memory_a: str, memory_b: str) -> dict:
    engine = DreamEngine.__new__(DreamEngine)
    captured = {}

    def audited_chat(**kwargs):
        try:
            response = DreamEngine._ollama_chat(engine, **kwargs)
            captured["raw"] = response.message.content or ""
            return response
        except Exception as exc:
            captured["error"] = repr(exc)
            raise

    engine._ollama_chat = audited_chat
    result = DreamEngine._llm_assess_pair(engine, memory_a, memory_b)
    if captured.get("error"):
        raise Loop2FV2Error(
            f"structured ha mascherato errore modello: {captured['error']}"
        )
    raw = re.sub(
        r"<think>.*?</think>",
        "",
        str(captured.get("raw") or ""),
        flags=re.DOTALL,
    ).strip()
    return {
        **result,
        "raw_sha256": _sha_text(raw),
        "assessment": audit_basis(result.get("assessment")),
    }


def _normalize_result(value: Any) -> dict:
    if isinstance(value, str):
        value = {
            "relation": value,
            "contract_ok": True,
            "diagnostic": "",
            "raw_sha256": None,
            "assessment": None,
        }
    if not isinstance(value, dict) or value.get("relation") not in RELATIONS:
        raise Loop2FV2Error("risultato classificatore non valido")
    if not isinstance(value.get("contract_ok"), bool):
        raise Loop2FV2Error("contract_ok non valido")
    assessment = value.get("assessment")
    if assessment is not None and not isinstance(assessment, dict):
        raise Loop2FV2Error("assessment non valido")
    raw_sha = value.get("raw_sha256")
    if raw_sha is not None and (
        not isinstance(raw_sha, str) or len(raw_sha) != 64
    ):
        raise Loop2FV2Error("raw_sha256 non valido")
    return {
        "relation": value["relation"],
        "contract_ok": value["contract_ok"],
        "diagnostic": str(value.get("diagnostic") or ""),
        "raw_sha256": raw_sha,
        "assessment": assessment,
    }


def _validate_checkpoint(payload: dict, identity: dict, expected: set[str]) -> None:
    if payload.get("identity") != identity:
        raise Loop2FV2Error("checkpoint con identità divergente")
    records = payload.get("records")
    if not isinstance(records, dict) or not set(records).issubset(expected):
        raise Loop2FV2Error("checkpoint con records estranei")
    for key, record in records.items():
        if record.get("observation_key") != key:
            raise Loop2FV2Error(f"{key}: identità record divergente")
        labels = record.get("arms") or {}
        if not set(labels).issubset(ARMS):
            raise Loop2FV2Error(f"{key}: arm estraneo")
        for result in labels.values():
            _normalize_result(result)


def execute(
    *,
    output_dir: Path,
    execute_models: bool = False,
    legacy_fn: Callable[[str, str], Any] | None = None,
    structured_fn: Callable[[str, str], Any] | None = None,
    model: str | None = None,
    model_digest: str | None = None,
    enforce_git: bool = True,
    enforce_output_guard: bool = True,
    digest_resolver: Callable[[str], str] | None = None,
) -> dict:
    if not execute_models:
        raise Loop2FV2Error("esecuzione bloccata senza --execute")
    if enforce_git:
        assert_worktree_clean(REPO_ROOT)
        _assert_committed()
    output_dir = (
        _assert_output(output_dir)
        if enforce_output_guard
        else Path(output_dir).resolve()
    )
    model = model or config.DREAM_OLLAMA_MODEL
    actual_digest = (digest_resolver or _model_digest)(model)
    if model_digest is not None and model_digest != actual_digest:
        raise Loop2FV2Error("digest modello divergente")
    identity = _identity(model, model_digest or actual_digest)
    cases = load_cases()
    expected = {
        observation_key(case, replica)
        for case in cases
        for replica in range(repetitions(case))
    }
    manifest_path = output_dir / "run_manifest.json"
    checkpoint_path = output_dir / "checkpoint.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("identity") != identity:
            raise Loop2FV2Error("output-dir legato a identità diversa")
    else:
        _atomic_json(
            manifest_path,
            {
                "schema_version": 1,
                "identity": identity,
                "expected_observations": len(expected),
                "expected_calls": len(expected) * 2,
            },
        )
    checkpoint = {"schema_version": 1, "identity": identity, "records": {}}
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        _validate_checkpoint(checkpoint, identity, expected)
    legacy_fn = legacy_fn or classify_legacy
    structured_fn = structured_fn or classify_structured

    for case in cases:
        for replica in range(repetitions(case)):
            key = observation_key(case, replica)
            record = checkpoint["records"].setdefault(
                key,
                {
                    "observation_key": key,
                    "suite": case["suite"],
                    "case_id": case["case_id"],
                    "replica": replica,
                    "order": list(arm_order(key)),
                    "arms": {},
                },
            )
            for arm in record["order"]:
                if arm in record["arms"]:
                    continue
                started = time.monotonic()
                value = (
                    legacy_fn(case["memory_a"], case["memory_b"])
                    if arm == "legacy"
                    else structured_fn(case["memory_a"], case["memory_b"])
                )
                result = _normalize_result(value)
                record["arms"][arm] = {
                    **result,
                    "latency_s": round(time.monotonic() - started, 6),
                    "completed_at": time.time(),
                }
                _atomic_json(checkpoint_path, checkpoint)

    _validate_checkpoint(checkpoint, identity, expected)
    if set(checkpoint["records"]) != expected or any(
        set(record["arms"]) != set(ARMS)
        for record in checkpoint["records"].values()
    ):
        raise Loop2FV2Error("run incompleto")
    results_path = output_dir / "results.json"
    _atomic_json(results_path, checkpoint)
    return {
        "output": str(results_path),
        "observations": len(expected),
        "classifier_calls": len(expected) * 2,
    }


def _action(relation: str) -> str:
    return "supersede_a" if relation == "contradiction" else "keep_both"


def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _metrics(rows: list[dict], arm: str) -> dict:
    keep = [row for row in rows if row["expected_action"] == "keep_both"]
    supersede = [row for row in rows if row["expected_action"] == "supersede_a"]
    correct = sum(row[f"action_{arm}"] == row["expected_action"] for row in rows)
    false_sup = sum(row[f"action_{arm}"] == "supersede_a" for row in keep)
    true_sup = sum(row[f"action_{arm}"] == "supersede_a" for row in supersede)
    return {
        "correct": correct,
        "total": len(rows),
        "accuracy": _rate(correct, len(rows)),
        "false_supersessions": false_sup,
        "keep_cases": len(keep),
        "false_supersession_rate": _rate(false_sup, len(keep)),
        "true_supersessions": true_sup,
        "supersede_cases": len(supersede),
        "true_supersession_recall": _rate(true_sup, len(supersede)),
    }


def analyze(*, results_path: Path) -> dict:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    identity = results.get("identity") or {}
    if identity.get("protocol_sha256") != protocol_digest():
        raise Loop2FV2Error("results legati a protocollo diverso")
    cases = {
        f"{case['suite']}:{case['case_id']}": case for case in load_cases()
    }
    expected = {
        observation_key(case, replica)
        for case in cases.values()
        for replica in range(repetitions(case))
    }
    _validate_checkpoint(results, identity, expected)
    if set(results["records"]) != expected:
        raise Loop2FV2Error("copertura incompleta")

    rows = []
    stability = defaultdict(lambda: {arm: [] for arm in ARMS})
    contract = {arm: {"valid": 0, "total": 0} for arm in ARMS}
    for record in results["records"].values():
        case = cases[f"{record['suite']}:{record['case_id']}"]
        for arm in ARMS:
            result = record["arms"][arm]
            contract[arm]["total"] += 1
            contract[arm]["valid"] += int(result["contract_ok"])
            stability[f"{case['suite']}:{case['case_id']}"][arm].append(
                result["relation"]
            )
        if record["replica"] != 0:
            continue
        rows.append(
            {
                "suite": case["suite"],
                "case_id": case["case_id"],
                "stratum": case["stratum"],
                "expected_action": case["expected_action"],
                **{
                    f"relation_{arm}": record["arms"][arm]["relation"]
                    for arm in ARMS
                },
                **{
                    f"action_{arm}": _action(record["arms"][arm]["relation"])
                    for arm in ARMS
                },
                "structured_assessment": record["arms"]["structured"].get(
                    "assessment"
                ),
            }
        )
    for value in contract.values():
        value["rate"] = _rate(value["valid"], value["total"])

    suites = {}
    for suite in ("v1_open", "v2_challenge"):
        group = [row for row in rows if row["suite"] == suite]
        suites[suite] = {arm: _metrics(group, arm) for arm in ARMS}
    challenge = [row for row in rows if row["suite"] == "v2_challenge"]
    regression = [row for row in rows if row["suite"] == "v1_open"]

    improved = sum(
        row["action_legacy"] != row["expected_action"]
        and row["action_structured"] == row["expected_action"]
        for row in challenge
    )
    worsened = sum(
        row["action_legacy"] == row["expected_action"]
        and row["action_structured"] != row["expected_action"]
        for row in challenge
    )
    paired = _exact_mcnemar(improved, worsened)

    v1_struct = suites["v1_open"]["structured"]
    v2_struct = suites["v2_challenge"]["structured"]
    typed_false = sum(
        row["action_structured"] == "supersede_a"
        for row in challenge
        if row["stratum"] == "typed_coexistence"
    )
    ambiguous_false = sum(
        row["action_structured"] == "supersede_a"
        for row in challenge
        if row["stratum"] == "insufficient_identity"
    )
    regression_typed_false = sum(
        row["action_structured"] == "supersede_a"
        for row in regression
        if row["stratum"] == "target_vs_result_keep"
    )
    gates = {
        "v1_accuracy_at_least_34_of_36": v1_struct["correct"] >= 34,
        "v1_false_supersessions_at_most_1": (
            v1_struct["false_supersessions"] <= 1
        ),
        "v1_true_supersessions_at_least_11": (
            v1_struct["true_supersessions"] >= 11
        ),
        "v1_zero_target_result_supersessions": regression_typed_false == 0,
        "v2_accuracy_at_least_90pct": v2_struct["accuracy"] >= 0.90,
        "v2_false_supersession_at_most_5pct": (
            v2_struct["false_supersession_rate"] <= 0.05
        ),
        "v2_true_recall_at_least_80pct": (
            v2_struct["true_supersession_recall"] >= 0.80
        ),
        "v2_zero_typed_supersessions": typed_false == 0,
        "v2_zero_ambiguous_supersessions": ambiguous_false == 0,
        "structured_contract_at_least_95pct": contract["structured"]["rate"] >= 0.95,
        "v2_not_worse_than_legacy": (
            v2_struct["correct"] >= suites["v2_challenge"]["legacy"]["correct"]
        ),
    }
    verdict = "GO_DEV" if all(gates.values()) else "NO_GO_DEV"

    strata = {}
    for stratum in sorted({row["stratum"] for row in challenge}):
        group = [row for row in challenge if row["stratum"] == stratum]
        strata[stratum] = {
            arm: _metrics(group, arm) for arm in ARMS
        }
    stability_report = {}
    for arm in ARMS:
        sentinels = [
            labels[arm]
            for key, labels in stability.items()
            if key.startswith("v2_challenge:") and len(labels[arm]) > 1
        ]
        stable = sum(len(set(labels)) == 1 for labels in sentinels)
        stability_report[arm] = {
            "stable": stable,
            "total": len(sentinels),
            "rate": _rate(stable, len(sentinels)),
        }

    return {
        "schema_version": 1,
        "identity": identity,
        "verdict": verdict,
        "gates": gates,
        "suites": suites,
        "paired_challenge": paired,
        "contract": contract,
        "stability": stability_report,
        "challenge_strata": strata,
        "case_rows": sorted(rows, key=lambda row: (row["suite"], row["case_id"])),
        "limitations": [
            "development set controllato, non campione casuale",
            "v1 aperto e usato soltanto come regressione",
            "challenge scritto conoscendo i failure mode ma congelato prima del codice",
            "un solo modello locale",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--output", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--model", default=None)
    run.add_argument("--model-digest", default=None)
    ana = sub.add_parser("analyze")
    ana.add_argument("--results", type=Path, required=True)
    ana.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "dry-run":
            report = dry_run()
            if args.output:
                _atomic_json(args.output, report)
        elif args.command == "run":
            report = execute(
                output_dir=args.output_dir,
                execute_models=args.execute,
                model=args.model,
                model_digest=args.model_digest,
            )
        else:
            report = analyze(results_path=args.results)
            _atomic_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except Loop2FV2Error as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
