"""
Client Ollama instradati — UN punto solo per i due host configurabili.

Prima: tutte le chiamate usavano `ollama.chat(...)` module-level → sempre il default
localhost della libreria, e `config.OLLAMA_HOST` era morto (definito ma mai usato).
Ora: due Client distinti, uno per il realtime (Gemma) e uno per il Dream Engine (Qwen),
con host da config (default localhost → comportamento offline invariato).

I Client restano centralizzati qui. Per i loop offline sono disponibili istanze
cacheate con timeout HTTP reale: il limite interrompe la socket, non soltanto
l'attesa del thread chiamante.
"""
from functools import lru_cache

import ollama
import config

# Realtime (Gemma): brain, validator, classifier, domain gating, ecc.
chat_client = ollama.Client(host=config.CHAT_OLLAMA_HOST)

# Dream Engine + Loop 2h (Qwen): ragionamento notturno.
dream_client = ollama.Client(host=config.DREAM_OLLAMA_HOST)


@lru_cache(maxsize=8)
def get_dream_client(timeout_s: float | None = None):
    """Client Dream con timeout di trasporto; ``None`` mantiene il client storico."""
    if timeout_s is None:
        return dream_client
    return ollama.Client(
        host=config.DREAM_OLLAMA_HOST,
        timeout=max(1.0, float(timeout_s)),
    )
