"""Ablation DEVELOPMENT v2: strict vs balanced vs two-stage (percorso 3).

Separa quattro possibili confondenti, trattati simmetricamente: prompt di
astensione, distrattori, thinking, sottostima del token-F1.

Percorso 3 (ricostruzione byte-esatta ORA, cattura per il futuro):
- riusa i contesti dual RICOSTRUITI byte-per-byte dagli artefatti del census
  (``prompt_ablation._reconstruct_contexts`` → build_rag_context + memoria-stub);
- verifica lo SHA-256 del contesto contro l'originale per OGNI caso (già dentro la
  ricostruzione: solleva se diverge);
- nessuna nuova ingestion, nessun nuovo retrieval, nessun Redis personale;
- rigenera A0 fresco insieme agli altri arm per la primaria; la vecchia A0 è solo
  un CONTROLLO di stabilità generativa nel tempo;
- gold, expected_answer ed evidence ID non entrano MAI nei messaggi al modello.

Cinque arm: A0 strict/no-think, A1 strict/think, B0 balanced/no-think,
B1 balanced/think, C0 two-stage/no-think. Budget max 774 chiamate
(645 risposte + 129 selettore C0).

Questo modulo NON esegue modelli in fase di preparazione: la generazione reale è
gated da ``execute=True`` e non viene invocata prima dell'audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
EXPERIMENT_ID = "euri_prompt_ablation_v2"
ANSWER_SEED = 42  # congelato: unico su tutti gli arm/casi, così varia solo prompt×think


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
Restituisci SOLTANTO un oggetto JSON con:
- "answerable": true oppure false — true se il contesto contiene, direttamente o
  tramite una chiara parafrasi, l'informazione necessaria alla domanda;
- "supporting_fragments": lista degli indici interi N dei soli frammenti che
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


@dataclass(frozen=True)
class Arm:
    name: str
    family: str      # strict | balanced | two_stage
    think: bool
    answer_calls: int          # generazioni di RISPOSTA per caso
    selector_calls: int = 0    # chiamate extra del selettore


ARMS: tuple[Arm, ...] = (
    Arm("A0", "strict", False, 1),
    Arm("A1", "strict", True, 1),
    Arm("B0", "balanced", False, 1),
    Arm("B1", "balanced", True, 1),
    Arm("C0", "two_stage", False, 1, selector_calls=1),
)
# La primaria rigenera TUTTI gli arm (A0 incluso): niente riuso della vecchia A0.
REUSE_PREVIOUS_A0 = False


# --------------------------------------------------------------------------- #
# Frammentazione deterministica (two-stage): indici ricostruibili
# --------------------------------------------------------------------------- #
def fragments(context_text: str) -> list[str]:
    return [line for line in context_text.split("\n") if line.strip()]


def numbered_context(context_text: str) -> str:
    return "\n".join(f"[{i}] {frag}" for i, frag in enumerate(fragments(context_text), 1))


def select_by_indices(context_text: str, indices: list[int]) -> tuple[str, list[int]]:
    """Frammenti selezionati + indici VALIDATI in-range (ricostruibili)."""

    frags = fragments(context_text)
    valid = [i for i in indices if 1 <= i <= len(frags)]
    return "\n".join(frags[i - 1] for i in valid), valid


def parse_selector(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return {"answerable": False, "supporting_fragments": []}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"answerable": False, "supporting_fragments": []}
    idx = [int(x) for x in (data.get("supporting_fragments") or []) if str(x).lstrip("-").isdigit()]
    return {"answerable": bool(data.get("answerable")), "supporting_fragments": idx}


# --------------------------------------------------------------------------- #
# Costruzione messaggi — firma SENZA gold/evidence: non possono entrare
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


def messages_forbidden_hits(messages: list[dict], forbidden: list[str]) -> list[str]:
    blob = "\n".join(str(m.get("content") or "") for m in messages)
    return [s for s in forbidden if s and s in blob]


def messages_payload_sha256(messages: list[dict]) -> str:
    return _sha(json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


# --------------------------------------------------------------------------- #
# Manifest CONGELATO dei casi (43/43/43) — solo ID, nessun gold
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


def build_case_manifest(*, validation_runs_dir: Path, git_commit: str,
                        corpus_sha256: str, localization_sha256: str) -> dict:
    flip: list[tuple] = []
    ans_pool: dict = defaultdict(list)
    adv_pool: dict = defaultdict(list)
    for f in sorted(Path(validation_runs_dir).glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        conv = r["dataset"]["sample_id"]
        rep = r["run"]["run_label"].split("__r")[-1]
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
                    out.append({"conversation": conv, "replica": rep, "question_id": cand})
                    break
        return out

    stratum_a = [{"conversation": c, "replica": r, "category": cat, "question_id": q}
                 for c, r, cat, q in sorted(flip)]
    manifest = {
        "schema_version": 1,
        "experiment": EXPERIMENT_ID,
        "experiment_version": "v2",
        "stage": "cases_frozen",
        "kind": "development",
        "note": "Development su LoCoMo ormai interamente aperto; non validazione indipendente.",
        "context_source": "reconstructed_byte_exact_from_census_artifacts",
        "context_verification": "sha256_per_case_vs_original_final_sha256",
        "git_commit": git_commit,
        "corpus_sha256": corpus_sha256,
        "localization_sha256": localization_sha256,
        "source_validation": "dual_channel_validation_v1_seed396895560",
        "arms": [a.name for a in ARMS],
        "prompt_sha256": prompt_sha256(),
        "answer_seed": ANSWER_SEED,
        "reuse_previous_a0": REUSE_PREVIOUS_A0,
        "a0_stability_control": "vecchia A0 = controllo di stabilità generativa, non baseline",
        "strata": {
            "A_evidence_flip": stratum_a,
            "B_answerable_control": pair(lambda c, r, cat: (c, r, cat), ans_pool),
            "C_adversarial": pair(lambda c, r, cat: (c, r), adv_pool),
        },
    }
    counts = {k: len(v) for k, v in manifest["strata"].items()}
    counts["total"] = sum(counts.values())
    manifest["strata_counts"] = counts
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def forecast(manifest: dict) -> dict:
    total = manifest["strata_counts"]["total"]
    answers = len(ARMS) * total                         # 5 risposte/caso
    selectors = sum(a.selector_calls for a in ARMS) * total  # C0 selettore
    return {
        "cases_total": total,
        "answer_generations_five_arms": answers,
        "selector_calls_C0": selectors,
        "total_max_calls": answers + selectors,
        "cap_max_calls": 774,
        "note": "Nessuna nuova ingestion/retrieval: contesti ricostruiti byte-esatti "
        "dagli artefatti. Costo = sola generazione risposte + selettore C0.",
    }


# --------------------------------------------------------------------------- #
# Guardie
# --------------------------------------------------------------------------- #
def assert_no_personal_redis() -> None:
    # L'ablation NON usa Redis. Se l'ambiente punta esplicitamente al Redis
    # personale (porta 6379), rifiuta di partire per non toccarlo mai.
    if os.environ.get("EURI_REDIS_PORT") == "6379":
        raise AblationError("Redis personale (porta 6379): l'ablation non deve usarlo")


def assert_context_matches(context_text: str, expected_sha256: str) -> None:
    """Contesto assente o corrotto → l'ablation si ferma chiusa."""

    if not context_text:
        raise AblationError("contesto assente: ablation impedita")
    if _sha(context_text) != expected_sha256:
        raise AblationError(
            f"contesto corrotto: sha {_sha(context_text)} != atteso {expected_sha256}"
        )


def load_reconstructed_context(report: dict, case, question_id: str) -> str:
    """Contesto dual byte-esatto (APPEND) verificato via SHA dalla ricostruzione."""

    from benchmarks.euri_memory.prompt_ablation import _reconstruct_contexts, APPEND

    contexts = _reconstruct_contexts(report, case)  # solleva se lo SHA diverge
    if question_id not in contexts:
        raise AblationError(f"contesto assente per {question_id}")
    return contexts[question_id][APPEND]


# --------------------------------------------------------------------------- #
# Metadati di provenienza registrati PRIMA di ogni generazione
# --------------------------------------------------------------------------- #
def call_metadata(*, arm: Arm, stage: str, context_text: str, question_id: str,
                  localization_sha256: str, messages: list[dict],
                  num_predict: int, model: str, model_digest: str | None) -> dict:
    system = messages[0]["content"] if messages else ""
    return {
        "arm": arm.name,
        "stage": stage,
        "question_id": question_id,
        "localization_sha256": localization_sha256,
        "context_sha256": _sha(context_text),
        "system_prompt_sha256": _sha(system),
        "messages_payload_sha256": messages_payload_sha256(messages),
        "answer_seed": ANSWER_SEED,
        "model": model,
        "model_digest": model_digest,
        "temperature": 0,
        "num_predict": num_predict,
        "think": arm.think,
    }


NUM_PREDICT_ANSWER = 160
NUM_PREDICT_THINK = 2000
NUM_PREDICT_SELECTOR = 400


# --------------------------------------------------------------------------- #
# Runner (gated da execute=True; NON invocato prima dell'audit)
# --------------------------------------------------------------------------- #
def _all_cases(manifest: dict) -> list[dict]:
    out = []
    for stratum, rows in manifest["strata"].items():
        for row in rows:
            out.append({**row, "stratum": stratum})
    return out


def _forbidden_strings(gold_answer: Any, evidence_ids: list) -> list[str]:
    forbidden = []
    if gold_answer:
        forbidden.append(str(gold_answer))
    forbidden.extend(str(e) for e in (evidence_ids or []))
    return forbidden


def run_ablation(*, manifest: dict, validation_root: Path, output_dir: Path,
                 capture_dir: Path, execute: bool = False,
                 chat_fn: Callable | None = None, model: str = "gemma4:26b",
                 model_digest: str | None = None) -> dict:
    """Esegue (se execute=True) i 5 arm sui contesti ricostruiti byte-esatti.

    In preparazione execute=False: valida binding, ricostruisce/verifica i
    contesti e prepara metadati/messaggi SENZA chiamare il modello. I testi
    completi vanno in ``capture_dir`` (gitignored); i report tracciabili portano
    solo hash, metadati e path relativi.
    """

    verify_manifest(manifest)
    assert_no_personal_redis()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = output_dir / "manifest.json"
    if existing.exists():
        prev = json.loads(existing.read_text(encoding="utf-8"))
        if prev.get("manifest_sha256") != manifest["manifest_sha256"]:
            raise AblationError("output-dir con manifest diverso: fail-closed")
    else:
        existing.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    if execute and chat_fn is None:
        from core.ollama_client import chat_client
        chat_fn = lambda **kw: chat_client.chat(**kw)  # noqa: E731

    from benchmarks.euri_memory.prompt_ablation import _load_case

    checkpoint_path = output_dir / "checkpoint.json"
    done = set(json.loads(checkpoint_path.read_text()).get("done", [])) if checkpoint_path.is_file() else set()
    capture_dir = Path(capture_dir); capture_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"; runs_dir.mkdir(exist_ok=True)

    # raggruppa per conversazione (una ricostruzione per report)
    by_conv = defaultdict(list)
    for case in _all_cases(manifest):
        by_conv[(case["conversation"], case["replica"])].append(case)

    planned = 0
    for (conv, rep), cases in sorted(by_conv.items()):
        report = json.loads((validation_root / "runs" / f"{conv}__r{rep}.json").read_text(encoding="utf-8"))
        loaded = _load_case(validation_root, conv, _corpus_path())
        questions = {q.question_id: q for q in loaded.questions}
        gold_by_q = {q.question_id: q for q in loaded.questions}
        for case in cases:
            qid = case["question_id"]
            key = f"{qid}"
            if key in done:
                continue
            ctx = load_reconstructed_context(report, loaded, qid)  # SHA verificato
            question = questions[qid].text
            forbidden = _forbidden_strings(
                gold_by_q[qid].expected_answer, list(gold_by_q[qid].evidence_turn_ids)
            )
            arm_records = []
            for arm in ARMS:
                rec = _run_arm(arm, ctx, question, qid, manifest, forbidden,
                               execute, chat_fn, model, model_digest, capture_dir)
                arm_records.append(rec)
                planned += arm.answer_calls + arm.selector_calls
            (runs_dir / f"{qid}.json").write_text(
                json.dumps({"question_id": qid, "stratum": case["stratum"], "arms": arm_records},
                           indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            done.add(key)
            checkpoint_path.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False), encoding="utf-8")

    return {"mode": "run" if execute else "prepared_no_model",
            "cases": len(_all_cases(manifest)), "planned_calls": planned}


def _run_arm(arm, ctx, question, qid, manifest, forbidden, execute, chat_fn,
             model, model_digest, capture_dir) -> dict:
    loc_sha = manifest["localization_sha256"]
    if arm.family == "two_stage":
        sel_msgs = build_messages("two_stage", context_text=ctx, question=question, stage="selector")
        hits = messages_forbidden_hits(sel_msgs, forbidden)
        # Nota: il gold può comparire nel contesto come dialogo legittimo; qui
        # verifichiamo solo che NON iniettiamo evidence ID (non contenuti nel ctx).
        sel_meta = call_metadata(arm=arm, stage="selector", context_text=numbered_context(ctx),
                                 question_id=qid, localization_sha256=loc_sha, messages=sel_msgs,
                                 num_predict=NUM_PREDICT_SELECTOR, model=model, model_digest=model_digest)
        selected, sel_idx, answer = "", [], None
        if execute:
            raw = _content(chat_fn(model=model, messages=sel_msgs,
                                   options={"temperature": 0, "num_predict": NUM_PREDICT_SELECTOR, "seed": ANSWER_SEED},
                                   think=False))
            parsed = parse_selector(raw)
            if parsed["answerable"] and parsed["supporting_fragments"]:
                selected, sel_idx = select_by_indices(ctx, parsed["supporting_fragments"])
                ans_msgs = build_messages("two_stage", context_text=ctx, question=question,
                                          stage="answer", selected_fragments=selected)
                answer = _content(chat_fn(model=model, messages=ans_msgs,
                                          options={"temperature": 0, "num_predict": NUM_PREDICT_ANSWER, "seed": ANSWER_SEED},
                                          think=False))
            else:
                answer = "Non lo so."
        _persist_texts(capture_dir, qid, arm.name, {"selector_messages": sel_msgs, "context": ctx})
        return {"arm": arm.name, "selector_metadata": sel_meta, "selected_indices": sel_idx,
                "answer": answer, "forbidden_evidence_hits": [h for h in hits if h in str(forbidden)]}
    # single-prompt arm
    num_predict = NUM_PREDICT_THINK if arm.think else NUM_PREDICT_ANSWER
    msgs = build_messages(arm.family, context_text=ctx, question=question)
    meta = call_metadata(arm=arm, stage="single", context_text=ctx, question_id=qid,
                         localization_sha256=loc_sha, messages=msgs, num_predict=num_predict,
                         model=model, model_digest=model_digest)
    answer = None
    if execute:
        answer = _content(chat_fn(model=model, messages=msgs,
                                  options={"temperature": 0, "num_predict": num_predict, "seed": ANSWER_SEED},
                                  think=arm.think))
    _persist_texts(capture_dir, qid, arm.name, {"messages": msgs, "context": ctx})
    return {"arm": arm.name, "metadata": meta, "answer": answer}


def _persist_texts(capture_dir: Path, qid: str, arm: str, payload: dict) -> None:
    (capture_dir / f"{qid}__{arm}.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _content(resp: Any) -> str:
    msg = getattr(resp, "message", None)
    if msg is None and isinstance(resp, dict):
        msg = resp.get("message")
    c = getattr(msg, "content", None)
    if c is None and isinstance(msg, dict):
        c = msg.get("content")
    return str(c or "").strip()


def _corpus_path() -> Path:
    return ROOT / "data" / "locomo10.json"


# --------------------------------------------------------------------------- #
# Controllo stabilità A0 (vecchia vs fresca) — solo in analisi
# --------------------------------------------------------------------------- #
def a0_stability(*, fresh_a0: dict[str, str], validation_root: Path, manifest: dict) -> dict:
    """Confronta la vecchia A0 (risposta dual del census) con la A0 fresca."""

    old = {}
    for (conv, rep) in {(c["conversation"], c["replica"]) for c in _all_cases(manifest)}:
        r = json.loads((validation_root / "runs" / f"{conv}__r{rep}.json").read_text(encoding="utf-8"))
        dual = {x["question_id"]: x for x in {a["arm"]: a for a in r["arms"]}["dual_channel"]["results"]}
        for qid, x in dual.items():
            old[qid] = x.get("answer")
    ids = [q for q in fresh_a0 if q in old]
    identical = sum(1 for q in ids if _norm(fresh_a0[q]) == _norm(old[q]))
    return {
        "n": len(ids),
        "identical_pct": round(100 * identical / len(ids), 1) if ids else None,
        "divergent_question_ids": sorted(q for q in ids if _norm(fresh_a0[q]) != _norm(old[q])),
        "note": "delta F1 e delta astensione richiedono lo scoring dei due set (calcolati in analyze).",
    }


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


# --------------------------------------------------------------------------- #
# Analisi per strato + audit cieco (post-esecuzione)
# --------------------------------------------------------------------------- #
import random
import unicodedata

_TOK = re.compile(r"\w+", re.UNICODE)
_ABST = re.compile(r"non lo so|non ci sono|non (?:è|e) (?:indicato|menzionato|specificato)|sconosciuto", re.I)


def _norm_tok(s):
    return _TOK.findall(unicodedata.normalize("NFKC", str(s or "")).casefold())


def token_f1(gold, pred):
    from collections import Counter
    g, p = _norm_tok(gold), _norm_tok(pred)
    if not g or not p:
        return float(g == p)
    ov = sum((Counter(g) & Counter(p)).values())
    return 0.0 if not ov else 2 * (ov / len(p)) * (ov / len(g)) / ((ov / len(p)) + (ov / len(g)))


def is_abstention(a):
    return (not _norm(a)) or bool(_ABST.search(a or ""))


def analyze(*, output_runs: Path, validation_root: Path, manifest: dict, gold_lookup: dict) -> dict:
    """Metriche di sviluppo per strato/arm. gold_lookup: qid -> {answer, answerable}.

    gold_lookup è costruito dal chiamante dal corpus localizzato (NON entra nei
    prompt); qui rientra soltanto per lo scoring, dopo la generazione.
    """

    per = defaultdict(lambda: defaultdict(list))  # (stratum,arm) -> metric -> []
    answers = defaultdict(dict)                     # arm -> qid -> answer
    for f in sorted(Path(output_runs).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        qid, stratum = rec["question_id"], rec["stratum"]
        g = gold_lookup.get(qid, {})
        for arm_rec in rec["arms"]:
            arm = arm_rec["arm"]
            a = arm_rec.get("answer")
            answers[arm][qid] = a
            if a is None:
                continue
            if g.get("answerable"):
                per[(stratum, arm)]["token_f1"].append(token_f1(g.get("answer"), a))
                per[(stratum, arm)]["false_abstention"].append(1.0 if is_abstention(a) else 0.0)
            else:
                per[(stratum, arm)]["adversarial_correct"].append(1.0 if is_abstention(a) else 0.0)

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    metrics = {}
    for (stratum, arm), m in sorted(per.items()):
        metrics.setdefault(stratum, {})[arm] = {k: mean(v) for k, v in m.items()}
    return {
        "experiment": EXPERIMENT_ID,
        "kind": "development",
        "manifest_sha256": manifest.get("manifest_sha256"),
        "per_stratum_arm": metrics,
        "a0_stability": a0_stability(fresh_a0=answers.get("A0", {}),
                                     validation_root=validation_root, manifest=manifest),
        "note": "token-F1 non è l'unico verdetto: vedi audit cieco. Development, N piccolo.",
    }


def blind_audit_export(*, output_runs: Path, gold_lookup: dict, seed: int = 20260728) -> list[dict]:
    """Righe per l'audit umano cieco: arm anonimizzato, ordine randomizzato.

    Solo risposte CAMBIATE rispetto ad A0. Nessuna indicazione dell'arm.
    Etichette umane attese: corretta/parzialmente/errata/astensione corretta/falsa astensione.
    """

    rng = random.Random(seed)
    rows = []
    for f in sorted(Path(output_runs).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        qid = rec["question_id"]
        a0 = next((x.get("answer") for x in rec["arms"] if x["arm"] == "A0"), None)
        g = gold_lookup.get(qid, {})
        for arm_rec in rec["arms"]:
            a = arm_rec.get("answer")
            if a is None or _norm(a) == _norm(a0):
                continue
            rows.append({
                "audit_id": _sha(f"{qid}:{arm_rec['arm']}:{seed}")[:12],  # arm nascosto
                "question_id": qid,
                "gold": g.get("answer"),
                "answer": a,
                "human_label": None,  # da compilare: corretta/parziale/errata/astensione corretta/falsa astensione
            })
    rng.shuffle(rows)
    return rows

