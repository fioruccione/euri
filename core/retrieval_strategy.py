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
  - entity_recall   : nomi/ruoli/chi fa cosa/relazioni tra entità
  - recent_context  : si risolve con la conversazione recente (nessun augmento: la history è
                      già nel contesto del brain)

Vincoli (Stefano): leggero e additivo; non toccare il retrieval normale quando la strategia è
specific_search; usare Gemma solo quando serve; fallback TOTALE a specific_search su
errore/confidence bassa/pre-gate non scattata. Non tocca passive learner, save, correzioni.
Vedi [[project_euri_memory_controller]].
"""
import re

from core.memory_risk import memory_risk_rank, memory_verification_suffix
from core.wide_recall import build_wide_recall_map, _gist

STRATEGIES = ("specific_search", "wide_recall", "subject_recall", "entity_recall", "recent_context")
_CONF_FLOOR = 0.6

# Pre-gate cheap: cue di domanda APERTA / panoramica / narrativa. Se NON scatta → si assume
# specific_search SENZA chiamare Gemma. Volutamente largo sui cue aperti, ma NON scatta su
# domande fattuali secche ('quanto pesa…', 'quando scade…') prive di cue aperti.
_OPEN_RE = re.compile(
    r'\bcosa\s+(?:sai|ricordi|conosci)\b'
    r'|\bche\s+(?:cosa\s+)?(?:sai|ricordi|conosci)\b'
    r'|\bquali?\s+(?:progett|cose|argoment|temi|material|nomi|ruoli?)'
    r'|\bche\s+progett'
    r'|\bparlami\b|\braccontami\b|\bdimmi\s+(?:di|tutto\s+(?:su|di))\b'
    r'|\bpanoramica\b|\bfai\s+(?:il\s+punto|un\s+quadro|una\s+sintesi)\b'
    r'|\belenca\b|\bmetti\s+in\s+ordine\b|\briassumi\b'
    r'|\bchi\s+(?:sono|è|fa|lavora|si\s+occupa)\b|\bche\s+ruol[oi]\b'
    r'|\bchi\s+ruol[oi]\b|\bcos[\'’]?\s*altro\b',
    re.IGNORECASE,
)

_ENTITY_RE = re.compile(
    r'\b(?:quali?\s+(?:nomi|ruoli?)|che\s+ruol[oi]|chi\s+(?:fa|lavora|si\s+occupa)|'
    r'chi\s+ruol[oi]|organigramma|composizione\s+del\s+gruppo)\b',
    re.IGNORECASE,
)

_ROLE_MARKER_RE = re.compile(
    r'\b(?:si\s+occupa|supervisiona|gestisc\w*|lavor(?:a|ano|iamo|ate|o)\b|team|gruppo|'
    r'collabor\w*|responsabil\w*|ruol\w*|compit\w*|comprende|'
    r'controllo\s+qualit[aà]|implementazione\s+pratica)\b',
    re.IGNORECASE,
)

_ENTITY_SRC_PRIORITY = {
    "user": 5,
    "teach": 5,
    "episode": 4,
    "conversation": 3,
    "passive": 3,
    "loop2e": 1,
    "reflection": 1,
    "web": 0,
}

_SUBJECT_SRC_PRIORITY = {
    "user": 5,
    "teach": 5,
    "episode": 4,
    "conversation": 4,
    "passive": 4,
    "reflection": 2,
    "loop2e": 2,
    "reaction": 1,
    "web": 0,
}


def _doc_id(doc: dict, key) -> str:
    if doc.get("id"):
        return str(doc["id"])
    if isinstance(key, bytes):
        key = key.decode("utf-8", errors="ignore")
    return str(key).rsplit(":", 1)[-1]


def _split_rows(rows) -> tuple[list[str], list[str]]:
    text_rows: list[str] = []
    ids: list[str] = []
    for row in rows:
        if isinstance(row, tuple) and len(row) == 2:
            mid, text = row
            if mid:
                ids.append(str(mid))
            text_rows.append(str(text))
        else:
            text_rows.append(str(row))
    return text_rows, list(dict.fromkeys(ids))


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
    if _ENTITY_RE.search(text or "") and strat == "subject_recall":
        return "entity_recall", ""
    if strat == "subject_recall" and not subject:
        return "specific_search", ""
    return strat, subject


def build_subject_recall(
    memory,
    subject: str,
    limit: int = 12,
    *,
    include_ids: bool = False,
) -> list[str] | list[tuple[str, str]]:
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
            score = (
                memory_risk_rank(doc),
                -_SUBJECT_SRC_PRIORITY.get(doc.get("source") or "", 0),
                -int(doc.get("recalled_count") or 0),
                -float(doc.get("created_at") or 0),
            )
            found.append((score, doc, _doc_id(doc, key)))
    found.sort(key=lambda x: x[0])
    rows = []
    for _score, doc, mid in found[:limit]:
        dom = doc.get("domain") or "generale"
        row = f"[{dom}] {_gist(doc.get('content', ''), width=140)}{memory_verification_suffix(doc)}"
        rows.append((mid, row) if include_ids else row)
    return rows


def build_entity_recall(
    memory,
    text: str,
    limit: int = 12,
    *,
    include_ids: bool = False,
) -> list[str] | list[tuple[str, str]]:
    """
    Raccoglie memorie che descrivono relazioni tra entità: nomi, ruoli, responsabilità,
    chi fa cosa. È agnostico rispetto al dominio: usa marcatori linguistici generali,
    non liste di persone, reparti o aziende.
    """
    query_tokens = {
        w.lower() for w in re.findall(r'\b\w{4,}\b', text or "")
        if w.lower() not in {
            "cosa", "quali", "quale", "come", "invece", "conosci", "parliamo",
            "azienda", "organigramma", "nomi", "nome", "ruoli", "ruolo",
            "dell", "della", "delle", "degli", "dello", "ricordi", "quello",
            "quella", "questo", "questa", "lavora", "lavorano", "lavorare",
            "detto", "prima",
        }
    }
    found = []
    for key in memory.r.scan_iter("euri:memory:*"):
        try:
            d = memory.r.json().get(key, "$")
        except Exception:
            continue
        if not d:
            continue
        doc = d[0]
        source = doc.get("source") or ""
        if doc.get("superseded_by") or source == "web":
            continue
        content = doc.get("content") or ""
        if content.strip().lower().startswith("[confronto]"):
            continue
        if content.strip().lower().startswith("testo analizzato dagli appunti:"):
            continue
        if not _ROLE_MARKER_RE.search(content):
            continue
        low = content.lower()
        overlap = sum(1 for tok in query_tokens if tok in low)
        if source in {"teach", "reflection", "loop2e"} and overlap == 0:
            continue
        role_hits = len(_ROLE_MARKER_RE.findall(content))
        score = (
            memory_risk_rank(doc),
            -overlap,
            -_ENTITY_SRC_PRIORITY.get(source, 0),
            -role_hits,
            -float(doc.get("created_at") or 0),
            -int(doc.get("recalled_count") or 0),
        )
        found.append((score, doc, _doc_id(doc, key)))
    found.sort(key=lambda x: x[0])
    rows = []
    for _score, doc, mid in found[:limit]:
        dom = doc.get("domain") or "generale"
        src = doc.get("source") or "?"
        row = f"[{src}/{dom}] {_gist(doc.get('content', ''), width=150)}{memory_verification_suffix(doc)}"
        rows.append((mid, row) if include_ids else row)
    return rows


def augment_context_with_ids(
    text: str,
    context: str,
    memory,
    brain,
    recent_history=None,
) -> tuple[str, str, list[str]]:
    """
    Amplia il context secondo la strategia scelta (Gradino 2). specific_search/recent_context
    → context INVARIATO (retrieval normale intatto). Ritorna (context, note) dove note è una
    breve stringa di log ('' se nessun augmento). Fail-safe: su qualunque errore, invariato.
    """
    try:
        strategy, subject = choose_strategy(text, brain, recent_history)
    except Exception:
        return context, "", []

    if strategy == "wide_recall":
        try:
            _rec = memory.get_recent_memories(limit=1, touch=False)
            _cur = _rec[0].get("domain") if _rec else None
            raw_rows = build_wide_recall_map(memory.r, current_domain=_cur, include_ids=True)
            rows, ids = _split_rows(raw_rows)
        except Exception:
            rows = []
            ids = []
        if rows:
            context = (context + "\n\n" if context else "") + (
                "[Panoramica per AREE della tua memoria — CAMPIONE rappresentativo, NON "
                "esaustivo (non è una scansione completa di Redis). Presenta le aree che "
                "conosci e offri di approfondirne una; non dichiarare di sapere tutto.]\n"
                + "\n".join(f"- {r}" for r in rows)
            )
            return context, f"wide_recall (+{len(rows)} aree)", ids
        return context, "", []

    if strategy == "subject_recall" and subject:
        try:
            raw_rows = build_subject_recall(memory, subject, include_ids=True)
            rows, ids = _split_rows(raw_rows)
        except Exception:
            rows = []
            ids = []
        if rows:
            context = (context + "\n\n" if context else "") + (
                f"[Ciò che ricordi sul soggetto «{subject}» — CAMPIONE dalle memorie; se manca "
                "qualcosa dillo onestamente, non inventare.]\n"
                + "\n".join(f"- {r}" for r in rows)
            )
            return context, f"subject_recall «{subject}» (+{len(rows)})", ids
        return context, "", []

    if strategy == "entity_recall":
        try:
            raw_rows = build_entity_recall(memory, text, include_ids=True)
            rows, ids = _split_rows(raw_rows)
        except Exception:
            rows = []
            ids = []
        if rows:
            context = (context + "\n\n" if context else "") + (
                "[Memorie su entità, ruoli e relazioni — usa questi fatti diretti; "
                "se mancano dettagli, dichiaralo senza inventare.]\n"
                + "\n".join(f"- {r}" for r in rows)
            )
            return context, f"entity_recall (+{len(rows)})", ids
        return context, "", []

    return context, "", []  # specific_search / recent_context → nessun augmento


def augment_context(text: str, context: str, memory, brain, recent_history=None) -> tuple[str, str]:
    """
    Compatibilità per chiamanti che non tracciano provenance dell'augment.
    Usa augment_context_with_ids e scarta gli ID.
    """
    context, note, _ids = augment_context_with_ids(text, context, memory, brain, recent_history)
    return context, note
