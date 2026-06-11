#!/usr/bin/env python3
"""
Diag RAG context — misura la composizione del contesto iniettato da
voice_daemon._build_context: per ogni query, quanti slot finiscono ON-TOPIC vs
OFF-TOPIC e da DOVE arrivano (base di recency vs match semantico).

Motivazione (caso Eurostampi, 11/06/2026): durante una conversazione tecnica su
estrusione/perossido, 5 slot su 6 del contesto RAG erano nodi freschi del ciclo
Dream di mezzogiorno (informatica, IA, logistica, automazione) — off-topic — e
alla conoscenza polimeri vera restava ~1 slot. Causa: la selezione parte ancorata
alle 5 memorie PIÙ RECENTI (recency), e solo dopo aggiunge i match semantici, con
cap 6 → la recency annega la rilevanza.

Questo script è il NUMERO che il fix deve muovere (metodo Fase −1: prima la
metrica, poi il codice). Read-only: touch=False ovunque, NON tocca recalled_count
(che è proprio la statistica che il bug droga — vedi punto #3 della spec).

Replica fedele di voice_daemon._build_context righe ~853-896. Quando il fix
modifica quelle righe, aggiornare in lockstep la funzione select_context() qui
sotto (o, meglio, farla chiamare dal codice reale rifattorizzato).

Uso:  venv/bin/python diag_rag_context.py
"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.temporal_recall import prioritize_window
from utils.temporal import extract_temporal_range
from utils.date_utils import now

# Stesse stop-word di voice_daemon._STOP_WORDS (righe 828-839)
_STOP_WORDS = {
    "come", "cosa", "quando", "dove", "perché", "però", "anche", "solo",
    "tutto", "tutti", "tutta", "tutte", "questo", "questa", "questi", "queste",
    "quello", "quella", "quelli", "quelle", "volevo", "volendo", "posso", "devo",
    "sono", "essere", "avere", "fare", "dire", "stare", "della", "delle", "degli",
    "dello", "nella", "nelle", "negli", "nello", "negli", "oppure", "invece",
    "ancora", "adesso", "quindi", "allora", "certo", "magari", "tanto", "molto",
    "poco", "bene", "male", "così", "tipo", "parte", "fatto", "fatto", "altra",
    "altro", "altri", "altre", "prima", "dopo", "sempre", "spesso", "quasi",
    "circa", "forse", "senza", "verso", "dentro", "fuori", "sopra", "sotto",
    "mentre", "però", "comunque", "ricordi", "saper", "sapere",
}

# ── Rubrica on/off-topic (esplicita, non un'impressione) ───────────────────
# Per le query tecniche Eurostampi: on-topic = domini plastica/polimeri/processo.
ONTOPIC_PLASTICS = {
    "chimica polimeri", "estrusione plastica", "produzione plastica",
    "riciclo plastica", "materiali", "plastica", "stampaggio", "polimeri",
}
# Off-topic noti = output del ciclo Dream di mezzogiorno e affini gestionali/IT.
OFFTOPIC_KNOWN = {
    "informatica", "intelligenza artificiale", "logistica", "logistica materiale",
    "automazione industriale", "gestione aziendale", "gestione dati",
    "comunicazione digitale", "tecnologia informatica", "relazioni personali",
}


def classify_domain(domain: str, ontopic: set[str]) -> str:
    d = (domain or "").lower()
    if any(t in d or d in t for t in ontopic):
        return "ON"
    if any(t in d or d in t for t in OFFTOPIC_KNOWN):
        return "OFF"
    return "neu"


def select_context(mem: MemoryManager, text: str):
    """
    Replica la selezione di results in _build_context (NON temporale + temporale),
    annotando la PROVENIENZA di ogni nodo: 'recency' (base get_recent_memories) o
    'semantic' (search_memories/search_notes) o 'temporal' (prioritize_window).
    touch=False ovunque: misura senza rinforzare.
    Ritorna (results_capped, provenance_per_id, is_temporal).
    """
    provenance: dict[str, str] = {}

    # Base recency (riga 853): le 5 più recenti, a prescindere dal tema.
    results = mem.get_recent_memories(limit=5, touch=False)
    for r in results:
        provenance[r.get("id")] = "recency"
    seen_ids = {r.get("id") for r in results}

    # Ramo temporale (righe 863-881)
    is_temporal = False
    time_range = extract_temporal_range(text, now())
    if time_range:
        is_temporal = True
        ts_start, ts_end = time_range
        window = mem.search_memories_by_timerange(ts_start, ts_end, limit=200, touch=False)
        merged, merged_seen = [], set()
        for r in prioritize_window(window) + results:
            rid = r.get("id")
            if rid not in merged_seen:
                merged.append(r)
                merged_seen.add(rid)
                provenance.setdefault(rid, "temporal")
        results = merged
        seen_ids = merged_seen

    # Ramo keyword/semantico (righe 883-896)
    words = re.findall(
        r'\b[a-zA-ZàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]{4,}\b', text
    )
    keywords = list(dict.fromkeys(w for w in words if w.lower() not in _STOP_WORDS))
    if keywords:
        extra = mem.search_memories(text, limit=3, source_filter=None, touch=False)
        for r in extra:
            rid = r.get("id")
            if rid not in seen_ids:
                results.append(r)
                seen_ids.add(rid)
                provenance[rid] = "semantic"

    mem_cap = 10 if is_temporal else 6
    return results[:mem_cap], provenance, is_temporal


# ── Fixture: turni reali Eurostampi + contro-caso temporale obbligatorio ───
TECH_QUERIES = [
    "Ma secondo te, a mettere del perossido su un materiale con un grado così "
    "basso, avrebbe un senso direttamente sulla pressa, o va proprio selezionato "
    "un materiale diverso?",
    "Eurostampi ha utilizzato un grado 6 che secondo me era ancora troppo basso "
    "per l'articolo, hanno una pressa BMB 4500 tonnellate per i collaudi.",
    "Scrivimi una relazione su quello che ci siamo detti da inviare ad Eurostampi.",
]
# Contro-caso (punto #2): DEVONO continuare a funzionare come oggi.
TEMPORAL_QUERIES = [
    "di cosa parlavamo prima?",
    "di cosa parlavamo ieri?",
]


def report_query(mem, text, ontopic, temporal_expected):
    capped, prov, is_temporal = select_context(mem, text)
    print(f"\n  Query: \"{text[:70]}{'…' if len(text) > 70 else ''}\"")
    print(f"  temporale={is_temporal} (atteso {temporal_expected}) | slot mostrati={len(capped)}")
    counts = {"ON": 0, "OFF": 0, "neu": 0}
    prov_counts = {"recency": 0, "semantic": 0, "temporal": 0}
    for r in capped:
        rid = r.get("id", "")
        dom = r.get("domain", "?")
        src = r.get("source", "?")
        cls = classify_domain(dom, ontopic)
        p = prov.get(rid, "?")
        counts[cls] += 1
        prov_counts[p] = prov_counts.get(p, 0) + 1
        print(f"    [{cls:3}] {p:8} | {src}:{dom} ({rid[:8]})")
    print(f"  → ON={counts['ON']} OFF={counts['OFF']} neu={counts['neu']} "
          f"| provenienza: recency={prov_counts['recency']} "
          f"semantic={prov_counts['semantic']} temporal={prov_counts['temporal']}")
    return counts, prov_counts, is_temporal


def main():
    r = None
    from utils.redis_client import get_client
    r = get_client()
    embedder = Embedder()
    embedder.load()
    mem = MemoryManager(r, embedder)

    print("=" * 72)
    print("BASELINE — composizione contesto RAG (read-only, touch=False)")
    print("=" * 72)

    print("\n### QUERY TECNICHE (Eurostampi) — obiettivo del fix: ON↑, OFF↓")
    tot_on = tot_off = 0
    for q in TECH_QUERIES:
        c, _, _ = report_query(mem, q, ONTOPIC_PLASTICS, temporal_expected=False)
        tot_on += c["ON"]
        tot_off += c["OFF"]
    print(f"\n  AGGREGATO TECNICO: ON totali={tot_on}  OFF totali={tot_off}")

    print("\n### CONTRO-CASO TEMPORALE — DEVE restare invariato dopo il fix")
    for q in TEMPORAL_QUERIES:
        report_query(mem, q, ONTOPIC_PLASTICS, temporal_expected=True)

    print("\n" + "=" * 72)
    print("Numero da muovere: AGGREGATO TECNICO ON↑ / OFF↓.")
    print("Invariante da non rompere: le query temporali restano temporale=True")
    print("con il diario vissuto in testa (stesso ordine di oggi).")
    print("=" * 72)


if __name__ == "__main__":
    main()
