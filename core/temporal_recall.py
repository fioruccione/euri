"""
Recall temporale — distingue memoria VISSUTA da memoria RIFLESSIVA quando la query ha un
riferimento di tempo ("ieri", "oggi", "stamattina", "il 5 maggio", "3 giorni fa"...).

Problema osservato (9 giu 2026): a "cosa abbiamo fatto ieri?" il context era dominato da
reflection recenti e auto-rinforzate (i pensieri di Euri su vecchi temi Wi-Fi/Superbike),
che coprivano il diario reale della giornata (Seari, carbonato, perossido, Lucy/Fanti,
Poseidon). Le reflection erano le più recenti nella finestra → con un cap basso le pescava
tutte, mancando le memorie vissute del pomeriggio.

Cura (A+B, leggera): su query temporale si prende una finestra AMPIA e si mettono le fonti
VISSUTE/fattuali in testa (boost), le reflection in coda e limitate. Nessun effetto quando
non c'è un riferimento temporale. Non cancella reflection, non tocca i loop.
"""

from core.memory_risk import rank_memories_epistemically

# Tre livelli, per "quando è davvero successo":
# - CONVERSATIONAL: il diario PARLATO/insegnato — created_at ≈ momento in cui è stato detto.
# - DERIVED: consolidazioni — created_at = momento del CONSOLIDAMENTO (può raccogliere
#   materiale vecchio: es. un loop2e stampato ieri sera che ri-consolida vecchi ricordi Wi-Fi),
#   quindi affidabile come fatto ma NON come "quando è successo" → sotto il diario parlato.
# - rest (reflection/web/…): i pensieri di Euri sul periodo → in coda, cappati.
CONVERSATIONAL_SOURCES = {"user", "passive", "episode", "teach"}
DERIVED_SOURCES = {"loop2e"}
# Retrocompat: alias storico (vissute = parlato + consolidato).
LIVED_SOURCES = CONVERSATIONAL_SOURCES | DERIVED_SOURCES


def _timestamp(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def memory_event_interval(memory: dict) -> tuple[float, float] | None:
    """Tempo dell'evento, con fallback all'affermazione e poi al salvataggio."""
    temporal = memory.get("temporal_context") or {}
    start = _timestamp(memory.get("event_start"))
    if start is None:
        start = _timestamp(temporal.get("event_start"))
    end = _timestamp(memory.get("event_end"))
    if end is None:
        end = _timestamp(temporal.get("event_end"))
    if start is not None:
        return start, end if end is not None else start

    asserted = _timestamp(memory.get("asserted_at"))
    if asserted is None:
        asserted = _timestamp(temporal.get("asserted_at"))
    created = _timestamp(memory.get("created_at"))
    anchor = asserted if asserted is not None else created
    return (anchor, anchor) if anchor is not None else None


def memory_occurs_in_window(memory: dict, ts_start: float, ts_end: float) -> bool:
    """Vero se il tempo effettivo della memoria interseca la finestra."""
    interval = memory_event_interval(memory)
    if interval is None:
        return False
    start, end = interval
    return start <= ts_end and end >= ts_start


def _recent_sort_key(memory: dict) -> float:
    interval = memory_event_interval(memory)
    return interval[1] if interval is not None else float("-inf")


def prioritize_window(window_mems: list[dict], max_conv: int = 8,
                      max_derived: int = 6, max_other: int = 2) -> list[dict]:
    """
    Ordina le memorie di una finestra temporale per "diario vissuto" prima di "pensieri":
    diario PARLATO (conversational) in testa, poi le CONSOLIDAZIONI (loop2e), infine
    reflection/altro in coda e limitati. Preserva l'ordine d'ingresso entro i gruppi (la
    finestra arriva già ordinata per created_at desc).
    """
    usable = rank_memories_epistemically(window_mems)
    conv = [m for m in usable if m.get("source") in CONVERSATIONAL_SOURCES]
    derived = [m for m in usable if m.get("source") in DERIVED_SOURCES]
    other = [m for m in usable
             if m.get("source") not in CONVERSATIONAL_SOURCES
             and m.get("source") not in DERIVED_SOURCES]
    return conv[:max_conv] + derived[:max_derived] + other[:max_other]


def prioritize_recent_window(
    window_mems: list[dict],
    ts_start: float,
    ts_end: float,
    *,
    max_conv: int = 8,
    max_derived: int = 6,
    max_other: int = 2,
) -> list[dict]:
    """Diario recente fail-closed, ordinato cronologicamente entro le sorgenti.

    La query Redis include anche il tempo di affermazione/salvataggio per le
    normali domande temporali. Per "di recente" applichiamo un secondo gate sul
    tempo effettivo dell'evento: una reflection creata oggi su un fatto di un
    mese fa non può quindi diventare artificialmente recente.
    """
    usable = [
        memory
        for memory in rank_memories_epistemically(window_mems)
        if memory_occurs_in_window(memory, ts_start, ts_end)
    ]
    conv = sorted(
        (m for m in usable if m.get("source") in CONVERSATIONAL_SOURCES),
        key=_recent_sort_key,
        reverse=True,
    )
    derived = sorted(
        (m for m in usable if m.get("source") in DERIVED_SOURCES),
        key=_recent_sort_key,
        reverse=True,
    )
    other = sorted(
        (
            m
            for m in usable
            if m.get("source") not in CONVERSATIONAL_SOURCES
            and m.get("source") not in DERIVED_SOURCES
        ),
        key=_recent_sort_key,
        reverse=True,
    )
    return conv[:max_conv] + derived[:max_derived] + other[:max_other]
