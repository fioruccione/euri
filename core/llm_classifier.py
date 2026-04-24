"""
Fallback LLM per la classificazione degli intent non catturati dalle regex.

Pipeline:
  1. AdaptiveClassifier (5ms) — stato Welford persistito (es. n=15, σ=0.03)
  2. LLM Gemma 26B (600ms)    — fallback solo se embedding < soglia adattiva

Viene chiamato SOLO quando il router regex restituisce CHAT.
Verifica solo i 6 intent critici dove il fallback ha valore reale.
Tutto il resto resta CHAT — non vogliamo over-classify.
"""
import re
import ollama
import config
from loguru import logger

# Singleton del classificatore adattivo (viene inizializzato da voice_daemon)
_adaptive_clf = None


def set_adaptive_classifier(clf) -> None:
    """Iniettato da voice_daemon dopo il caricamento dell'embedder."""
    global _adaptive_clf
    _adaptive_clf = clf


def _clean(text: str) -> str:
    """Rimuove il reasoning interno di Gemma 4 dal content."""
    if not text:
        return ""
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


_PROMPT = """\
Classifica questa frase con UNA SOLA parola tra: WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, CHAT.

Regole:
- WEB_SEARCH: l'utente vuole cercare qualcosa su internet / nel web (esplicito: "cerca online", "cerca su internet", "cerca nel web")
- SEARCH: l'utente chiede se Euri RICORDA o HA IN MEMORIA qualcosa — cerca nella memoria interna di Euri, NON su internet (es: "ricordi X?", "hai in memoria X?", "cosa sai di X?", "hai informazioni su X?")
- SAVE_TODO: l'utente vuole ricordarsi di fare qualcosa in futuro (azione, appuntamento, compito)
- SAVE_MEMORY: l'utente vuole che Euri ricordi un fatto, un'informazione, un dato importante
- EXECUTE: l'utente vuole controllare lo stato del sistema (CPU, RAM, GPU, disco, processi, uptime, log)
- CHAT: tutto il resto — domande generali, conversazione, spiegazioni, saluti

Frase: "{text}"

Rispondi SOLO con una delle sei parole. Nient'altro."""


def llm_fallback_classify(text: str) -> str | None:
    """
    Ritorna una stringa intent ("WEB_SEARCH", "SAVE_TODO", "SAVE_MEMORY", "EXECUTE")
    oppure None se la risposta è CHAT o non riconoscibile.

    Pipeline:
      1. Prova l'Adaptive Classifier (Welford-based, ~5ms)
      2. Se non sicuro, chiama il LLM Gemma (lento, ~600ms)
      3. Se LLM classifica con successo, aggiorna il centroide Welford per imparare
    """
    # ── Layer 1: Adaptive classifier (Welford) ──
    if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
        result = _adaptive_clf.classify(text)
        if result is not None:
            return result.value

    # ── Layer 2: LLM fallback ──
    try:
        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
            options={"temperature": 0, "num_predict": 400},
            think=False,
        )
        result = _clean(response.message.content or "").upper().split()[0] if (response.message.content or "").strip() else ""
        if result in ("WEB_SEARCH", "SEARCH", "SAVE_TODO", "SAVE_MEMORY", "EXECUTE"):
            logger.info(f"LLM fallback: '{text[:50]}' → {result}")
            
            # ── Layer 3: Feedback Loop Welford ──
            if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
                _adaptive_clf.update(text, result)
                
            return result
        return None
    except Exception as e:
        logger.debug(f"LLM fallback error: {e}")
        return None
