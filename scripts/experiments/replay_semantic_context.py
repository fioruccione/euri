#!/usr/bin/env python3
"""Ablazione offline tra memoria narrativa e contesto semantico strutturato.

Il pacchetto semantico e' costruito in memoria a partire da fatti gia' verificati
nel caso ICMA2. Il test misura il consumo del contesto da parte del Brain, non
la qualita' dell'estrattore che in futuro potrebbe costruirlo.
Nessuna scrittura Redis, nessun Dream e nessuna modifica alle memorie reali.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from replay_memory_order import IDS, QUESTION, _doc, _render, _score
from core.brain import Brain
from utils.redis_client import get_client


SEMANTIC_MINIMAL = """PACCHETTO SEMANTICO SITUAZIONALE — ICMA2

Entita':
- bivite ICMA2: origine del fuso.
- filtro: FIMIC RAS500.
- pompa attuale: pompa a ingranaggi.
- pompa proposta: pompa a pistoni FIMIC FPP20.

Relazioni attuali:
- bivite ICMA2 --a_monte_di--> RAS500.
- RAS500 --a_monte_di--> pompa a ingranaggi.
- pompa a ingranaggi --a_monte_di--> taglio.
- pompa a ingranaggi --non_accetta--> materiale contaminato.

Relazioni proposte:
- bivite ICMA2 --a_monte_di--> FIMIC FPP20.
- FIMIC FPP20 --a_monte_di--> RAS500.
- stato della FPP20: proposta/ipotesi, non configurazione attuale.
"""

SEMANTIC_EVIDENCE = SEMANTIC_MINIMAL + """

Vincoli e motivazioni:
- La pompa a ingranaggi resta dopo il RAS500 perche' il filtro rimuove il
  materiale contaminato prima che raggiunga la pompa.
- La FPP20 e' pensata per lavorare a monte del RAS500 e stabilizzare pressione
  e portata verso il filtro; l'effetto e' da verificare con una prova.

Provenienza:
- configurazione attuale: correzione esplicita dell'utente e memoria canonica;
- modifica FPP20: proposta dell'utente, da validare;
- effetti produttivi: ipotesi progettuale, non fatto osservato.
"""

SEMANTIC_CONFLICT = SEMANTIC_EVIDENCE + """

Conflitti storici:
- alcune memorie precedenti descrivono genericamente una pompa prima del RAS500;
  sono prive di modello e stato temporale e non possono sovrascrivere la
  configurazione attuale canonica.
- una vecchia nota usa LAS500 invece di RAS500: possibile refuso, da verificare.
"""

SEMANTIC_NO_STATE = """RELAZIONI ICMA2 (stato non indicato)
- bivite -> filtro RAS500
- filtro RAS500 -> pompa
- bivite -> pompa FIMIC FPP20 -> filtro RAS500
- pompa a ingranaggi -> taglio
"""


def _rich_context(client) -> str:
    names = (
        "topology_teach", "operations_teach", "historical_drop", "current_drop",
        "flow_passive", "canonical_project", "pressure_quality", "maintenance",
    )
    docs = {name: _doc(client, IDS[name]) for name in names}
    return _render("raw_ordered_core", [docs[name] for name in names])


def _packet_with_evidence(packet: str, client) -> str:
    # Le evidenze narrative restano disponibili, ma dopo il modello semantico.
    docs = [_doc(client, IDS[name]) for name in ("canonical_project", "pressure_quality", "maintenance")]
    return packet + "\n\nEVIDENZE NARRATIVE COLLEGATE\n" + _render("evidence", docs)


def _score_semantic(reply: str) -> dict:
    base = _score(reply)
    low = reply.lower()
    base.update({
        "mentions_current_state": any(x in low for x in ("attuale", "configurazione corrente")),
        "mentions_proposed_state": any(x in low for x in ("proposta", "ipotetic", "fpp20")),
        "mentions_contamination_constraint": any(x in low for x in ("contaminat", "inquinat")),
        "mentions_provenance": any(x in low for x in ("fonte", "memoria", "utente", "verific")),
    })
    # Score aggiuntivo prudente: cerca la sequenza nei blocchi gia' estratti.
    return base


def run(output: Path) -> dict:
    client = get_client()
    contexts = {
        "raw_narrative": _rich_context(client),
        "semantic_minimal": SEMANTIC_MINIMAL,
        "semantic_with_evidence": _packet_with_evidence(SEMANTIC_EVIDENCE, client),
        "semantic_with_conflict": SEMANTIC_CONFLICT,
        "semantic_missing_state": SEMANTIC_NO_STATE,
        "semantic_first_plus_narrative": _packet_with_evidence(SEMANTIC_EVIDENCE, client) + "\n\n" + _rich_context(client),
        "narrative_first_plus_semantic": _rich_context(client) + "\n\n" + SEMANTIC_CONFLICT,
    }
    records = []
    for label, context in contexts.items():
        brain = Brain()
        started = time.perf_counter()
        try:
            reply = brain.respond(
                QUESTION,
                context=context,
                memory_scope="experiment_semantic_context_icma2",
                thinking=False,
            )
            error = ""
        except Exception as exc:
            reply = ""
            error = f"{type(exc).__name__}: {exc}"
        records.append({
            "variant": label,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
            "score": _score_semantic(reply),
            "reply": reply,
        })
    result = {
        "schema_version": 1,
        "protocol": "semantic_context_ablation_v1",
        "created_at": time.time(),
        "model_path": "core.brain.Brain.respond",
        "question": QUESTION,
        "semantic_packet_is_gold": True,
        "redis_mutation": False,
        "dream": False,
        "variants": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({
        "output": str(args.output),
        "variants": [
            {"variant": item["variant"], "elapsed_ms": item["elapsed_ms"],
             "score": item["score"], "error": item["error"]}
            for item in result["variants"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
