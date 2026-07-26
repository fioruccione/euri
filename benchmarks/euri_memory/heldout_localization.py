"""Pipeline di traduzione italiana congelata per l'held-out.

Variante preregistrata (per non tradurre ~4.800 turni prima del seed):

1. Questo protocollo di traduzione è **congelato e committato prima del seed**:
   deterministico, locale, stessa versione per tutti.
2. **Dopo** il seed si traducono automaticamente **solo le 3 conversazioni
   selezionate**, ma integralmente (tutti i turni + le domande campionate).
3. Nessuna ispezione o correzione manuale del contenuto selezionato.
4. Controlli automatici soltanto: completezza ID/turni/domande, evidence, date,
   numeri, nomi, answerability e risposte avversariali.
5. L'artefatto italiano è sigillato con SHA-256; un manifest finale derivato lega
   selection manifest + protocollo di traduzione + localization SHA.
6. Entrambi i bracci ricevono ESATTAMENTE lo stesso artefatto italiano.
7. Nessun risultato è osservabile prima della chiusura del manifest finale.

Il traduttore è iniettabile (``translate_fn``): la CLI collega il modello locale
Ollama, i test un finto traduttore deterministico. Nessun servizio a pagamento.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory.adapters import LoCoMoAdapter


class LocalizationError(ValueError):
    pass


# --- Protocollo di traduzione CONGELATO (pre-seed) ------------------------- #
TRANSLATION_PROTOCOL_ID = "euri-heldout-it-translation-v1"
TRANSLATION_PROTOCOL_VERSION = "v1"
TARGET_LANGUAGE = "it"
SOURCE_LANGUAGE = "en"
TRANSLATION_OPTIONS = {"temperature": 0, "seed": 42}

TRANSLATION_SYSTEM_PROMPT = """\
Sei un traduttore professionale dall'inglese all'italiano per un benchmark di
memoria conversazionale. Traduci fedelmente il testo che ricevi.
Regole vincolanti:
- Conserva ESATTAMENTE nomi propri di persona e di luogo, così come sono.
- Conserva ESATTAMENTE tutti i numeri, gli anni, le quantità, i prezzi e le date.
- Conserva i riferimenti temporali relativi (ieri, venerdì scorso, questa
  mattina) senza convertirli in date assolute.
- Non aggiungere, non spiegare, non omettere informazioni.
- Rispondi soltanto con la traduzione italiana, senza virgolette e senza note."""

# Anni/numeri "grandi" (>= 4 cifre) devono sopravvivere alla traduzione.
_BIG_NUMBER = re.compile(r"\d{4,}")
# Numeri 2-3 cifre: riportati come possibili derive, non come errore fatale
# (possono essere legittimamente scritti in lettere).
_SMALL_NUMBER = re.compile(r"\d{2,3}")


def translation_prompt_sha256() -> str:
    return hashlib.sha256(TRANSLATION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def translation_protocol() -> dict:
    return {
        "protocol_id": TRANSLATION_PROTOCOL_ID,
        "version": TRANSLATION_PROTOCOL_VERSION,
        "target_language": TARGET_LANGUAGE,
        "source_language": SOURCE_LANGUAGE,
        "options": dict(TRANSLATION_OPTIONS),
        "think": False,
        "prompt_sha256": translation_prompt_sha256(),
    }


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def localization_digest(localization: dict) -> str:
    body = {k: v for k, v in localization.items() if k != "localization_sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def verify_localization_seal(localization: dict) -> None:
    recorded = localization.get("localization_sha256")
    if not recorded:
        raise LocalizationError("localizzazione priva di localization_sha256")
    if recorded != localization_digest(localization):
        raise LocalizationError("localization_sha256 non corrisponde: artefatto alterato")


def _big_numbers(text: str) -> set[str]:
    return set(_BIG_NUMBER.findall(text or ""))


# --------------------------------------------------------------------------- #
# Builder (post-seed, automatico): traduce SOLO le conversazioni selezionate
# --------------------------------------------------------------------------- #
def build_selected_localization(
    *,
    corpus_path: Path,
    selection_manifest: dict,
    translate_fn: Callable[[str, str], str],
    model: str,
    model_version: str | None,
) -> dict:
    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    conversations: dict[str, dict] = {}
    for conv in selection_manifest["conversations"]:
        sample_id = conv["sample_id"]
        case = cases.get(sample_id)
        if case is None:
            raise LocalizationError(f"conversazione selezionata assente dal corpus: {sample_id}")
        question_ids = set(conv["question_ids"])
        turns = {}
        for turn in case.turns:
            translated = str(translate_fn(turn.text, "turn") or "").strip()
            if not translated:
                raise LocalizationError(f"traduzione vuota per il turno {turn.turn_id}")
            turns[turn.turn_id] = translated
        questions: dict[str, dict] = {}
        for question in case.questions:
            if question.question_id not in question_ids:
                continue
            entry: dict[str, Any] = {
                "text": str(translate_fn(question.text, "question") or "").strip()
            }
            if question.expected_answer is None:
                entry["answer"] = None
            else:
                entry["answer"] = str(
                    translate_fn(str(question.expected_answer), "answer") or ""
                ).strip()
            if "adversarial_answer" in question.metadata:
                entry["adversarial_answer"] = str(
                    translate_fn(str(question.metadata["adversarial_answer"]), "adversarial")
                    or ""
                ).strip()
            questions[question.question_id] = entry
        conversations[sample_id] = {"turns": turns, "questions": questions}

    localization = {
        "schema_version": 1,
        "localization_id": f"heldout-it-seed{selection_manifest['seed']}-{selection_manifest['budget']['name']}",
        "language": TARGET_LANGUAGE,
        "source_language": SOURCE_LANGUAGE,
        "method": "frozen-local-ollama-per-unit-translation",
        "model": model,
        "model_version": model_version,
        "translation_protocol": translation_protocol(),
        "source_sha256": selection_manifest["corpus"]["sha256"],
        "selection_manifest_sha256": selection_manifest["manifest_sha256"],
        "selected_sample_ids": sorted(conversations),
        "built_at": time.time(),
        "conversations": conversations,
    }
    localization["localization_sha256"] = localization_digest(localization)
    return localization


# --------------------------------------------------------------------------- #
# Verifica automatica di completezza e invarianti
# --------------------------------------------------------------------------- #
_ALLOWED_QUESTION_KEYS = {"text", "answer", "adversarial_answer"}


def verify_selected_localization(
    localization: dict,
    corpus_path: Path,
    selection_manifest: dict,
) -> dict:
    """Controlli automatici; solleva su fallimento duro, riporta le derive molli."""

    verify_localization_seal(localization)
    if localization.get("language") != TARGET_LANGUAGE:
        raise LocalizationError("lingua della localizzazione non è italiano")
    if localization.get("selection_manifest_sha256") != selection_manifest["manifest_sha256"]:
        raise LocalizationError("localizzazione non legata a questo selection manifest")
    if localization.get("source_sha256") != selection_manifest["corpus"]["sha256"]:
        raise LocalizationError("source_sha256 diverso dal corpus del selection manifest")
    protocol = localization.get("translation_protocol") or {}
    if protocol.get("prompt_sha256") != translation_prompt_sha256():
        raise LocalizationError("protocollo di traduzione diverso da quello congelato")

    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    expected_ids = sorted(conv["sample_id"] for conv in selection_manifest["conversations"])
    if sorted(localization.get("selected_sample_ids") or []) != expected_ids:
        raise LocalizationError("insieme delle conversazioni tradotte diverso dalla selezione")
    if sorted(localization.get("conversations") or {}) != expected_ids:
        raise LocalizationError("conversazioni tradotte non combaciano con la selezione")

    soft_number_drift: list[str] = []
    for conv in selection_manifest["conversations"]:
        sample_id = conv["sample_id"]
        case = cases[sample_id]
        loc_conv = localization["conversations"][sample_id]
        loc_turns = loc_conv.get("turns") or {}
        loc_questions = loc_conv.get("questions") or {}

        corpus_turn_ids = {turn.turn_id for turn in case.turns}
        if set(loc_turns) != corpus_turn_ids:
            raise LocalizationError(f"{sample_id}: turni tradotti != turni del corpus")
        expected_qids = set(conv["question_ids"])
        if set(loc_questions) != expected_qids:
            raise LocalizationError(f"{sample_id}: domande tradotte != domande selezionate")

        by_qid = {q.question_id: q for q in case.questions}
        for turn in case.turns:
            src, tgt = turn.text, loc_turns[turn.turn_id]
            if not tgt.strip():
                raise LocalizationError(f"{sample_id}/{turn.turn_id}: traduzione vuota")
            missing_big = _big_numbers(src) - _big_numbers(tgt)
            if missing_big:
                raise LocalizationError(
                    f"{sample_id}/{turn.turn_id}: numeri {sorted(missing_big)} persi"
                )
            if set(_SMALL_NUMBER.findall(src)) - set(_SMALL_NUMBER.findall(tgt)):
                soft_number_drift.append(f"{sample_id}/{turn.turn_id}")
        for qid, entry in loc_questions.items():
            extra = set(entry) - _ALLOWED_QUESTION_KEYS
            if extra:
                raise LocalizationError(f"{qid}: chiavi non ammesse {sorted(extra)}")
            question = by_qid[qid]
            # Answerability preservata
            if (question.expected_answer is None) != (entry.get("answer") is None):
                raise LocalizationError(f"{qid}: answerability alterata dalla traduzione")
            if question.expected_answer is not None and not str(entry.get("answer") or "").strip():
                raise LocalizationError(f"{qid}: risposta tradotta vuota")
            if not str(entry.get("text") or "").strip():
                raise LocalizationError(f"{qid}: testo domanda tradotto vuoto")
            # Risposta avversariale preservata come presenza
            has_adv_src = "adversarial_answer" in question.metadata
            has_adv_tgt = "adversarial_answer" in entry
            if has_adv_src != has_adv_tgt:
                raise LocalizationError(f"{qid}: presenza risposta avversariale alterata")
            if has_adv_tgt and not str(entry.get("adversarial_answer") or "").strip():
                raise LocalizationError(f"{qid}: risposta avversariale tradotta vuota")

    return {
        "verified": True,
        "conversations": expected_ids,
        "soft_number_drift_units": soft_number_drift,
    }


# --------------------------------------------------------------------------- #
# Slice per conversazione (formato BenchmarkLocalization per il worker)
# --------------------------------------------------------------------------- #
def selection_localization_slice(
    localization: dict,
    sample_id: str,
    question_ids: list[str],
    selection_id: str,
) -> dict:
    conv = localization["conversations"][sample_id]
    questions = {qid: conv["questions"][qid] for qid in question_ids}
    return {
        "localization_id": localization["localization_id"],
        "selection_id": selection_id,
        "language": localization["language"],
        "source_language": localization["source_language"],
        "turns": dict(conv["turns"]),
        "questions": questions,
    }


# --------------------------------------------------------------------------- #
# Forecast della sola traduzione delle conversazioni selezionate
# --------------------------------------------------------------------------- #
def localization_forecast(
    selection_manifest: dict,
    corpus_path: Path,
    *,
    seconds_per_call: float = 2.0,
) -> dict:
    cases = {case.sample_id: case for case in LoCoMoAdapter().load(corpus_path)}
    per_conv = []
    total_units = 0
    for conv in selection_manifest["conversations"]:
        sample_id = conv["sample_id"]
        case = cases[sample_id]
        turns = len(case.turns)
        questions = len(conv["question_ids"])
        by_qid = {q.question_id: q for q in case.questions}
        answer_units = sum(
            1
            for qid in conv["question_ids"]
            if by_qid[qid].expected_answer is not None
        )
        adv_units = sum(
            1
            for qid in conv["question_ids"]
            if "adversarial_answer" in by_qid[qid].metadata
        )
        units = turns + questions + answer_units + adv_units
        total_units += units
        per_conv.append(
            {
                "sample_id": sample_id,
                "turns": turns,
                "questions": questions,
                "translation_units": units,
            }
        )
    return {
        "note": "Una unità = una chiamata di traduzione (turno, domanda, risposta "
        "o risposta avversariale). Stima strutturale, nessun modello avviato.",
        "translation_units_total": total_units,
        "estimated_seconds": round(total_units * seconds_per_call),
        "estimated_minutes": round(total_units * seconds_per_call / 60, 1),
        "seconds_per_call_assumed": seconds_per_call,
        "per_conversation": per_conv,
    }


def ollama_translator(model: str) -> Callable[[str, str], str]:
    """Traduttore locale deterministico via Ollama. Usato solo dalla CLI reale."""

    from core.ollama_client import chat_client  # import locale: non a livello modulo

    def translate(text: str, _kind: str) -> str:
        response = chat_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            options=dict(TRANSLATION_OPTIONS),
            think=False,
        )
        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message")
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return str(content or "").strip()

    return translate
