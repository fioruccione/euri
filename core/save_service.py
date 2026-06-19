"""
Coordinatore di salvataggio memoria — CONDIVISO tra voce (voice_daemon) e Silent Chat (ui/app).

Unica fonte di verità della logica SAVE_MEMORY:
  - risoluzione di COSA salvare: fatto diretto dopo il trigger, oppure fatto PRIMA del
    trigger nella stessa frase ("X, quindi memorizza questo"), oppure — se anaforico puro —
    sintesi dell'ultimo scambio (mix tuo turno + risposta di Euri);
  - DEDUP/MERGE costruttivo: invece del vecchio sì/no fragile (che scartava i raffinamenti
    incrementali), per il SAVE esplicito si costruisce l'UNIONE con la memoria esistente più
    simile — niente di nuovo → "già segnato"; aggiunge qualcosa → arricchisce la memoria
    (salva la fusa, soft-delete della vecchia via superseded_by) e annuncia cosa ha aggiunto.

NESSUN I/O qui: ritorna un dict, il chiamante fa output (voce: _speak; chat: markdown) e
l'eventuale logging della conversazione. is_duplicate_memory resta invariato e in uso solo
dal passive learner (conservativo, niente auto-merge passivo).
"""
import re

from loguru import logger

from core.validator import validate_payload
from core.intent_router import extract_content_after_trigger, SAVE_MEMORY_TRIGGERS

# Riferimento meta ANAFORICO: "memorizza questa informazione / queste informazioni /
# questo concetto / quello che ti ho appena spiegato / quello che hai detto / la
# conversazione". Match PIENO (^...$) = niente fatto frammisto → il contenuto NON è in
# queste parole ma nello scambio precedente. \beuri\b copre il vocativo finale ("...Euri").
_META_SAVE_REF = re.compile(
    r'^(?:\W|\s|,|\be\b|\beuri\b'
    r'|\bl[\'’]immagin[ei]\b|\bla\s+conversazione\b|\bla\s+descrizione\b'
    r'|\bquest[ae]\s+informazion[ei]\b|\bquest[oae]\s+concett[oi]\b'
    r'|\bquello\s+che\s+(?:hai\s+(?:detto|visto|descritto)|ci\s+siamo\s+detti'
    r'|ti\s+ho\s+(?:appena\s+)?(?:detto|spiegato|raccontato))\b'
    r'|\bquanto\s+(?:detto|ci\s+siamo\s+detti|(?:ti\s+ho\s+)?(?:appena\s+)?spiegato)\b'
    r'|\bquesto\b|\btutto(?:\s+questo)?\b'
    r')+$',
    re.IGNORECASE,
)

_MIN_FACT_LEN = 12        # sotto questa lunghezza un testo è un frammento, non un fatto
_SIM_MERGE_FLOOR = 0.70   # ≥ → zona di fusione costruttiva (tutto: niente gate cieco); sotto → memoria nuova
_SAVE_CONFIDENCE_FLOOR = 0.6  # sotto → il risolutore semantico cede al fallback a regex


def _norm(s: str) -> str:
    """Normalizza per confronto di identità testuale (minuscole + spazi collassati)."""
    return " ".join((s or "").lower().split())


def is_anaphoric(content: str) -> bool:
    """True se il payload è un puro riferimento meta (il fatto è altrove, non in queste parole)."""
    return bool(content and _META_SAVE_REF.match(content))


def _content_before_trigger(text: str, triggers: list[str]) -> str:
    """Testo PRIMA del primo trigger: copre 'X, quindi memorizza questo' (trigger a fine frase)."""
    for trigger in triggers:
        m = re.search(trigger, text, re.IGNORECASE)
        if m:
            return text[:m.start()].strip(" ,.-")
    return ""


def _resolve_content_semantic(text: str, brain, recent_history):
    """
    Gradino 1 del controllore di memoria: risolutore SAVE semantico sul modello GIÀ CALDO.
    Prima del resolver a regex, capisce se il comando è un fatto diretto, un riferimento a
    un soggetto discusso poco fa ("ricordati il macinato di Seari" → cattura la sostanza,
    non l'etichetta) o un anaforico puro. Ritorna (content|None, kind) come _resolve_content,
    oppure None per CEDERE al fallback a regex (assenza history, errore, parse fallito,
    confidence bassa, mode sconosciuto). Vedi [[feedback_insegnamento_naturale]].
    """
    if not recent_history or not hasattr(brain, "resolve_save_intent"):
        return None
    res = brain.resolve_save_intent(text, recent_history)
    if not isinstance(res, dict) or not res:
        return None
    mode = (res.get("mode") or "").strip().lower()
    memory = (res.get("memory") or "").strip()
    try:
        conf = float(res.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf < _SAVE_CONFIDENCE_FLOOR:
        return None
    # 'direct' passa dal Buttafuori a valle; 'recent_topic'/'last_exchange' sono già
    # sintesi pulite del modello → trattati come 'mix' (niente Buttafuori).
    if mode == "direct" and len(memory) >= _MIN_FACT_LEN:
        return memory, "direct"
    if mode in ("recent_topic", "last_exchange") and len(memory) >= 3:
        return memory, "mix"
    if mode == "ask":
        return None, "ask"
    return None  # mode/memory inutilizzabili → fallback a regex


def _resolve_content(text: str, brain, prev_user_text: str, prev_assistant_text: str,
                     fresh: bool, recent_history=None):
    """
    Determina COSA salvare. Ritorna (content|None, kind) dove kind ∈
    {'direct','pre','mix','ask','fail'}. 'direct'/'pre' vanno ripuliti dal Buttafuori;
    'mix' è già una sintesi.
    Prima prova il risolutore semantico (Gradino 1); se cede (None) si usa la regex.
    """
    sem = _resolve_content_semantic(text, brain, recent_history)
    if sem is not None:
        return sem
    after = extract_content_after_trigger(text, SAVE_MEMORY_TRIGGERS)
    # A) fatto in chiaro dopo il trigger ("memorizza che X")
    if after and not is_anaphoric(after):
        return after, "direct"
    # B) trigger a fine frase: il fatto è PRIMA ("Giovanna è responsabile..., quindi memorizza questo")
    before = _content_before_trigger(text, SAVE_MEMORY_TRIGGERS)
    if before and not is_anaphoric(before) and len(before) >= _MIN_FACT_LEN:
        return before, "pre"
    # C) anaforico puro → estrai il FATTO dall'ultimo scambio (NON riassumere la
    # conversazione): fonte primaria Stefano, Euri solo per disambiguare. L'estrattore
    # esclude meta-commenti/preamboli ("il sistema non ha…", "carica un documento") →
    # memoria pulita, e find_similar/merge agganciano meglio. Niente Buttafuori a valle:
    # l'estrattore È già il filtro (a differenza del vecchio riassunto narrativo).
    if not fresh or not (prev_user_text or prev_assistant_text):
        return None, "ask"
    fact = (brain.extract_fact_from_exchange(prev_user_text, prev_assistant_text) or "").strip()
    if len(fact) < 3:
        return None, "ask"  # nessun fatto memorizzabile → chiede, non salva rumore
    return fact, "mix"


def _save_or_merge(content: str, memory, brain) -> dict:
    """Salva nuovo, oppure ARRICCHISCE la memoria esistente più simile (fusione costruttiva)."""
    match = memory.find_similar_memory(content)
    # Niente di abbastanza simile → memoria nuova
    if match is None or match.get("similarity", 0.0) < _SIM_MERGE_FLOOR:
        new_id = memory.save_memory(content, source="user")
        if not new_id:
            return {"saved": False, "merged": False, "reply": "Non sono riuscito a salvare.", "content": None}
        return {"saved": True, "merged": False, "reply": brain.confirm_save("memory", content), "content": content}
    # Identico TESTUALE → niente da fare. Skip SOLO su uguaglianza esatta normalizzata,
    # non più su una soglia cosine: "molto simile" può voler dire "stesso tema + dettaglio
    # nuovo" (lezione Poseidon), quindi tutto il resto passa dal merge → niente più gate
    # cieco sui save espliciti.
    if _norm(content) == _norm(match["content"]):
        return {"saved": False, "merged": False, "reply": "Lo avevo già segnato, ma grazie per la conferma.", "content": content}
    # Zona grigia → fusione costruttiva a 3 vie (sostituisce il vecchio probe sì/no)
    merged = (brain.merge_memories(match["content"], content) or "").strip()
    mu = merged.upper()
    if mu.startswith("NESSUNA AGGIUNTA"):
        return {"saved": False, "merged": False, "reply": "Lo avevo già segnato — non aggiunge nulla di nuovo.", "content": content}
    if (not merged) or mu.startswith("DIVERSO"):
        # Soggetto diverso (o dubbio) → salva SEPARATO, niente supersede: meglio un
        # doppione (lo consolida il Loop 2e) che conflare due entità distinte.
        new_id = memory.save_memory(content, source="user")
        if not new_id:
            return {"saved": False, "merged": False, "reply": "Non sono riuscito a salvare.", "content": None}
        return {"saved": True, "merged": False, "reply": brain.confirm_save("memory", content), "content": content}
    # Arricchimento reale (stesso soggetto) → salva la fusa, soft-delete della vecchia
    new_id = memory.save_memory(merged, source="user")
    if not new_id:
        return {"saved": False, "merged": False, "reply": "Non sono riuscito a salvare.", "content": None}
    if not memory.supersede_memory(match["id"], new_id):
        # Merge PARZIALE (Codex #3): la fusa è salvata ma la vecchia non è stata ritirata →
        # doppione transitorio (lo recupera Loop 2e/2f). Non più silenzioso: supersede_memory
        # ha già loggato WARNING + tracciato in euri:integrity:failures. L'utente vede comunque
        # l'info aggiornata, quindi 'saved' resta True.
        logger.warning(f"Merge parziale: supersede di {match['id']} fallito → doppione transitorio")
    return {"saved": True, "merged": True, "reply": f"Ho aggiornato la memoria: {merged}", "content": merged}


def save_memory_command(
    text: str,
    memory,
    brain,
    prev_user_text: str = "",
    prev_assistant_text: str = "",
    fresh: bool = True,
    recent_history=None,
) -> dict:
    """
    Esegue SAVE_MEMORY in modo channel-agnostic a partire dal comando completo `text`.
    Ritorna {'saved': bool, 'merged': bool, 'reply': str, 'content': str|None}.
    Il chiamante parla/stampa 'reply' e, se 'saved', logga la conversazione.
    `recent_history` (lista di {role,content}) abilita il risolutore semantico Gradino 1.
    """
    content, kind = _resolve_content(text, brain, prev_user_text, prev_assistant_text,
                                     fresh, recent_history)
    if content is None:
        reply = "Cosa devo ricordare?" if kind == "ask" else "Non sono riuscito a capire cosa salvare."
        return {"saved": False, "merged": False, "reply": reply, "content": None}
    # I fatti diretti/pre-trigger passano dal Buttafuori (pulizia + filtro JUNK);
    # la sintesi 'mix' è già validata.
    if kind in ("direct", "pre"):
        clean = validate_payload(content, "memory")
        if not clean:
            return {"saved": False, "merged": False, "reply": "Non sembra una cosa utile da ricordare.", "content": None}
        content = clean
    return _save_or_merge(content, memory, brain)
