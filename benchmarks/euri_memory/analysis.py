"""Analisi appaiata clusterizzata per conversazione del campione held-out.

L'unità indipendente è la **conversazione**: N = numero di conversazioni, non
conversazioni × repliche. Domande e repliche sono osservazioni annidate. Perciò
l'inferenza primaria è un **bootstrap clusterizzato** che ricampiona le
conversazioni, e i delta sono riportati anche per singola conversazione, così un
dialogo molto favorevole non può nascondere gli altri.

McNemar esatto è fornito come test secondario descrittivo sugli esiti binari a
livello di domanda: tratta le domande come indipendenti (ignora il clustering) e
non è l'inferenza primaria.

Qualità, prudenza, costo e frammentazione restano fronti separati: nessun
punteggio unico, nessun successo definito sul solo F1.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory.heldout import verify_manifest
from benchmarks.euri_memory.integrity import expected_pairs, validate_pair_report


# Soglia sotto la quale l'inferenza è dichiarata sotto-potenziata a prescindere.
_MIN_CLUSTERS_FOR_POWER = 10
_BOOTSTRAP_RESAMPLES = 10_000
_BOOTSTRAP_SEED = 12345
_PRIMARY_METRICS = ("evidence_recall", "adversarial_accuracy", "mean_token_f1", "exact_match")


class AnalysisError(RuntimeError):
    pass


def _profiles_by_name(report: dict) -> dict[str, dict]:
    return {item.get("profile", {}).get("name"): item for item in report.get("profiles", [])}


def _load_raw_reports(runs_dir: Path) -> list[tuple[Path, dict]]:
    reports = []
    for path in sorted(Path(runs_dir).glob("*.json")):
        reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return reports


def load_completed_pairs(runs_dir: Path) -> list[dict]:
    """Carica i report delle coppie complete (entrambi i bracci con scoring).

    Percorso permissivo: usato solo senza manifest. Con manifest la selezione è
    legata rigidamente in :func:`analyze`.
    """

    pairs = []
    for path, report in _load_raw_reports(runs_dir):
        profiles = _profiles_by_name(report)
        if {"rag_only", "passive_memory"} - set(profiles):
            continue
        if not all("scoring" in profiles[name] for name in ("rag_only", "passive_memory")):
            continue
        sample_id = report.get("dataset", {}).get("sample_id")
        replica = report.get("run", {}).get("run_label")
        pairs.append({"path": str(path), "sample_id": sample_id, "run_label": replica, "report": report})
    return pairs


def _bind_pairs_to_manifest(runs_dir: Path, manifest: dict) -> tuple[list[dict], dict]:
    """Accetta solo report legati ESATTAMENTE al manifest.

    Rifiuta (fail-closed) report estranei, duplicati o non validi. Le coppie
    mancanti sono ammesse ma dichiarate: una run parziale non è una validation
    completa.
    """

    expected = expected_pairs(manifest)
    seen: dict[str, dict] = {}
    foreign: list[str] = []
    duplicate: list[str] = []
    invalid: list[dict] = []
    for path, report in _load_raw_reports(runs_dir):
        key = report.get("run", {}).get("run_label")
        if key not in expected:
            foreign.append(str(path))
            continue
        problems = validate_pair_report(report, manifest, expected[key])
        if problems:
            invalid.append({"path": str(path), "problems": problems})
            continue
        if key in seen:
            duplicate.append(str(path))
            continue
        seen[key] = {
            "path": str(path),
            "sample_id": report.get("dataset", {}).get("sample_id"),
            "run_label": key,
            "report": report,
        }
    if foreign or duplicate or invalid:
        raise AnalysisError(
            "report non legati al manifest: "
            f"estranei={foreign}, duplicati={duplicate}, non_validi={invalid}"
        )
    missing = sorted(set(expected) - set(seen))
    binding = {
        "manifest_bound": True,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "expected_pairs": sorted(expected),
        "complete_pairs": sorted(seen),
        "missing_pairs": missing,
        "partial": bool(missing),
    }
    return list(seen.values()), binding


def mcnemar_exact(b: int, c: int) -> dict:
    """McNemar esatto a due code sulle coppie discordanti (b, c)."""

    n = b + c
    if n == 0:
        return {"discordant": 0, "b": b, "c": c, "p_value": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5**n)
    return {"discordant": n, "b": b, "c": c, "p_value": min(1.0, 2.0 * tail)}


def cluster_bootstrap_ci(
    per_conversation: list[float],
    *,
    resamples: int = _BOOTSTRAP_RESAMPLES,
    seed: int = _BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> dict | None:
    """CI percentile del delta medio ricampionando le CONVERSAZIONI (cluster)."""

    n = len(per_conversation)
    if n == 0:
        return None
    point = sum(per_conversation) / n
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += per_conversation[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return {
        "point_estimate": point,
        "ci_low": lo,
        "ci_high": hi,
        "ci_crosses_zero": lo <= 0.0 <= hi,
        "n_clusters": n,
        "resamples": resamples,
        "unit": "conversation",
    }


def _per_conversation_deltas(pairs: list[dict]) -> dict[str, dict[str, float]]:
    """Per ogni metrica: delta (passive − rag) medio per conversazione.

    Ogni report è una conversazione×replica; lo scoring aggrega già le domande di
    quella conversazione. Il delta di conversazione media sulle repliche.
    """

    per_conv_replica: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for pair in pairs:
        profiles = _profiles_by_name(pair["report"])
        base = profiles["rag_only"]["scoring"]
        treat = profiles["passive_memory"]["scoring"]
        for metric in _PRIMARY_METRICS:
            if base.get(metric) is None or treat.get(metric) is None:
                continue
            per_conv_replica[pair["sample_id"]][metric].append(
                treat[metric] - base[metric]
            )
    per_conv: dict[str, dict[str, float]] = {}
    for sample_id, metrics in per_conv_replica.items():
        per_conv[sample_id] = {
            metric: sum(values) / len(values)
            for metric, values in metrics.items()
            if values
        }
    return per_conv


def _binary_mcnemar(
    pairs: list[dict],
    extract: Callable[[dict], bool | None],
) -> dict:
    """McNemar sugli esiti binari per (conversazione, domanda), repliche mediate.

    ``extract`` mappa un item di scoring in True/False/None (None = non
    applicabile). Le repliche sono mediate e soglia 0,5; i pareggi esatti sono
    scartati. Tratta le domande come indipendenti: descrittivo, non clusterizzato.
    """

    base_vals: dict[tuple[str, str], list[float]] = defaultdict(list)
    treat_vals: dict[tuple[str, str], list[float]] = defaultdict(list)
    for pair in pairs:
        profiles = _profiles_by_name(pair["report"])
        for item in profiles["rag_only"]["scoring"]["items"]:
            value = extract(item)
            if value is not None:
                base_vals[(pair["sample_id"], item["question_id"])].append(float(value))
        for item in profiles["passive_memory"]["scoring"]["items"]:
            value = extract(item)
            if value is not None:
                treat_vals[(pair["sample_id"], item["question_id"])].append(float(value))
    b = c = 0
    units = 0
    for key in set(base_vals) & set(treat_vals):
        base_mean = sum(base_vals[key]) / len(base_vals[key])
        treat_mean = sum(treat_vals[key]) / len(treat_vals[key])
        base_bit = None if base_mean == 0.5 else base_mean > 0.5
        treat_bit = None if treat_mean == 0.5 else treat_mean > 0.5
        if base_bit is None or treat_bit is None:
            continue
        units += 1
        if not base_bit and treat_bit:
            b += 1  # passive migliora
        elif base_bit and not treat_bit:
            c += 1  # passive peggiora
    result = mcnemar_exact(b, c)
    result["units"] = units
    result["direction"] = "b = passive migliora, c = passive peggiora"
    return result


def _cost_and_fragmentation(pairs: list[dict]) -> dict:
    """Fronti separati costo/frammentazione, aggregati sulle coppie complete."""

    per_profile: dict[str, dict[str, float]] = {
        "rag_only": defaultdict(float),
        "passive_memory": defaultdict(float),
    }
    passive = defaultdict(float)
    source_ids_total = 0
    source_ids_with_provenance = 0
    for pair in pairs:
        profiles = _profiles_by_name(pair["report"])
        for name in ("rag_only", "passive_memory"):
            llm = profiles[name].get("llm", {})
            per_profile[name]["llm_calls"] += int(llm.get("calls", 0))
            per_profile[name]["eval_count"] += int(llm.get("eval_count", 0))
            per_profile[name]["prompt_eval_count"] += int(llm.get("prompt_eval_count", 0))
            per_profile[name]["elapsed_ms"] += float(profiles[name].get("elapsed_ms", 0.0))
        stats = (profiles["passive_memory"].get("ingest", {}) or {}).get("passive") or {}
        for field in (
            "extracted",
            "validated",
            "rejected",
            "provenance_rejected",
            "provenance_repaired",
            "duplicates",
            "saved",
            "temporal_corrections",
        ):
            passive[field] += int(stats.get(field, 0) or 0)
        for memory in stats.get("saved_memories", []) or []:
            source_ids_total += 1
            if memory.get("source_turn_ids"):
                source_ids_with_provenance += 1
        before = profiles["passive_memory"].get("database_before_questions", {})
        after = profiles["passive_memory"].get("database_after_questions", {})
        passive["db_memories_growth"] += int(after.get("memories", 0)) - int(
            before.get("memories", 0)
        )
    coverage = (
        source_ids_with_provenance / source_ids_total if source_ids_total else None
    )
    return {
        "cost_by_profile": {name: dict(values) for name, values in per_profile.items()},
        "extra_llm_calls_passive_vs_rag": (
            per_profile["passive_memory"]["llm_calls"]
            - per_profile["rag_only"]["llm_calls"]
        ),
        "fragmentation": {
            **{key: int(value) for key, value in passive.items()},
            "saved_memories_with_source_turn_ids": source_ids_with_provenance,
            "saved_memories_total": source_ids_total,
            "source_turn_ids_coverage": coverage,
        },
    }


# Estrattori di esiti binari per McNemar.
def _adversarial_correct(item: dict) -> bool | None:
    if item.get("answerable") is not False:
        return None
    return bool(item.get("correct"))


def _evidence_hit(item: dict) -> bool | None:
    if item.get("evidence_hit") is None:
        return None
    return bool(item.get("evidence_hit"))


def _exact_match(item: dict) -> bool | None:
    if not item.get("answerable"):
        return None
    return bool(item.get("exact_match"))


def analyze(runs_dir: Path, manifest_path: Path | None = None) -> dict:
    manifest = None
    binding: dict = {"manifest_bound": False}
    if manifest_path is not None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        verify_manifest(manifest)
        pairs, binding = _bind_pairs_to_manifest(runs_dir, manifest)
    else:
        pairs = load_completed_pairs(runs_dir)
    if not pairs:
        raise AnalysisError(f"nessuna coppia completa/valida in {runs_dir}")

    per_conv_deltas = _per_conversation_deltas(pairs)
    conversations = sorted(per_conv_deltas)
    n_clusters = len(conversations)
    replicas = sorted({pair["run_label"] for pair in pairs})

    primary = {}
    any_crosses_zero = False
    for metric in _PRIMARY_METRICS:
        values = [
            per_conv_deltas[conv][metric]
            for conv in conversations
            if metric in per_conv_deltas[conv]
        ]
        ci = cluster_bootstrap_ci(values)
        primary[metric] = {
            "cluster_bootstrap": ci,
            "per_conversation_delta": {
                conv: per_conv_deltas[conv].get(metric) for conv in conversations
            },
        }
        if ci and ci["ci_crosses_zero"]:
            any_crosses_zero = True

    mcnemar = {
        "adversarial_correct": _binary_mcnemar(pairs, _adversarial_correct),
        "evidence_hit": _binary_mcnemar(pairs, _evidence_hit),
        "exact_match": _binary_mcnemar(pairs, _exact_match),
    }

    underpowered = n_clusters < _MIN_CLUSTERS_FOR_POWER or any_crosses_zero

    adv = primary["adversarial_accuracy"]["cluster_bootstrap"]
    prudence_regression = bool(adv and adv["ci_high"] < 0.0)

    return {
        "schema_version": 1,
        "analysis": "euri_passive_memory_heldout_paired_clustered",
        "independent_unit": "conversation",
        "n_conversations": n_clusters,
        "conversations": conversations,
        "replicas_present": replicas,
        "pairs_complete": len(pairs),
        "manifest_sha256": manifest.get("manifest_sha256") if manifest else None,
        "binding": binding,
        "is_partial_run": bool(binding.get("partial")),
        "direction": "passive_memory_minus_rag_only",
        "primary_metrics": primary,
        "secondary_mcnemar_exact": {
            "note": "descrittivo: tratta le domande come indipendenti, ignora il "
            "clustering per conversazione; non è l'inferenza primaria",
            **mcnemar,
        },
        "cost_and_fragmentation": _cost_and_fragmentation(pairs),
        "power": {
            "underpowered": underpowered,
            "n_clusters": n_clusters,
            "min_clusters_for_power": _MIN_CLUSTERS_FOR_POWER,
            "reason": (
                "N conversazioni sotto soglia" if n_clusters < _MIN_CLUSTERS_FOR_POWER
                else ("un CI primario attraversa lo zero" if any_crosses_zero else "n/d")
            ),
        },
        "prudence_guardrail": {
            "adversarial_abstention_regression": prudence_regression,
            "note": "un calo significativo dell'astensione avversariale è un "
            "fallimento anche con F1 in aumento",
        },
        "interpretation_limit": (
            "3 conversazioni × repliche è una validazione held-out sostanziale ma "
            "resta un pilot clusterizzato: non è una stima definitiva sull'intero "
            "LoCoMo."
            + (
                " ATTENZIONE: run PARZIALE, coppie mancanti "
                f"{binding.get('missing_pairs')}: non confondere con la validation "
                "completa."
                if binding.get("partial")
                else ""
            )
        ),
    }
