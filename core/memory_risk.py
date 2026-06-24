"""
Helper leggeri per trattare i flag epistemici delle memorie in modo uniforme.

Non decidono la verita' del contenuto: danno solo un ordinamento prudente e una
nota breve da portare nel prompt quando una memoria ha basi fragili.
"""
from __future__ import annotations


_CONSOLIDATION_RISK_RANK = {"ok": 0, "watch": 25, "high": 80}


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


def memory_verification_suffix(doc: dict) -> str:
    """Nota compatta da appendere al testo iniettato nel RAG."""
    if not doc:
        return ""
    reasons: list[str] = []
    if doc.get("provenance_stale"):
        reasons.append("provenienza fragile")
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
    if not reasons:
        return ""
    return f" [DATO DA VERIFICARE: {', '.join(dict.fromkeys(reasons))}]"
