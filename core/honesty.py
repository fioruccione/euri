"""
Pavimento di onestà sul SALVATAGGIO — guard deterministico sull'output di Euri.

Negli handler che NON salvano (CHAT, SEARCH, mobile), l'LLM a volte rivendica un
salvataggio persistente che non è avvenuto ("ho memorizzato…", "ho aggiornato la
memoria"). Il prompt da solo non basta (Gemma asseconda quando la conversazione *parla*
di salvare). Qui, a valle di brain.respond, si rimuovono le frasi che rivendicano un
salvataggio persistente e si sostituiscono con un'offerta onesta — che dice anche COME
salvare davvero (così l'utente non deve ricordare la "formula magica" a memoria).

Ambito: SOLO il salvataggio (il pavimento). NON tocca la memoria di conversazione
("lo tengo a mente / per ora ricordo" = vera, la sessione è memoria quanto Redis).
Si applica SOLO nei handler che non salvano: i veri salvataggi (confirm_save, merge in
save_service) non passano da qui, quindi le loro conferme restano intatte.
"""
import re

# Rivendicazioni di salvataggio PERSISTENTE nell'output di Euri (prima persona).
# NON include "tengo a mente / ricordo / terrò presente" (memoria di sessione = vera).
_SAVE_CLAIM_RE = re.compile(
    r'\b('
    r'ho\s+(?:gi[àa]\s+)?(?:memorizzat[oaei]|salvat[oaei]|annotat[oaei]|registrat[oaei]|segnat[oaei]|fissat[oaei])'
    r'|ho\s+(?:gi[àa]\s+)?preso\s+nota'
    r'|ho\s+integrat[oi]\b[^.\n]{0,30}\bmemoria'
    r'|aggiornat[oi]\s+la\s+(?:mia\s+)?memoria'
    r'|(?:l[\'’]ho|li\s+ho|le\s+ho)\s+(?:memorizzat|salvat|fissat|annotat|registrat)[oaei]'
    r'|memorizzat[oi]\s+in\s+modo\s+permanente'
    r'|tutto\s+(?:memorizzat[oi]|salvat[oi])'
    r')\b',
    re.IGNORECASE,
)

_HONEST_OFFER = "Se vuoi che resti in memoria, dimmi «memorizza questo» e lo fisso."


def scrub_unbacked_save_claim(text: str) -> str:
    """
    In un handler che NON salva: se `text` rivendica un salvataggio persistente, rimuove
    le frasi che lo rivendicano e aggiunge un'offerta onesta. Altrimenti ritorna invariato.
    """
    if not text or not _SAVE_CLAIM_RE.search(text):
        return text
    sentences = re.split(r'(?<=[.!?…])\s+', text.strip())
    kept = [s for s in sentences if not _SAVE_CLAIM_RE.search(s)]
    cleaned = " ".join(kept).strip()
    return f"{cleaned}\n\n{_HONEST_OFFER}" if cleaned else _HONEST_OFFER
