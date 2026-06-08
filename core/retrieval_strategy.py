"""
Gradino 2 del controllore di memoria — sceglie la STRATEGIA di retrieval (non genera risposta).

Ruolo svolto dal modello GIÀ CALDO (Gemma realtime, via brain.classify_retrieval_strategy),
chiamato SOLO quando una pre-gate cheap (regex 0ms) sospetta una domanda non-specifica. Sulle
domande chiaramente specifiche/fattuali ('quanto pesa il Poseidon?') la pre-gate NON scatta →
nessuna chiamata a Gemma, retrieval attuale intatto, costo zero.

Strategie:
  - specific_search : domanda specifica → retrieval attuale (nessun augmento)
  - wide_recall     : panoramica/autobiografica/progetti ("cosa sai di me")
  - subject_recall  : domanda aperta su un soggetto nominato ("parlami di Poseidon")
  - recent_context  : si risolve con la conversazione recente (nessun augmento: la history è
                      già nel contesto del brain)

Vincoli (Stefano): leggero e additivo; non toccare il retrieval normale quando la strategia è
specific_search; usare Gemma solo quando serve; fallback TOTALE a specific_search su
errore/confidence bassa/pre-gate non scattata. Non tocca passive learner, save, correzioni.
Vedi [[project_euri_memory_controller]].
"""
import re

from core.wide_recall import build_wide_recall_map, _SRC_PRIORITY, _gist

STRATEGIES = ("specific_search", "wide_recall", "subject_recall", "recent_context")
_CONF_FLOOR = 0.6

# Pre-gate cheap: cue di domanda APERTA / panoramica / narrativa. Se NON scatta → si assume
# specific_search SENZA chiamare Gemma. Volutamente largo sui cue aperti, ma NON scatta su
# domande fattuali secche ('quanto pesa…', 'quando scade…') prive di cue aperti.
_OPEN_RE = re.compile(
    r'\bcosa\s+(?:sai|ricordi|conosci)\b'
    r'|\bche\s+(?:cosa\s+)?(?:sai|ricordi|conosci)\b'
    r'|\bquali?\s+(?:progett|cose|argoment|temi|material)'
    r'|\bche\s+progett'
    r'|\bparlami\b|\braccontami\b|\bdimmi\s+(?:di|tutto\s+(?:su|di))\b'
    r'|\bpanoramica\b|\bfai\s+(?:il\s+punto|un\s+quadro|una\s+sintesi)\b'
    r'|\belenca\b|\bmetti\s+in\s+ordine\b|\briassumi\b'
    r'|\bchi\s+(?:sono|è)\b|\bcos[\'’]?\s*altro\b',
    re.IGNORECASE,
)


def _maybe_nonspecific(text: str) -> bool:
    """Pre-gate cheap (0ms): la domanda PUÒ essere non-specifica → vale interpellare Gemma."""
    return bool(text and _OPEN_RE.search(text))


def choose_strategy(text: str, brain, recent_history=None) -> tuple[str, str]:
    """
    Ritorna (strategy, subject). subject valorizzato solo per subject_recall.
    specific_search se: pre-gate non scatta, brain senza classifier, errore, mode ignoto,
    confidence < floor, o subject_recall senza soggetto (inutilizzabile).
    """
    if not _maybe_nonspecific(text) or not hasattr(brain, "classify_retrieval_strategy"):
        return "specific_search", ""
    res = brain.classify_retrieval_strategy(text, recent_history)
    if not isinstance(res, dict) or not res:
        return "specific_search", ""
    strat = (res.get("strategy") or "").strip().lower()
    subject = (res.get("subject") or "").strip()
    try:
        conf = float(res.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if strat not in STRATEGIES or conf < _CONF_FLOOR:
        return "specific_search", ""
    if strat == "subject_recall" and not subject:
        return "specific_search", ""
    return strat, subject


def build_subject_recall(memory, subject: str, limit: int = 12) -> list[str]:
    """
    Raccoglie le memorie che NOMINANO il soggetto — SCAN read-only, touch=False. Match per
    substring sui token significativi del soggetto (≥4 char; es. 'Seari' da 'macinato Seari').
    Esclude superseded e web. Ordina per ricchezza di source + recall. Ritorna gist lines.
    """
    tokens = [w.lower() for w in re.findall(r'\b\w{4,}\b', subject or "")]
    if not tokens and subject:
        tokens = [subject.strip().lower()]
    if not tokens:
        return []
    found = []
    for key in memory.r.scan_iter("euri:memory:*"):
        try:
            d = memory.r.json().get(key, "$")
        except Exception:
            continue
        if not d:
            continue
        doc = d[0]
        if doc.get("superseded_by") or doc.get("source") == "web":
            continue
        low = (doc.get("content") or "").lower()
        if any(tok in low for tok in tokens):
            score = (_SRC_PRIORITY.get(doc.get("source") or "", 0),
                     int(doc.get("recalled_count") or 0))
            found.append((score, doc))
    found.sort(key=lambda x: x[0], reverse=True)
    rows = []
    for _score, doc in found[:limit]:
        dom = doc.get("domain") or "generale"
        rows.append(f"[{dom}] {_gist(doc.get('content', ''), width=140)}")
    return rows


def augment_context(text: str, context: str, memory, brain, recent_history=None) -> tuple[str, str]:
    """
    Amplia il context secondo la strategia scelta (Gradino 2). specific_search/recent_context
    → context INVARIATO (retrieval normale intatto). Ritorna (context, note) dove note è una
    breve stringa di log ('' se nessun augmento). Fail-safe: su qualunque errore, invariato.
    """
    try:
        strategy, subject = choose_strategy(text, brain, recent_history)
    except Exception:
        return context, ""

    if strategy == "wide_recall":
        try:
            _rec = memory.get_recent_memories(limit=1, touch=False)
            _cur = _rec[0].get("domain") if _rec else None
            rows = build_wide_recall_map(memory.r, current_domain=_cur)
        except Exception:
            rows = []
        if rows:
            context = (context + "\n\n" if context else "") + (
                "[Panoramica per AREE della tua memoria — CAMPIONE rappresentativo, NON "
                "esaustivo (non è una scansione completa di Redis). Presenta le aree che "
                "conosci e offri di approfondirne una; non dichiarare di sapere tutto.]\n"
                + "\n".join(f"- {r}" for r in rows)
            )
            return context, f"wide_recall (+{len(rows)} aree)"
        return context, ""

    if strategy == "subject_recall" and subject:
        try:
            rows = build_subject_recall(memory, subject)
        except Exception:
            rows = []
        if rows:
            context = (context + "\n\n" if context else "") + (
                f"[Ciò che ricordi sul soggetto «{subject}» — CAMPIONE dalle memorie; se manca "
                "qualcosa dillo onestamente, non inventare.]\n"
                + "\n".join(f"- {r}" for r in rows)
            )
            return context, f"subject_recall «{subject}» (+{len(rows)})"
        return context, ""

    return context, ""  # specific_search / recent_context → nessun augmento
