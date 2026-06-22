"""
Estrae date/orari da testo italiano.
Strategia: trova giorno e orario separatamente, li combina, poi passa a dateparser.
"""
import re
from datetime import datetime
from loguru import logger
from utils.date_utils import parse_italian_date, _normalize_italian_time, now


# Parole che indicano un giorno
_DAY_RE = re.compile(
    r"\b(domani|dopodomani|stamani|stamattina|stasera|stanotte|oggi"
    r"|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica"
    r"|il\s+\d{1,2}(?:\s+\w+)?|(?:tra|fra)\s+\d+\s+giorn[io])\b",
    re.IGNORECASE,
)

# Orario già normalizzato (es. "7:45")
_CLOCK_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")

# Espressioni relative complete (es. "tra 2 minuti", "fra 5 minuti")
_RELATIVE_RE = re.compile(
    r"\b(?:tra|fra)\s+\d+\s+(?:minut[io]|or[ae]|second[io]|giorn[io]|settiman[ae])\b",
    re.IGNORECASE,
)

# Frammento temporale semplice (fallback)
_FRAGMENT_RE = re.compile(
    r"\b(?:entro|alle?|le|domani|dopodomani|stamani|stamattina|stasera|stanotte|oggi"
    r"|lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica"
    r"|la\s+prossima?\s+\w+|il\s+\d+)"
    r"(?:\s+\w+){0,4}",
    re.IGNORECASE,
)

# Numeri italiani scritti in lettere → cifra, MA solo se seguiti da un'unità di tempo
# (così "un blend" / "tre prove" NON vengono toccati: si converte solo "tra TRE minuti").
_WORD_NUM = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
    "dodici": 12, "quindici": 15, "venti": 20, "trenta": 30, "quaranta": 40, "sessanta": 60,
}
_TIME_UNIT = r"minut[io]|or[ae]|second[io]|giorn[io]|settiman[ae]|mes[ei]"
_WORDNUM_RE = re.compile(
    r"\b(" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) + r")\b['\s]+(" + _TIME_UNIT + r")\b",
    re.IGNORECASE,
)


def _digitize_relative_words(text: str) -> str:
    """'tra tre minuti'→'tra 3 minuti', "tra un'ora"→'tra 1 ora', "mezz'ora"→'30 minuti'.
    Converte il numero-parola SOLO davanti a un'unità di tempo (non tocca 'un blend')."""
    text = re.sub(r"\bmezz[a']?\s*(or[ae])\b", "30 minuti", text, flags=re.IGNORECASE)
    return _WORDNUM_RE.sub(lambda m: f"{_WORD_NUM[m.group(1).lower()]} {m.group(2)}", text)


def extract_due_date(text: str) -> datetime | None:
    """
    Cerca un riferimento temporale nel testo e lo converte in datetime.
    Ritorna None se non trovato.
    """
    ref = now()
    # "fra" e "tra" sono sinonimi — dateparser conosce solo "tra"
    normalized = _normalize_italian_time(text)
    normalized = re.sub(r'\bfra\b', 'tra', normalized, flags=re.IGNORECASE)
    normalized = _digitize_relative_words(normalized)  # "tra tre minuti" → "tra 3 minuti"

    # 1. Espressioni relative ("tra 2 minuti", "tra 3 giorni")
    m = _RELATIVE_RE.search(normalized)
    if m:
        result = parse_italian_date(m.group(0), reference=ref)
        if result and result > ref:
            logger.debug(f"Data relativa '{m.group(0)}': {result}")
            return result

    # 2. Combina giorno + orario trovati separatamente nel testo
    day_m = _DAY_RE.search(normalized)
    clock_m = _CLOCK_RE.search(normalized)
    if day_m and clock_m:
        day_str = re.sub(r"^\s*il\s+", "", day_m.group(0), flags=re.IGNORECASE).strip()
        candidate = f"{day_str} {clock_m.group(0)}"
        result = parse_italian_date(candidate, reference=ref)
        if result and result > ref:
            logger.debug(f"Giorno+ora combinati '{candidate}': {result}")
            return result

    # 3. Solo orario (senza giorno esplicito) → dateparser sceglie il prossimo futuro
    if clock_m:
        result = parse_italian_date(clock_m.group(0), reference=ref)
        if result and result > ref:
            logger.debug(f"Solo orario '{clock_m.group(0)}': {result}")
            return result

    # 4. Solo giorno senza orario
    if day_m:
        result = parse_italian_date(day_m.group(0), reference=ref)
        if result and result > ref:
            logger.debug(f"Solo giorno '{day_m.group(0)}': {result}")
            return result

    # 5. Fallback: frammento testuale generico
    for m in _FRAGMENT_RE.finditer(normalized):
        candidate = m.group(0).strip()
        result = parse_italian_date(candidate, reference=ref)
        if result and result > ref:
            logger.debug(f"Frammento '{candidate}': {result}")
            return result

    return None


def resolve_relative_date(text: str) -> datetime | None:
    return parse_italian_date(text, reference=now())
