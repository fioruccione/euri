"""Validazione controllata e appaiata dei Loop 2f e 2h.

Il banco non apre Redis e non costruisce un DreamEngine operativo. Invoca soltanto
i due classificatori di produzione, sugli stessi casi congelati:

* 2f: CONTRADDIZIONE / CONFRONTO / NESSUNA;
* 2h: SAME / RELATED / DIFFERENT / UNKNOWN.

Il braccio ``2f`` nasconde A solo su CONTRADDIZIONE. Il braccio ``2f+2h`` applica
la correzione realmente disponibile nel runtime: RELATED/DIFFERENT ripristinano A,
SAME mantiene la supersessione, UNKNOWN lascia l'arco nascosto e ritentabile.
L'esecuzione richiede ``--execute``, un worktree tracciato pulito e salva checkpoint
atomici sotto ``audit_output``. I gold non entrano mai nei prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import config
import core.self_observation as self_observation_module
from benchmarks.euri_memory.integrity import (
    IntegrityError,
    assert_worktree_clean,
    git_head,
    sha256_file,
)
from core.dream_engine import DreamEngine
from core.ollama_client import get_dream_client
from core.operational_context import load_operational_context
from core.self_observation import SelfObservation


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = REPO_ROOT / "audit_output"
DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "loop2fh_v1.json"
)
PROTOCOL_VERSION = "loop2fh-controlled-it-v1"
PRODUCTION_BASELINE_COMMIT = "5dc3554"

LABELS_2F = frozenset({"contradiction", "comparison", "none"})
LABELS_2H = frozenset({"same", "related", "different", "unknown"})
PRIMARY_ACTIONS = frozenset({"supersede_a", "keep_both"})

THRESHOLDS = {
    "loop2f": {
        "go_accuracy_min": 0.80,
        "go_false_supersession_rate_max": 0.10,
        "go_true_supersession_recall_min": 0.80,
        "no_go_false_supersession_rate_above": 0.20,
        "no_go_true_supersession_recall_below": 0.60,
    },
    "loop2h": {
        "min_cross_entity_opportunities": 2,
        "go_cross_entity_correction_rate_min": 0.50,
        "go_true_supersession_damage_max": 0,
        "go_relation_accuracy_min": 0.80,
        "go_triggered_unknown_rate_max": 0.20,
        "go_stability_rate_min": 0.90,
    },
}


class Loop2FHError(RuntimeError):
    pass


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _assert_output_under_audit(path: Path) -> Path:
    resolved = path.resolve()
    root = AUDIT_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise Loop2FHError(
            f"output-dir deve stare sotto {root}, trovato {resolved}"
        ) from exc
    if resolved == root:
        raise Loop2FHError("output-dir non può coincidere con audit_output")
    return resolved


def _assert_protocol_committed(fixture_path: Path) -> None:
    """Il run reale parte soltanto da harness e fixture presenti in HEAD."""

    required = (
        Path(__file__).resolve(),
        fixture_path.resolve(),
        REPO_ROOT / "docs" / "EURI_LOOP2F_LOOP2H_PREREGISTRATION.md",
    )
    for path in required:
        try:
            relative = path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise Loop2FHError(f"file di protocollo fuori repo: {path}") from exc
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(relative)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise Loop2FHError(
                f"protocollo non committato in HEAD: {relative}"
            )


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise Loop2FHError("fixture con schema_version non supportata")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise Loop2FHError("fixture senza casi")

    seen: set[str] = set()
    for index, case in enumerate(cases):
        case_id = str(case.get("case_id") or "")
        if not case_id or case_id in seen:
            raise Loop2FHError(f"case_id assente o duplicato alla riga {index}")
        seen.add(case_id)
        if not str(case.get("memory_a") or "").strip():
            raise Loop2FHError(f"{case_id}: memory_a vuota")
        if not str(case.get("memory_b") or "").strip():
            raise Loop2FHError(f"{case_id}: memory_b vuota")
        expected_action = case.get("expected_action")
        if expected_action is not None and expected_action not in PRIMARY_ACTIONS:
            raise Loop2FHError(
                f"{case_id}: expected_action non valida {expected_action!r}"
            )
        relation = case.get("expected_2h_relation")
        if relation not in LABELS_2H:
            raise Loop2FHError(
                f"{case_id}: expected_2h_relation non valida {relation!r}"
            )
        if (
            case.get("stratum") == "ambiguous_identity_diagnostic"
            and expected_action is not None
        ):
            raise Loop2FHError(f"{case_id}: un caso ambiguo non deve avere gold azione")
    return payload


def repetitions(case: dict) -> int:
    return 3 if bool(case.get("stability_probe")) else 1


def observation_key(case_id: str, replica: int) -> str:
    return f"{case_id}__r{replica}"


def counterbalanced_order(case_id: str, replica: int) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{PROTOCOL_VERSION}:{case_id}:{replica}".encode("utf-8")
    ).digest()
    return ("2f", "2h") if digest[0] % 2 == 0 else ("2h", "2f")


def source_hashes() -> dict[str, str]:
    return {
        "loop2f_classifier": _sha_text(
            inspect.getsource(DreamEngine._llm_classify_pair)
        ),
        "loop2f_ollama_wrapper": _sha_text(
            inspect.getsource(DreamEngine._ollama_chat)
        ),
        "loop2h_classifier": _sha_text(
            inspect.getsource(SelfObservation._classify_pair_relation)
        ),
    }


def protocol_payload(fixture_path: Path = DEFAULT_FIXTURE) -> dict:
    fixture = load_fixture(fixture_path)
    cases = fixture["cases"]
    observations = sum(repetitions(case) for case in cases)
    return {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "production_baseline_commit": PRODUCTION_BASELINE_COMMIT,
        "fixture": {
            "fixture_id": fixture["fixture_id"],
            "sha256": sha256_file(Path(fixture_path)),
            "cases": len(cases),
            "primary_cases": sum(
                case.get("expected_action") in PRIMARY_ACTIONS for case in cases
            ),
            "diagnostic_ambiguous_cases": sum(
                case.get("expected_action") is None for case in cases
            ),
            "stability_cases": sum(
                bool(case.get("stability_probe")) for case in cases
            ),
        },
        "execution": {
            "observations": observations,
            "classifier_calls": observations * 2,
            "temperature": 0,
            "early_stop_on_metrics": False,
            "redis_access": False,
            "order": "counterbalanced_by_case_and_replica",
        },
        "source_sha256": source_hashes(),
        "operational_context_sha256": _sha_text(load_operational_context() or ""),
        "thresholds": THRESHOLDS,
    }


def protocol_digest(fixture_path: Path = DEFAULT_FIXTURE) -> str:
    return _sha_text(_canonical(protocol_payload(fixture_path)))


def dry_run(fixture_path: Path = DEFAULT_FIXTURE) -> dict:
    protocol = protocol_payload(fixture_path)
    fixture = load_fixture(fixture_path)
    strata = Counter(case["stratum"] for case in fixture["cases"])
    orders = Counter()
    for case in fixture["cases"]:
        for replica in range(repetitions(case)):
            orders["→".join(counterbalanced_order(case["case_id"], replica))] += 1
    return {
        "protocol": protocol,
        "protocol_sha256": _sha_text(_canonical(protocol)),
        "strata": dict(sorted(strata.items())),
        "orders": dict(sorted(orders.items())),
        "invariants": {
            "unique_case_ids": len(
                {case["case_id"] for case in fixture["cases"]}
            )
            == len(fixture["cases"]),
            "gold_not_passed_to_classifier_signature": True,
            "no_redis_connection_or_mutation": True,
            "all_primary_actions_valid": all(
                case.get("expected_action") in PRIMARY_ACTIONS
                for case in fixture["cases"]
                if case.get("expected_action") is not None
            ),
        },
    }


def _model_digest(model: str) -> str:
    response = get_dream_client().list()
    models = getattr(response, "models", None)
    if models is None and isinstance(response, dict):
        models = response.get("models")
    for item in models or []:
        name = getattr(item, "model", None)
        digest = getattr(item, "digest", None)
        if isinstance(item, dict):
            name = name or item.get("model") or item.get("name")
            digest = digest or item.get("digest")
        if str(name or "") == model and str(digest or ""):
            return str(digest)
    raise Loop2FHError(f"digest Ollama non trovato per {model!r}")


def production_classify_2f(memory_a: str, memory_b: str) -> str:
    engine = DreamEngine.__new__(DreamEngine)
    captured: dict[str, Any] = {}

    def audited_chat(**kwargs):
        try:
            response = DreamEngine._ollama_chat(engine, **kwargs)
            captured["raw"] = response.message.content or ""
            return response
        except Exception as exc:
            captured["error"] = repr(exc)
            raise

    engine._ollama_chat = audited_chat
    label = DreamEngine._llm_classify_pair(engine, memory_a, memory_b)
    if captured.get("error"):
        raise Loop2FHError(
            f"2f ha mascherato un errore modello come NESSUNA: {captured['error']}"
        )
    raw = str(captured.get("raw") or "").strip().upper()
    if not raw or not any(
        token in raw for token in ("CONTRADD", "CONFRONT", "NESSUNA")
    ):
        raise Loop2FHError(
            f"2f output fuori contratto mascherato come {label!r}"
        )
    return label


def production_classify_2h(memory_a: str, memory_b: str) -> str:
    captured: dict[str, Any] = {}
    original_get_client = self_observation_module.get_dream_client

    class AuditedClient:
        def __init__(self, client):
            self._client = client

        def chat(self, **kwargs):
            try:
                response = self._client.chat(**kwargs)
                captured["raw"] = response.message.content or ""
                return response
            except Exception as exc:
                captured["error"] = repr(exc)
                raise

    def audited_get_client(timeout_s=None):
        return AuditedClient(original_get_client(timeout_s))

    observation = SelfObservation(None, None)
    self_observation_module.get_dream_client = audited_get_client
    try:
        relation, _note = observation._classify_pair_relation(
            {
                "pair_key": "benchmark",
                "loser": {"content": memory_a},
                "winner": {"content": memory_b},
            }
        )
    finally:
        self_observation_module.get_dream_client = original_get_client
    if captured.get("error"):
        raise Loop2FHError(
            f"2h ha mascherato un errore modello come UNKNOWN: {captured['error']}"
        )
    raw = str(captured.get("raw") or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise Loop2FHError(
            f"2h output fuori contratto mascherato come {relation!r}"
        )
    try:
        raw_relation = str(
            json.loads(raw[start : end + 1]).get("relation") or ""
        ).strip().lower()
    except (json.JSONDecodeError, AttributeError) as exc:
        raise Loop2FHError("2h JSON fuori contratto") from exc
    if raw_relation not in LABELS_2H:
        raise Loop2FHError(
            f"2h relazione raw fuori contratto {raw_relation!r}"
        )
    return relation


def _identity(fixture_path: Path, *, model: str, model_digest: str) -> dict:
    head = git_head(REPO_ROOT)
    if not head:
        raise Loop2FHError("HEAD Git non disponibile")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "protocol_sha256": protocol_digest(fixture_path),
        "fixture_sha256": sha256_file(fixture_path),
        "git_commit": head,
        "model": model,
        "model_digest": model_digest,
        "source_sha256": source_hashes(),
        "operational_context_sha256": _sha_text(load_operational_context() or ""),
    }


def _validate_checkpoint(checkpoint: dict, identity: dict, expected_keys: set[str]) -> None:
    if checkpoint.get("identity") != identity:
        raise Loop2FHError("checkpoint con identità divergente: resume rifiutato")
    records = checkpoint.get("records")
    if not isinstance(records, dict):
        raise Loop2FHError("checkpoint senza records")
    if not set(records).issubset(expected_keys):
        raise Loop2FHError("checkpoint contiene osservazioni estranee")
    for key, record in records.items():
        if record.get("observation_key") != key:
            raise Loop2FHError(f"{key}: observation_key divergente")
        labels = record.get("labels") or {}
        if not set(labels).issubset({"2f", "2h"}):
            raise Loop2FHError(f"{key}: stadi estranei")
        if "2f" in labels and labels["2f"].get("label") not in LABELS_2F:
            raise Loop2FHError(f"{key}: label 2f invalida")
        if "2h" in labels and labels["2h"].get("label") not in LABELS_2H:
            raise Loop2FHError(f"{key}: label 2h invalida")


def execute(
    *,
    fixture_path: Path = DEFAULT_FIXTURE,
    output_dir: Path,
    execute_models: bool = False,
    classify_2f: Callable[[str, str], str] | None = None,
    classify_2h: Callable[[str, str], str] | None = None,
    model: str | None = None,
    model_digest: str | None = None,
    enforce_git: bool = True,
    enforce_output_guard: bool = True,
    model_digest_resolver: Callable[[str], str] | None = None,
) -> dict:
    if not execute_models:
        raise Loop2FHError("esecuzione LLM bloccata: passare execute_models=True")
    fixture_path = Path(fixture_path).resolve()
    fixture = load_fixture(fixture_path)
    output_dir = (
        _assert_output_under_audit(output_dir)
        if enforce_output_guard
        else Path(output_dir).resolve()
    )

    if enforce_git:
        assert_worktree_clean(REPO_ROOT)
        _assert_protocol_committed(fixture_path)
    model = model or config.DREAM_OLLAMA_MODEL
    uses_production_model = classify_2f is None or classify_2h is None
    if uses_production_model and model != config.DREAM_OLLAMA_MODEL:
        raise Loop2FHError(
            f"il classificatore di produzione usa {config.DREAM_OLLAMA_MODEL!r}; "
            f"non può sigillare il modello diverso {model!r}"
        )
    actual_digest = (model_digest_resolver or _model_digest)(model)
    if model_digest is not None and model_digest != actual_digest:
        raise Loop2FHError("model_digest richiesto diverso dal modello installato")
    identity = _identity(
        fixture_path, model=model, model_digest=model_digest or actual_digest
    )
    expected_keys = {
        observation_key(case["case_id"], replica)
        for case in fixture["cases"]
        for replica in range(repetitions(case))
    }

    checkpoint_path = output_dir / "checkpoint.json"
    manifest_path = output_dir / "run_manifest.json"
    manifest = {
        "schema_version": 1,
        "identity": identity,
        "expected_observations": len(expected_keys),
        "expected_classifier_calls": len(expected_keys) * 2,
        "created_at": time.time(),
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("identity") != identity:
            raise Loop2FHError("output-dir legato a un'altra esecuzione")
    else:
        _atomic_json(manifest_path, manifest)

    checkpoint = {"schema_version": 1, "identity": identity, "records": {}}
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        _validate_checkpoint(checkpoint, identity, expected_keys)

    classify_2f = classify_2f or production_classify_2f
    classify_2h = classify_2h or production_classify_2h
    by_case = {case["case_id"]: case for case in fixture["cases"]}

    for case in fixture["cases"]:
        case_id = case["case_id"]
        for replica in range(repetitions(case)):
            key = observation_key(case_id, replica)
            record = checkpoint["records"].setdefault(
                key,
                {
                    "observation_key": key,
                    "case_id": case_id,
                    "replica": replica,
                    "order": list(counterbalanced_order(case_id, replica)),
                    "labels": {},
                },
            )
            if record["case_id"] not in by_case:
                raise Loop2FHError(f"{key}: case_id non previsto")
            for stage in record["order"]:
                if stage in record["labels"]:
                    continue
                started = time.monotonic()
                if stage == "2f":
                    label = classify_2f(case["memory_a"], case["memory_b"])
                    allowed = LABELS_2F
                else:
                    label = classify_2h(case["memory_a"], case["memory_b"])
                    allowed = LABELS_2H
                elapsed = time.monotonic() - started
                if label not in allowed:
                    raise Loop2FHError(f"{key}: {stage} ha restituito {label!r}")
                record["labels"][stage] = {
                    "label": label,
                    "latency_s": round(elapsed, 6),
                    "completed_at": time.time(),
                }
                _atomic_json(checkpoint_path, checkpoint)

    _validate_checkpoint(checkpoint, identity, expected_keys)
    incomplete = [
        key
        for key, record in checkpoint["records"].items()
        if set(record.get("labels") or {}) != {"2f", "2h"}
    ]
    if set(checkpoint["records"]) != expected_keys or incomplete:
        raise Loop2FHError(
            f"esecuzione incompleta: records={len(checkpoint['records'])}/"
            f"{len(expected_keys)}, incompleti={len(incomplete)}"
        )
    final_path = output_dir / "results.json"
    _atomic_json(final_path, checkpoint)
    return {
        "output": str(final_path),
        "observations": len(expected_keys),
        "classifier_calls": len(expected_keys) * 2,
        "identity": identity,
    }


def _action_2f(label_2f: str) -> str:
    return "supersede_a" if label_2f == "contradiction" else "keep_both"


def _action_2fh(label_2f: str, label_2h: str) -> str:
    if label_2f != "contradiction":
        return "keep_both"
    if label_2h in {"related", "different"}:
        return "keep_both"
    # SAME mantiene l'arco; UNKNOWN nel runtime lo lascia nascosto e ritentabile.
    return "supersede_a"


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _exact_mcnemar(improved: int, worsened: int) -> dict:
    discordant = improved + worsened
    if not discordant:
        return {"improved": 0, "worsened": 0, "discordant": 0, "p_two_sided": 1.0}
    tail = sum(
        math.comb(discordant, k) for k in range(min(improved, worsened) + 1)
    ) / (2**discordant)
    return {
        "improved": improved,
        "worsened": worsened,
        "discordant": discordant,
        "p_two_sided": min(1.0, 2 * tail),
    }


def _confusion(rows: list[dict], field: str, gold_field: str) -> dict:
    counter = Counter((row[gold_field], row[field]) for row in rows)
    return {
        f"{gold}→{predicted}": count
        for (gold, predicted), count in sorted(counter.items())
    }


def analyze(
    *,
    results_path: Path,
    fixture_path: Path = DEFAULT_FIXTURE,
) -> dict:
    fixture_path = Path(fixture_path).resolve()
    fixture = load_fixture(fixture_path)
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))
    identity = results.get("identity") or {}
    if identity.get("fixture_sha256") != sha256_file(fixture_path):
        raise Loop2FHError("results legati a una fixture diversa")
    if identity.get("protocol_sha256") != protocol_digest(fixture_path):
        raise Loop2FHError("results legati a un protocollo diverso")

    cases = {case["case_id"]: case for case in fixture["cases"]}
    expected_keys = {
        observation_key(case["case_id"], replica)
        for case in fixture["cases"]
        for replica in range(repetitions(case))
    }
    _validate_checkpoint(results, identity, expected_keys)
    if set(results["records"]) != expected_keys:
        raise Loop2FHError("copertura risultati incompleta")

    rows: list[dict] = []
    stability_labels: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"2f": [], "2h": [], "2fh_action": []}
    )
    for key, record in sorted(results["records"].items()):
        case = cases[record["case_id"]]
        label_2f = record["labels"]["2f"]["label"]
        label_2h = record["labels"]["2h"]["label"]
        action_2f = _action_2f(label_2f)
        action_2fh = _action_2fh(label_2f, label_2h)
        stability_labels[case["case_id"]]["2f"].append(label_2f)
        stability_labels[case["case_id"]]["2h"].append(label_2h)
        stability_labels[case["case_id"]]["2fh_action"].append(action_2fh)
        if record["replica"] != 0:
            continue
        rows.append(
            {
                "case_id": case["case_id"],
                "stratum": case["stratum"],
                "expected_action": case.get("expected_action"),
                "expected_2h_relation": case["expected_2h_relation"],
                "label_2f": label_2f,
                "label_2h": label_2h,
                "action_2f": action_2f,
                "action_2fh": action_2fh,
            }
        )

    primary = [row for row in rows if row["expected_action"] in PRIMARY_ACTIONS]
    keep = [row for row in primary if row["expected_action"] == "keep_both"]
    supersede = [
        row for row in primary if row["expected_action"] == "supersede_a"
    ]
    cross_entity = [
        row for row in primary if row["stratum"] == "distinct_entities_keep"
    ]

    def arm_metrics(action_field: str) -> dict:
        correct = sum(row[action_field] == row["expected_action"] for row in primary)
        false_sup = sum(row[action_field] == "supersede_a" for row in keep)
        true_sup = sum(row[action_field] == "supersede_a" for row in supersede)
        return {
            "correct": correct,
            "total": len(primary),
            "accuracy": _rate(correct, len(primary)),
            "false_supersessions": false_sup,
            "keep_cases": len(keep),
            "false_supersession_rate": _rate(false_sup, len(keep)),
            "true_supersessions_retained": true_sup,
            "supersede_cases": len(supersede),
            "true_supersession_recall": _rate(true_sup, len(supersede)),
        }

    metrics_2f = arm_metrics("action_2f")
    metrics_2fh = arm_metrics("action_2fh")
    opportunities = [
        row
        for row in cross_entity
        if row["action_2f"] == "supersede_a"
    ]
    corrected = [
        row for row in opportunities if row["action_2fh"] == "keep_both"
    ]
    damaged_true = [
        row
        for row in supersede
        if row["action_2f"] == "supersede_a"
        and row["action_2fh"] == "keep_both"
    ]
    triggered = [row for row in rows if row["label_2f"] == "contradiction"]
    unknown_triggered = [row for row in triggered if row["label_2h"] == "unknown"]
    relation_correct = sum(
        row["label_2h"] == row["expected_2h_relation"] for row in rows
    )

    improved = sum(
        row["action_2f"] != row["expected_action"]
        and row["action_2fh"] == row["expected_action"]
        for row in primary
    )
    worsened = sum(
        row["action_2f"] == row["expected_action"]
        and row["action_2fh"] != row["expected_action"]
        for row in primary
    )

    stability_cases = {
        case_id: labels
        for case_id, labels in stability_labels.items()
        if len(labels["2f"]) > 1
    }
    stability_by_stage = {}
    for stage in ("2f", "2h", "2fh_action"):
        stable = sum(len(set(labels[stage])) == 1 for labels in stability_cases.values())
        stability_by_stage[stage] = {
            "stable_cases": stable,
            "total": len(stability_cases),
            "rate": _rate(stable, len(stability_cases)),
        }

    t2f = THRESHOLDS["loop2f"]
    if (
        metrics_2f["accuracy"] >= t2f["go_accuracy_min"]
        and metrics_2f["false_supersession_rate"]
        <= t2f["go_false_supersession_rate_max"]
        and metrics_2f["true_supersession_recall"]
        >= t2f["go_true_supersession_recall_min"]
    ):
        verdict_2f = "GO_DEV"
    elif (
        metrics_2f["false_supersession_rate"]
        > t2f["no_go_false_supersession_rate_above"]
        or metrics_2f["true_supersession_recall"]
        < t2f["no_go_true_supersession_recall_below"]
    ):
        verdict_2f = "NO_GO_DEV"
    else:
        verdict_2f = "INCONCLUSIVE_DEV"

    t2h = THRESHOLDS["loop2h"]
    correction_rate = _rate(len(corrected), len(opportunities))
    relation_accuracy = _rate(relation_correct, len(rows))
    unknown_rate = _rate(len(unknown_triggered), len(triggered))
    final_stability = stability_by_stage["2fh_action"]["rate"]
    if len(opportunities) < t2h["min_cross_entity_opportunities"]:
        verdict_2h = "INCONCLUSIVE_DEV_NO_OPPORTUNITY"
    elif (
        correction_rate >= t2h["go_cross_entity_correction_rate_min"]
        and len(damaged_true) <= t2h["go_true_supersession_damage_max"]
        and relation_accuracy >= t2h["go_relation_accuracy_min"]
        and unknown_rate <= t2h["go_triggered_unknown_rate_max"]
        and final_stability >= t2h["go_stability_rate_min"]
    ):
        verdict_2h = "GO_DEV"
    elif (
        len(damaged_true) > t2h["go_true_supersession_damage_max"]
        or len(corrected) == 0
        or metrics_2fh["false_supersessions"]
        >= metrics_2f["false_supersessions"]
    ):
        verdict_2h = "NO_GO_DEV"
    else:
        verdict_2h = "INCONCLUSIVE_DEV"

    strata = {}
    for stratum in sorted({row["stratum"] for row in rows}):
        group = [row for row in rows if row["stratum"] == stratum]
        labeled = [row for row in group if row["expected_action"] in PRIMARY_ACTIONS]
        strata[stratum] = {
            "n": len(group),
            "2f_correct": sum(
                row["action_2f"] == row["expected_action"] for row in labeled
            ),
            "2fh_correct": sum(
                row["action_2fh"] == row["expected_action"] for row in labeled
            ),
            "2h_relation_correct": sum(
                row["label_2h"] == row["expected_2h_relation"] for row in group
            ),
        }

    latency = {}
    for stage in ("2f", "2h"):
        values = [
            float(record["labels"][stage]["latency_s"])
            for record in results["records"].values()
        ]
        latency[stage] = {
            "calls": len(values),
            "total_s": sum(values),
            "mean_s": sum(values) / len(values),
            "max_s": max(values),
        }

    return {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "identity": identity,
        "verdict": {"loop2f": verdict_2f, "loop2h_incremental": verdict_2h},
        "arms": {"2f": metrics_2f, "2f_plus_2h": metrics_2fh},
        "loop2h_incremental": {
            "cross_entity_opportunities": len(opportunities),
            "cross_entity_corrected": len(corrected),
            "cross_entity_correction_rate": correction_rate,
            "true_supersessions_damaged": len(damaged_true),
            "triggered_cases": len(triggered),
            "triggered_unknown": len(unknown_triggered),
            "triggered_unknown_rate": unknown_rate,
            "relation_accuracy": relation_accuracy,
        },
        "paired": _exact_mcnemar(improved, worsened),
        "stability": stability_by_stage,
        "confusion": {
            "2f_action": _confusion(primary, "action_2f", "expected_action"),
            "2fh_action": _confusion(primary, "action_2fh", "expected_action"),
            "2h_relation": _confusion(
                rows, "label_2h", "expected_2h_relation"
            ),
        },
        "strata": strata,
        "latency": latency,
        "case_rows": rows,
        "limitations": [
            "development set controllato e scritto conoscendo l'architettura",
            "un solo modello locale; le repliche di stabilità coprono solo i casi sentinella",
            "la qualità narrativa di 2h non è valutata in questa fase",
            "nessuna inferenza statistica sulla distribuzione delle memorie personali",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    dry.add_argument("--output", type=Path)

    run = sub.add_parser("run")
    run.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--model", default=None)
    run.add_argument("--model-digest", default=None)

    ana = sub.add_parser("analyze")
    ana.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ana.add_argument("--results", type=Path, required=True)
    ana.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "dry-run":
            report = dry_run(args.fixture)
            if args.output:
                _atomic_json(args.output, report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "run":
            report = execute(
                fixture_path=args.fixture,
                output_dir=args.output_dir,
                execute_models=args.execute,
                model=args.model,
                model_digest=args.model_digest,
            )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        report = analyze(results_path=args.results, fixture_path=args.fixture)
        _atomic_json(args.output, report)
        print(json.dumps(report["verdict"], sort_keys=True))
        return 0
    except (Loop2FHError, IntegrityError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
