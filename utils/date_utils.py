from datetime import datetime
import re
import dateparser
import config


def _normalize_italian_time(text: str) -> str:
    """
    Normalizza espressioni orarie colloquiali italiane prima di passarle a dateparser.
    Es: "7 e 45 minuti" → "7:45", "una e mezza" → "1:30", "7 e un quarto" → "7:15"
    """
    # "alle 7.45" o "le 7.45" (punto come separatore ore:minuti) → "7:45"
    text = re.sub(r"\b(alle?\s+|le\s+)(\d{1,2})\.(\d{2})\b", r"\2:\3", text, flags=re.IGNORECASE)
    # "alle 7:45" → "7:45" (dateparser italiano capisce meglio senza "alle")
    text = re.sub(r"\balle?\s+(\d{1,2}:\d{2})\b", r"\1", text, flags=re.IGNORECASE)
    # "mattina/pomeriggio/sera/notte" quando c'è un orario esplicito → rimosso (confonde dateparser)
    if re.search(r"\d{1,2}:\d{2}", text):
        text = re.sub(r"\b(mattina|pomeriggio|sera|notte)\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
    # "X e YY minuti" → "X:YY"
    text = re.sub(r"\b(\d{1,2})\s+e\s+(\d{1,2})\s+minuti?\b", r"\1:\2", text, flags=re.IGNORECASE)
    # "X e mezza" → "X:30"
    text = re.sub(r"\b(\d{1,2})\s+e\s+mezza\b", r"\1:30", text, flags=re.IGNORECASE)
    # "X e un quarto" → "X:15"
    text = re.sub(r"\b(\d{1,2})\s+e\s+un\s+quarto\b", r"\1:15", text, flags=re.IGNORECASE)
    # "X e tre quarti" → "X:45"
    text = re.sub(r"\b(\d{1,2})\s+e\s+tre\s+quarti\b", r"\1:45", text, flags=re.IGNORECASE)
    # "alle 15" o "alle 9" (ora senza minuti) → "15:00" — deve stare dopo le altre normalizzazioni
    text = re.sub(r"\balle?\s+(\d{1,2})\b(?!\s*[:\d])", r"\1:00", text, flags=re.IGNORECASE)
    return text


def parse_italian_date(text: str, reference: datetime = None) -> datetime | None:
    """
    Converte testo italiano in datetime.
    Esempi: "lunedì", "domani", "tra una settimana", "il 3 aprile alle 15"
    Ritorna None se il parsing fallisce.
    """
    ref = reference or now()
    text = _normalize_italian_time(text)
    result = dateparser.parse(
        text,
        languages=["it"],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Rome",
            "RELATIVE_BASE": ref,
            "PREFER_DATES_FROM": "future",
        },
    )
    return result


def now() -> datetime:
    """Ora corrente con timezone Europe/Rome."""
    return datetime.now(config.TIMEZONE)


def to_timestamp(dt: datetime) -> float | None:
    """Converte datetime in Unix timestamp (per RediSearch NumericField)."""
    if dt is None:
        return None
    return dt.timestamp()


def from_timestamp(ts: float | None) -> datetime | None:
    """Converte Unix timestamp in datetime con timezone."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=config.TIMEZONE)


# Giorni e mesi in italiano, espliciti. NON usiamo strftime('%A'/'%B') perché
# dipendono dal locale di sistema (spesso C/POSIX → giorno in inglese). Per usare
# Euri in un'altra lingua: tradurre questi due array (e vedere la nota nel README).
_GIORNI = ["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]
_MESI = ["gennaio","febbraio","marzo","aprile","maggio","giugno",
         "luglio","agosto","settembre","ottobre","novembre","dicembre"]

def format_datetime(dt: datetime | None) -> str:
    """Formatta datetime in italiano per TTS/UI."""
    if dt is None:
        return "nessuna scadenza"
    mese = _MESI[dt.month - 1]
    return f"{dt.day} {mese} {dt.year} alle {dt.strftime('%H:%M')}"


def format_datetime_full(dt: datetime | None) -> str:
    """
    Data e ora in italiano CON il giorno della settimana, per l'àncora temporale
    iniettata nel contesto LLM (es. "sabato 13 giugno 2026, ore 17:18").

    Esplicita di proposito: un'àncora resa via strftime('%A') esce nella lingua del
    locale di sistema — tipicamente inglese ("Saturday") — e un modello che risponde
    in italiano la ignora, preferendo un cliché ("è venerdì sera"). Scriverla nella
    lingua della conversazione la rende un'àncora forte. dt.weekday(): 0=lunedì.
    """
    if dt is None:
        return "data sconosciuta"
    giorno = _GIORNI[dt.weekday()]
    mese = _MESI[dt.month - 1]
    return f"{giorno} {dt.day} {mese} {dt.year}, ore {dt.strftime('%H:%M')}"
