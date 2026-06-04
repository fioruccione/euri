"""
Coordinatore di salvataggio memoria — CONDIVISO tra voce (voice_daemon) e Silent Chat (ui/app).

Unica fonte di verità della logica SAVE_MEMORY: risoluzione anaforica (mix dell'ultimo
scambio), Buttafuori, dedup, salvataggio source="user", testo di conferma.
NESSUN I/O qui: ritorna un dict, il chiamante fa output (voce: _speak; chat: markdown)
e l'eventuale logging della conversazione.
"""
import re

from core.validator import validate_payload

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


def is_anaphoric(content: str) -> bool:
    """True se il payload è un puro riferimento meta (il fatto è nello scambio precedente)."""
    return bool(content and _META_SAVE_REF.match(content))


def _persist(content: str, memory, brain) -> dict:
    if memory.is_duplicate_memory(content, llm_probe_fn=brain.probe_same_meaning):
        return {"saved": False, "reply": "Lo avevo già segnato, ma grazie per la conferma.", "content": content}
    memory.save_memory(content, source="user")
    reply = brain.confirm_save("memory", content)
    return {"saved": True, "reply": reply, "content": content}


def save_memory_command(
    content: str,
    memory,
    brain,
    prev_user_text: str = "",
    prev_assistant_text: str = "",
    fresh: bool = True,
) -> dict:
    """
    Esegue SAVE_MEMORY in modo channel-agnostic.

    - content: payload già estratto dal trigger (extract_content_after_trigger).
    - prev_user_text / prev_assistant_text: ultimo scambio, per la risoluzione anaforica.
    - fresh: se lo scambio precedente è abbastanza recente da poter essere usato.

    Ritorna {'saved': bool, 'reply': str, 'content': str|None}. Il chiamante parla/stampa
    'reply' e, se 'saved', logga la conversazione.
    """
    # 1) ANAFORICO: il fatto è nello scambio precedente, non nel payload.
    if is_anaphoric(content):
        if not fresh or not (prev_user_text or prev_assistant_text):
            # Nessuno scambio fresco da recuperare: chiedere, non salvare il pronome (→ JUNK).
            return {"saved": False, "reply": "Cosa devo ricordare?", "content": None}
        # Mix: tuo turno (sorgente di verità) + risposta di Euri, sintesi fedele.
        exchange = ""
        if prev_user_text:
            exchange += f"Stefano: {prev_user_text}\n"
        if prev_assistant_text:
            exchange += f"Euri: {prev_assistant_text}"
        summary = (brain.summarize_knowledge(exchange) or "").strip()
        if len(summary) < 3:
            return {"saved": False, "reply": "Non sono riuscito a sintetizzare cosa salvare.", "content": None}
        return _persist(summary, memory, brain)

    # 2) Payload vuoto.
    if not content:
        return {"saved": False, "reply": "Cosa devo ricordare?", "content": None}

    # 3) Fatto diretto → Buttafuori, poi salva.
    clean = validate_payload(content, "memory")
    if not clean:
        return {"saved": False, "reply": "Non sembra una cosa utile da ricordare.", "content": None}
    return _persist(clean, memory, brain)
