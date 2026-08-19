#!/usr/bin/env python3
"""Read-only sweep della profondita' del retrieval semantico di Euri.

Confronta diversi valori di ``limit`` senza modificare configurazione o runtime.
Esegue due pannelli:

1. LoCoMo italiano in un Redis effimero, con evidence ID gold;
2. memorie personali correnti, con una rubrica lessicale dichiarata e touch=False.

Il pannello personale misura una lower bound riproducibile: una memoria e' marcata
``direct`` soltanto se nomina esplicitamente il soggetto. Una memoria anaforica ma
pertinente puo' quindi risultare ``indirect``; non viene automaticamente chiamata
rumore.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.live_worker import _ingest_raw_turns
from benchmarks.euri_memory.localization import BenchmarkLocalization
from benchmarks.euri_memory.runtime import IsolatedRuntime
from benchmarks.euri_memory.selection import BenchmarkSelection
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from utils.redis_client import _create_memory_index, get_client


LOCOMO = ROOT / "benchmarks/euri_memory/data/locomo10.json"
SELECTION = ROOT / "benchmarks/euri_memory/fixtures/locomo_reduced_v2.json"
LOCALIZATION = ROOT / "benchmarks/euri_memory/fixtures/locomo_reduced_v2_it.json"
K_VALUES = (3, 5, 6, 8, 10, 12)
MUTATION_FIELDS = ("recalled_count", "last_recalled_at", "expires_at")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip()


@dataclass(frozen=True)
class PersonalProbe:
    name: str
    query: str
    direct: Callable[[str], bool]
    expected_present: bool = True


def _has_all(*parts: str) -> Callable[[str], bool]:
    needles = tuple(_normalize(part) for part in parts)
    return lambda content: all(part in _normalize(content) for part in needles)


PERSONAL_PROBES = (
    PersonalProbe(
        "lucy_plast_profile",
        "Descrivimi il lavoro svolto da Lucy Plast spa basandoti sulle memorie.",
        _has_all("lucy", "plast"),
    ),
    PersonalProbe(
        "eurostampi_profile",
        "Che cosa sai di Eurostampi e delle prove tecniche svolte?",
        _has_all("eurostampi"),
    ),
    PersonalProbe(
        "plastvision_profile",
        "Descrivimi PlastVision, le sue funzioni e il suo utilizzo.",
        _has_all("plastvision"),
    ),
    PersonalProbe(
        "assistente_ufficio_profile",
        "Che cosa ricordi del progetto Assistente Ufficio?",
        _has_all("assistente", "ufficio"),
    ),
    PersonalProbe(
        "icma2_fact",
        "Che macchina e' ICMA2 e quali caratteristiche ricordi?",
        _has_all("icma2"),
    ),
    PersonalProbe(
        "missing_project",
        "Che cosa sai del progetto inesistente Apollo-404-Zeta?",
        lambda _content: False,
        expected_present=False,
    ),
)


def _doc_id(doc: dict[str, Any]) -> str:
    return str(doc.get("id") or "").removeprefix("euri:memory:")


def _risk(doc: dict[str, Any]) -> bool:
    return bool(
        doc.get("requires_verification")
        or doc.get("provenance_stale")
        or doc.get("audit_flag")
        or (doc.get("consolidation_risk") or {}).get("level") in {"watch", "high"}
    )


def _snapshot(redis_client: Any, ids: set[str]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for memory_id in ids:
        try:
            raw = redis_client.json().get(f"euri:memory:{memory_id}", "$")
        except Exception:
            # Il namespace personale contiene anche poche chiavi legacy non-JSON.
            continue
        doc = raw[0] if raw else {}
        if not isinstance(doc, dict):
            continue
        snapshot[memory_id] = {field: doc.get(field) for field in MUTATION_FIELDS}
    return snapshot


def _cache(embedder: Embedder, query: str, domain: str) -> dict[str, Any]:
    vector = embedder.encode(query, mode="query")
    if vector is None:
        raise RuntimeError(f"embedding query fallito: {query}")
    return {
        "entries": {query: {"domain": domain, "vector": vector}},
        "hits": 0,
    }


def _search_sweep(
    memory: MemoryManager,
    embedder: Embedder,
    query: str,
    *,
    domain: str,
) -> dict[int, list[dict[str, Any]]]:
    cache = _cache(embedder, query, domain)
    out: dict[int, list[dict[str, Any]]] = {}
    for k in K_VALUES:
        out[k] = memory.search_memories(
            query,
            limit=k,
            touch=False,
            query_feature_cache=cache,
        )
    return out


def _aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {"queries": len(rows)}
    for key in keys:
        values = [float(row[key]) for row in rows]
        out[key] = round(mean(values), 4) if values else None
    return out


def run_locomo(embedder: Embedder) -> dict[str, Any]:
    if not LOCOMO.is_file():
        raise RuntimeError(f"dataset LoCoMo mancante: {LOCOMO}")
    cases = LoCoMoAdapter().load(LOCOMO)
    case = BenchmarkSelection.load(SELECTION).apply(cases)
    case = BenchmarkLocalization.load(LOCALIZATION).apply(case)

    with tempfile.TemporaryDirectory(prefix="euri-depth-probe-") as parent:
        with IsolatedRuntime(base_dir=Path(parent)) as runtime:
            client = runtime.client
            # Serve soltanto l'indice delle memorie. init_indexes() importerebbe
            # anche i turni dai log personali, estranei a questo corpus isolato.
            _create_memory_index(client)
            ingest = _ingest_raw_turns(client, embedder, case.corpus())
            memory = MemoryManager(client, embedder)
            per_k: dict[str, list[dict[str, Any]]] = {str(k): [] for k in K_VALUES}
            questions: list[dict[str, Any]] = []

            for question in case.questions:
                sweep = _search_sweep(
                    memory,
                    embedder,
                    question.text,
                    domain="conversation",
                )
                gold = set(question.evidence_turn_ids)
                q_record = {
                    "question_id": question.question_id,
                    "category": question.category,
                    "gold_count": len(gold),
                    "by_k": {},
                }
                for k, docs in sweep.items():
                    selected = [
                        str(doc.get("benchmark_turn_id"))
                        for doc in docs
                        if doc.get("benchmark_turn_id")
                    ]
                    selected_set = set(selected)
                    overlap = gold & selected_set
                    coverage = len(overlap) / len(gold) if gold else math.nan
                    density = len(overlap) / len(selected) if selected else 0.0
                    hit = 1.0 if overlap else 0.0
                    chars = sum(len(str(doc.get("content") or "")) for doc in docs)
                    row = {
                        "hit": hit,
                        "evidence_coverage": coverage,
                        "gold_density": density,
                        "nodes": len(docs),
                        "chars": chars,
                    }
                    if gold:
                        per_k[str(k)].append(row)
                    q_record["by_k"][str(k)] = {
                        **row,
                        "selected_turn_ids": selected,
                        "gold_found": sorted(overlap),
                    }
                questions.append(q_record)

            summary = {
                key: _aggregate(
                    rows,
                    ("hit", "evidence_coverage", "gold_density", "nodes", "chars"),
                )
                for key, rows in per_k.items()
            }
            return {
                "fixture": str(LOCALIZATION.relative_to(ROOT)),
                "query_domain_policy": "conversation fisso; isola la profondita' K",
                "turns_ingested": ingest["turns"],
                "questions_with_gold": len(next(iter(per_k.values()), [])),
                "summary_by_k": summary,
                "questions": questions,
            }


def run_personal(embedder: Embedder) -> dict[str, Any]:
    client = get_client()
    memory = MemoryManager(client, embedder)
    selected_ids: set[str] = set()
    probe_rows: list[dict[str, Any]] = []

    # Lo snapshot globale dei soli campi di lifecycle rende verificabile il
    # contratto read-only anche se i set selezionati cambiano fra i valori di K.
    raw_ids = {
        str(key.decode() if isinstance(key, bytes) else key).removeprefix("euri:memory:")
        for key in client.scan_iter("euri:memory:*")
    }
    before = _snapshot(client, raw_ids)
    all_ids = set(before)

    for probe in PERSONAL_PROBES:
        sweep = _search_sweep(memory, embedder, probe.query, domain="generale")
        record = {
            "name": probe.name,
            "query": probe.query,
            "expected_present": probe.expected_present,
            "by_k": {},
        }
        for k, docs in sweep.items():
            ids = [_doc_id(doc) for doc in docs]
            selected_ids.update(ids)
            direct = [doc for doc in docs if probe.direct(str(doc.get("content") or ""))]
            record["by_k"][str(k)] = {
                "nodes": len(docs),
                "direct": len(direct),
                "direct_precision": round(len(direct) / len(docs), 4) if docs else 0.0,
                "risk": sum(1 for doc in docs if _risk(doc)),
                "chars": sum(len(str(doc.get("content") or "")) for doc in docs),
                "ids": ids,
                "rows": [
                    {
                        "id": _doc_id(doc),
                        "direct": probe.direct(str(doc.get("content") or "")),
                        "source": doc.get("source"),
                        "domain": doc.get("domain"),
                        "score": doc.get("score"),
                        "risk": _risk(doc),
                        "content": str(doc.get("content") or "")[:240],
                    }
                    for doc in docs
                ],
            }
        probe_rows.append(record)

    after = _snapshot(client, all_ids)
    mutations = {
        memory_id: {"before": before[memory_id], "after": after[memory_id]}
        for memory_id in all_ids
        if before[memory_id] != after[memory_id]
    }
    if mutations:
        raise RuntimeError(
            f"probe non read-only: {len(mutations)} memorie mutate nei campi lifecycle"
        )

    summary_by_k: dict[str, Any] = {}
    for k in K_VALUES:
        present = [row["by_k"][str(k)] for row in probe_rows if row["expected_present"]]
        absent = [row["by_k"][str(k)] for row in probe_rows if not row["expected_present"]]
        summary_by_k[str(k)] = {
            **_aggregate(present, ("direct_precision", "direct", "risk", "nodes", "chars")),
            "absent_query_nodes": sum(row["nodes"] for row in absent),
            "absent_query_risk": sum(row["risk"] for row in absent),
        }

    return {
        "rubric": "direct-name lower bound; indirect non equivale automaticamente a rumore",
        "query_domain_policy": "generale fisso; isola embedding/ranking dalla variabilita' del classificatore",
        "memory_count_checked_for_mutation": len(all_ids),
        "selected_ids": len(selected_ids),
        "lifecycle_mutations": 0,
        "summary_by_k": summary_by_k,
        "probes": probe_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--panel",
        choices=("all", "locomo", "personal"),
        default="all",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    embedder = Embedder()
    embedder.load()
    report: dict[str, Any] = {
        "schema": "euri_retrieval_depth_probe_v1",
        "k_values": list(K_VALUES),
        "read_only_personal": True,
    }
    if args.panel in {"all", "locomo"}:
        report["locomo"] = run_locomo(embedder)
    if args.panel in {"all", "personal"}:
        report["personal"] = run_personal(embedder)
    report["elapsed_s"] = round(time.perf_counter() - started, 3)

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
