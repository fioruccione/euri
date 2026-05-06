"""
Parsing di riferimenti temporali in italiano da testo libero.
Restituisce (ts_start, ts_end) come Unix timestamp float, o None.
Usato da _build_context per aggiungere un filtro temporale alla ricerca Redis.
"""
import re
from datetime import datetime, timedelta

_MONTHS_IT = {
    'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4,
    'maggio': 5, 'giugno': 6, 'luglio': 7, 'agosto': 8,
    'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
    'gen': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mag': 5, 'giu': 6,
    'lug': 7, 'ago': 8, 'set': 9, 'ott': 10, 'nov': 11, 'dic': 12,
}

_WEEKDAYS_IT = {
    'lunedì': 0, 'martedì': 1, 'mercoledì': 2, 'giovedì': 3,
    'venerdì': 4, 'sabato': 5, 'domenica': 6,
    'lunedi': 0, 'martedi': 1, 'mercoledi': 2, 'giovedi': 3,
    'venerdi': 4,
}

_NUMS_IT = {'uno': 1, 'due': 2, 'tre': 3, 'quattro': 4, 'cinque': 5,
            'sei': 6, 'sette': 7, 'otto': 8, 'nove': 9, 'dieci': 10}

_MONTH_PAT = '|'.join(_MONTHS_IT.keys())


def extract_temporal_range(text: str, now: datetime) -> tuple[float, float] | None:
    """
    Parsa riferimenti temporali italiani e restituisce (ts_start, ts_end).
    Ordine di priorità: data esplicita > ieri/oggi > N giorni fa > giorno settimana > settimana/mese.
    """
    t = text.lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Data esplicita: "5 maggio", "il 5 maggio 2026", "5 mag"
    m = re.search(
        rf'\b(\d{{1,2}})\s+({_MONTH_PAT})(?:\s+(\d{{4}}))?\b', t
    )
    if m:
        day, month_str, year_str = int(m.group(1)), m.group(2), m.group(3)
        month = _MONTHS_IT[month_str]
        year = int(year_str) if year_str else now.year
        try:
            start = datetime(year, month, day)
            return start.timestamp(), (start + timedelta(days=1)).timestamp()
        except ValueError:
            pass

    # "ieri"
    if re.search(r'\bieri\b', t):
        start = today - timedelta(days=1)
        return start.timestamp(), today.timestamp()

    # "l'altro ieri"
    if re.search(r"l['''\s]altro\s+ieri", t):
        start = today - timedelta(days=2)
        return start.timestamp(), (today - timedelta(days=1)).timestamp()

    # "oggi" / "stamattina" / "stamani" / "stamane"
    if re.search(r'\b(oggi|stamattina|stamani|stamane|stanotte)\b', t):
        return today.timestamp(), now.timestamp()

    # "N giorni fa" (numeri o parole)
    m = re.search(r'(\d+|uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)\s+giorni\s+fa', t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _NUMS_IT.get(raw, 2)
        start = today - timedelta(days=n)
        return start.timestamp(), (start + timedelta(days=1)).timestamp()

    # Giorno della settimana: "lunedì", "martedì" ecc. → ultimo occorso
    for day_name, weekday in _WEEKDAYS_IT.items():
        if re.search(rf'\b{day_name}\b', t):
            days_ago = (now.weekday() - weekday) % 7 or 7
            start = today - timedelta(days=days_ago)
            return start.timestamp(), (start + timedelta(days=1)).timestamp()

    # "la settimana scorsa"
    if re.search(r'settimana\s+scorsa', t):
        start = today - timedelta(days=today.weekday() + 7)
        return start.timestamp(), (start + timedelta(days=7)).timestamp()

    # "questa settimana"
    if re.search(r'questa\s+settimana', t):
        start = today - timedelta(days=today.weekday())
        return start.timestamp(), now.timestamp()

    # "il mese scorso"
    if re.search(r'mese\s+scorso', t):
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start.timestamp(), first_this.timestamp()

    return None
