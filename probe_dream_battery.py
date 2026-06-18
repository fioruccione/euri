#!/usr/bin/env python3
"""
probe_dream_battery.py — confronto motori sul COMPITO REALE del Dream Engine.

Stesse coppie di memorie (substrato costante) fatte sognare da motori diversi:
Qwen3.6-thinking (il sognatore attuale) vs gemma4:31b-thinking (il candidato).
Replica FEDELE del prompt di core.dream_engine._generate_dream (temp 0.6,
num_predict 4500, think=True) — copiato sotto per fedeltà, NON reinventato.

Disegno: coppie PULITE (testano oro/novità) + coppie CONTAMINATE apposta
(VistaMax×polimero, Leonardo×tecnico) per misurare se il thinking AMPLIFICA la
contaminazione. READ-ONLY: genera e confronta, NON scrive sogni/insight in Redis.

USO (NON mentre il Dream Engine vero sta girando: stesso modello, contende):
    python3 probe_dream_battery.py                 # 2 contaminate + 3 pulite (seed fisso)
    python3 probe_dream_battery.py --pairs 5 --seed 7
Scrive probe_dream_<ts>.md col confronto affiancato.
"""
import argparse, re, time, random, datetime, sys

import config
from core.ollama_client import chat_client, dream_client
from core.embedder import Embedder
from utils.redis_client import get_client

# ── prompt COPIATO da core.dream_engine._generate_dream (fedeltà) ──────────────
DREAM_PROMPT = """\
Hai due memorie da domini diversi. Il tuo compito è trovare una connessione operativa non ovvia — qualcosa che non emerge guardando un solo dominio.

Memoria A (dominio: {dom_a}):
"{a}"

Memoria B (dominio: {dom_b}):
"{b}"

Se esiste una connessione genuina, rispondi ESATTAMENTE in questo formato (tre righe, niente altro):
Nel dominio [{dom_a}] succede: [descrivi cosa succede concretamente, con i dettagli specifici della memoria A]
Nel dominio [{dom_b}] succede: [descrivi cosa succede concretamente, con i dettagli specifici della memoria B]
La connessione operativa non ovvia è: [effetto pratico verificabile — cosa puoi fare o evitare sapendo entrambe le cose]

REGOLE:
- La terza riga deve descrivere un effetto pratico che si può verificare o applicare, non un principio filosofico.
- Se la connessione che trovi è ovvia (es. "entrambi ottimizzano un processo"), rispondi NESSUN INSIGHT.
- Se non riesci a formulare la terza riga con un effetto concreto, rispondi NESSUN INSIGHT.
- Nessuna frase introduttiva, nessun commento fuori formato."""

ENGINES = {
    "qwen":      (dream_client, config.DREAM_OLLAMA_MODEL),   # qwen3.6:35b
    "gemma31":   (chat_client,  "gemma4:31b"),
}


def clean(text):
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def has_structure(t):
    return ("Nel dominio [" in t) and ("connessione operativa" in t.lower())


def dream(engine, a, dom_a, b, dom_b):
    client, model = ENGINES[engine]
    prompt = DREAM_PROMPT.format(dom_a=dom_a, dom_b=dom_b, a=a, b=b)
    t0 = time.time()
    resp = client.chat(model=model, messages=[{"role": "user", "content": prompt}],
                       options={"temperature": 0.6, "num_predict": 4500}, think=True)
    dt = time.time() - t0
    out = clean(resp.message.content or "")
    if not out or "NESSUN INSIGHT" in out.upper():
        verdict = "NESSUN INSIGHT"
    elif has_structure(out):
        verdict = "CANDIDATE"
    else:
        verdict = "fuori-formato"
    return out, dt, verdict


def load_memories(r):
    mems = []
    for k in r.scan_iter("euri:memory:*"):
        d = r.json().get(k, "$"); d = d[0] if isinstance(d, list) else d
        c = (d.get("content") or "").strip()
        if 20 <= len(c) <= 600:  # scarta troppo corti/lunghi, come materiale-sogno sensato
            mems.append({"id": d.get("id"), "content": c, "domain": d.get("domain", "generale")})
    return mems


def first(mems, *, kw=None, dom=None, exclude_ids=()):
    for m in mems:
        if m["id"] in exclude_ids:
            continue
        if kw and kw.lower() not in m["content"].lower():
            continue
        if dom and dom.lower() not in (m["domain"] or "").lower():
            continue
        return m
    return None


def build_pairs(mems, n_random, seed):
    rng = random.Random(seed)
    pairs = []  # (etichetta, mem_a, mem_b)
    used = set()

    # ── CONTAMINATE (decisive sull'amplificazione) ──
    # VistaMax MAL-ETICHETTATA: il nodo il cui DOMINIO è 'test medico' (l'allucinazione).
    vista = first(mems, kw="VistaMax", dom="test medico") or first(mems, dom="test medico")
    poly = first(mems, kw="degasificazione") or first(mems, dom="estrusione plastica")
    if vista and poly and vista["id"] != poly["id"]:
        pairs.append(("CONTAMINATA: VistaMax[test medico] × polimero pulito", vista, poly))
        used |= {vista["id"], poly["id"]}
    # LEONARDO contaminato: la memoria che FONDE le identità (alias/nome abituale/stessa persona),
    # non quella corretta 'collega Leonardo'.
    leo = (first(mems, kw="nome abituale") or first(mems, kw="alias Stefano")
           or first(mems, kw="stessa persona"))
    tech = first(mems, kw="grado 50", exclude_ids=used) or first(mems, kw="MFI", exclude_ids=used)
    if leo and tech and leo["id"] != tech["id"]:
        pairs.append(("CONTAMINATA: Leonardo[identità fusa] × tecnico", leo, tech))
        used |= {leo["id"], tech["id"]}

    # ── PULITE (oro/novità) — domini distinti, seeded ──
    by_dom = {}
    for m in mems:
        if m["id"] in used:
            continue
        by_dom.setdefault(m["domain"], []).append(m)
    doms = [d for d, lst in by_dom.items() if lst]
    tries = 0
    while len([p for p in pairs if p[0].startswith("PULITA")]) < n_random and tries < 200:
        tries += 1
        da, db = rng.sample(doms, 2) if len(doms) >= 2 else (None, None)
        if not da:
            break
        ma, mb = rng.choice(by_dom[da]), rng.choice(by_dom[db])
        if ma["id"] in used or mb["id"] in used or ma["id"] == mb["id"]:
            continue
        pairs.append((f"PULITA: {da} × {db}", ma, mb))
        used |= {ma["id"], mb["id"]}
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3, help="numero coppie PULITE (oltre alle 2 contaminate)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--engines", default="qwen,gemma31")
    args = ap.parse_args()
    engines = [e.strip() for e in args.engines.split(",") if e.strip() in ENGINES]

    print("[setup] embedder(CPU) + redis…")
    r = get_client()
    Embedder().load()  # scalda l'embedder (non serve qui, ma allinea l'ambiente al dream reale)
    mems = load_memories(r)
    print(f"[setup] {len(mems)} memorie candidate-sogno")
    pairs = build_pairs(mems, args.pairs, args.seed)
    print(f"[setup] {len(pairs)} coppie: " + " | ".join(p[0][:28] for p in pairs))

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"probe_dream_{ts}.md"
    out = [f"# Sonda SOGNO: {' vs '.join(engines)} (think=True, temp0.6)  ({datetime.datetime.now():%Y-%m-%d %H:%M})",
           "_stesse coppie, prompt reale di _generate_dream, read-only_\n"]
    for label, ma, mb in pairs:
        print(f"\n################ {label} ################")
        out.append(f"## {label}")
        out.append(f"**A** [{ma['domain']}]: {ma['content'][:200]}")
        out.append(f"**B** [{mb['domain']}]: {mb['content'][:200]}\n")
        for eng in engines:
            try:
                ans, dt, verdict = dream(eng, ma["content"], ma["domain"], mb["content"], mb["domain"])
            except Exception as e:
                ans, dt, verdict = f"(ERRORE: {e})", 0.0, "errore"
            print(f"  [{eng}] {verdict} ({dt:.0f}s)")
            out.append(f"### → {eng}  ·  {verdict}  ·  {dt:.0f}s")
            out.append(ans + "\n")
        out.append("---\n")
    with open(path, "w") as f:
        f.write("\n".join(out))
    print(f"\n[scritto] {path}")


if __name__ == "__main__":
    main()
