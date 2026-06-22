#!/usr/bin/env python3
"""
probe_event_quality.py — micro-valutatore di QUALITÀ INTERNA del testo di un evento.

⚠ COSA NON È (distinzione decisa con Stefano 22/06, dopo lo STOP sul grounding):
  - NON misura grounding (la premessa è vera? → serve la provenienza, non c'è ancora);
  - NON misura overclaim epistemico vero (idem);
  - NON usa evidenza vissuta (il retrieval multi-hop è un muro a parte).

COSA È: giudica SOLO dal testo dell'evento la sua qualità interna —
  specificità · non-banalità · coerenza · utilità operativa · azionabilità,
  e `should_surface` nel senso "vale attenzione", NON "è fondato".

OBIETTIVO: dare a `core.tension` un segnale di QUALITÀ per non trattare tutti gli
`insight/promoted` uguali (oggi T=0.29 piatto). Questo segnale NON sostituisce il grounding
(opzione 2, provenienza, lavoro architetturale separato): lo affianca.

METODO: si confrontano qwen2.5:3b e llama3.2:3b contro gemma4:26b (ancora d'ablazione).
CRITERIO DI PASSAGGIO: se i piccoli rankano coerentemente scoria < medio < gioiello vicino
a Gemma, il segnale può entrare in tension.

USO (Ollama non conteso):
    ./venv/bin/python probe_event_quality.py
    ./venv/bin/python probe_event_quality.py --models qwen2.5:3b,llama3.2:3b --reference gemma4:26b
    ./venv/bin/python probe_event_quality.py --recent 6        # quanti insight recenti come "medi"
"""
from __future__ import annotations

import argparse
import json
import time

from utils.redis_client import get_client
from core.ollama_client import chat_client
from core.reaction import _insight_brief

# Ancore note dal review 22/06 (id reali). Il resto dei casi = insight recenti = "medi".
GEM_ID = "euri:insight:d1a9ede6-2066-46e7-b586-6eca1ada5431"      # automotive × supply chain
SCORIA_ID = "euri:insight:c440fd57-158b-4f76-82c1-bd37caf180ba"   # tempo libero × RF

FLOAT_FIELDS = ["specificity", "non_triviality", "coherence", "operational_utility", "actionability"]

EVAL_PROMPT = """Sei il valutatore della QUALITÀ INTERNA degli eventi di un assistente personale. Giudichi SOLO il testo che ti do, per quanto è ben formato come pensiero — NON se è vero, NON se è fondato su prove. Non hai accesso a fonti: non inventare conferme.

Restituisci SOLO un oggetto JSON su una riga, con ESATTAMENTE questi campi (float 0..1 dove indicato):
- "specificity": quanto è specifico e concreto vs vago/generico.
- "non_triviality": quanto è non-banale, non-ovvio, non una constatazione scontata.
- "coherence": quanto è internamente coerente; un accostamento non-sequitur (due cose senza nesso reale) vale BASSO.
- "operational_utility": quanto sarebbe utile al lavoro pratico dell'utente, se vero.
- "actionability": quanto suggerisce un'azione concreta e prossima.
- "should_surface": true/false — vale la pena di portare attenzione su questo? (= merita attenzione, NON = è dimostrato vero).
- "reason": stringa BREVE (max 15 parole), in italiano.

EVENTO ({kind}):
{event}

JSON:"""


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _composite(d: dict | None) -> float | None:
    if not isinstance(d, dict) or "_error" in d:
        return None
    vals = []
    for k in FLOAT_FIELDS:
        try:
            vals.append(max(0.0, min(1.0, float(d[k]))))
        except (TypeError, ValueError, KeyError):
            return None
    return sum(vals) / len(vals)


def evaluate(model: str, kind: str, event_text: str) -> tuple[dict | None, float]:
    prompt = EVAL_PROMPT.format(kind=kind, event=event_text[:1200])
    t0 = time.time()
    try:
        r = chat_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 220},
            think=False,
        )
        out = r.message.content or ""
    except Exception as e:
        return {"_error": str(e)[:80]}, time.time() - t0
    return _parse_json(out), time.time() - t0


def collect_cases(r, recent: int) -> list[dict]:
    from redis.commands.search.query import Query
    cases, seen = [], set()
    # ancore note prima (gioiello/scoria), così ci sono di sicuro
    for iid, label in [(GEM_ID, "GIOIELLO"), (SCORIA_ID, "SCORIA")]:
        o = r.json().get(iid)
        if o:
            cases.append({"label": label, "kind": "insight/promoted", "id": iid, "event": _insight_brief(o)})
            seen.add(iid)
    # insight recenti come "medi"
    try:
        res = r.ft("idx:insights").search(
            Query("@status:{promoted}").sort_by("created_at", asc=False).paging(0, recent + 4)
        )
        for doc in res.docs:
            if doc.id in seen:
                continue
            o = r.json().get(doc.id)
            if not o:
                continue
            cases.append({"label": "medio?", "kind": "insight/promoted", "id": doc.id, "event": _insight_brief(o)})
            seen.add(doc.id)
            if len(cases) >= recent + 2:
                break
    except Exception as e:
        print(f"  (insights: {e})")
    return cases


def clock_baseline() -> dict:
    return {"label": "BASELINE", "kind": "clock/threshold", "id": "(clock)",
            "event": "Un promemoria dell'utente è SCADUTO 5 minuti fa: «richiamare il cliente per la conferma d'ordine»."}


def _fmt(d: dict | None, comp: float | None) -> str:
    if d is None:
        return "JSON non parsato"
    if "_error" in d:
        return f"errore: {d['_error']}"
    def g(k):
        v = d.get(k)
        if isinstance(v, bool):
            return "Y" if v else "n"
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "?"
    c = f"{comp:.2f}" if comp is not None else "  ? "
    return (f"Q={c} | spec={g('specificity')} ntr={g('non_triviality')} coh={g('coherence')} "
            f"util={g('operational_utility')} act={g('actionability')} surf={g('should_surface')}  «{str(d.get('reason',''))[:50]}»")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen2.5:3b,llama3.2:3b")
    ap.add_argument("--reference", default="gemma4:26b")
    ap.add_argument("--no-reference", action="store_true")
    ap.add_argument("--recent", type=int, default=4, help="insight recenti come casi 'medi'")
    ap.add_argument("--no-clock", action="store_true", help="ometti il baseline clock")
    args = ap.parse_args()

    small = [m.strip() for m in args.models.split(",") if m.strip()]
    ref = None if args.no_reference else args.reference
    models = small + ([] if ref is None else [ref])

    r = get_client()
    print("── raccolta casi (solo testo, niente evidenza/grounding) ──")
    cases = collect_cases(r, args.recent)
    if not args.no_clock:
        cases.append(clock_baseline())
    print(f"   {len(cases)} casi  |  modelli: {', '.join(small)}" + (f"  | ancora: {ref}" if ref else ""))

    # comp[model][case_idx] = composite
    comp = {m: {} for m in models}
    for idx, c in enumerate(cases):
        print(f"\n{'='*80}\n[{idx+1}/{len(cases)}] {c['label']:9} {c['kind']}  id={c['id']}")
        print(f"  «{c['event'][:160]}»")
        for m in models:
            d, dt = evaluate(m, c["kind"], c["event"])
            cm = _composite(d)
            comp[m][idx] = cm
            tag = " ◀ancora" if m == ref else ""
            print(f"  {m:13} {_fmt(d, cm)}  ({dt:.1f}s){tag}")

    # ── RANKING: ogni modello ordina i casi per qualità composta ──
    print(f"\n{'='*80}\n== RANKING per qualità composta (alto→basso) ==")
    insight_idx = [i for i, c in enumerate(cases) if c["kind"].startswith("insight")]
    for m in models:
        ranked = sorted(
            [i for i in range(len(cases)) if comp[m].get(i) is not None],
            key=lambda i: -comp[m][i],
        )
        order = "  >  ".join(f"{cases[i]['label']}:{comp[m][i]:.2f}" for i in ranked)
        tag = " ◀ancora" if m == ref else ""
        print(f"\n  {m}{tag}\n    {order}")
        # check del criterio sugli insight: scoria in fondo? gioiello in cima?
        ins_ranked = [i for i in ranked if i in insight_idx]
        if ins_ranked:
            labels = [cases[i]["label"] for i in ins_ranked]
            gem_ok = "GIOIELLO" in labels and labels.index("GIOIELLO") <= 1
            scoria_ok = "SCORIA" in labels and labels[-1] == "SCORIA"
            print(f"    criterio: gioiello-in-alto={'✅' if gem_ok else '❌'}  scoria-in-basso={'✅' if scoria_ok else '❌'}")

    print("\nNB: misura QUALITÀ INTERNA del testo, NON grounding. Il criterio è l'ACCORDO")
    print("    di ranking coi piccoli vs Gemma (scoria<medio<gioiello), non un valore assoluto.")


if __name__ == "__main__":
    main()
