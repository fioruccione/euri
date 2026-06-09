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


def prioritize_window(window_mems: list[dict], max_conv: int = 8,
                      max_derived: int = 6, max_other: int = 2) -> list[dict]:
    """
    Ordina le memorie di una finestra temporale per "diario vissuto" prima di "pensieri":
    diario PARLATO (conversational) in testa, poi le CONSOLIDAZIONI (loop2e), infine
    reflection/altro in coda e limitati. Preserva l'ordine d'ingresso entro i gruppi (la
    finestra arriva già ordinata per created_at desc).
    """
    conv = [m for m in window_mems if m.get("source") in CONVERSATIONAL_SOURCES]
    derived = [m for m in window_mems if m.get("source") in DERIVED_SOURCES]
    other = [m for m in window_mems
             if m.get("source") not in CONVERSATIONAL_SOURCES
             and m.get("source") not in DERIVED_SOURCES]
    return conv[:max_conv] + derived[:max_derived] + other[:max_other]
