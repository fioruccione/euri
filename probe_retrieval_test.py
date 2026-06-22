#!/usr/bin/env python3
"""
probe_retrieval_test.py — test MIRATO del grounding retrieval (step A, prima del benchmark).

Domanda: con l'embedder CALDO, `gather_grounded_evidence` recupera l'evidenza vissuta
PERTINENTE per un insight? Senza questo, il valutatore-qualità giudica contro rumore.

Casi noti (dal review 22/06):
  - GIOIELLO  (automotive × supply-chain, Safic-Alcan): deve recuperare i fatti che
    Lucy←Safic e ICS←Safic (la premessa groundata della connessione).
  - SCORIA    (tempo-libero × RF): NON deve trovare grounding forte.

Criterio di passaggio:
  - gioiello recupera evidenza pertinente (idealmente i fatti Safic) → si procede al benchmark;
  - gioiello NON la recupera → ci si ferma e si lavora sul retrieval.

Distinzione chiave: se i fatti Safic ESISTONO e sono raggiungibili con una query DIRETTA
("Safic fornitore") ma NON dall'insight (portiere/PP), il limite è il MULTI-HOP di
ragionamento, non l'embedder.

USO (daemon giù, Ollama non conteso):
    ./venv/bin/python probe_retrieval_test.py
"""
from __future__ import annotations

import sys

from utils.redis_client import get_client
from core.embedder import Embedder
from core.reaction import _insight_brief, gather_grounded_evidence

# id reali dalla run di validazione (22/06)
GEM_ID = "euri:insight:d1a9ede6-2066-46e7-b586-6eca1ada5431"      # automotive × supply chain
SCORIA_ID = "euri:insight:c440fd57-158b-4f76-82c1-bd37caf180ba"   # tempo libero × RF


def _show(r, label, ev_cold, ev_warm, must_contain=None):
    print(f"\n{'='*78}\n{label}")
    print(f"  COLD (keyword): {len(ev_cold)} frammenti")
    for e in ev_cold:
        print(f"    - {e[:110]}")
    print(f"  WARM (semantico+entità): {len(ev_warm)} frammenti")
    for e in ev_warm:
        print(f"    - {e[:110]}")
    if must_contain:
        joined = " ".join(ev_warm).lower()
        hits = [w for w in must_contain if w.lower() in joined]
        verdict = "✅" if hits else "❌"
        print(f"  {verdict} attesi {must_contain} → trovati: {hits or 'NESSUNO'}")


def main():
    r = get_client()
    print("── caricamento embedder (CPU, ~10-20s) ──")
    emb = Embedder()
    emb.load()
    assert emb.available, "embedder non caricato"

    for label, iid, expect in [
        ("GIOIELLO — automotive × supply-chain (Safic-Alcan)", GEM_ID, ["safic"]),
        ("SCORIA — tempo-libero × RF (deve NON groundare)", SCORIA_ID, None),
    ]:
        o = r.json().get(iid)
        if not o:
            print(f"\n[!] insight {iid} non trovato in Redis — salto")
            continue
        topic = f"{o.get('domain_a','')} {o.get('domain_b','')} {o.get('content','')}"
        print(f"\n>>> {label}\n    brief: {_insight_brief(o)[:160]}")
        ev_cold = gather_grounded_evidence(r, topic, embedder=None, limit=6)
        ev_warm = gather_grounded_evidence(r, topic, embedder=emb, limit=6)
        _show(r, label, ev_cold, ev_warm, must_contain=expect)

    # PROVA DIRETTA: i fatti Safic sono raggiungibili con una query mirata?
    # Se sì qui ma no dall'insight → il limite è il multi-hop, non l'embedder.
    print(f"\n{'='*78}\nPROVA DIRETTA — i fatti grounding esistono e sono raggiungibili?")
    for q in ["Safic Alcan fornitore additivi", "ICS cliente additivi Safic", "Lucy Plast fornitore Safic"]:
        ev = gather_grounded_evidence(r, q, embedder=emb, limit=4)
        print(f"\n  query: «{q}» → {len(ev)} frammenti")
        for e in ev:
            print(f"    - {e[:110]}")


if __name__ == "__main__":
    sys.exit(main())
