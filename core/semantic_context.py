"""Compilatore leggero del contesto semantico per il prompt RAG.

Le memorie canoniche restano narrative e continuano a essere la fonte storica.
Questo modulo costruisce soltanto una proiezione additiva, bounded e leggibile
dal Brain: stato, provenienza, entita' e grado di verifica. Non deduce nuovi
fatti, non modifica Redis e non assegna autorita' al testo.
"""
from __future__ import annotations

import re
from copy import deepcopy
from collections import Counter
from dataclasses import dataclass
from typing import Any


_CURRENT_RE = re.compile(
    r"\b(?:configurazione\s+attuale|configurazione\s+corrente|attuale|"
    r"attualmente|adesso|ora|oggi|rimane|resta|presente)\b",
    re.IGNORECASE,
)
_PROPOSED_RE = re.compile(
    r"\b(?:modifica\s+proposta|proposta|proposto|ipotetic\w*|"
    r"verrebbe|verr[àa]|dovrebbe|obiettivo|inserimento|installare|"
    r"installazione|prevede|prevedere|sostituzione)\b",
    re.IGNORECASE,
)
_HISTORICAL_RE = re.compile(
    r"\b(?:storico|storica|storici|precedente|precedentemente|in\s+passato|"
    r"vecchi[aoei]|all['’]inizio|prima\s+del|era\s+una\s+volta)\b",
    re.IGNORECASE,
)

_SOURCE_LABELS = {
    "user": "comunicata dall'utente",
    "teach": "insegnata/salvata dall'utente",
    "passive": "estratta passivamente, non verificata",
    "reflection": "interpretazione interna di Euri",
    "reaction": "lezione interna da feedback",
    "loop2e": "consolidamento interno",
    "web": "fonte Web esterna",
    "obsidian_vault": "documento dal Vault",
}


@dataclass(frozen=True)
class SemanticContext:
    text: str
    diagnostics: dict[str, Any]


_OPERATIONAL_ACTS = frozenset({
    "REQUEST_ACTION",
    "REQUEST_SAVE",
    "REQUEST_WEB_SEARCH",
    "DICTATE",
    "TRANSLATE",
    "INITIATE_TEACHING",
    "REQUEST_DELIBERATION",
})


def build_memory_clarification_contract(
    semantic_frame: dict | None,
    nodes: list[dict] | None,
    *,
    minimum_confidence: float = 0.72,
) -> SemanticContext:
    """Trasforma un'ambiguita' del frame in policy di risposta post-RAG.

    Il frame decide se il riferimento e' insufficiente; questo bordo controlla
    soltanto che si tratti di una domanda conversazionale affidabile. I nodi
    recuperati restano candidati utili a formulare le alternative, mai prova che
    una di esse fosse il soggetto inteso dall'utente.
    """
    diagnostics: dict[str, Any] = {
        "required": False,
        "reason": "invalid_frame",
        "candidate_node_ids": [],
        "missing_facts": [],
    }
    if not isinstance(semantic_frame, dict):
        return SemanticContext("", diagnostics)
    if semantic_frame.get("status") != "interpreted":
        diagnostics["reason"] = "frame_not_interpreted"
        return SemanticContext("", diagnostics)
    if semantic_frame.get("requires_clarification") is not True:
        diagnostics["reason"] = "not_required_by_frame"
        return SemanticContext("", diagnostics)

    acts = {
        str(item or "").strip().upper()
        for item in (semantic_frame.get("speech_acts") or [])
        if str(item or "").strip()
    }
    if acts & _OPERATIONAL_ACTS:
        diagnostics["reason"] = "operational_turn"
        return SemanticContext("", diagnostics)
    if not (acts & {"ASK", "REQUEST_MEMORY_SEARCH"}):
        diagnostics["reason"] = "not_a_question"
        return SemanticContext("", diagnostics)

    try:
        confidence = float(semantic_frame.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence_basis = "frame_confidence"
    if confidence < float(minimum_confidence):
        request = semantic_frame.get("evidence_request") or {}
        retrieval = semantic_frame.get("memory_retrieval") or {}
        structurally_convergent = (
            retrieval.get("needed") is True
            and not (retrieval.get("focus") or [])
            and str(request.get("dependency") or "").strip().lower() == "required"
            and bool([
                item for item in (request.get("missing_facts") or [])
                if str(item or "").strip()
            ])
            and str(semantic_frame.get("memory_disposition") or "").lower()
            in {"no_store", "ephemeral"}
        )
        if not structurally_convergent:
            diagnostics["reason"] = "frame_low_confidence"
            return SemanticContext("", diagnostics)
        confidence_basis = "structural_convergence"

    candidate_ids = list(dict.fromkeys(
        str(item.get("id") or "")
        for item in (nodes or [])
        if isinstance(item, dict) and str(item.get("id") or "")
    ))[:6]
    request = semantic_frame.get("evidence_request") or {}
    missing = list(dict.fromkeys(
        str(item or "").strip()
        for item in (request.get("missing_facts") or [])
        if str(item or "").strip()
    ))[:4]
    diagnostics.update({
        "required": True,
        "reason": "referential_ambiguity",
        "confidence_basis": confidence_basis,
        "candidate_node_ids": candidate_ids,
        "missing_facts": missing,
    })
    text = "\n".join((
        "[CHIARIMENTO MNEMONICO OBBLIGATORIO — CONTROLLO DEL REFERENTE]",
        "- Il turno non identifica con sufficiente certezza il soggetto, lo stato "
        "o la configurazione necessari alla risposta.",
        "- Le memorie recuperate sono soltanto alternative plausibili: non scegliere "
        "quella piu' disponibile e non rispondere alla domanda sottostante.",
        "- Formula una sola domanda breve e naturale per chiarire il riferimento.",
        "- Se il contesto sostiene alternative incompatibili, proponi massimo due "
        "alternative concrete nel formato 'intendi A oppure B?'.",
        "- Non anticipare come vera la posizione, lo stato o la relazione che la "
        "domanda di chiarimento deve ancora stabilire.",
    ))
    return SemanticContext(text, diagnostics)


def with_memory_clarification_frame(
    semantic_frame: dict | None,
    rag_diagnostics: dict | None,
) -> dict | None:
    """Marca una copia del frame per escludere lo scambio dal learner passivo."""
    if not isinstance(semantic_frame, dict):
        return semantic_frame
    decision = (rag_diagnostics or {}).get("memory_clarification") or {}
    if decision.get("required") is not True:
        return semantic_frame
    marked = deepcopy(semantic_frame)
    marked["memory_clarification_required"] = True
    marked["memory_clarification_reason"] = str(
        decision.get("reason") or "referential_ambiguity"
    )
    marked["passive_memory_blocked"] = True
    marked["passive_memory_block_scope"] = "turn"
    marked["passive_memory_block_reason"] = "chiarimento mnemonico in attesa"
    return marked


def classify_memory_state(memory: dict) -> str:
    """Classifica solo marcatori espliciti; non interpreta il contenuto tecnico."""
    if memory.get("superseded_by"):
        return "storica/superata"
    explicit = str(memory.get("semantic_state") or "").strip().lower()
    if explicit in {"current", "attuale"}:
        return "attuale"
    if explicit in {"proposed", "proposal", "proposta"}:
        return "proposta"
    if explicit in {"historical", "storica", "stale"}:
        return "storica"

    content = str(memory.get("content") or "")
    current = bool(_CURRENT_RE.search(content))
    proposed = bool(_PROPOSED_RE.search(content))
    historical = bool(_HISTORICAL_RE.search(content))
    source = str(memory.get("source") or "").strip().lower()
    if current and proposed:
        return "attuale + proposta nello stesso ricordo"
    if proposed:
        return "proposta/ipotesi"
    if current:
        return "attuale"
    if historical or source in {"reflection", "reaction", "loop2e"}:
        return "storica/derivata"
    if source == "passive":
        return "non specificato (passiva)"
    return "non specificato"


def _source_label(memory: dict) -> str:
    source = str(memory.get("source") or "").strip().lower()
    return _SOURCE_LABELS.get(source, source or "origine non registrata")


def _entities(memory: dict) -> list[str]:
    axes = memory.get("memory_axes") or {}
    values = axes.get("entity_mentions") or []
    out: list[str] = []
    for value in values:
        label = " ".join(str(value or "").split()).strip()
        if not label or label.isdigit() or label in out:
            continue
        out.append(label)
        if len(out) >= 8:
            break
    return out


def _verification_label(memory: dict) -> str:
    if memory.get("correction_pending"):
        return "correzione pendente"
    if memory.get("requires_verification"):
        return "da verificare"
    return "non segnalata"


def _clip_claim(content: str, limit: int) -> str:
    content = " ".join(str(content or "").split())
    if len(content) <= limit:
        return content
    return content[: max(0, limit - 1)].rstrip() + "…"


def build_semantic_context(
    memories: list[dict] | None,
    *,
    semantic_plan: dict | None = None,
    limit: int = 3,
    claim_chars: int = 760,
) -> SemanticContext:
    """Costruisce il pacchetto bounded senza cambiare i documenti sorgente.

    Il testo contiene le affermazioni originali abbreviate, ma ogni riga porta
    lo stato e la provenienza. Una memoria puo' essere ``attuale + proposta``:
    il compilatore non la spezza artificialmente e obbliga il Brain a mantenere
    la distinzione interna.
    """
    selected: list[dict] = []
    seen: set[str] = set()
    for memory in memories or []:
        if not isinstance(memory, dict):
            continue
        content = str(memory.get("content") or "").strip()
        if not content:
            continue
        mid = str(memory.get("id") or "")
        key = mid or content
        if key in seen:
            continue
        seen.add(key)
        selected.append(memory)
        if len(selected) >= max(0, int(limit)):
            break

    if not selected:
        return SemanticContext("", {"enabled": True, "memory_count": 0})

    focus = [
        str(item.get("entity") or "").strip()
        for item in (semantic_plan or {}).get("focus") or []
        if isinstance(item, dict) and str(item.get("entity") or "").strip()
    ]
    lines = [
        "Mappa semantica del contesto (proiezione, non una nuova fonte di verita'):",
        "- Usa stato e provenienza per separare attuale, proposta e storico.",
        "- Le righe narrative sottostanti restano evidenza; non fondere due stati "
        "solo perche' condividono una parola o una relazione generica.",
    ]
    if focus:
        relation = str((semantic_plan or {}).get("relation") or "").strip()
        goal = str((semantic_plan or {}).get("evidence_goal") or "").strip()
        request_bits = [f"focus={', '.join(dict.fromkeys(focus))}"]
        if relation:
            request_bits.append(f"relazione richiesta={relation[:180]}")
        if goal:
            request_bits.append(f"obiettivo evidenziale={goal}")
        lines.append("- Richiesta semantica del turno: " + "; ".join(request_bits))

    state_counts: Counter[str] = Counter()
    for memory in selected:
        state = classify_memory_state(memory)
        state_counts[state] += 1
        mid = str(memory.get("id") or "").removeprefix("euri:memory:")
        entities = _entities(memory)
        entity_text = ", ".join(entities) if entities else "non annotate"
        lines.append(
            f"- [stato={state}; fonte={_source_label(memory)}; "
            f"verifica={_verification_label(memory)}; entita'={entity_text}; "
            f"memory_id={mid or 'non registrato'}] "
            f"{_clip_claim(memory.get('content'), claim_chars)}"
        )

    lines.append(
        "- Regola: una relazione non esplicitamente identificata come attuale "
        "non puo' sovrascrivere una relazione attuale piu' specifica; se manca "
        "il modello o la posizione di un componente, dichiaralo."
    )
    return SemanticContext(
        "\n".join(lines),
        {
            "enabled": True,
            "memory_count": len(selected),
            "state_counts": dict(state_counts),
            "focus": list(dict.fromkeys(focus)),
            "claim_chars": int(claim_chars),
        },
    )
