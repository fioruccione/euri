"""
Wide recall — panorama per AREE della memoria, solo per domande panoramiche/autobiografiche.

Problema: il retrieval principale (domain_aware_search) booster il dominio della conversazione
corrente → su domande ampie ("cosa sai di me", "fammi una panoramica") pesca solo il vicinato
locale. Qui si aggiunge — SENZA toccare il retrieval principale — un campione DIVERSIFICATO
per dominio/source, reso come poche righe brevi da iniettare nel context.

Vincoli (decisi da Stefano): max 3 righe dal dominio corrente + max 12 laterali da altri
domini (totale ≤15); read-only, touch=False (chiedere "cosa sai di me" NON deve rinforzare le
memorie); dichiarato nel context come CAMPIONE rappresentativo, non scansione completa di Redis.
"""
import re

from core.memory_risk import memory_epistemic_rank, memory_verification_suffix

# Domande panoramiche/autobiografiche (generiche, non lessico di settore).
_WIDE_RE = re.compile(
    r'\bcosa\s+(?:sai|ricordi|conosci)\s+(?:di|su)\s+me\b'
    r'|\bche\s+(?:cosa\s+)?(?:sai|ricordi)\s+(?:di|su)\s+me\b'
    r'|\bcos[\'’]?\s*altro\s+sai\b'
    r'|\bhai\s+altre\s+informazioni\s+su\s+di\s+me\b'
    r'|\bricordi\s+non\s+lavorativ'
    r'|\bcosa\s+sai\s+(?:di\s+me\s+)?oltre\b'
    r'|\b(?:oltre|a\s+parte)\s+(?:al\s+|il\s+)?lavoro\b'
    r'|\b(?:fammi|dammi|fai)\s+una\s+panoramica\b'
    r'|\bpanoramica\s+(?:di|su|delle|generale|completa)\b'
    r'|\bche\s+ricordi\s+hai\b'
    r'|\bchi\s+sono\s+per\s+te\b',
    re.IGNORECASE,
)

# Priorità di sorgente per scegliere il rappresentante di un dominio: prima ciò che è già
# sintetizzato/curato (loop2e consolidati, reflection, user/teach), poi il resto.
_SRC_PRIORITY = {
    "loop2e": 5, "reflection": 4, "user": 4, "teach": 4,
    "episode": 2, "conversation": 1, "passive": 1, "web": 0,
}

_HEADER_RE = re.compile(r'^\s*#\s*Memoria\s*\([^)]*\)\s*', re.IGNORECASE)


def is_wide_recall_query(text: str) -> bool:
    """True se la domanda è panoramica/autobiografica (merita il wide recall)."""
    return bool(text and _WIDE_RE.search(text))


def _gist(content: str, width: int = 90) -> str:
    c = _HEADER_RE.sub("", content or "")
    c = " ".join(c.split())
    return c if len(c) <= width else c[: width - 1].rstrip() + "…"


def _doc_id(doc: dict, key) -> str:
    if doc.get("id"):
        return str(doc["id"])
    if isinstance(key, bytes):
        key = key.decode("utf-8", errors="ignore")
    return str(key).rsplit(":", 1)[-1]


def build_wide_recall_map(
    r,
    current_domain: str | None = None,
    max_current: int = 3,
    max_lateral: int = 12,
    *,
    include_ids: bool = False,
) -> list[str] | list[tuple[str, str]]:
    """
    Campione diversificato per dominio (read-only, touch=False — solo SCAN + JSON.GET).
    Ritorna righe "dominio — gist": fino a max_current dal dominio corrente + fino a
    max_lateral da altri domini (un rappresentante per dominio). Esclude superseded e web.
    """
    by_domain: dict[str, list[tuple]] = {}
    for key in r.scan_iter("euri:memory:*"):
        try:
            d = r.json().get(key, "$")
        except Exception:
            continue
        if not d:
            continue
        doc = d[0]
        if doc.get("superseded_by") or doc.get("correction_pending") or doc.get("source") == "web":
            continue
        dom = doc.get("domain") or "generale"
        score = (
            memory_epistemic_rank(doc),
            -_SRC_PRIORITY.get(doc.get("source") or "", 0),
            -int(doc.get("recalled_count") or 0),
            -float(doc.get("created_at") or 0),
        )
        by_domain.setdefault(dom, []).append((score, doc, _doc_id(doc, key)))

    rows = []
    used: set[str] = set()

    # Dominio corrente: fino a max_current memorie (le meglio scoranti)
    if current_domain and current_domain in by_domain:
        for score, doc, mid in sorted(by_domain[current_domain], key=lambda x: x[0])[:max_current]:
            row = f"{current_domain} — {_gist(doc.get('content', ''))}{memory_verification_suffix(doc)}"
            rows.append((mid, row) if include_ids else row)
        used.add(current_domain)

    # Laterali: un rappresentante per ALTRO dominio, ordinati per ricchezza (score)
    laterals = []
    for dom, items in by_domain.items():
        if dom in used:
            continue
        best = min(items, key=lambda x: x[0])
        laterals.append((best[0], dom, best[1], best[2]))
    for score, dom, doc, mid in sorted(laterals, key=lambda x: x[0])[:max_lateral]:
        row = f"{dom} — {_gist(doc.get('content', ''))}{memory_verification_suffix(doc)}"
        rows.append((mid, row) if include_ids else row)

    return rows
