#!/usr/bin/env python3
"""
build_thought_map.py — Riorganizzatore di memorie, PRIMO MATTONE (strada A, 30/06/2026).

Cartografo del pensiero di Euri: legge TUTTO il grafo memoria (read-only) e produce
una mappa ADDITIVA `MAPPA_DEL_PENSIERO.md`. NON tocca un solo nodo.

Principi (load-bearing, dalle lezioni di Stefano):
- GENERA-NON-MUTA / RIORGANIZZA-NON-CURA: la mappa DESCRIVE e SEGNALA, non corregge,
  non cancella, non setta superseded/requires_verification. La memoria fallibile resta
  fallibile: è la tesi, non il bug. (feedback_memoria_fallibile_non_curata)
- READ-ONLY assoluto: nessuna scrittura su euri:memory:*, nessun touch (recalled_count
  invariato). Unico output = il file .md.
- Model-agnostic: usa il modello caldo dream_client (Qwen ora); swappabile a ds4 dopo
  per l'ablazione Qwen-vs-frontier.
- Domini DAL DATO, mai hardcoded. (no_hardcoded_domain)

Per dominio fa emergere: (1) temi portanti + entità, (2) tensioni/contraddizioni
[solo segnalate], (3) punti sotto-groundati (requires_verification / molto derivato).
Reduce finale: ponti cross-dominio + "cosa guardare" per Stefano.

Uso:
  ./venv/bin/python build_thought_map.py --domain "intelligenza artificiale"   # trial 1 dominio
  ./venv/bin/python build_thought_map.py                                        # mappa intera
  ./venv/bin/python build_thought_map.py --max-domains 4                        # i 4 domini piu' grandi
"""
import argparse, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import redis
import config
from core.ollama_client import dream_client

MODEL = config.DREAM_OLLAMA_MODEL
R = redis.Redis(host=config.REDIS_HOST, port=getattr(config, "REDIS_PORT", 6379), decode_responses=True)

# ⚠️ MAI scrivere nel Vault Obsidian: è sorvegliato dall'Obsidian Sync e re-ingerirebbe la mappa
# come MEMORIA (observer-effect: la lente che descrive le memorie diventa memoria). Cartella dedicata,
# fuori dal watcher. (Incidente 01/07 risolto così.)
MAPS_DIR = Path(getattr(config, "BASE_DIR", Path(__file__).parent)) / "thought_maps"
MAPS_DIR.mkdir(exist_ok=True)

# Sorgenti che NON vanno mai toccate/curate: tutto comunque read-only, ma le distinguiamo
# nella mappa (cosa l'ha DETTO Stefano vs cosa Euri ha DERIVATO).
PRIMARY = {"user", "teach", "obsidian_vault"}


def load_nodes():
    nodes = []
    for k in R.scan_iter("euri:memory:*", count=1000):
        try:
            d = R.json().get(k)            # read puro: nessun touch, recalled_count invariato
        except Exception:
            continue
        if isinstance(d, list):
            d = d[0] if d else None
        if isinstance(d, dict) and d.get("content"):
            nodes.append(d)
    return nodes


def fmt_node(d):
    rv = " [DA-VERIFICARE]" if d.get("requires_verification") else ""
    prov = "DETTO-DA-STEFANO" if d.get("source") in PRIMARY else f"derivato:{d.get('source')}"
    rc = d.get("recalled_count", 0)
    cid = str(d.get("id", "?"))[:8]
    content = (d.get("content") or "").strip().replace("\n", " ")
    return f"- [{cid} | {prov} | richiami:{rc}]{rv} {content}"


CARTO_SYS = (
    "Sei un CARTOGRAFO della memoria di un'AI (Euri). Il tuo compito NON è correggere, "
    "validare o ripulire: è DESCRIVERE fedelmente cosa c'è. La memoria può contenere "
    "errori, scherzi, cose datate: NON li correggere, al massimo li SEGNALI come osservazione. "
    "Resta aderente al testo, non inventare fatti non presenti, e dichiara l'incertezza."
)

def carto_prompt(domain, block):
    return (
        f"Dominio: «{domain}». Qui sotto le note di memoria di Euri in questo dominio "
        f"(ognuna: [id | provenienza | n.richiami] testo; [DA-VERIFICARE] = già marcata incerta).\n\n"
        f"{block}\n\n"
        "Produci una sezione Markdown con QUESTE sottoparti, concise e fedeli:\n"
        "### Temi portanti & entità — i 3-6 temi/entità centrali (cosa sa, attorno a cosa ruota).\n"
        "### Tensioni — note che si contraddicono o entità descritte in modo incoerente. "
        "SOLO segnalare (cita gli id), NON dire quale sia giusta.\n"
        "### Sotto-groundato — cosa poggia su base debole: molte note 'derivato' e poche "
        "'DETTO-DA-STEFANO', o [DA-VERIFICARE]. Indica le zone che una parola di Stefano "
        "consoliderebbe. NON correggere.\n"
        "Se una sottoparte è vuota, scrivi '— nulla di rilevante'. Niente preamboli."
    )

def ask(messages, think=False, num_predict=2000, num_ctx=8192):
    resp = dream_client.chat(
        model=MODEL, messages=messages, think=think,
        options={"num_predict": num_predict, "temperature": 0.3, "num_ctx": num_ctx},
    )
    return (resp.get("message", {}) or {}).get("content", "").strip()


def map_domain(domain, dnodes, batch, think):
    # batch per qualità su domini grossi (chimica polimeri ~400)
    parts = []
    for i in range(0, len(dnodes), batch):
        block = "\n".join(fmt_node(n) for n in dnodes[i:i + batch])
        out = ask([{"role": "system", "content": CARTO_SYS},
                   {"role": "user", "content": carto_prompt(domain, block)}],
                  think=think, num_predict=3500, num_ctx=16384)
        parts.append(out)
        print(f"    batch {i//batch+1}/{(len(dnodes)+batch-1)//batch} ok ({len(out)} char)", file=sys.stderr)
    if len(parts) == 1:
        return parts[0]
    # merge dei batch dello stesso dominio (fedele, no invenzione) — budget ampio: il merge è verboso
    merged = ask([{"role": "system", "content": CARTO_SYS},
                  {"role": "user", "content":
                   f"Dominio «{domain}»: unisci queste analisi parziali in UNA sezione coerente "
                   f"(stesse sottoparti ### Temi / ### Tensioni / ### Sotto-groundato), senza perdere "
                   f"id citati e senza inventare:\n\n" + "\n\n---\n\n".join(parts)}],
                 think=think, num_predict=4000, num_ctx=16384)
    return merged


SUBJ_PROMPT_HEAD = (
    "Cosa sa Euri di «{subj}»? Sotto le note di memoria che lo menzionano "
    "(ognuna: [id | provenienza | n.richiami] testo; [DA-VERIFICARE] = già marcata incerta).\n\n"
    "{block}\n\n"
    "Produci un profilo Markdown, fedele e senza invenzioni:\n"
    "### Cosa sa — i fatti principali su «{subj}» (cosa, quando, con chi, valori).\n"
    "### Fondatezza — distingui ciò che poggia su DETTO-DA-STEFANO da ciò che è 'derivato' "
    "(reflection/loop2e/passive) o [DA-VERIFICARE]. Sii esplicito su quanto è solido.\n"
    "### Tensioni — affermazioni incoerenti su «{subj}» tra le note (cita gli id, NON dire quale è giusta).\n"
    "### Da confermare — cosa una parola di Stefano consoliderebbe. NON correggere.\n"
    "Se una parte è vuota: '— nulla di rilevante'. Niente preamboli."
)

def map_subject(subj, snodes, batch, think):
    parts = []
    for i in range(0, len(snodes), batch):
        block = "\n".join(fmt_node(n) for n in snodes[i:i + batch])
        out = ask([{"role": "system", "content": CARTO_SYS},
                   {"role": "user", "content": SUBJ_PROMPT_HEAD.format(subj=subj, block=block)}],
                  think=think, num_predict=3500, num_ctx=16384)
        parts.append(out)
        print(f"    batch {i//batch+1}/{(len(snodes)+batch-1)//batch} ok ({len(out)} char)", file=sys.stderr)
    if len(parts) == 1:
        return parts[0]
    return ask([{"role": "system", "content": CARTO_SYS},
                {"role": "user", "content":
                 f"Profilo di «{subj}»: unisci queste analisi parziali in UN profilo coerente "
                 f"(stesse sottoparti ### Cosa sa / ### Fondatezza / ### Tensioni / ### Da confermare), "
                 f"senza perdere id citati e senza inventare:\n\n" + "\n\n---\n\n".join(parts)}],
               think=think, num_predict=4000, num_ctx=16384)


def find_subject_nodes(nodes, subj):
    """Read-only: nodi che menzionano il soggetto (content o tag, case-insensitive)."""
    q = subj.lower()
    hits = []
    for n in nodes:
        hay = (n.get("content") or "").lower()
        tags = " ".join(n.get("tags") or []).lower()
        if q in hay or q in tags:
            hits.append(n)
    # piu' richiamati / piu' recenti prima (solo per ORDINE di lettura, non tocca nulla)
    hits.sort(key=lambda d: (d.get("recalled_count", 0), d.get("last_recalled_at") or ""), reverse=True)
    return hits


def reduce_cross(sections, think, num_predict=3500):
    # Le sezioni intere sommano a ~27k token: troppo per UNA passata (l'input riempie il
    # contesto e strozza l'output). Il reduce ha bisogno del SUCCO per-dominio, non di tutto:
    # tronco ogni sezione (i dettagli restano nel corpo sotto la Sintesi).
    joined = "\n\n".join(f"## {dom}\n{txt[:1100].strip()}" for dom, txt in sections)
    prompt = (
        "Queste sono le mappe per-dominio della memoria di Euri. Scrivi UNA sintesi di apertura, "
        "ASCIUTTA (Qwen tende a dilungarsi: NON farlo). Esattamente tre sottoparti:\n"
        "### Ponti cross-dominio — max 5 punti, 1 frase l'uno: collegamenti tra domini diversi "
        "(un tema/entità che attraversa più domini).\n"
        "### Pattern ricorrenti — max 4 punti, 1 frase l'uno: cosa si ripete nel MODO in cui Euri "
        "pensa/ricorda (non i contenuti).\n"
        "### Cosa guardare (per Stefano) — max 5 punti: dove una sua parola consoliderebbe di più, "
        "o tensioni che vale la pena sciogliere. Suggerimenti, NON correzioni.\n"
        "Niente preambolo, niente conclusione, niente ripetere i contenuti dei domini. "
        "Fedele, nessuna invenzione.\n\n" + joined
    )
    # input grande (tutte le sezioni): serve una finestra di contesto ampia o l'output viene strozzato
    return ask([{"role": "system", "content": CARTO_SYS},
                {"role": "user", "content": prompt}], think=think, num_predict=num_predict, num_ctx=32768)


def parse_sections(md_text):
    """Estrae le sezioni per-dominio da una MAPPA esistente (per il --reduce-only).
    Scarta il titolo e la vecchia Sintesi; tiene (dominio, testo)."""
    out, cur_h, cur_b = [], None, []
    for line in md_text.splitlines():
        if line.startswith("# "):
            if cur_h is not None:
                out.append((cur_h, "\n".join(cur_b).strip()))
            cur_h, cur_b = line[2:].strip(), []
        else:
            cur_b.append(line)
    if cur_h is not None:
        out.append((cur_h, "\n".join(cur_b).strip()))
    skip = ("Mappa del pensiero di Euri", "Sintesi")
    return [(h, b) for h, b in out if h not in skip and b]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", help="solo questo dominio (trial)")
    ap.add_argument("--max-domains", type=int, default=0, help="solo gli N domini piu' grandi")
    ap.add_argument("--min-domain-nodes", type=int, default=10,
                    help="domini sotto questa soglia vengono raggruppati in '(domini minori)'")
    ap.add_argument("--batch", type=int, default=70, help="nodi per chiamata")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--out", default="MAPPA_DEL_PENSIERO.md")
    ap.add_argument("--reduce-only", action="store_true",
                    help="rigenera SOLO la Sintesi dalle sezioni gia' presenti nel file (no Redis, no re-map)")
    ap.add_argument("--reduce-predict", type=int, default=3500, help="num_predict del reduce")
    ap.add_argument("--subject", help="modalità entità-centrica: 'cosa sa Euri di X'")
    ap.add_argument("--subject-cap", type=int, default=200, help="max nodi per un soggetto")
    args = ap.parse_args()

    def out_target():
        return MAPS_DIR / args.out   # fuori dal Vault sorvegliato

    if args.reduce_only:
        op = out_target()
        md = op.read_text(encoding="utf-8")
        secs = parse_sections(md)
        print(f"reduce-only: {len(secs)} sezioni dominio dal file esistente", file=sys.stderr)
        syn = reduce_cross(secs, args.think, args.reduce_predict)
        head_lines = []
        for line in md.splitlines():
            if line.startswith("# ") and head_lines:
                break
            head_lines.append(line)
        head = "\n".join(head_lines).rstrip() + "\n\n"
        body = "# Sintesi\n" + syn + "\n\n" + "\n\n".join(f"# {d}\n{t}" for d, t in secs)
        op.write_text(head + body, encoding="utf-8")
        print(f"Sintesi rigenerata in {op}", file=sys.stderr)
        return

    if args.subject:
        nodes = load_nodes()
        snodes = find_subject_nodes(nodes, args.subject)
        print(f"soggetto «{args.subject}»: {len(snodes)} note che lo menzionano", file=sys.stderr)
        if not snodes:
            print("Nessuna nota: Euri non ha memoria diretta di questo soggetto.", file=sys.stderr)
            return
        if len(snodes) > args.subject_cap:
            print(f"  (limito alle {args.subject_cap} piu' richiamate)", file=sys.stderr)
            snodes = snodes[:args.subject_cap]
        profile = map_subject(args.subject, snodes, args.batch, args.think)
        head = (f"# Cosa sa Euri di: {args.subject}\n"
                f"*Generato {datetime.now():%Y-%m-%d %H:%M} — {len(snodes)} note — modello {MODEL}.*\n"
                f"*Lente additiva read-only: descrive e segnala, non corregge.*\n\n")
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in args.subject)[:50].strip()
        op = MAPS_DIR / f"SU_{safe}.md"
        op.write_text(head + profile, encoding="utf-8")
        print(f"Scritto: {op}", file=sys.stderr)
        return

    nodes = load_nodes()
    by_dom = defaultdict(list)
    for n in nodes:
        by_dom[n.get("domain") or n.get("category") or "generale"].append(n)
    doms = sorted(by_dom.items(), key=lambda kv: len(kv[1]), reverse=True)
    if args.domain:
        doms = [(args.domain, by_dom.get(args.domain, []))]
    elif args.max_domains:
        doms = doms[:args.max_domains]
    else:
        # mappa i domini "veri" (>= soglia); accorpa la coda lunga di micro-domini
        # (etichette libere dell'LLM, spesso 1-2 nodi) in un blocco unico.
        big = [(d, ns) for d, ns in doms if len(ns) >= args.min_domain_nodes]
        tail = [n for d, ns in doms if len(ns) < args.min_domain_nodes for n in ns]
        doms = big
        if tail:
            doms.append((f"(domini minori: {len(tail)} note sparse)", tail))
    print(f"Nodi: {len(nodes)} | domini da mappare: {len(doms)} | modello: {MODEL}", file=sys.stderr)

    sections = []
    for dom, dnodes in doms:
        if not dnodes:
            print(f"  [{dom}] nessun nodo, salto", file=sys.stderr); continue
        print(f"  mappo «{dom}» ({len(dnodes)} nodi)...", file=sys.stderr)
        sections.append((dom, map_domain(dom, dnodes, args.batch, args.think)))

    head = (f"# Mappa del pensiero di Euri\n"
            f"*Generata {datetime.now():%Y-%m-%d %H:%M} — {len(nodes)} nodi, {len(sections)} domini — modello {MODEL}.*\n"
            f"*Lente additiva read-only: descrive e segnala, non corregge. La memoria resta com'è.*\n\n")
    body = ""
    if len(sections) > 1:
        print("  reduce cross-dominio...", file=sys.stderr)
        body += "# Sintesi\n" + reduce_cross(sections, args.think) + "\n\n"
    body += "\n\n".join(f"# {dom}\n{txt}" for dom, txt in sections)

    out_path = out_target()   # fuori dal Vault sorvegliato (mai re-ingerire la mappa)
    out_path.write_text(head + body, encoding="utf-8")
    print(f"\nScritto: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
