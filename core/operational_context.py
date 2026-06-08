"""
Contesto operativo opzionale — modulo di portabilità.

Se esiste EURI_CONTEXT.md (path override via env EURI_CONTEXT_FILE), il suo contenuto viene
iniettato come messaggio `system` nel prompt del modello realtime (Gemma, via brain.respond)
e notturno (Qwen, via dream_engine._ollama_chat). Dà la cornice del mondo in cui Euri opera
(es. azienda di riciclo: i parametri escono dai range del vergine ed è atteso), così il
modello smette di giudicare i materiali riciclati coi parametri del vergine da manuale.

Fail-open totale: se il file manca / è vuoto / illeggibile, ritorna "" e Euri parte IDENTICO
a ora (condizione di portabilità). Letto UNA volta e cacheato (Euri si riavvia per cambiarlo).
Per design il file deve restare SOLO descrittivo, senza istruzioni né fatti di dominio.
"""
import os
import re

_CONTEXT_FILE = os.environ.get(
    "EURI_CONTEXT_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "EURI_CONTEXT.md"),
)

# Cappello fisso (NON nel file: il file resta prosa pulita) — inquadra il blocco per il modello.
_HEADER = (
    "[Contesto operativo — la cornice del mondo in cui operi, vera prima di ogni "
    "conversazione. Descrive l'ambiente, non detta conclusioni; i fatti specifici li "
    "impari e li ricordi.]"
)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_cache = None  # sentinella: None = non ancora caricato


def load_operational_context() -> str:
    """
    Ritorna il blocco di contesto (cappello + corpo del file) oppure "" se il file manca/è
    vuoto. Cacheato dopo la prima lettura. Fail-open su qualunque errore.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_CONTEXT_FILE, encoding="utf-8") as f:
            raw = f.read()
        body = _COMMENT_RE.sub("", raw).strip()
        _cache = f"{_HEADER}\n{body}" if body else ""
    except FileNotFoundError:
        _cache = ""
    except Exception:
        _cache = ""
    return _cache
