"""
Buttafuori — validatore LLM prima del salvataggio in Redis.

Chiamato su ogni SAVE_MEMORY / SAVE_TODO / SAVE_NOTE prima di scrivere.
Se il contenuto è spazzatura o conversazione, blocca il salvataggio.
Se è reale, restituisce il testo riscritto in modo pulito e sintetico.
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
