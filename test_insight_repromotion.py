#!/usr/bin/env python3
"""
Test isolato e deterministico per il gate di ri-promozione (V2.19, opzione b).

Verifica la regola: la PRIMA promozione di un insight è libera (per sola
convergenza), ma un insight già demoto da Gate 1 (`demoted_once=True`) e mai
validato dall'uso (`recalled_count=0`) NON deve risorgere per ri-convergenza —
resta candidate e si spegne al giorno 30. Se invece è stato richiamato
(`recalled_count>0`), la ri-promozione è concessa.

Strategia (no LLM, no idle, no mutazione dei dati reali):
  - Crea 3 cluster sintetici di insight `euri:insight:__test_repromo_*`.
  - Ogni cluster: 1 SUBJECT (content ben formato) + 3 FILLER (content malformato
    ma embedding IDENTICO al subject → distanza coseno 0 → 3 convergenze).
    I filler falliscono il format gate, quindi non vengono mai promossi loro
    stessi: servono solo a far raggiungere al subject la soglia di convergenza.
  - Si patcha `_llm_judge_same_insight` → False (zona grigia mai contata: solo i
    vicini identici <0.15 fanno convergenza) e `write_insight` → no-op.
  - Si esegue UNA volta `_evaluate_insights()` e si controlla lo stato finale.

Casi:
  A) demoted_once=True,  recalled_count=0  → atteso: resta 'candidate' (bloccato)
  B) demoted_once=True,  recalled_count=2  → atteso: 'promoted' (validato dall'uso)
  C) (nessun flag, fresco)                 → atteso: 'promoted' (sogno libero)

Teardown garantito via try/finally: tocca SOLO le chiavi __test_repromo_*.

Uso: venv/bin/python test_insight_repromotion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
import core.dream_engine as de_mod
from core.embedder import Embedder
from core.dream_engine import DreamEngine
from utils.date_utils import now, to_timestamp

PREFIX = "euri:insight:__test_repromo_"


def _make_subject_content(a: str, b: str) -> str:
    """Content che passa _has_required_structure (contiene le frasi chiave)."""
    return (
        f"Nel dominio [{a}] succede: si osserva un parametro di processo. "
        f"Nel dominio [{b}] succede: si calcola una grandezza correlata. "
        f"La connessione operativa non ovvia è: usare il primo come trigger del secondo."
    )


def _set_insight(r, key_suffix, content, embedding_list, *, demoted_once=None,
                 recalled_count=0):
    key = PREFIX + key_suffix
    doc = {
        "id": key,
        "content": content,
        "status": "candidate",
        "domain_a": "__test_a__",
        "domain_b": "__test_b__",
        "created_at": to_timestamp(now()),
        "recalled_count": recalled_count,
        "embedding": embedding_list,
        "convergence_count": 1,
    }
    if demoted_once is not None:
        doc["demoted_once"] = demoted_once
    r.json().set(key, "$", doc)
    return key


def _cluster(r, emb, label, *, demoted_once, recalled_count, subj_a, subj_b):
    """Crea 1 subject + 3 filler con embedding identico al subject."""
    content = _make_subject_content(subj_a, subj_b)
    vec = emb.encode(content, mode="passage").tolist()
    subj_key = _set_insight(
        r, f"{label}_subject", content, vec,
        demoted_once=demoted_once, recalled_count=recalled_count,
    )
    # 3 filler: embedding identico (convergenza), content malformato (no format gate)
    for i in range(1, 4):
        _set_insight(
            r, f"{label}_filler{i}",
            "filler malformato senza struttura operativa né domini",
            vec, recalled_count=0,
        )
    return subj_key


def _status(r, key):
    if not r.exists(key):
        return "DELETED"
    s = r.json().get(key, "$.status")
    return s[0] if s else "?"


def main():
    r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                        db=config.REDIS_DB, decode_responses=True)
    r.ping()
    print("✓ Redis connesso")

    emb = Embedder()
    emb.load()
    print("✓ Embedder caricato")

    engine = DreamEngine(r, emb)
    # Deterministico: la zona grigia (0.15–0.40) non conta mai → solo i vicini
    # identici (<0.15) fanno convergenza. Niente Vault reale.
    engine._llm_judge_same_insight = lambda *a, **k: False
    de_mod.write_insight = lambda *a, **k: None
    print("✓ DreamEngine costruito (judge LLM e write_insight neutralizzati)\n")

    # Pulizia preventiva di eventuali residui da run precedenti
    for k in r.scan_iter(PREFIX + "*"):
        r.delete(k)

    keys = {}
    try:
        keys["A"] = _cluster(r, emb, "A", demoted_once=True,  recalled_count=0,
                             subj_a="alfa", subj_b="beta")
        keys["B"] = _cluster(r, emb, "B", demoted_once=True,  recalled_count=2,
                             subj_a="gamma", subj_b="delta")
        keys["C"] = _cluster(r, emb, "C", demoted_once=None,  recalled_count=0,
                             subj_a="epsilon", subj_b="zeta")
        print(f"✓ Creati 3 cluster sintetici (3 subject + 9 filler), soglia "
              f"convergenze = {config.DREAM_INSIGHT_MIN_CONVERGENCES}\n")

        print("── Esecuzione _evaluate_insights() ────────────")
        engine._evaluate_insights()
        print()

        expected = {"A": "candidate", "B": "promoted", "C": "promoted"}
        labels = {
            "A": "demoted_once + recalled=0 → deve restare candidate (BLOCCATO)",
            "B": "demoted_once + recalled=2 → deve promuovere (validato dall'uso)",
            "C": "fresco (no flag)          → deve promuovere (sogno libero)",
        }
        print("── Risultati ──────────────────────────────────")
        ok = True
        for label in ("A", "B", "C"):
            got = _status(r, keys[label])
            exp = expected[label]
            passed = (got == exp)
            ok = ok and passed
            mark = "✅" if passed else "❌"
            print(f"  {mark} Caso {label}: {labels[label]}")
            print(f"       atteso={exp:<10} ottenuto={got}")

        print("\n" + ("🎉 TUTTI I CASI OK — opzione (b) valida."
                       if ok else "💥 FALLITO — vedi sopra."))
        return 0 if ok else 1

    finally:
        n = 0
        for k in r.scan_iter(PREFIX + "*"):
            r.delete(k)
            n += 1
        print(f"\n🧹 Teardown: {n} chiavi di test rimosse.")


if __name__ == "__main__":
    sys.exit(main())
