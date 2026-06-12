#!/usr/bin/env python3
"""
Baseline N1 — tasso di correzioni-fantasma nei correction signal.

Problema (roadmap N1): la cattura `detect_correction` è troppo larga → intercetta
come "correzione" anche domande, elaborazioni, accordi, pensieri ad alta voce. E
il classify notturno di Loop 2g NON filtra: presuppone che il signal SIA una
correzione e ne classifica solo il tipo (bad_memory/bad_reasoning/ambiguous) →
genera lesson spurie che competono nel richiamo (caso misurato 11/06).

Questo script è il NUMERO "prima" (P-METODO). Read-only: NON scrive nulla in Redis.
Un giudice LLM fa la domanda che il sistema non fa — "è davvero una correzione di
un errore di Euri, o è altro?" — su ogni signal esistente, e incrocia il giudizio
col verdict notturno per mostrare che il verdict attuale non separa i fantasma.

Uso:  venv/bin/python diag_phantom_corrections.py
"""
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))

import config
from core.ollama_client import chat_client
from utils.redis_client import get_client

_JUDGE_PROMPT = """\
Devi dire se un'affermazione dell'utente è una CORREZIONE di un errore dell'assistente Euri, oppure NO.

È CORREZIONE solo se l'utente sta dicendo che Euri ha SBAGLIATO qualcosa (un fatto, un \
ricordo, un ragionamento) e fornisce o implica la versione giusta.
NON è correzione (rispondi ALTRO) se l'utente: fa una domanda, chiede una prova, elabora o \
aggiunge informazioni proprie, concorda, pensa ad alta voce, cambia argomento, o semplicemente \
continua il discorso — anche se la frase contiene "no", "non è", "in realtà".

Euri aveva detto: "{risposta}"
L'utente ha detto: "{correzione}"

Rispondi con UNA SOLA parola: CORREZIONE oppure ALTRO."""


def judge(risposta: str, correzione: str) -> str:
    try:
        resp = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": _JUDGE_PROMPT.format(
                risposta=(risposta or "(nessuna risposta registrata)")[:600],
                correzione=(correzione or "")[:600],
            )}],
            options={"temperature": 0, "num_predict": 10},
            think=False,
        )
        out = (resp.message.content or "").upper()
        if "CORREZIONE" in out:
            return "CORREZIONE"
        if "ALTRO" in out:
            return "ALTRO"
        return "?"
    except Exception as e:
        return f"ERR:{e}"


def main():
    r = get_client()
    sigs = []
    for k in r.scan_iter("euri:correction:*"):
        d = r.json().get(k, "$")
        if d:
            sigs.append(d[0])
    sigs.sort(key=lambda x: x.get("created_at", 0))

    print(f"Correction signal totali: {len(sigs)}")
    print("Giudizio LLM in corso (read-only)…\n")

    rows = []
    for s in sigs:
        g = judge(s.get("risposta_euri", ""), s.get("correzione_user", ""))
        rows.append((s.get("id", "")[:8], s.get("verdict"), g, s.get("correzione_user", "")))
        flag = "  ⚑FANTASMA" if g == "ALTRO" else ""
        print(f"  {rows[-1][0]} | verdict={str(s.get('verdict')):13} | giudice={g:10}{flag} | {s.get('correzione_user','')[:62]}")

    judged = [r for r in rows if r[2] in ("CORREZIONE", "ALTRO")]
    phantom = [r for r in judged if r[2] == "ALTRO"]
    print("\n" + "=" * 70)
    print(f"TASSO FANTASMA: {len(phantom)}/{len(judged)} = "
          f"{100*len(phantom)/max(len(judged),1):.0f}% NON sono correzioni di Euri")
    print("=" * 70)

    # Il punto chiave per l'obiezione #3: il verdict notturno NON separa i fantasma.
    print("\nIncrocio giudice × verdict notturno (i fantasma sparsi su tutti i verdict = il")
    print("classify attuale non li filtra, perché non chiede mai 'è una correzione?'):")
    cross = Counter((r[1], r[2]) for r in judged)
    verdicts = sorted({r[1] for r in judged}, key=str)
    print(f"  {'verdict':16} | CORREZIONE | ALTRO(fantasma)")
    for v in verdicts:
        print(f"  {str(v):16} |    {cross.get((v,'CORREZIONE'),0):5}   |   {cross.get((v,'ALTRO'),0):5}")

    # I fantasma noti dai log dell'11/06 — il giudice li becca?
    known = {"9beb750e", "9e33a85e", "65a8941e", "30192d55", "bf1e535b"}
    print("\nControllo sui fantasma noti dell'11/06:")
    for r_ in rows:
        if r_[0] in known:
            ok = "✓ beccato" if r_[2] == "ALTRO" else "✗ MANCATO"
            print(f"  {r_[0]} → giudice={r_[2]} {ok}")


if __name__ == "__main__":
    main()
