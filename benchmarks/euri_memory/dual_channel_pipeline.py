"""Pipeline eseguibile della validazione dual-channel (census).

Riusa integralmente le guardie di integrità dell'held-out: corpus SHA, commit +
worktree, manifest cieco firmato, localizzazione italiana sigillata, legame
crittografico report↔manifest, checkpoint/resume per coppia, cap SOLO tecnici.

Non esegue nulla in questo turno: la run reale (``run_all`` senza dry-run) avvia
il worker stadiato ``dual_channel_worker`` in un runtime isolato. Nessuna modifica
alla produzione.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.analysis import cluster_bootstrap_ci, mcnemar_exact
from benchmarks.euri_memory.dual_channel import POLICY_ID
from benchmarks.euri_memory.dual_channel_worker import build_census, forecast
import random

from benchmarks.euri_memory.heldout import _git_commit, manifest_digest, verify_manifest
from benchmarks.euri_memory.heldout_localization import (
    selection_localization_slice,
    verify_localization_seal,
    verify_selected_localization,
)
from benchmarks.euri_memory.integrity import (
    IntegrityError,
    assert_corpus_matches,
    assert_head_matches_manifest,
    assert_same_identity,
    assert_worktree_clean,
    canonical_selection_bytes,
    canonical_selection_payload,
    run_identity,
    selection_sha256,
    sha256_file,
)


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
_SPEAKER_MAPPING = {"speaker_a": "owner_user", "speaker_b": "assistant"}
# Cap SOLO tecnici (runaway guard): tempo/workstation non sono un vincolo.
_PER_PAIR_TIMEOUT = 86_400  # 24h per coppia, arresto tecnico
_ARMS = ("rag_only", "dual_channel")


class DualPipelineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Manifest census cieco
# --------------------------------------------------------------------------- #
def _dual_replicas(seed: int, replicas: int) -> list[dict]:
    """answer_seed e braccio iniziale derivati dal seed; repliche alternate.

    Il seed NON seleziona domande (il census resta identico): fissa solo, in modo
    riproducibile, gli answer_seed e quale braccio parte per primo.
    """

    order_rng = random.Random(f"{seed}:generation-order")
    seed_rng = random.Random(f"{seed}:answer-seed")
    base = ["rag_only", "dual_channel"]
    start_reversed = order_rng.random() < 0.5
    out = []
    for index in range(replicas):
        reversed_here = start_reversed ^ (index % 2 == 1)
        order = list(reversed(base)) if reversed_here else list(base)
        out.append(
            {
                "replica_index": index,
                "generation_order": order,
                "answer_seed": seed_rng.randrange(1, 2**31 - 1),
            }
        )
    return out


def build_census_manifest(
    *,
    seed: int,
    corpus_path: Path = DEFAULT_SOURCE,
    replicas: int = 2,
    git_commit: str | None = None,
) -> dict:
    commit = git_commit if git_commit is not None else _git_commit()
    if not commit:
        raise DualPipelineError("manifest senza git commit: rifiuto (HEAD non disponibile)")
    census = build_census(corpus_path)
    cases = {c.sample_id: c for c in LoCoMoAdapter().load(Path(corpus_path))}
    conversations = []
    for conv in census["conversations"]:
        case = cases[conv["sample_id"]]
        conversations.append(
            {
                "sample_id": conv["sample_id"],
                "sessions": conv["sessions"],
                "session_ids": [s.session_id for s in case.sessions],
                "question_ids": list(conv["eligible_question_ids"]),
                "question_count": conv["eligible_count"],
                "answerable": conv["answerable"],
                "adversarial": conv["adversarial"],
                "excluded": conv["excluded"],
            }
        )
    replica_list = _dual_replicas(seed, replicas)
    manifest = {
        "schema_version": 1,
        "experiment": "euri_dual_channel_validation",
        "experiment_version": "v1",
        "stage": "selection",
        "policy_id": POLICY_ID,
        "seed": seed,
        "budget": {"name": "census", "max_seconds_per_pair": _PER_PAIR_TIMEOUT},
        "selection_mode": "census_all_eligible",
        "git_commit": commit,
        "corpus": {"path": str(Path(corpus_path).resolve()), "sha256": sha256_file(corpus_path)},
        "universe": census["universe"],
        "independent_unit": "conversation",
        "n_independent": len(conversations),
        "conversations": conversations,
        "replicas": replica_list,
        "arms": list(_ARMS),
        "scorer": "locomo_reduced_deterministic_v1_not_official",
        "language_target": "it",
        "census_totals": census["totals"],
        "notes": [
            "Manifest cieco: solo ID/conteggi, nessun testo di domanda o gold.",
            "Census: tutte le domande eleggibili delle 5 untouched, avversariali incluse.",
        ],
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def build_dual_final_manifest(
    census_manifest: dict, localization: dict, *, corpus_path: Path = DEFAULT_SOURCE
) -> dict:
    verify_selected_localization(localization, corpus_path, census_manifest)
    final = dict(census_manifest)
    final.pop("manifest_sha256", None)
    final.update(
        {
            "stage": "final",
            "language": "it",
            "selection_manifest_sha256": census_manifest["manifest_sha256"],
            "translation_protocol": localization["translation_protocol"],
            "localization": {
                "localization_id": localization["localization_id"],
                "localization_sha256": localization["localization_sha256"],
                "language": localization["language"],
                "selected_sample_ids": localization["selected_sample_ids"],
            },
        }
    )
    final["manifest_sha256"] = manifest_digest(final)
    return final


# --------------------------------------------------------------------------- #
# Legame report↔manifest (arm rag_only/dual_channel)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExpectedDualPair:
    sample_id: str
    replica_index: int
    key: str
    question_ids: tuple[str, ...]
    answer_seed: int
    generation_order: tuple[str, ...]
    selection_sha256: str


def expected_dual_pairs(manifest: dict) -> dict[str, ExpectedDualPair]:
    pairs = {}
    for conv in manifest["conversations"]:
        payload = canonical_selection_payload(
            conv["sample_id"], conv["session_ids"], conv["question_ids"]
        )
        sel_sha = selection_sha256(payload)
        for rep in manifest["replicas"]:
            idx = int(rep["replica_index"])
            key = f"{conv['sample_id']}__r{idx}"
            pairs[key] = ExpectedDualPair(
                sample_id=conv["sample_id"],
                replica_index=idx,
                key=key,
                question_ids=tuple(conv["question_ids"]),
                answer_seed=int(rep["answer_seed"]),
                generation_order=tuple(rep["generation_order"]),
                selection_sha256=sel_sha,
            )
    return pairs


def validate_dual_report(report: dict, manifest: dict, expected: ExpectedDualPair) -> list[str]:
    problems: list[str] = []
    dataset = report.get("dataset", {})
    run = report.get("run", {})
    selection = report.get("selection", {})
    git = report.get("git", {})
    binding = report.get("binding", {})
    models = report.get("models", {})
    seq = tuple(expected.question_ids)

    if dataset.get("sample_id") != expected.sample_id:
        problems.append("sample_id diverso")
    if run.get("run_label") != expected.key:
        problems.append("run_label diverso")
    if int(run.get("answer_seed", -1)) != expected.answer_seed:
        problems.append("answer_seed diverso")
    if int(models.get("answer_seed", -1)) != expected.answer_seed:
        problems.append("models.answer_seed diverso")
    if tuple(run.get("generation_order") or ()) != expected.generation_order:
        problems.append("generation_order diverso")
    if dataset.get("source_sha256") != manifest["corpus"]["sha256"]:
        problems.append("source_sha256 diverso dal corpus")
    if manifest.get("git_commit") and git.get("commit") != manifest["git_commit"]:
        problems.append("git commit diverso")
    if git.get("worktree_tracked_dirty") is not False:
        problems.append("worktree tracciata non pulita")
    if tuple(selection.get("question_ids") or ()) != seq:
        problems.append("question_ids diversi come sequenza")
    if selection.get("selection_sha256") != expected.selection_sha256:
        problems.append("selection_sha256 diverso")
    if binding.get("manifest_sha256") != manifest.get("manifest_sha256"):
        problems.append("binding.manifest_sha256 diverso")
    if binding.get("selection_manifest_sha256") != manifest.get("selection_manifest_sha256"):
        problems.append("binding.selection_manifest_sha256 diverso")
    if binding.get("localization_sha256") != manifest.get("localization", {}).get("localization_sha256"):
        problems.append("binding.localization_sha256 diverso")
    if binding.get("language") != "it":
        problems.append("lingua non italiana")

    # policy, localization_id, scorer, gold_boundary
    if report.get("policy_id") != manifest.get("policy_id"):
        problems.append("policy_id diverso dal manifest")
    if binding.get("localization_id") != manifest.get("localization", {}).get("localization_id"):
        problems.append("localization_id diverso dal manifest")
    gold_boundary = report.get("gold_boundary", {})
    if not gold_boundary or not all(bool(v) for v in gold_boundary.values()):
        problems.append("gold_boundary non tutto true")

    # base_nodes/locator_nodes: chiavi esattamente le domande attese
    for field in ("base_nodes_by_question", "locator_nodes_by_question"):
        if set(report.get(field, {}) or {}) != set(seq):
            problems.append(f"{field}: chiavi diverse dalle domande attese")

    names = sorted(a.get("arm") for a in report.get("arms", []))
    if names != ["dual_channel", "rag_only"]:
        problems.append("bracci non esattamente rag_only+dual_channel")
        return problems
    by = {a["arm"]: a for a in report["arms"]}
    rag_base_sha: dict[str, str] = {}
    for arm in _ARMS:
        arm_report = by[arm]
        scoring = arm_report.get("scoring")
        if not scoring:
            problems.append(f"scoring mancante per {arm}")
            continue
        if scoring.get("name") != manifest.get("scorer"):
            problems.append(f"scorer {arm} diverso dall'atteso")
        # risultati e item ESATTAMENTE nella sequenza delle domande, senza duplicati
        result_ids = tuple(r.get("question_id") for r in arm_report.get("results", []))
        item_ids = tuple(i.get("question_id") for i in scoring.get("items", []))
        if result_ids != seq:
            problems.append(f"risultati {arm} non nella sequenza delle domande")
        if item_ids != seq:
            problems.append(f"item scoring {arm} non nella sequenza delle domande")
        if arm == "rag_only":
            for r in arm_report.get("results", []):
                rag_base_sha[r.get("question_id")] = (r.get("metadata") or {}).get("base_sha256")

    # composizione dual: policy_id corretto e base_sha256 == base del rag
    for r in by["dual_channel"].get("results", []):
        comp = (r.get("metadata") or {}).get("composition") or {}
        qid = r.get("question_id")
        if comp.get("policy_id") != POLICY_ID:
            problems.append(f"composition.policy_id errato per {qid}")
        if comp.get("base_sha256") != rag_base_sha.get(qid):
            problems.append(f"composition.base_sha256 != base rag per {qid}")
    return problems


# --------------------------------------------------------------------------- #
# Runner (stadiato via worker) + dry-run completo
# --------------------------------------------------------------------------- #
def _write_selection(conv: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{conv['sample_id']}.json"
    payload = canonical_selection_payload(conv["sample_id"], conv["session_ids"], conv["question_ids"])
    data = canonical_selection_bytes(payload)
    if path.exists():
        if path.read_bytes() != data:
            raise DualPipelineError(f"selezione preesistente {path} differente: fail-closed")
        return path
    path.write_bytes(data)
    return path


def _write_slice(localization: dict, conv: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{conv['sample_id']}.it.json"
    payload = selection_localization_slice(
        localization, conv["sample_id"], list(conv["question_ids"]), f"heldout-{conv['sample_id']}"
    )
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise DualPipelineError(f"slice italiana preesistente {path} differente: fail-closed")
        return path
    path.write_bytes(data)
    return path


def _plan(manifest: dict) -> list[dict]:
    plan = []
    for conv in manifest["conversations"]:
        for rep in manifest["replicas"]:
            plan.append(
                {
                    "key": f"{conv['sample_id']}__r{rep['replica_index']}",
                    "sample_id": conv["sample_id"],
                    "replica_index": rep["replica_index"],
                    "generation_order": rep["generation_order"],
                    "answer_seed": rep["answer_seed"],
                    "questions": conv["question_count"],
                }
            )
    return plan


def run_all(
    *,
    manifest_path: Path,
    localization_path: Path | None,
    output_dir: Path,
    corpus_path: Path = DEFAULT_SOURCE,
    dry_run: bool = False,
) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    verify_manifest(manifest)
    assert_corpus_matches(manifest, corpus_path)
    identity = run_identity(manifest)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = output_dir / "manifest.json"
    if existing.exists():
        prev = json.loads(existing.read_text(encoding="utf-8"))
        verify_manifest(prev)
        if prev.get("manifest_sha256") != manifest["manifest_sha256"] or run_identity(prev) != identity:
            raise DualPipelineError("output-dir con manifest diverso: fail-closed")
    else:
        shutil.copy2(manifest_path, existing)

    census = build_census(corpus_path)
    fc = forecast(census, replicas=len(manifest["replicas"]), source=corpus_path)
    (output_dir / "forecast.json").write_text(json.dumps(fc, indent=2, ensure_ascii=False), encoding="utf-8")

    plan = _plan(manifest)
    if dry_run:
        for conv in manifest["conversations"]:
            _write_selection(conv, output_dir / "selections")
        isolation_ok = _probe_isolation()
        result = {
            "mode": "dry_run_complete",
            "manifest_sha256": manifest["manifest_sha256"],
            "stage": manifest.get("stage"),
            "isolation_probe_ok": isolation_ok,
            "plan": plan,
            "forecast": fc,
            "census_totals": census["totals"],
        }
        (output_dir / "dry_run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    # --- Run reale (worker LLM in ambiente isolato) ---
    if manifest.get("stage") != "final" or manifest.get("language") != "it":
        raise DualPipelineError("run reale richiede il manifest finale italiano")
    if localization_path is None:
        raise DualPipelineError("run reale senza artefatto italiano: passa --localization")
    localization = json.loads(Path(localization_path).read_text(encoding="utf-8"))
    verify_localization_seal(localization)
    if localization.get("localization_sha256") != manifest["localization"]["localization_sha256"]:
        raise DualPipelineError("localization SHA diverso dal manifest finale")
    assert_head_matches_manifest(manifest, REPO_ROOT)
    assert_worktree_clean(REPO_ROOT)

    expected = expected_dual_pairs(manifest)
    checkpoint_path = output_dir / "checkpoint.json"
    checkpoint = _prepare_resume(checkpoint_path, manifest=manifest, identity=identity,
                                 expected=expected, output_dir=output_dir)
    completed = dict(checkpoint["completed"])
    runs_dir = output_dir / "runs"
    conv_by_id = {c["sample_id"]: c for c in manifest["conversations"]}

    for spec in plan:
        if spec["key"] in completed:
            print(json.dumps({"event": "pair_skip_completed", "key": spec["key"]}), flush=True)
            continue
        conv = conv_by_id[spec["sample_id"]]
        selection_path = _write_selection(conv, output_dir / "selections")
        slice_path = _write_slice(localization, conv, output_dir / "localizations")
        report_path = _run_pair(
            spec, manifest=manifest, expected=expected[spec["key"]], corpus_path=corpus_path,
            selection_path=selection_path, slice_path=slice_path, runs_dir=runs_dir,
        )
        completed[spec["key"]] = {"report": f"runs/{spec['key']}.json"}
        _save_checkpoint(checkpoint_path, {"identity": identity, "completed": completed})
        print(json.dumps({"event": "pair_complete", "key": spec["key"]}), flush=True)
    return {"mode": "run", "pairs_completed": len(completed), "pairs_planned": len(plan)}


def _run_pair(spec, *, manifest, expected, corpus_path, selection_path, slice_path, runs_dir) -> Path:
    from benchmarks.euri_memory.runtime import IsolatedRuntime

    report_path = runs_dir / f"{spec['key']}.json"
    cases = {c.sample_id: c for c in LoCoMoAdapter().load(Path(corpus_path))}
    owner, assistant = cases[spec["sample_id"]].speakers
    with IsolatedRuntime() as runtime:
        worker_report = runtime.report_dir / f"{spec['key']}.json"
        env = dict(os.environ)
        env.update(runtime.environment())
        env.update({
            "EURI_OWNER_DISPLAY_NAME": owner, "EURI_ASSISTANT_DISPLAY_NAME": assistant,
            "EURI_OWNER_ACTOR_ID": f"locomo-{owner.lower()}",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
        })
        subprocess.run(
            [sys.executable, "-m", "benchmarks.euri_memory.dual_channel_worker",
             "--source", str(Path(corpus_path).resolve()),
             "--selection", str(selection_path.resolve()),
             "--localization", str(slice_path.resolve()),
             "--generation-order", ",".join(spec["generation_order"]),
             "--answer-seed", str(spec["answer_seed"]),
             "--run-label", spec["key"],
             "--manifest-sha256", str(manifest["manifest_sha256"]),
             "--selection-manifest-sha256", str(manifest["selection_manifest_sha256"]),
             "--localization-sha256", str(manifest["localization"]["localization_sha256"]),
             "--localization-id", str(manifest["localization"]["localization_id"]),
             "--output", str(worker_report)],
            cwd=REPO_ROOT, env=env, check=True, timeout=_PER_PAIR_TIMEOUT,
        )
        report = json.loads(worker_report.read_text(encoding="utf-8"))
        problems = validate_dual_report(report, manifest, expected)
        if problems:
            raise DualPipelineError(f"coppia {spec['key']} non valida: {'; '.join(problems)}")
        runs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(worker_report, report_path)
    return report_path


def _prepare_resume(checkpoint_path, *, manifest, identity, expected, output_dir) -> dict:
    if not checkpoint_path.is_file():
        return {"identity": identity, "completed": {}}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    recorded = checkpoint.get("identity") or {}
    if any(recorded.get(f) is None for f in ("manifest_sha256", "corpus_sha256", "git_commit")):
        raise IntegrityError("checkpoint senza identity completa: rifiuto")
    assert_same_identity(recorded, identity, context="resume dual")
    for key, entry in checkpoint.get("completed", {}).items():
        if key not in expected:
            raise IntegrityError(f"checkpoint con coppia estranea: {key}")
        report_file = output_dir / f"runs/{key}.json"
        if entry.get("report") != f"runs/{key}.json" or not report_file.is_file():
            raise IntegrityError(f"report mancante/percorso divergente per {key}")
        problems = validate_dual_report(json.loads(report_file.read_text()), manifest, expected[key])
        if problems:
            raise IntegrityError(f"report divergente dopo checkpoint {key}: {problems}")
    return {"identity": identity, "completed": dict(checkpoint.get("completed", {}))}


def _save_checkpoint(path: Path, checkpoint: dict) -> None:
    path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _probe_isolation() -> bool:
    from benchmarks.euri_memory.runtime import IsolatedRuntime

    with IsolatedRuntime() as runtime:
        assert runtime.port is not None and runtime.port != 6379
        runtime.client.set("euri:benchmark:probe", "1")
        runtime.reset()
        return runtime.client.get("euri:benchmark:probe") is None


# --------------------------------------------------------------------------- #
# Analisi clusterizzata N=5 (rag_only vs dual_channel)
# --------------------------------------------------------------------------- #
_METRICS = ("mean_token_f1", "exact_match", "adversarial_accuracy", "evidence_recall")


def analyze(runs_dir: Path, manifest_path: Path) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    verify_manifest(manifest)
    expected = expected_dual_pairs(manifest)
    seen, foreign, invalid = {}, [], []
    for path in sorted(Path(runs_dir).glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        key = report.get("run", {}).get("run_label")
        if key not in expected:
            foreign.append(str(path)); continue
        problems = validate_dual_report(report, manifest, expected[key])
        if problems:
            invalid.append({"path": str(path), "problems": problems}); continue
        if key in seen:
            invalid.append({"path": str(path), "problems": ["duplicato"]}); continue
        seen[key] = report
    if foreign or invalid:
        raise DualPipelineError(f"report non legati: estranei={foreign}, non_validi={invalid}")

    per_conv = defaultdict(lambda: defaultdict(list))
    rag_bin = defaultdict(lambda: defaultdict(list))   # metric -> (conv,qid) -> [0/1]
    dual_bin = defaultdict(lambda: defaultdict(list))
    cat_delta = defaultdict(list)                      # categoria -> [delta F1] (diagnostico)
    gold_lost = 0
    for key, report in seen.items():
        sample = report["dataset"]["sample_id"]
        by = {a["arm"]: a for a in report["arms"]}
        for m in _METRICS:
            r, d = by["rag_only"]["scoring"].get(m), by["dual_channel"]["scoring"].get(m)
            if r is not None and d is not None:
                per_conv[sample][m].append(d - r)
        rag_items = {i["question_id"]: i for i in by["rag_only"]["scoring"]["items"]}
        dual_items = {i["question_id"]: i for i in by["dual_channel"]["scoring"]["items"]}
        for qid, ri in rag_items.items():
            di = dual_items.get(qid, {})
            # gold_lost: evidence recuperata dal RAG e persa dal dual (deve essere 0)
            if ri.get("evidence_hit") is True and di.get("evidence_hit") is False:
                gold_lost += 1
            for metric, val in (
                ("adversarial_correct", lambda it: it["correct"] if it.get("answerable") is False else None),
                ("evidence_hit", lambda it: it["evidence_hit"] if it.get("evidence_hit") is not None else None),
                ("exact_match", lambda it: it["exact_match"] if it.get("answerable") else None),
            ):
                rv, dv = val(ri), val(di) if di else None
                if rv is not None and dv is not None:
                    rag_bin[metric][(sample, qid)].append(float(rv))
                    dual_bin[metric][(sample, qid)].append(float(dv))
            if ri.get("token_f1") is not None and di.get("token_f1") is not None:
                cat_delta[ri.get("category")].append(di["token_f1"] - ri["token_f1"])

    conversations = sorted(per_conv)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    primary = {}
    any_cross = False
    for m in _METRICS:
        values = [_mean(per_conv[c][m]) for c in conversations if per_conv[c][m]]
        ci = cluster_bootstrap_ci(values)
        primary[m] = {
            "cluster_bootstrap": ci,
            "per_conversation_delta": {c: _mean(per_conv[c][m]) for c in conversations},
        }
        if ci and ci["ci_crosses_zero"]:
            any_cross = True

    def _mcnemar(metric):
        b = c = units = 0
        for k in set(rag_bin[metric]) & set(dual_bin[metric]):
            rm, dm = _mean(rag_bin[metric][k]), _mean(dual_bin[metric][k])
            rb = None if rm == 0.5 else rm > 0.5
            db = None if dm == 0.5 else dm > 0.5
            if rb is None or db is None:
                continue
            units += 1
            if not rb and db:
                b += 1
            elif rb and not db:
                c += 1
        res = mcnemar_exact(b, c)
        res["units"] = units
        res["direction"] = "b = dual migliora, c = dual peggiora"
        return res

    mcnemar = {m: _mcnemar(m) for m in ("adversarial_correct", "evidence_hit", "exact_match")}

    f1 = primary["mean_token_f1"]["cluster_bootstrap"]
    adv = primary["adversarial_accuracy"]["cluster_bootstrap"]
    mean_f1_delta = f1["point_estimate"] if f1 else None
    adv_delta = adv["point_estimate"] if adv else None
    conv_f1 = primary["mean_token_f1"]["per_conversation_delta"]
    non_negative = sum(1 for v in conv_f1.values() if v is not None and v >= 0)

    # Verdetto a tre stati CONGELATO prima dei risultati.
    if mean_f1_delta is None or adv_delta is None:
        verdict = "INCONCLUSIVO"
    elif mean_f1_delta <= 0 or adv_delta < -0.02 or gold_lost > 0:
        verdict = "NO-GO"
    elif mean_f1_delta > 0 and non_negative >= 4 and adv_delta >= -0.02 and gold_lost == 0:
        verdict = "GO"
    else:
        verdict = "INCONCLUSIVO"

    return {
        "analysis": "euri_dual_channel_validation_paired_clustered",
        "direction": "dual_channel_minus_rag_only",
        "independent_unit": "conversation",
        "n_conversations": len(conversations),
        "conversations": conversations,
        "pairs_complete": len(seen),
        "expected_pairs": sorted(expected),
        "missing_pairs": sorted(set(expected) - set(seen)),
        "is_partial_run": bool(set(expected) - set(seen)),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "primary_metrics": primary,
        "f1_delta_per_conversation": conv_f1,
        "secondary_mcnemar_exact": {
            "note": "descrittivo: tratta le domande come indipendenti, non clusterizzato",
            **mcnemar,
        },
        "gold_lost": {
            "count": gold_lost,
            "must_be_zero": True,
            "invariant_holds": gold_lost == 0,
            "definition": "evidence_hit nel RAG e non nel dual",
        },
        "adversarial": {
            "delta_cluster_point": adv_delta,
            "discordants": {"b": mcnemar["adversarial_correct"]["b"], "c": mcnemar["adversarial_correct"]["c"]},
        },
        "category_breakdown_diagnostic": {
            "note": "SOLO diagnostico, mai un gate (policy category-agnostic)",
            "mean_f1_delta": {cat: _mean(v) for cat, v in sorted(cat_delta.items())},
        },
        "verdict": {
            "value": verdict,
            "frozen_criteria": {
                "GO": "delta medio F1 > 0 AND >=4/5 conv non-negative AND delta avversariale >= -0.02 AND gold_lost=0",
                "NO_GO": "delta medio F1 <= 0 OR delta avversariale < -0.02 OR gold_lost > 0",
                "INCONCLUSIVO": "altrimenti",
            },
            "inputs": {
                "mean_f1_delta": mean_f1_delta,
                "conversations_non_negative": non_negative,
                "adversarial_delta": adv_delta,
                "gold_lost": gold_lost,
            },
        },
        "power": {"underpowered": len(conversations) < 10 or any_cross, "n_clusters": len(conversations)},
        "interpretation_limit": "N=5 conversazioni: validazione clusterizzata (underpowered), non stima definitiva su LoCoMo.",
    }
