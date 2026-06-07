"""
Client Ollama instradati — UN punto solo per i due host configurabili.

Prima: tutte le chiamate usavano `ollama.chat(...)` module-level → sempre il default
localhost della libreria, e `config.OLLAMA_HOST` era morto (definito ma mai usato).
Ora: due Client distinti, uno per il realtime (Gemma) e uno per il Dream Engine (Qwen),
con host da config (default localhost → comportamento offline invariato).

I due Client sono creati UNA volta qui; i call site li referenziano (niente Client sparsi).
"""
import ollama
import config

# Realtime (Gemma): brain, validator, classifier, domain gating, ecc.
chat_client = ollama.Client(host=config.CHAT_OLLAMA_HOST)

# Dream Engine + Loop 2h (Qwen): ragionamento notturno.
dream_client = ollama.Client(host=config.DREAM_OLLAMA_HOST)
