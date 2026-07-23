"""
Helper leggeri per trattare i flag epistemici delle memorie in modo uniforme.

Non decidono la verita' del contenuto: danno solo un ordinamento prudente e una
nota breve da portare nel prompt quando una memoria ha basi fragili.
"""
from __future__ import annotations

import config


_CONSOLIDATION_RISK_RANK = {"ok": 0, "watch": 25, "high": 80}

# Penalita' piccole rispetto ai flag di rischio: la fonte rompe i pareggi e
# sposta di poco i derivati, senza cancellare la pertinenza del retrieval.
_SOURCE_RISK_RANK = {
    "user": 0,
    "teach": 0,
    "obsidian_vault": 0,
    "conversation": 5,
    "episode": 5,
    "mobile_in": 5,
    "passive": 10,
    "loop2e": 15,
    "reflection": 25,
    "reaction": 25,
    "web": 30,
}


def _audit_flag(doc: dict) -> int:
    try:
        return int(doc.get("audit_flag") or 0)
    except (TypeError, ValueError):
        return 0


def memory_risk_rank(doc: dict) -> int:
    """
    Rank crescente: 0 = nessun rischio noto, valori alti = demuovere nel retrieval.
    E' una euristica di ordinamento, non un filtro.
    """
    if not doc:
        return 0
    risk = 0
    if doc.get("safety_flag"):
        risk += 100
    if doc.get("correction_pending"):
        risk += 90
    if doc.get("provenance_stale"):
        risk += 80
    cr = doc.get("consolidation_risk") or {}
    if isinstance(cr, dict):
        risk += _CONSOLIDATION_RISK_RANK.get(str(cr.get("level") or "ok").lower(), 0)
    audit = _audit_flag(doc)
    if audit > 0:
        risk += min(90, 45 + audit * 10)
    if doc.get("requires_verification"):
        risk += 30
    axes = doc.get("memory_axes") or {}
    if isinstance(axes, dict) and axes.get("subject_status") == "acephalous":
        risk += 30
    return risk


def memory_epistemic_rank(doc: dict) -> int:
    """Rischio complessivo: flag del nodo piu' affidabilita' della fonte."""
    source = str((doc or {}).get("source") or "").lower()
    return memory_risk_rank(doc) + _SOURCE_RISK_RANK.get(source, 15)


def is_document_summary(doc: dict) -> bool:
    """Riconosce sintesi documentali nuove e legacy senza migrare Redis.

    Le vecchie analisi clipboard erano salvate come ``semantic_fact``. Il prefisso
    e i tag permettono di presentarle con la provenienza corretta in fase di RAG,
    lasciando intatti contenuto, ID e cronologia.
    """
    if not doc:
        return False
    if str(doc.get("memory_kind") or "").lower() == "document_summary":
        return True
    tags = doc.get("tags") or []
    if not isinstance(tags, (list, tuple, set)) or "clipboard" not in tags:
        return False
    if str(doc.get("source") or "").lower() != "teach":
        return False
    content = str(doc.get("content") or "").lstrip().lower()
    return content.startswith((
        "testo analizzato dagli appunti:",
        "immagine analizzata dagli appunti:",
    ))


def rank_memories_epistemically(
    results: list[dict],
    limit: int | None = None,
) -> list[dict]:
    """
    Riordina un pool gia' ordinato per pertinenza senza distruggerne il segnale.

    Ogni 25 punti di rischio valgono circa una posizione nel pool. Il sort e'
    stabile, quindi documenti puliti e di pari affidabilita' conservano l'ordine
    prodotto da KNN/keyword/recency. I nodi in quarantena non sono evidenza
    utilizzabile finche' la correzione non viene risolta.
    """
    if not results:
        return []

    ranked: list[tuple[float, int, int, dict]] = []
    for position, doc in enumerate(results):
        if doc.get("superseded_by") or doc.get("correction_pending"):
            continue
        risk = memory_epistemic_rank(doc)
        ranked.append((position + risk / 25.0, risk, position, doc))

    ranked.sort(key=lambda item: item[:3])
    ordered = [item[3] for item in ranked]
    return ordered if limit is None else ordered[:limit]


def memory_verification_suffix(doc: dict) -> str:
    """Nota compatta da appendere al testo iniettato nel RAG."""
    if not doc:
        return ""
    is_interpretation = (
        doc.get("memory_kind") == "reflection" or doc.get("source") == "reflection"
    )
    is_document = is_document_summary(doc)
    reasons: list[str] = []
    if doc.get("correction_pending"):
        reasons.append("contestato nel contesto, correzione in sospeso")
    if doc.get("provenance_stale"):
        reasons.append("provenienza fragile")
    if doc.get("passive_support") == "tacit_acceptance":
        reasons.append(
            f"vecchio assenso tacito, non conferma di {config.OWNER_DISPLAY_NAME}"
        )
    cr = doc.get("consolidation_risk") or {}
    if isinstance(cr, dict):
        level = str(cr.get("level") or "ok").lower()
        if level in {"watch", "high"}:
            reasons.append(f"rischio {level}")
    if _audit_flag(doc) > 0:
        reasons.append("contestato")
    axes = doc.get("memory_axes") or {}
    if isinstance(axes, dict) and axes.get("subject_status") == "acephalous":
        reasons.append("soggetto incerto")
    if doc.get("requires_verification") and not reasons:
        reasons.append("da verificare")
    if is_interpretation:
        assistant_label = config.ASSISTANT_DISPLAY_NAME.upper()
        if reasons:
            return (
                f" [INTERPRETAZIONE DI {assistant_label}, non fatto confermato; "
                f"DA VERIFICARE: {', '.join(dict.fromkeys(reasons))}]"
            )
        return (
            f" [INTERPRETAZIONE DI {assistant_label}, "
            "non fatto confermato]"
        )
    if is_document:
        owner = getattr(config, "OWNER_DISPLAY_NAME", "utente")
        if reasons:
            return (
                f" [SINTESI DI DOCUMENTO FORNITO DA {owner}, non verifica interna; "
                f"DA VERIFICARE: {', '.join(dict.fromkeys(reasons))}]"
            )
        return f" [SINTESI DI DOCUMENTO FORNITO DA {owner}, non verifica interna]"
    if not reasons:
        return ""
    return f" [DATO DA VERIFICARE: {', '.join(dict.fromkeys(reasons))}]"
