#!/usr/bin/env python3
"""
eval_recall.py — benchmark del RECUPERO reale (RAG) di Euri.

Complementare a eval_euri.py:
  - eval_euri.py  → CALIBRAZIONE dato un contesto fornito a mano (è onesto?).
  - eval_recall.py → RECUPERO vero contro la memoria Redis (ricorda? e a che rango?).

Per ogni domanda esercita `search_memories` (il path RAG reale, domain-boosted) e
verifica se un fatto che SAPPIAMO essere in memoria riemerge tra i risultati, e a quale
posizione. Isola il retrieval dalla generazione: non valuta come Euri *risponde*, ma se
il fatto giusto gli arriva DAVANTI.

Legge le soglie:
  - PASS  = trovato entro la finestra di iniezione (top-INJECT_WINDOW) → la Silent Chat
            lo vedrebbe.
  - WARN  = recuperato ma OLTRE la finestra → rischio recall-gap (c'è ma non entra).
  - FAIL  = non recuperato affatto → assente, oppure nascosto (superseded) o perso.

Read-only (nessuna scrittura). Uso:
    PYTHONPATH=. ./venv/bin/python scripts/eval_recall.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import redis  # noqa: E402
import config  # noqa: E402
from core.embedder import Embedder  # noqa: E402
from core.memory_manager import MemoryManager  # noqa: E402

LIMIT = 8          # quanti ne recupera il retrieval
INJECT_WINDOW = 6  # quanti ne inietta la Silent Chat (soglia di PASS)

# Fatti che SAPPIAMO essere stati messi in memoria nelle sessioni. Se un caso FALLISCE,
# o la query non li pesca (recall-gap) o il dato è stato perso (data-integrity).
CASES = [
    dict(id="grado17_cliente",   q="A chi è destinato il grado 17?",                 markers=["p-pile", "ppi"]),
    dict(id="grado25_cliente",   q="Chi ritira il materiale a fluidità 25 / MFI 25?", markers=["ics"]),
    dict(id="italrek_umidita",   q="Qual è il limite di umidità della linea Italrek?", markers=["italrek"]),
    dict(id="icma_dosaggio",     q="Quanto dosano gli additivi le ICMA?",            markers=["icma"]),
    dict(id="tecnova_cariche",   q="La Tecnova 160 può lavorare cariche minerali?",  markers=["tecnova"]),
    dict(id="gamma_specifiche",  q="Specifiche e umidità della linea Gamma",         markers=["gamma"]),
    dict(id="additivo_realube",  q="Cos'è l'additivo Realube 5014?",                 markers=["realube", "reagens"]),
    dict(id="leva_grado_mfi",    q="Cosa alza o abbassa il grado/MFI del polipropilene?", markers=["perossido"]),
    dict(id="figlia_stefano",    q="Come si chiama la figlia di Stefano?",           markers=["roberta"]),
    dict(id="regrado_pp",        q="Cos'è il progetto Regrado PP?",                  markers=["regrado"]),
]


def main():
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    emb = Embedder()
    emb.load()
    mm = MemoryManager(r, embedder=emb)

    passed = warned = failed = 0
    print("=" * 66)
    print("EVAL RECALL — recupero reale dal RAG")
    print("=" * 66)
    for c in CASES:
        try:
            results = mm.search_memories(c["q"], limit=LIMIT)
        except Exception as e:
            results = []
            print(f"\n[FAIL] {c['id']} — errore search: {e}")
            failed += 1
            continue
        rank = None
        for i, m in enumerate(results, 1):
            content = (m.get("content", "") or "").lower()
            if any(mk in content for mk in c["markers"]):
                rank = i
                break
        if rank and rank <= INJECT_WINDOW:
            flag = "PASS"; passed += 1
        elif rank:
            flag = "WARN"; warned += 1
        else:
            flag = "FAIL"; failed += 1
        pos = f"rank {rank}" if rank else "NON TROVATO"
        print(f"\n[{flag}] {c['id']}  ({pos}, su {len(results)} recuperati)")
        print(f"   Q: {c['q']}   (cerco: {c['markers']})")
        if rank:
            print(f"   → top-{rank}: {results[rank - 1].get('content', '')[:90].strip()}")

    print("\n" + "=" * 66)
    print(f"PASS (entro top-{INJECT_WINDOW}): {passed}/{len(CASES)}   "
          f"WARN (oltre la finestra): {warned}   FAIL (non recuperato): {failed}")
    print("WARN = il fatto c'è ma non entra nella finestra iniettata → recall-gap.")
    print("FAIL = non recuperato → assente, o superseded/consolidato via (data-integrity).")


if __name__ == "__main__":
    main()
