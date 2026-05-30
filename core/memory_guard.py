"""
Memory Guard — scansione anti-poisoning sull'ingest delle memorie.

Modello di minaccia di Euri: locale e mono-utente. La voce di Stefano è fidata;
i contenuti da fonti ESTERNE (ricerche web, canali in ingresso) no — una pagina
avvelenata può contenere istruzioni di override ("ignora le istruzioni", "sei ora…")
o tentativi di esfiltrazione che, salvati come memoria, riemergono poi nel contesto
dell'LLM. Questo modulo li rileva PRIMA del salvataggio.

Filosofia (vedi vincolo no-hardcoded-domain): qui NON c'è conoscenza di dominio
cablata — sono pattern di sicurezza, indipendenti dall'argomento. E non si butta MAI
un dato: al massimo lo si marca. Le fonti fidate vengono solo loggate, non bloccate.
"""
import re
from loguru import logger

# Fonti NON fidate: contenuto che non viene direttamente da Stefano.
UNTRUSTED_SOURCES = {"web", "mobile_in", "mobile"}

# --- Injection / dirottamento di ruolo (alta confidenza, raro nel testo legittimo) ---
_INJECTION = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignor[ae]\s+(le\s+|tutte\s+le\s+|ogni\s+|qualsiasi\s+)?(istruzion|regol|direttiv|quanto\s+detto|i\s+messaggi\s+precedenti|il\s+prompt)",
        r"dimentica\s+(tutto|tutte\s+le\s+istruzioni|quanto\s+detto|le\s+regole|il\s+contesto|il\s+prompt)",
        r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier|the\s+above)\s+(instruction|prompt|message|context|rule)",
        r"disregard\s+(all\s+|any\s+)?(previous|prior|above|the\s+above)",
        r"forget\s+(everything|all\s+previous|your\s+instructions|the\s+above)",
        r"\byou\s+are\s+now\s+(a|an|the)\b",
        r"\bact\s+as\s+(a|an|the|if\s+you)\b",
        r"(modalit[àa]|mode)\s+(sviluppatore|developer|debug|dan|jailbreak)",
        r"\b(jailbreak|prompt\s+injection|do\s+anything\s+now)\b",
        r"\bsystem\s+prompt\b",
        r"<\|[^>]*\|>",
        r"</?(system|assistant|user)>",
        r"(^|\n)\s*(system|assistant)\s*:",
        r"\[/?INST\]",
        r"#{2,}\s*(system|instruction|istruzion)",
        r"\bBEGIN\s+(SYSTEM|INSTRUCTIONS?)\b",
    ]
]

# --- Esfiltrazione: imperativo + bersaglio sensibile (entrambi richiesti → pochi falsi positivi) ---
_SENSITIVE = r"(tutte\s+le\s+)?(memori|password|token|api[\s_-]?key|chiav[ei]\s+(privat|api|segret)|segret|credenzial|\.env|secret|private\s+key|cookie\s+di\s+sessione)"
_EXFIL = [
    re.compile(p, re.IGNORECASE) for p in [
        r"(invia|manda|inoltra|posta|carica|esfiltra|trasmetti|send|post|upload|exfiltrat|leak|e-?mail)\b.{0,60}\b" + _SENSITIVE,
        r"(stampa|mostra|elenca|rivela|dump|print|list|reveal|show|leak)\b.{0,40}\b" + _SENSITIVE,
    ]
]


def scan_content(content: str) -> list[tuple[str, str]]:
    """
    Ritorna lista di (categoria, frammento_che_ha_fatto_match).
    categoria ∈ {"injection", "exfiltration"}. Lista vuota = pulito.
    """
    if not content:
        return []
    found: list[tuple[str, str]] = []
    for rx in _INJECTION:
        m = rx.search(content)
        if m:
            found.append(("injection", m.group(0)[:80]))
    for rx in _EXFIL:
        m = rx.search(content)
        if m:
            found.append(("exfiltration", m.group(0)[:80]))
    return found


def is_untrusted(source: str) -> bool:
    return source in UNTRUSTED_SOURCES


def evaluate(content: str, source: str) -> dict:
    """
    Politica di sicurezza all'ingest.
      - Non si scarta MAI un dato di Stefano (fonte fidata): si logga soltanto.
      - Fonte NON fidata + injection → reject (nessun motivo legittimo in un risultato web).
      - Ogni altro flag → si salva ma marcato (safety_flag), così il recall potrà trattarlo
        come dato non fidato e l'audit lo ritrova.
    Ritorna {flags: [cat...], reject: bool, safety_flag: [cat...]}.
    """
    flags = scan_content(content)
    cats = sorted({c for c, _ in flags})
    if not cats:
        return {"flags": [], "reject": False, "safety_flag": []}

    # Fonte non fidata + qualsiasi pattern (injection/esfiltrazione) → reject:
    # nessuno dei due ha un motivo legittimo in un contenuto esterno.
    reject = is_untrusted(source)
    if reject:
        logger.warning(f"MemoryGuard: RIFIUTO memoria ({cats} da fonte non fidata '{source}') — match: {[m for _, m in flags]}")
    else:
        logger.warning(f"MemoryGuard: memoria marcata {cats} (source='{source}') — match: {[m for _, m in flags]}")
    return {"flags": cats, "reject": reject, "safety_flag": cats}
