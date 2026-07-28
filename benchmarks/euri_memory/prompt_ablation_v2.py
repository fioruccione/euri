"""Ablation DEVELOPMENT v2: strict/balanced × (160 no-think, 2000 no-think,
2000 think) + two-stage. Percorso 3: ricostruzione byte-esatta, cattura futura.

Separa QUATTRO confondenti trattati simmetricamente: prompt di astensione,
distrattori, thinking (isolato dal budget 2000), sottostima del token-F1.

Nessun modello viene invocato in preparazione: la generazione è gated da
``execute=True``. Nessun Redis personale, nessun retrieval nuovo.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory.integrity import (
    IntegrityError,
    assert_corpus_matches,
    assert_head_matches_manifest,
    assert_same_identity,
    assert_worktree_clean,
    run_identity,
    sha256_file,
)


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
EXPERIMENT_ID = "euri_prompt_ablation_v2"
# Baseline di produzione da cui derivano census e casi (NON il commit sperimentale).
PRODUCTION_BASELINE_COMMIT = "bac00a0"
SOURCE_VALIDATION = "dual_channel_validation_v1_seed396895560"
AUDIT_ROOT = REPO_ROOT / "audit_output"


class AblationError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Prompt CONGELATI + SHA-256
# --------------------------------------------------------------------------- #
PROMPT_STRICT = """\
Stai rispondendo a un benchmark sulla memoria conversazionale a lungo termine.
Usa soltanto il contesto di memoria fornito. Non usare conoscenze esterne e non
dedurre la risposta dalla formulazione della domanda. Se il contesto non
supporta una risposta, rispondi esattamente: Non lo so.
Rispondi in italiano, in modo conciso e senza spiegazioni."""

PROMPT_BALANCED = """\
Stai rispondendo a un benchmark sulla memoria conversazionale a lungo termine.
Usa soltanto il contesto di memoria fornito. Non usare conoscenze esterne e non
dedurre la risposta dalla formulazione della domanda.
Se il contesto contiene direttamente, o tramite una chiara parafrasi, le
informazioni necessarie, rispondi usando quelle informazioni anche se sono
presenti elementi irrilevanti. La presenza di distrattori non rende il fatto non
supportato. Rispondi "Non lo so" soltanto quando l'informazione necessaria è
realmente assente dal contesto.
Rispondi in italiano, in modo conciso e senza spiegazioni."""

PROMPT_SELECTOR = """\
Sei un selettore di evidenza per un benchmark di memoria. Ricevi un contesto in
cui ogni frammento è numerato come [N]. NON rispondere alla domanda.
Restituisci SOLTANTO un oggetto JSON valido con esattamente:
- "answerable": un booleano true oppure false — true se il contesto contiene,
  direttamente o tramite una chiara parafrasi, l'informazione necessaria;
- "supporting_fragments": lista di indici interi UNICI dei soli frammenti che
  supportano direttamente la risposta (lista vuota se answerable è false).
Non usare conoscenze esterne e non dedurre dalla domanda. Nessun testo fuori dal JSON."""

PROMPT_TWO_STAGE_ANSWER = """\
Rispondi alla domanda usando ESCLUSIVAMENTE i frammenti forniti. Non usare
conoscenze esterne. Se i frammenti non contengono l'informazione necessaria,
rispondi esattamente: Non lo so.
Rispondi in italiano, in modo conciso e senza spiegazioni."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_sha256() -> dict[str, str]:
    return {
        "strict": _sha(PROMPT_STRICT),
        "balanced": _sha(PROMPT_BALANCED),
        "two_stage_selector": _sha(PROMPT_SELECTOR),
        "two_stage_answer": _sha(PROMPT_TWO_STAGE_ANSWER),
    }


_SYSTEM_BY_FAMILY = {"strict": PROMPT_STRICT, "balanced": PROMPT_BALANCED}


# --------------------------------------------------------------------------- #
# Sette arm: strict/balanced × {160 no-think, 2000 no-think, 2000 think} + C0
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Arm:
    name: str
    family: str          # strict | balanced | two_stage
    think: bool
    num_predict: int
    selector_calls: int = 0

    @property
    def factor(self) -> str:
        if self.family == "two_stage":
            return "two_stage/no-think"
        return f"{self.family}/{'think' if self.think else 'no-think'}/{self.num_predict}"


ANSWER_ARMS: tuple[Arm, ...] = (
    Arm("A0", "strict", False, 160),
    Arm("A2", "strict", False, 2000),   # controllo budget (isola il budget)
    Arm("A1", "strict", True, 2000),    # think (isolato: vs A2 stesso budget)
    Arm("B0", "balanced", False, 160),
    Arm("B2", "balanced", False, 2000),
    Arm("B1", "balanced", True, 2000),
    Arm("C0", "two_stage", False, 160, selector_calls=1),
)
ARM_NAMES = tuple(a.name for a in ANSWER_ARMS)
ARM_BY_NAME = {a.name: a for a in ANSWER_ARMS}
NUM_PREDICT_SELECTOR = 400
REUSE_PREVIOUS_A0 = False


def case_id(conversation: str, replica: str, question_id: str) -> str:
    """Canonico come documentato: conv-41__r0__q123 (la conversazione UNA volta).

    Il ``question_id`` LoCoMo è "conv-41:q123": nel case_id si usa la sola parte
    dopo i due punti, così la conversazione non è duplicata. Il ``question_id``
    pieno resta nella mappa del manifest (mai ricavato con split del case_id).
    """

    short = str(question_id).split(":")[-1]
    return f"{conversation}__r{replica}__{short}"


def counterbalanced_order(case_index: int) -> list[str]:
    """Rotazione deterministica: ogni arm è primo lo stesso numero di volte."""

    k = case_index % len(ARM_NAMES)
    return list(ARM_NAMES[k:] + ARM_NAMES[:k])


# --------------------------------------------------------------------------- #
# Frammentazione + selettore FAIL-CLOSED (punto 5)
# --------------------------------------------------------------------------- #
def fragments(context_text: str) -> list[str]:
    return [line for line in context_text.split("\n") if line.strip()]


def numbered_context(context_text: str) -> str:
    return "\n".join(f"[{i}] {frag}" for i, frag in enumerate(fragments(context_text), 1))


def select_by_indices(context_text: str, indices: list[int]) -> str:
    frags = fragments(context_text)
    return "\n".join(frags[i - 1] for i in indices if 1 <= i <= len(frags))


def parse_selector_strict(raw: str, n_fragments: int) -> dict | None:
    """JSON stretto e fail-closed. None => astensione (nessuna evidenza valida).

    Richiede: answerable booleano REALE; supporting_fragments lista di interi
    UNICI e IN-RANGE. Qualsiasi violazione → None (fail-closed).
    """

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    ans = data.get("answerable")
    if not isinstance(ans, bool):
        return None
    frags = data.get("supporting_fragments", [])
    if not isinstance(frags, list):
        return None
    seen: list[int] = []
    for x in frags:
        if not isinstance(x, int) or isinstance(x, bool):
            return None
        if x in seen or not (1 <= x <= n_fragments):
            return None
        seen.append(x)
    # answerable=false DEVE avere frammenti vuoti (coerenza, fail-closed).
    if not ans and seen:
        return None
    return {"answerable": ans, "supporting_fragments": seen}


# --------------------------------------------------------------------------- #
# Messaggi — firma SENZA gold/evidence (punto 9)
# --------------------------------------------------------------------------- #
def build_messages(family: str, *, context_text: str, question: str, speakers: tuple[str, str],
                   stage: str = "single", selected_fragments: str | None = None) -> list[dict]:
    """Messaggi per il modello. Accetta gli speaker (mai gold/evidence) e usa
    ESATTAMENTE il wrapper originale di ``dual_channel_worker._user_prompt``, così
    A0/A1/A2 e B0/B1/B2 sono byte-identici al prompt census. Anche C0 riceve
    coerentemente i partecipanti.
    """

    s0, s1 = speakers[0], speakers[1]

    def wrap(label: str, body: str) -> str:
        return (f"Partecipanti: {s0} e {s1}.\n\n"
                f"{label}:\n{body or '(nessuna memoria rilevante)'}\n\n"
                f"Domanda: {question}")

    if family in _SYSTEM_BY_FAMILY:
        return [{"role": "system", "content": _SYSTEM_BY_FAMILY[family]},
                {"role": "user", "content": wrap("Contesto di memoria", context_text)}]
    if family == "two_stage":
        if stage == "selector":
            return [{"role": "system", "content": PROMPT_SELECTOR},
                    {"role": "user", "content": wrap("Contesto di memoria", numbered_context(context_text))}]
        if stage == "answer":
            return [{"role": "system", "content": PROMPT_TWO_STAGE_ANSWER},
                    {"role": "user", "content": wrap("Frammenti", selected_fragments or "")}]
    raise AblationError(f"famiglia/stage non valido: {family}/{stage}")


def messages_payload_sha256(messages: list[dict]) -> str:
    return _sha(json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def call_metadata(*, arm: Arm, stage: str, context_text: str, cid: str, question_id: str,
                  localization_sha256: str, messages: list[dict], num_predict: int,
                  seed: int, model: str, model_digest: str | None,
                  context_reference_at: str | None = None) -> dict:
    system = messages[0]["content"] if messages else ""
    return {
        "case_id": cid, "arm": arm.name, "stage": stage, "question_id": question_id,
        "localization_sha256": localization_sha256,
        "context_sha256": _sha(context_text),
        "context_reference_at": context_reference_at,
        "system_prompt_sha256": _sha(system),
        "messages_payload_sha256": messages_payload_sha256(messages),
        "answer_seed": seed, "model": model, "model_digest": model_digest,
        "temperature": 0, "num_predict": num_predict, "think": arm.think,
    }


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def manifest_digest(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return _sha(_canonical(body))


def verify_manifest(manifest: dict) -> None:
    rec = manifest.get("manifest_sha256")
    if not rec or rec != manifest_digest(manifest):
        raise AblationError("manifest_sha256 non corrisponde")


def _census_report(validation_runs_dir: Path, conv: str, rep: str) -> dict:
    return json.loads((Path(validation_runs_dir) / f"{conv}__r{rep}.json").read_text(encoding="utf-8"))


def build_case_manifest(*, validation_runs_dir: Path) -> dict:
    """Case-manifest CONGELATO (committabile): case_id, seed per-caso dal census,
    ordine controbilanciato. Baseline = bac00a0. NESSUN commit sperimentale
    autoreferenziale, nessun gold. Firma con manifest_sha256 (hash di contenuto).
    """

    flip: list[tuple] = []
    ans_pool: dict = defaultdict(list)
    adv_pool: dict = defaultdict(list)
    seed_by_rep: dict = {}
    for f in sorted(Path(validation_runs_dir).glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        conv = r["dataset"]["sample_id"]
        rep = r["run"]["run_label"].split("__r")[-1]
        seed_by_rep[(conv, rep)] = int(r["run"]["answer_seed"])
        by = {a["arm"]: a for a in r["arms"]}
        ri = {i["question_id"]: i for i in by["rag_only"]["scoring"]["items"]}
        di = {i["question_id"]: i for i in by["dual_channel"]["scoring"]["items"]}
        for qid, it in ri.items():
            cat = it.get("category")
            if it.get("answerable"):
                if it.get("evidence_hit") is False and di[qid].get("evidence_hit") is True:
                    flip.append((conv, rep, cat, qid))
                else:
                    ans_pool[(conv, rep, cat)].append(qid)
            else:
                adv_pool[(conv, rep)].append(qid)

    def pair(key_fn, pool):
        used, out = set(), []
        for conv, rep, cat, _q in sorted(flip):
            for cand in sorted(pool.get(key_fn(conv, rep, cat), [])):
                if cand not in used:
                    used.add(cand)
                    out.append((conv, rep, cat, cand))
                    break
        return out

    raw = {
        "A_evidence_flip": sorted(flip),
        "B_answerable_control": pair(lambda c, r, cat: (c, r, cat), ans_pool),
        "C_adversarial": pair(lambda c, r, cat: (c, r), adv_pool),
    }
    # Assegna case_id + seed + ordine su TUTTI i casi, in ordine deterministico.
    all_rows = [(stratum, c, r, cat, q) for stratum, rows in raw.items() for (c, r, cat, q) in rows]
    all_rows.sort(key=lambda x: (x[1], x[2], x[4]))  # conv, replica, qid
    strata: dict = {k: [] for k in raw}
    for idx, (stratum, c, r, cat, q) in enumerate(all_rows):
        cid = case_id(c, r, q)
        strata[stratum].append({
            "case_id": cid, "stratum": stratum, "conversation": c, "replica": r,
            "question_id": q, "category": cat, "answer_seed": seed_by_rep[(c, r)],
            "arm_order": counterbalanced_order(idx),
        })

    manifest = {
        "schema_version": 2,
        "experiment": EXPERIMENT_ID, "experiment_version": "v2",
        "stage": "cases_frozen", "kind": "development",
        "note": "Development su LoCoMo interamente aperto; non validazione indipendente.",
        "production_baseline_commit": PRODUCTION_BASELINE_COMMIT,
        "source_validation": SOURCE_VALIDATION,
        "context_source": "reconstructed_byte_exact_from_census_artifacts",
        "arms": list(ARM_NAMES),
        "arm_factors": {a.name: a.factor for a in ANSWER_ARMS},
        "prompt_sha256": prompt_sha256(),
        "reuse_previous_a0": REUSE_PREVIOUS_A0,
        "seed_policy": "per-caso = run.answer_seed del report census della replica",
        "strata": strata,
    }
    counts = {k: len(v) for k, v in strata.items()}
    counts["total"] = sum(counts.values())
    counts["distinct_case_ids"] = len({row["case_id"] for rows in strata.values() for row in rows})
    counts["distinct_question_ids"] = len({row["question_id"] for rows in strata.values() for row in rows})
    manifest["strata_counts"] = counts
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def build_execution_manifest(case_manifest: dict, *, experimental_code_commit: str,
                             corpus_path: Path, localization_path: Path,
                             validation_runs_dir: Path, model: str, model_digest: str) -> dict:
    """Manifest di ESECUZIONE (non tracciato, in audit_output): firma con l'HEAD
    corrente e lega corpus, localizzazione, SHA dei 10 report census e
    **modello + digest congelati** (blocker 2).
    """

    verify_manifest(case_manifest)
    if not model or not model_digest:
        raise AblationError("modello e digest sono obbligatori nell'execution manifest")
    report_sha = {p.name: sha256_file(p)
                  for p in sorted(Path(validation_runs_dir).glob("*.json"))}
    if len(report_sha) != 10:
        raise AblationError(f"attesi 10 report census, trovati {len(report_sha)}")
    execution = {
        "schema_version": 2, "experiment": EXPERIMENT_ID, "stage": "execution",
        "case_manifest_sha256": case_manifest["manifest_sha256"],
        "production_baseline_commit": PRODUCTION_BASELINE_COMMIT,
        "git_commit": experimental_code_commit,
        "model": model, "model_digest": model_digest,
        "corpus": {"path": str(Path(corpus_path).resolve()), "sha256": sha256_file(corpus_path)},
        "localization": {"path": str(Path(localization_path).resolve()),
                         "sha256": sha256_file(localization_path)},
        "census_report_sha256": report_sha,
        "arms": list(ARM_NAMES),
        "cases": [row for rows in case_manifest["strata"].values() for row in rows],
    }
    execution["manifest_sha256"] = manifest_digest(execution)
    return execution


def ablation_identity(manifest: dict) -> dict:
    """Identità completa dell'ablation: include modello e digest congelati."""

    ident = dict(run_identity(manifest))
    ident["model"] = manifest.get("model")
    ident["model_digest"] = manifest.get("model_digest")
    return ident


def forecast(case_manifest: dict) -> dict:
    total = case_manifest["strata_counts"]["total"]
    answers = len(ANSWER_ARMS) * total
    selectors = sum(a.selector_calls for a in ANSWER_ARMS) * total
    return {
        "cases_total": total, "answer_arms": len(ANSWER_ARMS),
        "answer_generations": answers, "selector_calls_C0": selectors,
        "total_max_calls": answers + selectors, "cap_max_calls": 1032,
        "note": "129×7 arm + 129 selettori. Nessuna nuova ingestion/retrieval.",
    }


# --------------------------------------------------------------------------- #
# Guardie
# --------------------------------------------------------------------------- #
def assert_no_personal_redis() -> None:
    if os.environ.get("EURI_REDIS_PORT") == "6379":
        raise AblationError("Redis personale (porta 6379): l'ablation non deve usarlo")


def assert_context_matches(context_text: str, expected_sha256: str) -> None:
    if not context_text:
        raise AblationError("contesto assente: ablation impedita")
    if _sha(context_text) != expected_sha256:
        raise AblationError(f"contesto corrotto: {_sha(context_text)} != {expected_sha256}")


def assert_capture_dir_under_audit(capture_dir: Path) -> None:
    resolved = Path(capture_dir).resolve()
    if not str(resolved).startswith(str(AUDIT_ROOT.resolve()) + os.sep):
        raise AblationError(f"capture_dir deve stare sotto audit_output/: {resolved}")


def _cases(manifest: dict) -> list[dict]:
    if manifest.get("stage") == "execution":
        return list(manifest["cases"])
    return [row for rows in manifest["strata"].values() for row in rows]


import contextlib


@contextlib.contextmanager
def frozen_clock(created_at: float):
    """Congela ``core.rag_context.now`` al ``created_at`` del report census.

    build_rag_context produce etichette di recency dipendenti dal tempo: senza
    congelare il clock, la ricostruzione diverge in un giorno diverso dal census.
    Patch LOCALE all'harness; nessuna modifica di produzione persistita.
    """

    import config
    import core.rag_context as rc
    from datetime import datetime

    reference = datetime.fromtimestamp(float(created_at), tz=config.TIMEZONE)
    original = rc.now
    rc.now = lambda: reference
    try:
        yield reference
    finally:
        rc.now = original


def reconstruct_one(report: dict, case, question_id: str, *, freeze: bool = True) -> tuple[str, str]:
    """Ricostruisce il contesto dual (APPEND) di UNA domanda, byte-esatto.

    Con ``freeze=True`` (default) usa il clock congelato al ``created_at`` del
    report → byte-esatto. Con ``freeze=False`` usa il clock corrente (per la
    regressione: alcuni contesti divergono). Ritorna (final_text, reference_at_iso).
    Isola la singola domanda: una divergenza su una domanda NON target non blocca
    le altre. Solleva ``AblationError`` se base o final non combaciano con lo SHA.
    """

    from benchmarks.euri_memory.prompt_ablation import _FrozenBaseMemory, _raw_turn_document
    from benchmarks.euri_memory.dual_channel import render_additions_block
    from benchmarks.euri_memory.dual_channel_worker import build_turn_renderer
    from core.rag_context import build_rag_context

    by = {a["arm"]: a for a in report["arms"]}
    rag = {x["question_id"]: x for x in by["rag_only"]["results"]}
    dual = {x["question_id"]: x for x in by["dual_channel"]["results"]}
    turns = {t.turn_id: t for t in case.turns}
    questions = {q.question_id: q for q in case.questions}
    if question_id not in report["base_nodes_by_question"]:
        raise AblationError(f"contesto assente per {question_id}")
    docs = []
    for node in report["base_nodes_by_question"][question_id]:
        tid = node.get("benchmark_turn_id")
        if not tid or tid not in turns:
            raise AblationError(f"{question_id}: nodo base senza turno ({tid})")
        docs.append(_raw_turn_document(case.corpus(), turns[tid], []))

    ctx_manager = frozen_clock(report["created_at"]) if freeze else contextlib.nullcontext()
    with ctx_manager as reference:
        base = build_rag_context(questions[question_id].text, _FrozenBaseMemory(docs),
                                 mode="search", touch=False).text
    reference_iso = reference.isoformat() if freeze and reference is not None else None

    expected_base = rag[question_id]["metadata"]["base_sha256"]
    if _sha(base) != expected_base:
        raise AblationError(f"{question_id}: base non byte-esatta ({_sha(base)} != {expected_base})")
    comp = dual[question_id]["metadata"]["composition"]
    render_turn = build_turn_renderer(case)
    rendered = [render_turn(str(t)) for t in (comp.get("added_turn_ids") or [])]
    final = base + render_additions_block(rendered)
    if _sha(final) != comp["final_sha256"]:
        raise AblationError(f"{question_id}: final non byte-esatto")
    return final, reference_iso


def load_reconstructed_context(report: dict, case, question_id: str) -> tuple[str, str]:
    return reconstruct_one(report, case, question_id, freeze=True)


# --------------------------------------------------------------------------- #
# Dry-run integrale: materializza e rivalida TUTTI i 129 casi (nessun modello)
# --------------------------------------------------------------------------- #
def _corpus_path() -> Path:
    return ROOT / "data" / "locomo10.json"


def dry_run_materialize(*, case_manifest: dict, validation_root: Path) -> dict:
    from benchmarks.euri_memory.prompt_ablation import _load_case
    verify_manifest(case_manifest)
    cases = _cases(case_manifest)
    seen_cid: set[str] = set()
    per_conv = defaultdict(list)
    for c in cases:
        if c["case_id"] in seen_cid:
            raise AblationError(f"collisione case_id: {c['case_id']}")
        seen_cid.add(c["case_id"])
        per_conv[(c["conversation"], c["replica"])].append(c)

    materialized = 0
    non_reconstructible: list[dict] = []
    for (conv, rep), rows in sorted(per_conv.items()):
        report = _census_report(validation_root / "runs", conv, rep)
        loaded = _load_case(validation_root, conv, _corpus_path())
        if int(report["run"]["answer_seed"]) != rows[0]["answer_seed"]:
            raise AblationError(f"seed {conv}__r{rep} != census")
        for c in rows:
            assert sorted(c["arm_order"]) == sorted(ARM_NAMES), c["case_id"]
            try:
                ctx, _ref = reconstruct_one(report, loaded, c["question_id"], freeze=True)
                assert ctx
                materialized += 1
            except AblationError as exc:
                non_reconstructible.append({"case_id": c["case_id"], "reason": str(exc)[:80]})

    return {
        "mode": "dry_run_materialize", "no_model": True,
        "distinct_case_ids": len(seen_cid),
        "reports_expected": len(seen_cid),
        "materialized_and_verified": materialized,
        "non_reconstructible_count": len(non_reconstructible),
        "non_reconstructible": non_reconstructible,
        "distinct_question_ids": case_manifest["strata_counts"]["distinct_question_ids"],
        "duplicate_question_ids_across_replicas":
            case_manifest["strata_counts"]["total"] - case_manifest["strata_counts"]["distinct_question_ids"],
        "collisions": 0,
        "byte_exact_ok": len(non_reconstructible) == 0,
    }


# --------------------------------------------------------------------------- #
# Runner reale (gated execute=True; NON invocato in preparazione)
# --------------------------------------------------------------------------- #
def run_ablation(*, execution_manifest: dict, validation_root: Path, output_dir: Path,
                 capture_dir: Path, execute: bool = False, chat_fn: Callable | None = None,
                 model: str | None = None, model_digest: str | None = None) -> dict:
    verify_manifest(execution_manifest)
    if execution_manifest.get("stage") != "execution":
        raise AblationError("run richiede il manifest di ESECUZIONE")
    assert_no_personal_redis()
    assert_capture_dir_under_audit(capture_dir)

    # Modello e digest sono CONGELATI nel manifest (blocker 2): sono la verità.
    mani_model = execution_manifest.get("model")
    mani_digest = execution_manifest.get("model_digest")
    if not mani_model or not mani_digest:
        raise AblationError("execution manifest senza model/digest congelati")
    if model is not None and model != mani_model:
        raise AblationError(f"model {model!r} diverso dal manifest {mani_model!r}: fail-closed")
    if model_digest is not None and model_digest != mani_digest:
        raise AblationError(f"model_digest diverso dal manifest congelato: fail-closed")
    model, model_digest = mani_model, mani_digest

    # Integrità (punto 6)
    assert_corpus_matches(execution_manifest, _corpus_path())
    loc = execution_manifest["localization"]
    if sha256_file(loc["path"]) != loc["sha256"]:
        raise AblationError("artefatto di localizzazione diverso dal manifest")
    for name, sha in execution_manifest["census_report_sha256"].items():
        if sha256_file(validation_root / "runs" / name) != sha:
            raise AblationError(f"report census {name} diverso dallo SHA registrato")
    assert_head_matches_manifest(execution_manifest, REPO_ROOT)
    assert_worktree_clean(REPO_ROOT)

    identity = ablation_identity(execution_manifest)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    existing = output_dir / "manifest.json"
    if existing.exists():
        prev = json.loads(existing.read_text(encoding="utf-8"))
        try:
            verify_manifest(prev)
        except AblationError as exc:
            raise AblationError(f"manifest preesistente non integro: {exc}") from exc
        # Uguaglianza canonica COMPLETA, non solo run_identity (punto 2).
        if _canonical(prev) != _canonical(execution_manifest):
            raise AblationError("output-dir con manifest diverso (canonico): fail-closed")
    else:
        existing.write_text(json.dumps(execution_manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    if execute and chat_fn is None:
        from core.ollama_client import chat_client
        chat_fn = lambda **kw: chat_client.chat(**kw)  # noqa: E731

    from benchmarks.euri_memory.prompt_ablation import _load_case
    runs_dir = output_dir / "runs"; runs_dir.mkdir(exist_ok=True)
    checkpoint_path = output_dir / "checkpoint.json"
    checkpoint = _load_and_revalidate_checkpoint(checkpoint_path, execution_manifest, identity, runs_dir)
    done = set(checkpoint["done"])
    Path(capture_dir).mkdir(parents=True, exist_ok=True)

    per_conv = defaultdict(list)
    for c in _cases(execution_manifest):
        per_conv[(c["conversation"], c["replica"])].append(c)

    planned = 0
    for (conv, rep), rows in sorted(per_conv.items()):
        report = _census_report(validation_root / "runs", conv, rep)
        loaded = _load_case(validation_root, conv, _corpus_path())
        questions = {q.question_id: q for q in loaded.questions}
        for c in rows:
            cid = c["case_id"]
            if cid in done:
                continue
            ctx, ctx_ref = load_reconstructed_context(report, loaded, c["question_id"])
            qtext = questions[c["question_id"]].text
            seed = int(c["answer_seed"])
            arm_records = []
            for arm_name in c["arm_order"]:
                arm = ARM_BY_NAME[arm_name]
                rec = _run_arm(arm, ctx, qtext, cid, c["question_id"], seed,
                               execution_manifest["localization"]["sha256"], execute, chat_fn,
                               model, model_digest, capture_dir, c["replica"], ctx_ref, loaded.speakers)
                arm_records.append(rec)
                planned += 1 + arm.selector_calls
            case_report = {
                "case_id": cid,
                "question_id": c["question_id"],
                "replica": c["replica"],
                "conversation": conv,
                "stratum": c.get("stratum"),
                "arm_order": c["arm_order"],
                "manifest_sha256": execution_manifest["manifest_sha256"],
                "model": model,
                "model_digest": model_digest,
                "context_sha256": _sha(ctx),
                "context_reference_at": ctx_ref,
                "speakers": list(loaded.speakers),
                "arms": arm_records,
            }
            # Validazione ESATTA subito dopo la generazione, PRIMA del checkpoint
            # (blocker 3): metadati e context_sha256 byte-esatti contro il contesto ricostruito.
            if execute:
                problems = validate_case_report(case_report, c, execution_manifest,
                                                require_answers=True,
                                                dual_context_text=ctx,
                                                expected_context_reference_at=ctx_ref,
                                                question_text=qtext,
                                                speakers=loaded.speakers)
                if problems:
                    raise AblationError(f"report {cid} non valido subito dopo la generazione: "
                                        f"{'; '.join(problems)}")
            (runs_dir / f"{cid}.json").write_text(json.dumps(
                case_report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            done.add(cid)
            checkpoint_path.write_text(json.dumps(
                {"identity": identity, "done": sorted(done)}, ensure_ascii=False), encoding="utf-8")

    return {"mode": "run" if execute else "prepared_no_model",
            "cases": len(_cases(execution_manifest)), "planned_calls": planned}


def _expected_system_sha(arm: "Arm", stage: str) -> str:
    if stage == "selector":
        return _sha(PROMPT_SELECTOR)
    if stage == "answer":
        return _sha(PROMPT_TWO_STAGE_ANSWER)
    return _sha(_SYSTEM_BY_FAMILY[arm.family])


def _expected_num_predict(arm: "Arm", stage: str) -> int:
    return NUM_PREDICT_SELECTOR if stage == "selector" else arm.num_predict


def _expected_context_sha(arm: "Arm", stage: str, rec_arm: dict, dual_context_text: str) -> str:
    if stage == "selector":
        return _sha(numbered_context(dual_context_text))
    if stage == "answer":
        return _sha(select_by_indices(dual_context_text, rec_arm.get("selected_indices") or []))
    return _sha(dual_context_text)


def validate_case_report(rec: dict, case: dict, execution_manifest: dict, *,
                         require_answers: bool = True,
                         dual_context_text: str | None = None,
                         expected_context_reference_at: str | None = None,
                         question_text: str | None = None,
                         speakers: tuple[str, str] | None = None) -> list[str]:
    """Valida un report per-caso contro il manifest (punto 3): verifica i VALORI
    ESATTI di ogni metadato (case_id/question_id/arm/stage, seed, think,
    num_predict, model/digest, context_reference_at, system_prompt_sha256) e, se
    fornito ``dual_context_text``, anche il ``context_sha256`` byte-esatto per
    ogni stadio. Lista vuota = ok.
    """

    p: list[str] = []
    for field in ("case_id", "question_id", "conversation", "replica"):
        if rec.get(field) != case.get(field):
            p.append(f"{field} diverso")
    if rec.get("stratum") != case.get("stratum"):
        p.append("stratum diverso")
    if rec.get("arm_order") != case.get("arm_order"):
        p.append("arm_order diverso")
    if require_answers and rec.get("manifest_sha256") != execution_manifest.get("manifest_sha256"):
        p.append("manifest_sha256 diverso")
    exp_model = execution_manifest.get("model")
    exp_digest = execution_manifest.get("model_digest")
    if require_answers:
        if rec.get("model") != exp_model:
            p.append("model top-level diverso")
        if rec.get("model_digest") != exp_digest:
            p.append("model_digest top-level diverso")
    if dual_context_text is not None:
        if rec.get("context_sha256") != _sha(dual_context_text):
            p.append("context_sha256 top-level diverso")
    elif require_answers and not rec.get("context_sha256"):
        p.append("context_sha256 top-level assente")
    if expected_context_reference_at is not None:
        if rec.get("context_reference_at") != expected_context_reference_at:
            p.append("context_reference_at top-level diverso")
    elif require_answers and not rec.get("context_reference_at"):
        p.append("context_reference_at top-level assente")
    if speakers is not None:
        if rec.get("speakers") != list(speakers):
            p.append("speakers top-level diversi")
    elif require_answers:
        recorded_speakers = rec.get("speakers")
        if not isinstance(recorded_speakers, list) or len(recorded_speakers) != 2:
            p.append("speakers top-level assenti")
    names = sorted(a.get("arm") for a in rec.get("arms", []))
    if names != sorted(ARM_NAMES):
        p.append(f"arm != 7 esatti ({names})")
        return p
    loc_sha = execution_manifest["localization"]["sha256"]
    for a in rec["arms"]:
        name = a.get("arm")
        arm = ARM_BY_NAME.get(name)
        if arm is None:
            p.append(f"{name} arm sconosciuto")
            continue
        if arm.family == "two_stage":
            staged = [(a.get("selector_metadata"), "selector")]
            if a.get("answer_metadata"):
                staged.append((a.get("answer_metadata"), "answer"))
        else:
            staged = [(a.get("metadata"), "single")]
        for meta, stage in staged:
            if not meta:
                p.append(f"{name}/{stage} metadata assente")
                continue
            if meta.get("case_id") != case.get("case_id"):
                p.append(f"{name}/{stage} case_id")
            if meta.get("question_id") != case.get("question_id"):
                p.append(f"{name}/{stage} question_id")
            if meta.get("arm") != name:
                p.append(f"{name}/{stage} arm")
            if meta.get("stage") != stage:
                p.append(f"{name}/{stage} stage")
            if meta.get("localization_sha256") != loc_sha:
                p.append(f"{name}/{stage} localization_sha")
            if int(meta.get("answer_seed", -1)) != int(case["answer_seed"]):
                p.append(f"{name}/{stage} answer_seed")
            if meta.get("think") != arm.think:
                p.append(f"{name}/{stage} think")
            if meta.get("temperature") != 0:
                p.append(f"{name}/{stage} temperature")
            if meta.get("num_predict") != _expected_num_predict(arm, stage):
                p.append(f"{name}/{stage} num_predict")
            if not meta.get("context_reference_at"):
                p.append(f"{name}/{stage} context_reference_at assente")
            elif (
                expected_context_reference_at is not None
                and meta.get("context_reference_at") != expected_context_reference_at
            ):
                p.append(f"{name}/{stage} context_reference_at diverso")
            if meta.get("system_prompt_sha256") != _expected_system_sha(arm, stage):
                p.append(f"{name}/{stage} system_prompt_sha256")
            if not meta.get("messages_payload_sha256"):
                p.append(f"{name}/{stage} manca messages_payload_sha256")
            elif dual_context_text is not None and question_text is not None and speakers is not None:
                selected = select_by_indices(
                    dual_context_text, a.get("selected_indices") or []
                )
                expected_messages = build_messages(
                    arm.family,
                    context_text=dual_context_text,
                    question=question_text,
                    speakers=speakers,
                    stage=stage,
                    selected_fragments=selected if stage == "answer" else None,
                )
                if meta.get("messages_payload_sha256") != messages_payload_sha256(
                    expected_messages
                ):
                    p.append(f"{name}/{stage} messages_payload_sha256")
            if dual_context_text is not None:
                if meta.get("context_sha256") != _expected_context_sha(arm, stage, a, dual_context_text):
                    p.append(f"{name}/{stage} context_sha256 non byte-esatto")
            elif not meta.get("context_sha256"):
                p.append(f"{name}/{stage} manca context_sha256")
            if require_answers:
                if not meta.get("model") or meta.get("model") != exp_model:
                    p.append(f"{name}/{stage} model != manifest")
                if not meta.get("model_digest") or meta.get("model_digest") != exp_digest:
                    p.append(f"{name}/{stage} model_digest != manifest")
        if require_answers and a.get("answer") is None:
            p.append(f"{name} risposta assente")
        if require_answers:
            expected_calls = (
                2 if arm.family == "two_stage" and a.get("answer_metadata")
                else 1
            )
            if int(a.get("calls", -1)) != expected_calls:
                p.append(f"{name} calls != {expected_calls}")
            try:
                latency = float(a.get("latency_s"))
            except (TypeError, ValueError):
                latency = -1.0
            if latency < 0:
                p.append(f"{name} latency_s invalida")
    return p


def _load_and_revalidate_checkpoint(path: Path, manifest: dict, identity: dict, runs_dir: Path) -> dict:
    if not path.is_file():
        return {"done": []}
    cp = json.loads(path.read_text(encoding="utf-8"))
    rec = cp.get("identity") or {}
    if any(rec.get(k) is None for k in ("manifest_sha256", "corpus_sha256", "git_commit",
                                        "model", "model_digest")):
        raise AblationError("checkpoint senza identity completa (incl. model/digest)")
    assert_same_identity(rec, identity, context="resume ablation")
    for field in ("model", "model_digest"):
        if rec.get(field) != identity.get(field):
            raise AblationError(f"resume ablation: {field} del checkpoint diverso dal manifest")
    cases = {c["case_id"]: c for c in _cases(manifest)}
    seen = set()
    revalidated = []
    for cid in cp.get("done", []):
        if cid not in cases:
            raise AblationError(f"checkpoint con case_id estraneo: {cid}")
        if cid in seen:
            raise AblationError(f"case_id duplicato nel checkpoint: {cid}")
        seen.add(cid)
        rp = runs_dir / f"{cid}.json"
        if not rp.is_file():
            raise AblationError(f"report mancante per {cid}")
        try:
            rr = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AblationError(f"report corrotto per {cid}: {exc}") from exc
        problems = validate_case_report(rr, cases[cid], manifest, require_answers=True)
        if problems:
            raise AblationError(f"report {cid} non valido: {'; '.join(problems)}")
        revalidated.append(cid)
    return {"done": revalidated}


def _run_arm(arm, ctx, question, cid, qid, seed, loc_sha, execute, chat_fn,
             model, model_digest, capture_dir, replica, ctx_ref, speakers) -> dict:
    if arm.family == "two_stage":
        return _run_two_stage(arm, ctx, question, cid, qid, seed, loc_sha, execute,
                              chat_fn, model, model_digest, capture_dir, replica, ctx_ref, speakers)
    msgs = build_messages(arm.family, context_text=ctx, question=question, speakers=speakers)
    meta = call_metadata(arm=arm, stage="single", context_text=ctx, cid=cid, question_id=qid,
                         localization_sha256=loc_sha, messages=msgs, num_predict=arm.num_predict,
                         seed=seed, model=model, model_digest=model_digest, context_reference_at=ctx_ref)
    answer, latency, calls = None, None, 0
    if execute:
        import time
        t = time.perf_counter()
        answer = _content(chat_fn(model=model, messages=msgs,
                                  options={"temperature": 0, "num_predict": arm.num_predict, "seed": seed},
                                  think=arm.think))
        latency, calls = round(time.perf_counter() - t, 3), 1
    _capture(capture_dir, cid, arm.name, replica, {"messages": msgs, "context": ctx})
    return {"arm": arm.name, "metadata": meta, "answer": answer, "latency_s": latency, "calls": calls}


def _run_two_stage(arm, ctx, question, cid, qid, seed, loc_sha, execute, chat_fn,
                   model, model_digest, capture_dir, replica, ctx_ref, speakers) -> dict:
    n_frag = len(fragments(ctx))
    sel_msgs = build_messages("two_stage", context_text=ctx, question=question, speakers=speakers, stage="selector")
    sel_meta = call_metadata(arm=arm, stage="selector", context_text=numbered_context(ctx),
                             cid=cid, question_id=qid, localization_sha256=loc_sha, messages=sel_msgs,
                             num_predict=NUM_PREDICT_SELECTOR, seed=seed, model=model,
                             model_digest=model_digest, context_reference_at=ctx_ref)
    raw, parsed, indices, selected, answer = None, None, [], "", None
    ans_meta, ans_msgs, latency, calls = None, None, 0.0, 0
    if execute:
        import time
        t = time.perf_counter()
        raw = _content(chat_fn(model=model, messages=sel_msgs, format="json",
                               options={"temperature": 0, "num_predict": NUM_PREDICT_SELECTOR, "seed": seed},
                               think=False))
        calls += 1
        parsed = parse_selector_strict(raw, n_frag)  # None => fail-closed
        if parsed and parsed["answerable"] and parsed["supporting_fragments"]:
            indices = parsed["supporting_fragments"]
            selected = select_by_indices(ctx, indices)
            ans_msgs = build_messages("two_stage", context_text=ctx, question=question,
                                      speakers=speakers, stage="answer", selected_fragments=selected)
            ans_meta = call_metadata(arm=arm, stage="answer", context_text=selected, cid=cid,
                                     question_id=qid, localization_sha256=loc_sha, messages=ans_msgs,
                                     num_predict=arm.num_predict, seed=seed, model=model,
                                     model_digest=model_digest, context_reference_at=ctx_ref)
            answer = _content(chat_fn(model=model, messages=ans_msgs,
                                      options={"temperature": 0, "num_predict": arm.num_predict, "seed": seed},
                                      think=False))
            calls += 1
        else:
            answer = "Non lo so."  # fail-closed
        latency = round(time.perf_counter() - t, 3)
    # Cattura C0: entrambi gli stadi (punto 6).
    _capture(capture_dir, cid, arm.name, replica, {
        "selector_messages": sel_msgs, "answer_messages": ans_msgs, "context": ctx,
        "selector_raw": raw, "selector_parsed": parsed,
        "selected_indices": indices, "selected_fragments": selected,
        "selector_metadata": sel_meta, "answer_metadata": ans_meta})
    return {"arm": arm.name, "selector_metadata": sel_meta, "answer_metadata": ans_meta,
            "selector_raw": raw, "selector_parsed": parsed, "selected_indices": indices,
            "selected_fragments_sha256": _sha(selected) if selected else None,
            "answer": answer, "latency_s": latency, "calls": calls}


def _capture(capture_dir: Path, cid: str, arm: str, replica: str, payload: dict) -> None:
    (Path(capture_dir) / f"{cid}__{arm}.json").write_text(
        json.dumps({"case_id": cid, "arm": arm, "replica": replica, "run_label": cid.rsplit("__", 1)[0], **payload},
                   ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _content(resp: Any) -> str:
    msg = getattr(resp, "message", None)
    if msg is None and isinstance(resp, dict):
        msg = resp.get("message")
    c = getattr(msg, "content", None)
    if c is None and isinstance(msg, dict):
        c = msg.get("content")
    return str(c or "").strip()


# --------------------------------------------------------------------------- #
# Metriche + scoring (punto 10)
# --------------------------------------------------------------------------- #
_TOK = re.compile(r"\w+", re.UNICODE)
_ABST = re.compile(r"non lo so|non ci sono|non (?:è|e) (?:indicato|menzionato|specificato)|sconosciuto", re.I)


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _norm_tok(s):
    return _TOK.findall(unicodedata.normalize("NFKC", str(s or "")).casefold())


def token_f1(gold, pred):
    g, p = _norm_tok(gold), _norm_tok(pred)
    if not g or not p:
        return float(g == p)
    ov = sum((Counter(g) & Counter(p)).values())
    return 0.0 if not ov else 2 * (ov / len(p)) * (ov / len(g)) / ((ov / len(p)) + (ov / len(g)))


def exact_match(gold, pred):
    return 1.0 if _norm_tok(gold) == _norm_tok(pred) else 0.0


def is_abstention(a):
    return (not _norm(a)) or bool(_ABST.search(a or ""))


def case_meta_map(manifest: dict) -> dict[str, dict]:
    """case_id -> {question_id, conversation, replica, stratum}. MAI split del cid."""

    out = {}
    for c in _cases(manifest):
        out[c["case_id"]] = {"question_id": c["question_id"], "conversation": c["conversation"],
                             "replica": c["replica"], "stratum": c.get("stratum")}
    return out


def _assert_exact_coverage(output_runs: Path, manifest: dict) -> dict[str, dict]:
    meta = case_meta_map(manifest)
    seen = {}
    for f in sorted(Path(output_runs).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        cid = rec.get("case_id")
        if cid not in meta:
            raise AblationError(f"report estraneo: {cid}")
        if cid in seen:
            raise AblationError(f"report duplicato: {cid}")
        names = sorted(a.get("arm") for a in rec.get("arms", []))
        if names != sorted(ARM_NAMES):
            raise AblationError(f"{cid}: arm != 7 esatti ({names})")
        seen[cid] = rec
    missing = set(meta) - set(seen)
    if missing:
        raise AblationError(f"report mancanti: {sorted(missing)[:5]}… ({len(missing)})")
    return seen


def analyze(*, output_runs: Path, gold_lookup: dict, validation_root: Path, execution_manifest: dict) -> dict:
    meta = case_meta_map(execution_manifest)
    reports = _assert_exact_coverage(output_runs, execution_manifest)  # 129 esatti, 7 arm

    # Blocker 4: valida i VALORI ESATTI di tutti i 129 report (non solo il conteggio).
    cases_by_id = {c["case_id"]: c for c in _cases(execution_manifest)}
    for cid, rec in reports.items():
        problems = validate_case_report(rec, cases_by_id[cid], execution_manifest,
                                        require_answers=True)
        if problems:
            raise AblationError(f"analyze: report {cid} non valido: {'; '.join(problems)}")

    per = defaultdict(lambda: defaultdict(list))       # (stratum,arm)->metric
    glob = defaultdict(lambda: defaultdict(list))      # arm->metric (globale)
    by_cat = defaultdict(lambda: defaultdict(list))
    by_conv = defaultdict(lambda: defaultdict(list))
    cost = defaultdict(lambda: {"latency_s": 0.0, "calls": 0})
    answers = defaultdict(dict)                          # arm -> case_id -> answer
    for cid, rec in reports.items():
        m = meta[cid]
        g = gold_lookup.get(m["question_id"], {})
        stratum = m["stratum"]
        for ar in rec["arms"]:
            arm, a = ar["arm"], ar.get("answer")
            answers[arm][cid] = a
            cost[arm]["latency_s"] += float(ar.get("latency_s") or 0.0)
            cost[arm]["calls"] += int(ar.get("calls") or 0)
            if a is None:
                continue
            if g.get("answerable"):
                f1 = token_f1(g.get("answer"), a)
                for bucket in (per[(stratum, arm)], glob[arm]):
                    bucket["token_f1"].append(f1)
                    bucket["exact_match"].append(exact_match(g.get("answer"), a))
                    bucket["false_abstention"].append(1.0 if is_abstention(a) else 0.0)
                by_cat[(g.get("category"), arm)]["token_f1"].append(f1)
                by_conv[(m["conversation"], arm)]["token_f1"].append(f1)
            else:
                per[(stratum, arm)]["adversarial_correct"].append(1.0 if is_abstention(a) else 0.0)
                glob[arm]["adversarial_correct"].append(1.0 if is_abstention(a) else 0.0)

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    def gmean(arm, metric):
        return mean(glob[arm][metric])

    # changed/improved/worsened vs A0 (appaiato per caso)
    paired = {}
    for arm in ARM_NAMES:
        if arm == "A0":
            continue
        ch = imp = wor = 0
        for cid, a in answers[arm].items():
            a0a = answers["A0"].get(cid)
            if a is None or a0a is None:
                continue
            if _norm(a) != _norm(a0a):
                ch += 1
                g = gold_lookup.get(meta[cid]["question_id"], {})
                if g.get("answerable"):
                    d = token_f1(g.get("answer"), a) - token_f1(g.get("answer"), a0a)
                    imp += int(d > 1e-9)
                    wor += int(d < -1e-9)
        paired[arm] = {"changed_vs_a0": ch, "improved": imp, "worsened": wor}

    def contrast(hi, lo):
        return {"delta_token_f1": None if gmean(hi, "token_f1") is None or gmean(lo, "token_f1") is None
                else round(gmean(hi, "token_f1") - gmean(lo, "token_f1"), 4),
                "delta_adversarial": None if gmean(hi, "adversarial_correct") is None or gmean(lo, "adversarial_correct") is None
                else round(gmean(hi, "adversarial_correct") - gmean(lo, "adversarial_correct"), 4)}

    contrasts = {
        "budget_A2_minus_A0": contrast("A2", "A0"), "budget_B2_minus_B0": contrast("B2", "B0"),
        "thinking_A1_minus_A2": contrast("A1", "A2"), "thinking_B1_minus_B2": contrast("B1", "B2"),
        "prompt_B0_minus_A0": contrast("B0", "A0"),
        "two_stage_C0_minus_A0": contrast("C0", "A0"), "two_stage_C0_minus_B0": contrast("C0", "B0"),
    }

    metrics = defaultdict(dict)
    for (stratum, arm), mm in per.items():
        metrics[stratum][arm] = {k: mean(v) for k, v in mm.items()}

    return {
        "experiment": EXPERIMENT_ID, "kind": "development", "cases": len(reports),
        "manifest_sha256": execution_manifest.get("manifest_sha256"),
        "global_by_arm": {a: {k: gmean(a, k) for k in ("token_f1", "exact_match",
                          "false_abstention", "adversarial_correct")} for a in ARM_NAMES},
        "per_stratum_arm": {k: dict(v) for k, v in metrics.items()},
        "by_category_arm": {f"{c}|{a}": mean(mm["token_f1"]) for (c, a), mm in by_cat.items()},
        "by_conversation_arm": {f"{c}|{a}": mean(mm["token_f1"]) for (c, a), mm in by_conv.items()},
        "paired_vs_a0": paired,
        "preregistered_contrasts": contrasts,
        "cost_by_arm": {a: {"latency_s": round(cost[a]["latency_s"], 2), "calls": cost[a]["calls"]} for a in ARM_NAMES},
        "a0_stability": a0_stability(fresh_a0=answers.get("A0", {}), validation_root=validation_root,
                                     execution_manifest=execution_manifest, gold_lookup=gold_lookup),
        "note": "token-F1 non è l'unico verdetto: vedi audit cieco. Development, N piccolo.",
    }


def a0_stability(*, fresh_a0: dict, validation_root: Path, execution_manifest: dict, gold_lookup: dict) -> dict:
    """Vecchia A0 (dual del census, stesso seed) vs A0 fresca. Contesto e seed
    bloccati: le differenze misurano la stabilità generativa a parità di input."""

    meta = case_meta_map(execution_manifest)
    old = {}
    reps = {(c["conversation"], c["replica"]) for c in _cases(execution_manifest)}
    dual_by_rep = {}
    for (conv, rep) in reps:
        r = _census_report(validation_root / "runs", conv, rep)
        dual_by_rep[(conv, rep)] = {x["question_id"]: x.get("answer")
                                    for x in {a["arm"]: a for a in r["arms"]}["dual_channel"]["results"]}
    for cid, m in meta.items():
        old[cid] = dual_by_rep.get((m["conversation"], m["replica"]), {}).get(m["question_id"])

    ids = [c for c in fresh_a0 if c in old and old[c] is not None]
    identical = sum(1 for c in ids if _norm(fresh_a0[c]) == _norm(old[c]))
    df1: list[float] = []
    da: list[float] = []
    for c in ids:
        g = gold_lookup.get(meta[c]["question_id"], {})
        if g.get("answerable"):
            df1.append(token_f1(g.get("answer"), fresh_a0[c]) - token_f1(g.get("answer"), old[c]))
        else:
            da.append((1.0 if is_abstention(fresh_a0[c]) else 0.0) - (1.0 if is_abstention(old[c]) else 0.0))

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "n": len(ids), "context_and_seed_frozen": True,
        "identical_pct": round(100 * identical / len(ids), 1) if ids else None,
        "mean_delta_f1_fresh_minus_old": mean(df1),
        "mean_delta_abstention_fresh_minus_old": mean(da),
        "divergent_case_ids": sorted(c for c in ids if _norm(fresh_a0[c]) != _norm(old[c])),
        "note": "contesto e seed bloccati; se il digest storico del modello non è "
        "dimostrabile, le differenze sono stabilità generativa a parità di input, "
        "non necessariamente 'pura' instabilità.",
    }


# --------------------------------------------------------------------------- #
# Audit cieco con chiave arm↔codice SEPARATA (punto 11)
# --------------------------------------------------------------------------- #
def build_gold_lookup(localization_path: Path, corpus_path: Path) -> dict:
    """qid -> {answer, answerable, question, category}. Solo per scoring/audit,
    NON entra nei prompt di generazione o selezione."""

    from benchmarks.euri_memory.adapters import LoCoMoAdapter
    cat = {}
    for c in LoCoMoAdapter().load(Path(corpus_path)):
        for q in c.questions:
            cat[q.question_id] = q.category
    loc = json.loads(Path(localization_path).read_text(encoding="utf-8"))["conversations"]
    out = {}
    for sid, conv in loc.items():
        for qid, item in (conv.get("questions") or {}).items():
            out[qid] = {"answer": item.get("answer"),
                        "answerable": item.get("answer") is not None,
                        "question": item.get("text"), "category": cat.get(qid)}
    return out


def blind_audit_export(*, output_runs: Path, gold_lookup: dict, audit_seed: int = 20260728) -> dict:
    """Righe cieche (domanda, gold, risposta, replica) + chiave separata.

    Il codice NON deriva dal nome dell'arm (che sono pochi e indovinabili): è
    casuale, e la mappa codice→arm è restituita a parte, da conservare separata.
    """

    rng = random.Random(audit_seed)
    rows, key = [], {}
    for f in sorted(Path(output_runs).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        cid, qid, replica = rec["case_id"], rec["question_id"], rec["replica"]
        a0 = next((x.get("answer") for x in rec["arms"] if x["arm"] == "A0"), None)
        g = gold_lookup.get(qid, {})
        for ar in rec["arms"]:
            a = ar.get("answer")
            if a is None or _norm(a) == _norm(a0):
                continue
            code = f"{rng.randrange(16**8):08x}"
            key[code] = {"case_id": cid, "arm": ar["arm"]}
            rows.append({"code": code, "question_id": qid, "replica": replica,
                         "question": g.get("question"), "gold": g.get("answer"),
                         "answer": a, "human_label": None})
    rng.shuffle(rows)
    return {"rows": rows, "key": key,
            "labels": ["corretta", "parzialmente corretta", "errata",
                       "astensione corretta", "falsa astensione"],
            "note": "key va salvata SEPARATA dalle rows; nessun judge LLM come metrica ufficiale."}
