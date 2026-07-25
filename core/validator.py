"""
Buttafuori — validatore LLM prima del salvataggio in Redis.

I salvataggi espliciti vengono ripuliti e riscritti. Il percorso passivo usa
invece un gate KEEP/JUNK che non modifica il contenuto prima dell'audit delle
fonti.
"""
import ollama
from core.ollama_client import chat_client
import config
from loguru import logger

_PROMPT = """\
L'utente vuole salvare questo {intent_type}: '{text}'

Il testo contiene informazioni reali e utili da ricordare, oppure è solo conversazione, una domanda, un frammento senza senso o spazzatura?

Se è spazzatura, conversazione, una domanda o un frammento inutile: rispondi solo con la parola JUNK.
Se è reale e utile: riscrivilo in modo sintetico e pulito, in italiano, in una sola frase.

IMPORTANTE: riscrivi SOLO il contenuto informativo. NON aggiungere prefissi come "Salvare:", "Todo:", "Ricordare di:" o simili. Scrivi direttamente la frase pulita."""

_PASSIVE_PROMPT = """\
Valuta questa memoria già estratta da una conversazione:

'{text}'

Contiene un'informazione concreta e riutilizzabile, oppure è soltanto
conversazione generica, una domanda, un frammento senza senso o spazzatura?

Non riscrivere, non correggere e non aggiungere nulla: la fedeltà alla fonte
verrà verificata separatamente.
Rispondi con una sola parola: KEEP oppure JUNK."""


def validate_payload(text: str, intent_type: str) -> str | None:
    """
    Valida il contenuto prima del salvataggio.
    Ritorna il testo pulito e riscritto, oppure None se è spazzatura.
    intent_type: "memory" | "todo" | "note"
    """
    if not text or len(text.strip()) < 3:
        return None
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(intent_type=intent_type, text=text)
            }],
            options={"temperature": 0, "num_predict": 60},
            think=False,
        )
        result = (response.message.content or "").strip()
        if not result or result.upper().startswith("JUNK"):
            logger.info(f"Buttafuori: JUNK → '{text[:60]}'")
            return None
        logger.info(f"Buttafuori: OK → '{result[:60]}'")
        return result
    except Exception as e:
        logger.warning(f"Buttafuori error (passthrough): {e}")
        return text  # in caso di errore LLM, lascia passare


def validate_passive_payload(text: str) -> str | None:
    """Gate passivo senza riscrittura: KEEP restituisce il testo identico.

    Il Passive learner ha già formulato una memoria autosufficiente. Una seconda
    riscrittura può introdurre dettagli senza fonte; la copertura semantica viene
    quindi verificata dal provenance audit sul contenuto originale.
    """
    if not text or len(text.strip()) < 3:
        return None
    original = text.strip()
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": _PASSIVE_PROMPT.format(text=original),
                }
            ],
            options={"temperature": 0, "num_predict": 10},
            think=False,
        )
        verdict = (response.message.content or "").strip().upper().strip(" .,:;!?")
        if verdict == "KEEP":
            logger.info(f"Buttafuori passivo: KEEP → '{original[:60]}'")
            return original
        logger.info(f"Buttafuori passivo: JUNK/ambiguo → '{original[:60]}'")
        return None
    except Exception as exc:
        # L'audit di provenienza successivo resta fail-closed; un hiccup di
        # questo gate non deve perdere da solo un fatto potenzialmente valido.
        logger.warning(f"Buttafuori passivo error (passthrough verso audit): {exc}")
        return original
