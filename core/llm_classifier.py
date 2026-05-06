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


_MANUFACTURING_TERMS = {
    "xrf", "talco", "carbonato", "polimero", "polipropilene", "polietilene",
    "polistirolo", "mfi", "izod", "stampo", "granulatore", "estrusione",
    "trafila", "perossido", "additivo", "carica", "reagenz", "realube",
    "vistamax", "bicarbonato", "ceneri", "residuo", "campione", "analisi",
    "laboratorio", "misurazione", "certificato", "collaudo", "specifica",
    "fornitura", "lotto", "scheda tecnica", "polirefine", "luciflast",
}
_SYSTEM_TERMS = {"cpu", "ram", "gpu", "disco", "processi", "uptime", "log", "processo"}


def _is_manufacturing_context(text: str) -> bool:
    """True se il testo ha termini manifatturieri/analitici ma nessun termine di sistema."""
    lower = text.lower()
    return (
        any(t in lower for t in _MANUFACTURING_TERMS)
        and not any(t in lower for t in _SYSTEM_TERMS)
    )


_PROMPT = """\
Classifica questa frase con UNA SOLA parola tra: WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, CHAT.

Definizioni PRECISE — ogni confine è importante:

EXECUTE: l'utente vuole DATI HARDWARE del sistema. La frase deve menzionare esplicitamente: cpu, ram, gpu, disco, spazio, processi, uptime, log di errore, temperatura. Esempi corretti: "controlla la cpu", "quanta ram ho", "spazio su disco", "leggi il log". NON è EXECUTE: "funziona?", "stai bene?", "sei operativo?", "come va?", "tutto ok?" — queste sono CHAT.

SEARCH: l'utente vuole che Euri cerchi nelle SUE MEMORIE INTERNE. Esempi: "ricordi X?", "hai in memoria X?", "cosa sai di X?", "di cosa stavamo parlando?", "cerca nelle tue memorie", "cerca i ricordi di [data/argomento]", "cosa ricordi di ieri?". NON è SEARCH: cercare su internet.

WEB_SEARCH: l'utente vuole cercare su INTERNET, lo dice esplicitamente con parole come "online", "internet", "nel web", "su Google". NON è WEB_SEARCH: cercare nei ricordi interni di Euri.

SAVE_TODO: l'utente vuole ricordarsi di FARE qualcosa in futuro (compito, appuntamento, azione). Esempi: "devo fare X", "ricordami di chiamare X", "segna che devo X tra Y giorni".

SAVE_MEMORY: l'utente vuole che Euri ricordi un FATTO o informazione. Esempi: "ricordati che X", "segna che X", "tieni a mente che X", "il cliente X si chiama Y".

CHAT: tutto il resto — conversazione normale, domande generali, chiarimenti, spiegazioni, saluti, domande su Euri che non riguardano hardware.

Frase: "{text}"

Rispondi SOLO con una delle sei parole. Nient'altro."""


def llm_fallback_classify(text: str) -> str | None:
    """
    Ritorna una stringa intent ("WEB_SEARCH", "SAVE_TODO", "SAVE_MEMORY", "EXECUTE")
    oppure None se la risposta è CHAT o non riconoscibile.

    Pipeline:
      1. Prova l'Adaptive Classifier (Welford-based, ~400ms con e5-large su CPU)
      2. Se sotto soglia, chiama il LLM Gemma (~600ms)
      3. Se LLM classifica con successo, aggiorna il centroide Welford per imparare
    """
    # ── Layer 1: Adaptive classifier (Welford) ──
    if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
        result = _adaptive_clf.classify(text)
        if result is not None:
            if result.value == "EXECUTE" and _is_manufacturing_context(text):
                logger.debug(f"EXECUTE bloccato: contesto manifatturiero — '{text[:50]}'")
            else:
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
            if result == "EXECUTE" and _is_manufacturing_context(text):
                logger.debug(f"EXECUTE LLM bloccato: contesto manifatturiero — '{text[:50]}'")
                return None
            logger.info(f"LLM fallback: '{text[:50]}' → {result}")
            
            # ── Layer 3: Feedback Loop Welford ──
            if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
                _adaptive_clf.update(text, result)
                
            return result
        return None
    except Exception as e:
        logger.debug(f"LLM fallback error: {e}")
        return None
