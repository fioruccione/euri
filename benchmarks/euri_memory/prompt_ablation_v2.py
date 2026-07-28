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
    return f"{conversation}__r{replica}__{question_id}"


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
    return {"answerable": ans, "supporting_fragments": seen}


# --------------------------------------------------------------------------- #
# Messaggi — firma SENZA gold/evidence (punto 9)
# --------------------------------------------------------------------------- #
def build_messages(family: str, *, context_text: str, question: str,
                   stage: str = "single", selected_fragments: str | None = None) -> list[dict]:
    def user(ctx):
        return f"Contesto di memoria:\n{ctx}\n\nDomanda: {question}"

    if family in _SYSTEM_BY_FAMILY:
        return [{"role": "system", "content": _SYSTEM_BY_FAMILY[family]},
                {"role": "user", "content": user(context_text)}]
    if family == "two_stage":
        if stage == "selector":
            return [{"role": "system", "content": PROMPT_SELECTOR},
                    {"role": "user", "content": user(numbered_context(context_text))}]
        if stage == "answer":
            return [{"role": "system", "content": PROMPT_TWO_STAGE_ANSWER},
                    {"role": "user", "content": f"Frammenti:\n{selected_fragments or ''}\n\nDomanda: {question}"}]
    raise AblationError(f"famiglia/stage non valido: {family}/{stage}")


def messages_payload_sha256(messages: list[dict]) -> str:
    return _sha(json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def call_metadata(*, arm: Arm, stage: str, context_text: str, cid: str, question_id: str,
                  localization_sha256: str, messages: list[dict], num_predict: int,
                  seed: int, model: str, model_digest: str | None) -> dict:
    system = messages[0]["content"] if messages else ""
    return {
        "case_id": cid, "arm": arm.name, "stage": stage, "question_id": question_id,
        "localization_sha256": localization_sha256,
        "context_sha256": _sha(context_text),
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
                             validation_runs_dir: Path) -> dict:
    """Manifest di ESECUZIONE (non tracciato, in audit_output): firma con l'HEAD
    corrente e lega corpus, localizzazione e gli SHA dei 10 report census.
    """

    verify_manifest(case_manifest)
    report_sha = {p.name: sha256_file(p)
                  for p in sorted(Path(validation_runs_dir).glob("*.json"))}
    if len(report_sha) != 10:
        raise AblationError(f"attesi 10 report census, trovati {len(report_sha)}")
    execution = {
        "schema_version": 2, "experiment": EXPERIMENT_ID, "stage": "execution",
        "case_manifest_sha256": case_manifest["manifest_sha256"],
        "production_baseline_commit": PRODUCTION_BASELINE_COMMIT,
        "git_commit": experimental_code_commit,
        "corpus": {"path": str(Path(corpus_path).resolve()), "sha256": sha256_file(corpus_path)},
        "localization": {"path": str(Path(localization_path).resolve()),
                         "sha256": sha256_file(localization_path)},
        "census_report_sha256": report_sha,
        "arms": list(ARM_NAMES),
        "cases": [row for rows in case_manifest["strata"].values() for row in rows],
    }
    execution["manifest_sha256"] = manifest_digest(execution)
    return execution


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


def reconstruct_one(report: dict, case, question_id: str) -> str:
    """Ricostruisce SOLO il contesto dual (APPEND) di UNA domanda, byte-esatto.

    Diversamente da ``_reconstruct_contexts`` (che processa l'intero report e
    aborta alla prima divergenza), qui isoliamo la singola domanda target: una
    divergenza su una domanda NON target non deve bloccare le altre. Solleva
    ``AblationError`` se la base o il final non combaciano con lo SHA salvato.
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
    base = build_rag_context(questions[question_id].text, _FrozenBaseMemory(docs),
                             mode="search", touch=False).text
    expected_base = rag[question_id]["metadata"]["base_sha256"]
    if _sha(base) != expected_base:
        raise AblationError(f"{question_id}: base non byte-esatta ({_sha(base)} != {expected_base})")
    comp = dual[question_id]["metadata"]["composition"]
    render_turn = build_turn_renderer(case)
    rendered = [render_turn(str(t)) for t in (comp.get("added_turn_ids") or [])]
    final = base + render_additions_block(rendered)
    if _sha(final) != comp["final_sha256"]:
        raise AblationError(f"{question_id}: final non byte-esatto")
    return final


def load_reconstructed_context(report: dict, case, question_id: str) -> str:
    return reconstruct_one(report, case, question_id)


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
                ctx = reconstruct_one(report, loaded, c["question_id"])  # SHA verificato per-domanda
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
                 model: str = "gemma4:26b", model_digest: str | None = None) -> dict:
    verify_manifest(execution_manifest)
    if execution_manifest.get("stage") != "execution":
        raise AblationError("run richiede il manifest di ESECUZIONE")
    assert_no_personal_redis()
    assert_capture_dir_under_audit(capture_dir)

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
    if execute and (not model or not model_digest):
        raise AblationError("modello e digest devono essere non nulli per l'esecuzione")

    identity = run_identity(execution_manifest)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    existing = output_dir / "manifest.json"
    if existing.exists():
        prev = json.loads(existing.read_text(encoding="utf-8"))
        if run_identity(prev) != identity:
            raise AblationError("output-dir legata a un'altra identità: fail-closed")
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
            ctx = load_reconstructed_context(report, loaded, c["question_id"])
            qtext = questions[c["question_id"]].text
            seed = int(c["answer_seed"])
            arm_records = []
            for arm_name in c["arm_order"]:
                arm = ARM_BY_NAME[arm_name]
                rec = _run_arm(arm, ctx, qtext, cid, c["question_id"], seed,
                               execution_manifest["localization"]["sha256"], execute, chat_fn,
                               model, model_digest, capture_dir, c["replica"])
                arm_records.append(rec)
                planned += 1 + arm.selector_calls
            (runs_dir / f"{cid}.json").write_text(json.dumps(
                {"case_id": cid, "question_id": c["question_id"], "replica": c["replica"],
                 "conversation": conv, "stratum": c.get("stratum"),
                 "arm_order": c["arm_order"], "arms": arm_records},
                indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            done.add(cid)
            checkpoint_path.write_text(json.dumps(
                {"identity": identity, "done": sorted(done)}, ensure_ascii=False), encoding="utf-8")

    return {"mode": "run" if execute else "prepared_no_model",
            "cases": len(_cases(execution_manifest)), "planned_calls": planned}


def _load_and_revalidate_checkpoint(path: Path, manifest: dict, identity: dict, runs_dir: Path) -> dict:
    if not path.is_file():
        return {"done": []}
    cp = json.loads(path.read_text(encoding="utf-8"))
    rec = cp.get("identity") or {}
    if any(rec.get(k) is None for k in ("manifest_sha256", "corpus_sha256", "git_commit")):
        raise AblationError("checkpoint senza identity completa")
    assert_same_identity(rec, identity, context="resume ablation")
    expected_ids = {c["case_id"] for c in _cases(manifest)}
    revalidated = []
    for cid in cp.get("done", []):
        if cid not in expected_ids:
            raise AblationError(f"checkpoint con case_id estraneo: {cid}")
        rp = runs_dir / f"{cid}.json"
        if not rp.is_file():
            raise AblationError(f"report mancante per {cid}")
        try:
            rr = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AblationError(f"report corrotto per {cid}: {exc}") from exc
        if rr.get("case_id") != cid or sorted(a["arm"] for a in rr["arms"]) != sorted(ARM_NAMES):
            raise AblationError(f"report {cid} non valido")
        revalidated.append(cid)
    return {"done": revalidated}


def _run_arm(arm, ctx, question, cid, qid, seed, loc_sha, execute, chat_fn,
             model, model_digest, capture_dir, replica) -> dict:
    if arm.family == "two_stage":
        return _run_two_stage(arm, ctx, question, cid, qid, seed, loc_sha, execute,
                              chat_fn, model, model_digest, capture_dir, replica)
    msgs = build_messages(arm.family, context_text=ctx, question=question)
    meta = call_metadata(arm=arm, stage="single", context_text=ctx, cid=cid, question_id=qid,
                         localization_sha256=loc_sha, messages=msgs, num_predict=arm.num_predict,
                         seed=seed, model=model, model_digest=model_digest)
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
                   model, model_digest, capture_dir, replica) -> dict:
    n_frag = len(fragments(ctx))
    sel_msgs = build_messages("two_stage", context_text=ctx, question=question, stage="selector")
    sel_meta = call_metadata(arm=arm, stage="selector", context_text=numbered_context(ctx),
                             cid=cid, question_id=qid, localization_sha256=loc_sha, messages=sel_msgs,
                             num_predict=NUM_PREDICT_SELECTOR, seed=seed, model=model, model_digest=model_digest)
    raw, parsed, indices, selected, answer = None, None, [], "", None
    ans_meta, latency, calls = None, 0.0, 0
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
                                      stage="answer", selected_fragments=selected)
            ans_meta = call_metadata(arm=arm, stage="answer", context_text=selected, cid=cid,
                                     question_id=qid, localization_sha256=loc_sha, messages=ans_msgs,
                                     num_predict=arm.num_predict, seed=seed, model=model, model_digest=model_digest)
            answer = _content(chat_fn(model=model, messages=ans_msgs,
                                      options={"temperature": 0, "num_predict": arm.num_predict, "seed": seed},
                                      think=False))
            calls += 1
        else:
            answer = "Non lo so."  # fail-closed
        latency = round(time.perf_counter() - t, 3)
    _capture(capture_dir, cid, arm.name, replica, {"selector_messages": sel_msgs, "context": ctx})
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


def analyze(*, output_runs: Path, gold_lookup: dict, validation_root: Path, execution_manifest: dict) -> dict:
    per = defaultdict(lambda: defaultdict(list))     # (stratum,arm)->metric->[]
    by_cat = defaultdict(lambda: defaultdict(list))  # (category,arm)->f1
    by_conv = defaultdict(lambda: defaultdict(list))
    answers = defaultdict(dict)                        # arm -> case_id -> answer
    a0 = {}
    for f in sorted(Path(output_runs).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        cid, qid = rec["case_id"], rec["question_id"]
        g = gold_lookup.get(qid, {})
        stratum = rec.get("stratum", "unknown")
        for ar in rec["arms"]:
            arm, a = ar["arm"], ar.get("answer")
            answers[arm][cid] = a
            if arm == "A0":
                a0[cid] = a
            if a is None:
                continue
            if g.get("answerable"):
                f1 = token_f1(g.get("answer"), a)
                per[(stratum, arm)]["token_f1"].append(f1)
                per[(stratum, arm)]["exact_match"].append(exact_match(g.get("answer"), a))
                per[(stratum, arm)]["false_abstention"].append(1.0 if is_abstention(a) else 0.0)
                by_cat[(g.get("category"), arm)]["token_f1"].append(f1)
                by_conv[(cid.split("__")[0], arm)]["token_f1"].append(f1)
            else:
                per[(stratum, arm)]["adversarial_correct"].append(1.0 if is_abstention(a) else 0.0)

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    metrics = defaultdict(dict)
    for (stratum, arm), m in per.items():
        metrics[stratum][arm] = {k: mean(v) for k, v in m.items()}

    # confronti appaiati per caso (arm vs A0) + changed/improved/worsened
    paired = {}
    for arm in ARM_NAMES:
        if arm == "A0":
            continue
        ch = imp = wor = 0
        for cid, a in answers[arm].items():
            a0a = a0.get(cid)
            if a is None or a0a is None:
                continue
            g = gold_lookup.get(cid.split("__", 2)[-1], {})
            if _norm(a) != _norm(a0a):
                ch += 1
                if g.get("answerable"):
                    d = token_f1(g.get("answer"), a) - token_f1(g.get("answer"), a0a)
                    imp += 1 if d > 1e-9 else 0
                    wor += 1 if d < -1e-9 else 0
        paired[arm] = {"changed_vs_a0": ch, "improved": imp, "worsened": wor}

    return {
        "experiment": EXPERIMENT_ID, "kind": "development",
        "manifest_sha256": execution_manifest.get("manifest_sha256"),
        "per_stratum_arm": {k: dict(v) for k, v in metrics.items()},
        "by_category_arm": {f"{c}|{a}": {"token_f1": mean(m["token_f1"])} for (c, a), m in by_cat.items()},
        "by_conversation_arm": {f"{c}|{a}": {"token_f1": mean(m["token_f1"])} for (c, a), m in by_conv.items()},
        "paired_vs_a0": paired,
        "a0_stability": a0_stability(fresh_a0=answers.get("A0", {}), validation_root=validation_root,
                                     execution_manifest=execution_manifest, gold_lookup=gold_lookup),
        "note": "token-F1 non è l'unico verdetto: vedi audit cieco. Development, N piccolo.",
    }


def a0_stability(*, fresh_a0: dict, validation_root: Path, execution_manifest: dict, gold_lookup: dict) -> dict:
    """Vecchia A0 (dual del census, stesso seed) vs A0 fresca."""

    old = {}
    for (conv, rep) in {(c["conversation"], c["replica"]) for c in _cases(execution_manifest)}:
        r = _census_report(validation_root / "runs", conv, rep)
        dual = {x["question_id"]: x for x in {a["arm"]: a for a in r["arms"]}["dual_channel"]["results"]}
        for c in _cases(execution_manifest):
            if c["conversation"] == conv and c["replica"] == rep:
                old[c["case_id"]] = dual.get(c["question_id"], {}).get("answer")
    ids = [c for c in fresh_a0 if c in old]
    identical = sum(1 for c in ids if _norm(fresh_a0[c]) == _norm(old[c]))
    df1 = da = []
    for c in ids:
        qid = c.split("__", 2)[-1]
        g = gold_lookup.get(qid, {})
        if g.get("answerable"):
            df1.append(token_f1(g.get("answer"), fresh_a0[c]) - token_f1(g.get("answer"), old[c]))
    return {
        "n": len(ids), "same_seed": True,
        "identical_pct": round(100 * identical / len(ids), 1) if ids else None,
        "mean_delta_f1_fresh_minus_old": round(sum(df1) / len(df1), 4) if df1 else None,
        "divergent_case_ids": sorted(c for c in ids if _norm(fresh_a0[c]) != _norm(old[c])),
        "note": "stesso seed per-caso: differenze = pura instabilità generativa nel tempo.",
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
