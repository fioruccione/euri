"""
Fallback LLM per la classificazione degli intent non catturati dalle regex.

Pipeline (V2.18):
  1. ToolRegistry VectorSet (Fast Path, ~100ms embed + ~1ms KNN) — Redis 8.8 nativo
  2. AdaptiveClassifier (Welford-based) — sospeso oggi
  3. LLM Gemma 26B (Slow Path, ~600ms) — fallback se Fast Path ritorna None

Viene chiamato SOLO quando il router regex restituisce CHAT.
Verifica solo i 6 intent critici dove il fallback ha valore reale.
Tutto il resto resta CHAT — non vogliamo over-classify.
"""
import re
import time
import ollama
from core.ollama_client import chat_client
import config
from loguru import logger

# Singleton del classificatore adattivo (viene inizializzato da voice_daemon)
_adaptive_clf = None

# Singleton del ToolRegistry per Fast Path VectorSet (V2.18) — iniettato da voice_daemon
_tool_registry = None


def set_adaptive_classifier(clf) -> None:
    """Iniettato da voice_daemon dopo il caricamento dell'embedder."""
    global _adaptive_clf
    _adaptive_clf = clf


def set_tool_registry(reg) -> None:
    """Iniettato da voice_daemon dopo il bootstrap del catalogo tool (V2.18)."""
    global _tool_registry
    _tool_registry = reg


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
Classifica questa frase con UNA SOLA parola tra: WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, COMPLETE, CHAT.

Definizioni PRECISE — ogni confine è importante:

EXECUTE: l'utente vuole DATI HARDWARE del sistema. Menziona esplicitamente: cpu, ram, gpu, disco, spazio, processi, uptime, log di errore, temperatura. Es: "controlla la cpu", "quanta ram ho", "spazio su disco", "leggi il log". NON è EXECUTE: "funziona?", "stai bene?", "come va?" — queste sono CHAT.

SEARCH: l'utente vuole che Euri cerchi nelle SUE MEMORIE INTERNE. Es: "ricordi X?", "hai in memoria X?", "di cosa stavamo parlando?", "cerca i ricordi di ieri", "cosa sai di X?". NON è SEARCH: cercare su internet.

WEB_SEARCH: cerca su INTERNET, detto esplicitamente ("online", "internet", "nel web", "su Google"). NON è WEB_SEARCH: cercare nei ricordi di Euri.

SAVE_TODO: vuole ricordarsi di FARE qualcosa in futuro. Es: "devo fare X", "ricordami di chiamare X", "segna che devo X".

SAVE_MEMORY: vuole che Euri ricordi un FATTO. Es: "ricordati che X", "segna che X", "tieni a mente che X".

COMPLETE: l'utente dice di aver APPENA COMPLETATO un compito specifico, in modo diretto e breve. Es: "l'ho fatto", "ho chiamato Mario", "ho inviato la mail", "ho pagato la fattura". NON è COMPLETE: narrazioni lunghe di ciò che è successo durante la giornata, racconti con dettagli tecnici, frasi che descrivono un processo o un risultato.

CHAT: tutto il resto — conversazione, domande generali, narrazioni, spiegazioni, saluti.

Frase: "{text}"

Rispondi SOLO con una delle sette parole. Nient'altro."""


def llm_fallback_classify(text: str) -> str | None:
    """
    Ritorna una stringa intent ("WEB_SEARCH", "SAVE_TODO", "SAVE_MEMORY", "EXECUTE")
    oppure None se la risposta è CHAT o non riconoscibile.

    Pipeline V2.18:
      1. Fast Path VectorSet (Redis 8.8 nativo): KNN su descrizioni tool, ~100-200ms
      2. Adaptive Classifier (Welford) — sospeso oggi
      3. LLM Gemma fallback (Slow Path, ~600ms) se Fast Path None
    """
    # ── Fast Path V2.18: ToolRegistry VectorSet ──
    if _tool_registry is not None and config.TOOL_VECTORSET_ENABLED:
        t0 = time.time()
        match = _tool_registry.match_tool(text)
        total_ms = (time.time() - t0) * 1000
        if match:
            intent = match["intent"]
            # Stessa guard manifatturiero del Layer LLM — vale anche qui:
            # un EXECUTE che cade su contesto chimica/polimeri va bloccato a monte
            # del routing (non vogliamo che il VectorSet scavalchi questa regola).
            if intent == "EXECUTE" and _is_manufacturing_context(text):
                logger.debug(
                    f"[INTENT_FAST] EXECUTE bloccato (contesto manifatturiero): "
                    f"'{text[:50]}' (score={match['score']:.3f})"
                )
                # Non return None: lasciamo lo Slow Path decidere — magari l'LLM
                # capisce meglio il contesto e classifica come CHAT/SEARCH.
            else:
                logger.info(
                    f"[INTENT_FAST] {intent} via VectorSet — "
                    f"total={total_ms:.0f}ms vsim={match['elapsed_ms']:.1f}ms "
                    f"score={match['score']:.3f}"
                )
                return intent
        else:
            logger.debug(f"[INTENT_FAST] no match → fallback Slow Path ({total_ms:.0f}ms)")

    # ── Layer 1: Adaptive classifier (Welford) ──
    if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
        result = _adaptive_clf.classify(text)
        if result is not None:
            if result.value == "EXECUTE" and _is_manufacturing_context(text):
                logger.debug(f"EXECUTE bloccato: contesto manifatturiero — '{text[:50]}'")
            else:
                return result.value

    # ── Layer 2 (Slow Path): LLM fallback ──
    try:
        t0 = time.time()
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
            options={"temperature": 0, "num_predict": 400},
            think=False,
        )
        elapsed_ms = (time.time() - t0) * 1000
        result = _clean(response.message.content or "").upper().split()[0] if (response.message.content or "").strip() else ""
        if result in ("WEB_SEARCH", "SEARCH", "SAVE_TODO", "SAVE_MEMORY", "EXECUTE", "COMPLETE"):
            if result == "EXECUTE" and _is_manufacturing_context(text):
                logger.debug(f"[INTENT_SLOW] EXECUTE LLM bloccato: contesto manifatturiero — '{text[:50]}'")
                return None
            logger.info(f"[INTENT_SLOW] LLM fallback ({elapsed_ms:.0f}ms): '{text[:50]}' → {result}")

            # ── Layer 3: Feedback Loop Welford ──
            if _adaptive_clf is not None and config.ADAPTIVE_CLASSIFIER_ENABLED:
                _adaptive_clf.update(text, result)

            return result
        return None
    except Exception as e:
        logger.debug(f"[INTENT_SLOW] LLM fallback error: {e}")
        return None
