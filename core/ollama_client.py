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


class RealtimeClient(ollama.Client):
    """Client Gemma che mantiene invariata la configurazione del runner.

    Il context e' una proprieta' fisica del runner Ollama, non un innocuo limite
    per-request: alternare 4096/16384/32768 forza unload e reload del modello.
    Centralizzarlo qui copre anche i call site che omettono ``num_ctx`` e
    impedisce a una nuova utility di reintrodurre accidentalmente il problema.
    """

    def chat(self, *args, **kwargs):
        model = kwargs.get("model")
        if model is None and args:
            model = args[0]
        if model == config.OLLAMA_MODEL:
            raw_options = kwargs.get("options")
            if raw_options is None:
                options = {}
            elif isinstance(raw_options, dict):
                options = dict(raw_options)
            elif hasattr(raw_options, "model_dump"):
                options = raw_options.model_dump(exclude_none=True)
            else:
                options = dict(raw_options)
            options["num_ctx"] = config.CHAT_OLLAMA_NUM_CTX
            kwargs["options"] = options
        return super().chat(*args, **kwargs)


# Realtime (Gemma): brain, validator, classifier, domain gating, ecc.
chat_client = RealtimeClient(host=config.CHAT_OLLAMA_HOST)

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
