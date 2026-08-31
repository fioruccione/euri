"""Risoluzione bounded e fail-closed dell'antecedente di una correzione.

Il modulo e' puro: ordina candidati gia' recuperati, non legge Redis e non
autorizza mutazioni. Una correzione esplicita resta distinta da un normale
``SAVE_MEMORY``; in caso di parita' sostanziale non sceglie un vincitore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata


_TOKEN_RE = re.compile(r"\b[\wÀ-ü]+\b", re.UNICODE)
_SPECIFIC_RE = re.compile(r"\b[A-Za-zÀ-ÖØ-Þ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9_-]{1,}\b")
_STOP = frozenset({
    "adesso", "anche", "avevo", "correggi", "correggo", "correzione",
    "corretto", "corretta", "della", "delle", "dello", "dalla", "dalla",
    "deve", "devo", "detto", "dimmelo", "errore", "errori", "errato",
    "errata", "fatto", "filtro", "invece", "macchina", "memoria",
    "memorizza", "nuova", "nuovo", "pompa", "precedente", "quella",
    "quello", "questa", "questo", "registra", "ricordo", "sono", "stefano",
    "versione", "viene",
})
_DIRECT_AUTHORITY = {
    "user": 4,
    "teach": 4,
    "obsidian_vault": 4,
    "mobile_in": 3,
    "conversation": 3,
    "passive": 2,
}


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[^\wà-öø-ÿ]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(str(value or ""))
        if len(token) >= 3 and token.casefold() not in _STOP
    }


def _specific_tokens(value: str) -> set[str]:
    result: set[str] = set()
    for token in _SPECIFIC_RE.findall(str(value or "")):
        compact = token.replace("_", "").replace("-", "")
        if any(char.isdigit() for char in compact) or (
            compact.isupper() and len(compact) >= 3
        ):
            result.add(compact.casefold())
    return result


def build_correction_evidence(
    current_text: str,
    recent_history: list[dict] | None,
    *,
    max_prior_turns: int = 3,
    max_chars: int = 2400,
) -> str:
    """Unisce il comando corrente alle correzioni owner immediatamente precedenti.

    Usa soltanto frame affidabili e atti ``CORRECT_*``. Le risposte assistant non
    diventano evidenza della correzione e il testo resta bounded.
    """
    prior: list[str] = []
    for message in reversed(recent_history or []):
        if len(prior) >= max_prior_turns:
            break
        if str(message.get("role") or "") != "user":
            continue
        frame = message.get("semantic_frame") or {}
        if not isinstance(frame, dict) or frame.get("status") != "interpreted":
            continue
        if frame.get("requires_clarification"):
            continue
        try:
            confidence = float(frame.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        acts = {str(item or "").upper() for item in frame.get("speech_acts") or []}
        if confidence < 0.72 or not (acts & {"CORRECT_FACT", "CORRECT_ENTITY"}):
            continue
        content = str(message.get("content") or "").strip()
        if content:
            prior.append(content)
    pieces = list(reversed(prior)) + [str(current_text or "").strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        key = _normalise(piece)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(piece)
    return "\n".join(deduped)[-max_chars:]


@dataclass(frozen=True)
class CorrectionTargetResolution:
    target: dict | None
    reason: str
    excluded_exact_ids: tuple[str, ...] = field(default_factory=tuple)
    ambiguous_ids: tuple[str, ...] = field(default_factory=tuple)
    ranked_ids: tuple[str, ...] = field(default_factory=tuple)


def _candidate_similarity(candidate: dict) -> float:
    try:
        if candidate.get("similarity") is not None:
            return float(candidate["similarity"])
        return 1.0 - float(candidate.get("_vec_score", 1.0))
    except (TypeError, ValueError):
        return 0.0


def select_correction_target(
    new_content: str,
    correction_text: str,
    candidates: list[dict] | None,
    *,
    similarity_floor: float = 0.70,
    ambiguity_margin: float = 0.015,
) -> CorrectionTargetResolution:
    """Seleziona l'antecedente senza confondere la nuova versione con il vecchio fatto."""
    new_norm = _normalise(new_content)
    evidence_tokens = _tokens(f"{new_content}\n{correction_text}")
    new_specific = _specific_tokens(new_content)
    rejected_specific = _specific_tokens(correction_text) - new_specific
    excluded_exact: list[str] = []
    scored: list[tuple[tuple[int, int, int, int, float], dict]] = []

    for raw in candidates or []:
        candidate = dict(raw or {})
        mid = str(candidate.get("id") or "").replace("euri:memory:", "")
        content = str(candidate.get("content") or "")
        source = str(candidate.get("source") or "").lower()
        if not mid or not content or candidate.get("superseded_by"):
            continue
        if source not in _DIRECT_AUTHORITY:
            continue
        if _normalise(content) == new_norm:
            excluded_exact.append(mid)
            continue
        similarity = _candidate_similarity(candidate)
        if similarity < similarity_floor:
            continue
        candidate_tokens = _tokens(content)
        overlap = len(evidence_tokens & candidate_tokens)
        if overlap < 2:
            continue
        marker_hits = len(rejected_specific & _specific_tokens(content))
        authority = _DIRECT_AUTHORITY[source]
        # Il primo bit distingue una prova negativa esplicita (LAS500 rifiutato)
        # da una semplice affinita' di soggetto. La recenza non entra: una
        # correzione deve trovare l'antecedente, non il fatto piu' nuovo.
        score = (
            1 if marker_hits else 0,
            marker_hits,
            authority,
            overlap,
            similarity,
        )
        candidate["id"] = mid
        candidate["similarity"] = similarity
        scored.append((score, candidate))

    if not scored:
        return CorrectionTargetResolution(
            None,
            "no_supported_target",
            excluded_exact_ids=tuple(excluded_exact),
        )

    scored.sort(key=lambda item: (item[0], item[1]["id"]), reverse=True)
    top_score, top = scored[0]
    ranked_ids = tuple(item[1]["id"] for item in scored)
    if len(scored) > 1:
        second_score, second = scored[1]
        same_structural_score = top_score[:-1] == second_score[:-1]
        if same_structural_score and abs(top_score[-1] - second_score[-1]) <= ambiguity_margin:
            ambiguous = [top["id"], second["id"]]
            for score, candidate in scored[2:]:
                if score[:-1] != top_score[:-1]:
                    break
                if abs(top_score[-1] - score[-1]) <= ambiguity_margin:
                    ambiguous.append(candidate["id"])
            return CorrectionTargetResolution(
                None,
                "ambiguous",
                excluded_exact_ids=tuple(excluded_exact),
                ambiguous_ids=tuple(ambiguous),
                ranked_ids=ranked_ids,
            )

    return CorrectionTargetResolution(
        top,
        "resolved",
        excluded_exact_ids=tuple(excluded_exact),
        ranked_ids=ranked_ids,
    )
