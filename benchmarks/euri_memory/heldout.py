"""Selettore held-out deterministico e stratificato per la memoria passiva.

Il campione finale NON esiste finché non arriva un ``seed``. Da un seed il
manifest è riproducibile bit-a-bit: stesse conversazioni, stesse domande, stessi
ordini dei bracci. Il manifest contiene soltanto ID, categorie, conteggi, hash e
provenienza — mai il testo delle domande o le risposte gold, così chi costruisce
il test resta cieco al contenuto del campione.

Le selezioni di sviluppo (conv-26 e conv-42, incluse le versioni versionate nei
fixture) sono escluse per costruzione: non sono più held-out.
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.selection import BenchmarkSelection, SelectionError


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DEFAULT_SOURCE = ROOT / "data" / "locomo10.json"
REPO_ROOT = ROOT.parents[1]

# Guardia esplicita, oltre alla derivazione automatica dai fixture: queste due
# conversazioni non possono MAI entrare nel campione held-out.
GUARD_EXCLUDED_SAMPLE_IDS = frozenset({"conv-26", "conv-42"})

# Ordine canonico delle categorie LoCoMo usato per la stratificazione.
CATEGORIES = ("single_hop", "temporal", "multi_hop", "open_domain", "adversarial")

SCORER_NAME = "locomo_reduced_deterministic_v1_not_official"
EXPERIMENT_NAME = "euri_passive_memory_heldout"
EXPERIMENT_VERSION = "v1"
SCHEMA_VERSION = 1

_SPEAKER_MAPPING = {"speaker_a": "owner_user", "speaker_b": "assistant"}


class HeldoutError(ValueError):
    pass


@dataclass(frozen=True)
class BudgetLevel:
    name: str
    num_conversations: int
    per_category: int
    num_replicas: int
    # Cap preregistrati di arresto tecnico: la run si ferma solo se li supera o
    # per errore tecnico, MAI in base all'andamento delle metriche.
    max_llm_calls: int
    max_seconds: int


# Livelli preregistrati. Il costo è dominato dall'ingestione passiva su
# conversazioni intere (∝ conversazioni × repliche), non dal numero di domande.
# I cap sono guardrail generosi: fermano una run tecnicamente fuori controllo,
# non decidono il risultato.
BUDGETS: dict[str, BudgetLevel] = {
    "smoke": BudgetLevel(
        "smoke",
        num_conversations=3,
        per_category=1,
        num_replicas=1,
        max_llm_calls=6_000,
        max_seconds=7_200,
    ),
    "validation": BudgetLevel(
        "validation",
        num_conversations=3,
        per_category=6,
        num_replicas=3,
        max_llm_calls=40_000,
        max_seconds=28_800,
    ),
    "extended": BudgetLevel(
        "extended",
        num_conversations=5,
        per_category=10,
        num_replicas=5,
        max_llm_calls=160_000,
        max_seconds=115_200,
    ),
}
RECOMMENDED_BUDGET = "validation"


def get_budget(name: str) -> BudgetLevel:
    try:
        return BUDGETS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(BUDGETS))
        raise HeldoutError(f"budget sconosciuto {name!r}; disponibili: {choices}") from exc


def dev_excluded_sample_ids() -> frozenset[str]:
    """Sample id delle selezioni di sviluppo presenti nei fixture, più la guardia.

    Ogni fixture che carica come ``BenchmarkSelection`` è una selezione dev: il
    suo ``sample_id`` è escluso. I corpus e le localizzazioni non caricano come
    selezione e vengono ignorati.
    """

    excluded = set(GUARD_EXCLUDED_SAMPLE_IDS)
    for path in sorted(FIXTURES.glob("*.json")):
        try:
            selection = BenchmarkSelection.load(path)
        except (SelectionError, AttributeError, TypeError):
            # Non è una selezione (corpus con radice lista, localizzazione, ...).
            continue
        excluded.add(selection.sample_id)
    return frozenset(excluded)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def manifest_digest(manifest: dict) -> str:
    """Hash canonico del manifest, escluso il campo hash stesso."""

    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def verify_manifest(manifest: dict) -> None:
    recorded = manifest.get("manifest_sha256")
    if not recorded:
        raise HeldoutError("manifest privo di manifest_sha256")
    if recorded != manifest_digest(manifest):
        raise HeldoutError("manifest_sha256 non corrisponde: manifest alterato")


def build_manifest(
    *,
    seed: int,
    budget_name: str,
    corpus_path: Path = DEFAULT_SOURCE,
    git_commit: str | None = None,
) -> dict:
    """Costruisce il manifest immutabile del campione held-out.

    La procedura è interamente derivata dal ``seed`` e indipendente dall'ordine
    di iterazione: la scelta delle conversazioni usa un RNG master, il
    campionamento delle domande di ciascuna conversazione un RNG indipendente
    derivato da ``(seed, sample_id)``.
    """

    budget = get_budget(budget_name)
    corpus_path = Path(corpus_path)
    if not corpus_path.is_file():
        raise HeldoutError(f"corpus non trovato: {corpus_path}")

    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    excluded = dev_excluded_sample_ids()
    eligible = sorted(sid for sid in cases if sid not in excluded)
    if len(eligible) < budget.num_conversations:
        raise HeldoutError(
            f"universo eleggibile insufficiente: {len(eligible)} < "
            f"{budget.num_conversations} richieste"
        )

    master = random.Random(seed)
    chosen = sorted(master.sample(eligible, budget.num_conversations))

    conversations: list[dict] = []
    all_case_list = list(cases.values())
    for sample_id in chosen:
        case = cases[sample_id]
        question_rng = random.Random(f"{seed}:{sample_id}")
        known_turns = {turn.turn_id for turn in case.turns}
        by_category: dict[str, list[str]] = defaultdict(list)
        excluded_missing_evidence = 0
        for question in case.questions:
            # Criterio di esclusione: una domanda con evidence gold non presente
            # nel corpus (difetto del rilascio LoCoMo, es. "D:11:26") esce
            # dall'universo eleggibile ed è contata per trasparenza.
            if set(question.evidence_turn_ids) - known_turns:
                excluded_missing_evidence += 1
                continue
            by_category[question.category].append(question.question_id)

        sampled_ids: list[str] = []
        histogram: dict[str, int] = {}
        for category in CATEGORIES:
            available = sorted(by_category.get(category, []))
            take = min(budget.per_category, len(available))
            picked = sorted(question_rng.sample(available, take)) if take else []
            sampled_ids.extend(picked)
            histogram[category] = len(picked)
        sampled_ids = sorted(sampled_ids)
        if not sampled_ids:
            raise HeldoutError(f"{sample_id}: nessuna domanda campionata")

        session_ids = [session.session_id for session in case.sessions]
        # Validazione strutturale immediata (riuso della logica esistente):
        # conferma che l'evidence gold di ogni domanda cada dentro le sessioni
        # ingerite. Con conversazione intera è sempre vero, ma lo verifichiamo.
        selection = BenchmarkSelection(
            selection_id=f"heldout-{sample_id}-{budget.name}-seed{seed}",
            dataset="locomo",
            sample_id=sample_id,
            session_ids=tuple(session_ids),
            question_ids=tuple(sampled_ids),
            speaker_mapping=dict(_SPEAKER_MAPPING),
            metadata={},
        )
        selection.apply(all_case_list)

        conversations.append(
            {
                "sample_id": sample_id,
                "sessions": len(session_ids),
                "session_ids": session_ids,
                "question_ids": sampled_ids,
                "question_count": len(sampled_ids),
                "category_histogram": histogram,
                "excluded_missing_evidence": excluded_missing_evidence,
            }
        )

    replicas = _build_replicas(seed, budget.num_replicas)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT_NAME,
        "experiment_version": EXPERIMENT_VERSION,
        "seed": seed,
        "budget": {
            "name": budget.name,
            "num_conversations": budget.num_conversations,
            "per_category": budget.per_category,
            "num_replicas": budget.num_replicas,
            "max_llm_calls": budget.max_llm_calls,
            "max_seconds": budget.max_seconds,
        },
        # L'unità indipendente è la conversazione: N = num_conversations, non
        # conversazioni × repliche. Domande e repliche sono osservazioni
        # annidate; l'analisi deve clusterizzare per conversazione.
        "independent_unit": "conversation",
        "n_independent": budget.num_conversations,
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "corpus": {
            "path": str(corpus_path.resolve()),
            "sha256": _sha256_file(corpus_path),
        },
        "eligible_universe": eligible,
        "excluded_sample_ids": sorted(excluded),
        "categories": list(CATEGORIES),
        "conversations": conversations,
        "replicas": replicas,
        "scorer": SCORER_NAME,
        "profiles": ["rag_only", "passive_memory"],
        "ingestion_unit": "full_conversation",
        "notes": [
            "Manifest cieco: nessun testo di domanda o risposta gold.",
            "Campione generato solo dal seed; qualsiasi modifica post-risultati "
            "richiede una nuova versione dell'esperimento.",
            "Scorer interno non ufficiale: confronto A/B, non risultato LoCoMo.",
        ],
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def _build_replicas(seed: int, num_replicas: int) -> list[dict]:
    """Ordine dei bracci e answer_seed per replica, derivati dal seed.

    L'ordine alterna il braccio iniziale (bilanciato sulle repliche pari); la
    parità di partenza dipende dal seed. L'``answer_seed`` è unico per replica ma
    identico sui due bracci della stessa replica: fa variare la generazione fra
    repliche in modo simmetrico, senza favorire alcun braccio.
    """

    order_rng = random.Random(f"{seed}:branch-order")
    seed_rng = random.Random(f"{seed}:answer-seed")
    base = ["rag_only", "passive_memory"]
    start_reversed = order_rng.random() < 0.5
    replicas = []
    for index in range(num_replicas):
        reversed_here = start_reversed ^ (index % 2 == 1)
        order = list(reversed(base)) if reversed_here else list(base)
        replicas.append(
            {
                "replica_index": index,
                "branch_order": order,
                "answer_seed": seed_rng.randrange(1, 2**31 - 1),
            }
        )
    return replicas


def build_final_manifest(
    selection_manifest: dict,
    localization: dict,
    *,
    corpus_path: Path = DEFAULT_SOURCE,
) -> dict:
    """Manifest finale derivato che sigilla selezione + traduzione + localization.

    Verifica che l'artefatto italiano copra esattamente la selezione e sia legato
    a questo selection manifest e al corpus, poi produce un manifest finale con il
    proprio ``manifest_sha256``. È questo il manifest che runner e analisi
    consumano: la chiusura del manifest finale precede qualsiasi risultato.
    """

    from benchmarks.euri_memory.heldout_localization import (
        verify_selected_localization,
    )

    # La finalizzazione non deve fidarsi del solo hash incorporato nel file:
    # il selection manifest viene riverificato integralmente prima di copiarne
    # qualunque campo nel manifest finale.
    verify_manifest(selection_manifest)
    verify_selected_localization(localization, corpus_path, selection_manifest)
    final = {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT_NAME,
        "experiment_version": EXPERIMENT_VERSION,
        "stage": "final",
        "language": "it",
        "seed": selection_manifest["seed"],
        "budget": selection_manifest["budget"],
        "git_commit": selection_manifest["git_commit"],
        "corpus": selection_manifest["corpus"],
        "eligible_universe": selection_manifest["eligible_universe"],
        "excluded_sample_ids": selection_manifest["excluded_sample_ids"],
        "categories": selection_manifest["categories"],
        "independent_unit": selection_manifest["independent_unit"],
        "n_independent": selection_manifest["n_independent"],
        "conversations": selection_manifest["conversations"],
        "replicas": selection_manifest["replicas"],
        "scorer": selection_manifest["scorer"],
        "profiles": selection_manifest["profiles"],
        "ingestion_unit": selection_manifest["ingestion_unit"],
        "selection_manifest_sha256": selection_manifest["manifest_sha256"],
        "translation_protocol": localization["translation_protocol"],
        "localization": {
            "localization_id": localization["localization_id"],
            "localization_sha256": localization["localization_sha256"],
            "language": localization["language"],
            "source_language": localization["source_language"],
            "selected_sample_ids": localization["selected_sample_ids"],
        },
        "notes": list(selection_manifest.get("notes", []))
        + [
            "Manifest finale: lega selection manifest + protocollo di traduzione + "
            "localization SHA.",
            "Lingua primaria italiana; entrambi i bracci ricevono lo stesso "
            "artefatto tradotto.",
        ],
    }
    final["manifest_sha256"] = manifest_digest(final)
    return final


def write_manifest(manifest: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path
