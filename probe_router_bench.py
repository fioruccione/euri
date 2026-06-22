#!/usr/bin/env python3
"""
probe_router_bench.py — bench del modellino-controllore (memory-controller) sui COMPITI VERI.

Misura un modello piccolo (default qwen2.5:3b, confronto gemma2:2b) sui compiti che la
pipeline di Euri vuole scaricare da Gemma-26b: classificazione d'INTENTO e probe di DEDUP.
Casi REALI minati dal log del daemon (logs/voice_daemon.log), incluse le frasi dove il regex
oggi fallisce e serve il fallback LLM da 13s — quelle che il modellino DEVE azzeccare.

Disciplina: Codex consiglia, io incrocio, Stefano misura sul reale. I casi 'debatable' hanno
ground-truth da CONFERMARE con Stefano (cosa intendeva davvero) prima di fidarsi del punteggio.

USO (dopo `ollama pull qwen2.5:3b`):
    python3 probe_router_bench.py --model qwen2.5:3b
    python3 probe_router_bench.py --model gemma2:2b
    python3 probe_router_bench.py --model qwen2.5:3b --task intent   # o dedup
"""
import argparse, time, re

from core.ollama_client import chat_client

# ── INTENT: (frase, etichetta-verità, nota) — casi reali dal log ──────────────
# ⚠ = ground-truth da confermare con Stefano (intento ambiguo)
INTENT_CASES = [
    ("Euri buon pomeriggio, come stai?", "CHAT", ""),
    ("Le solite cose da fare.", "CHAT", ""),
    ("Euri aiutami a fare una proporzione. Ho fatto il Melt Index di un PHD a 230° col peso da 2,16 kg, mi viene 5. A 190° quanto potrebbe diminuire?", "CHAT", "ragionamento tecnico, non recall"),
    ("Euri, sai che abbiamo in laboratorio una nuova persona?", "CHAT", ""),
    ("Si chiama Giada, è qui in laboratorio per darci una mano. Vediamo, ma sinceramente boh.", "CHAT", ""),
    ("Tutto bello, però pochi fanno quello che fai tu, o mi sbaglio? Anzi, forse nessuno.", "CHAT", ""),
    # SAVE
    ("Euri memorizza queste informazioni.", "SAVE_MEMORY", ""),
    ("Euri memorizza tutto.", "SAVE_MEMORY", ""),
    ("Si memorizza questo.", "SAVE_MEMORY", ""),
    ("Giacomo ha rifatto la prova a 190° ed è venuto grado 15, quindi memorizza queste informazioni.", "SAVE_MEMORY", "save in coda a un fatto"),
    # SEARCH — i casi DURI (oggi regex fallisce → fallback LLM 13s)
    ("Euri, ma io come mi chiamo?", "SEARCH", "recall fattuale"),
    ("Euri, di cosa parlavamo prima, te lo ricordi?", "SEARCH", "recall"),
    ("A proposito Euri, cosa sai sul Leonardo invece?", "SEARCH", "recall su entità"),
    ("Euri, scusa, fai un riassunto di quello che hai detto su Giada e tutto quello che ci siamo detti.", "SEARCH", "riassunto-da-memoria"),
    ("Sei sicuro di sapere di quello che stavamo parlando?", "SEARCH", "⚠ meta-domanda, conferma"),
    ("Ormai sono entrato in fissa col codice che scrivo per te e volevo capire se posso migliorare la situazione. Accetto consigli.", "SEARCH", "⚠ chiede consigli, forse CHAT — conferma"),
    # WEB
    ("Esistono sistemi che fanno quello che fai tu? Potresti fare anche una ricerca nel web.", "WEB_SEARCH", ""),
]

INTENT_PROMPT = (
    "Sei il classificatore di intenti di un assistente personale di nome Euri. Stefano dice una "
    "frase. Classificala in UNA sola di queste etichette ESATTE:\n"
    "- CHAT: conversazione, opinione, ragionamento, racconto (anche tecnico).\n"
    "- SEARCH: chiede di RICHIAMARE qualcosa dalla TUA memoria (es. 'ti ricordi', 'cosa sai di X', "
    "'di cosa parlavamo', 'come mi chiamo', 'fai un riassunto di quello che hai detto').\n"
    "- SAVE_MEMORY: chiede di SALVARE/memorizzare ('memorizza', 'segnati', 'tieni a mente').\n"
    "- WEB_SEARCH: chiede esplicitamente una ricerca sul WEB / info esterne attuali.\n\n"
    "Regole di disambiguazione:\n"
    "- Se contiene 'web', 'internet', 'online' o 'ricerca nel web', scegli WEB_SEARCH.\n"
    "- Se chiede 'come mi chiamo', 'ti ricordi', 'cosa sai di X', 'di cosa parlavamo' o "
    "'fai un riassunto di quello che hai detto', scegli SEARCH.\n"
    "- Se e solo se sei indeciso tra CHAT e SEARCH e NON ci sono segnali di memoria, scegli CHAT.\n\n"
    "Frase: «{text}»\n\nRispondi SOLO con l'etichetta, niente altro."
)

# ── DEDUP: (frase A, frase B, verità sì/no, nota) — coppie reali dal log ───────
DEDUP_CASES = [
    ("Sostituzione dell'HDPE con l'MDPE nel blend chimico per ottimizzare.",
     "Stefano sta analizzando la sostituzione tecnica dell'HDPE con MDPE nel blend.", "SI", ""),
    ("Il VistaMax è un polimero ExxonMobil usato come compatibilizzante per i polipropileni.",
     "La sessione riguarda l'ottimizzazione dei gradi di polipropilene con additivo VistaMax, copolimero ExxonMobil compatibilizzante.", "SI", ""),
    ("Stefano sta testando il processo neutro con il polipropilene rigenerato a MFI 6.",
     "Stefano ha preparato materiali in polipropilene neutro per le prove di processo.", "SI", ""),
    ("Giada è la nuova persona in laboratorio per supportare il team.",
     "Stefano lavora al progetto Regrado PP, che prevede test sul polipropilene.", "NO", "CONFERMATO Stefano 19/06: soggetti diversi, era falso positivo del sistema (0.86+LLM)"),
    ("Stefano è incerto sul contributo della nuova collaboratrice.",
     "Se l'obiettivo viene raggiunto, Stefano fornirà un riconoscimento.", "NO", "CONFERMATO Stefano 19/06: soggetti diversi, era falso positivo del sistema (0.86+LLM)"),
    # CASO D'ORO same-subject (Codex 19/06, da diag_gate.py): stesso TOPIC (Friis) ma REGISTRO
    # epistemico opposto — "ho competenza" vs "lo sapevo e ho dimenticato". Fonderle appiattisce
    # il grounding. Un router che guarda solo l'overlap lessicale risponde SI (sbaglia).
    ("Competenze in elettronica (riparazione TV Mivar, radio FM) e calcolo della formula di Friis.",
     "In passato applicava la formula del link budget di Friis per stime di guadagno d'antenna, ma non ricorda più i dettagli dei calcoli.", "NO", "same-subject: topic uguale, registro/grounding opposto → soggetti diversi"),
]

DEDUP_PROMPT = (
    "Due frammenti di memoria. Esprimono lo STESSO fatto/significato (uno è doppione dell'altro), "
    "anche se con parole diverse?\n"
    "A: «{a}»\nB: «{b}»\n\nRispondi SOLO 'SI' o 'NO'."
)


def ask(model, prompt):
    t0 = time.time()
    r = chat_client.chat(model=model, messages=[{"role": "user", "content": prompt}],
                         options={"temperature": 0.0, "num_predict": 12}, think=False)
    dt = time.time() - t0
    out = (r.message.content or "").strip().upper()
    if "<CHANNEL|>" in out:
        out = out.split("<CHANNEL|>", 1)[-1].strip()
    return out, dt


def _norm(s):
    """Normalizza per il match: solo alfanumerici, uppercase. SAVEMEMORY == SAVE_MEMORY."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def run_intent(model):
    LABELS = ["WEB_SEARCH", "SAVE_MEMORY", "SEARCH", "CHAT"]  # ordine: match il più specifico prima
    ok = 0; lat = []
    print(f"\n=== INTENT ({model}) ===")
    for text, gold, note in INTENT_CASES:
        out, dt = ask(model, INTENT_PROMPT.format(text=text)); lat.append(dt)
        out_n = _norm(out)
        pred = next((l for l in LABELS if _norm(l) in out_n), out)
        hit = (pred == gold)
        ok += hit
        flag = "✅" if hit else "❌"
        tag = " ⚠" if note.startswith("⚠") else ""
        print(f"  {flag} gold={gold:11s} pred={pred:11s} {dt:.2f}s{tag}  «{text[:55]}»")
    print(f"  → accuratezza {ok}/{len(INTENT_CASES)} = {100*ok/len(INTENT_CASES):.0f}%  |  latenza media {sum(lat)/len(lat):.2f}s")


def run_dedup(model):
    ok = 0; lat = []
    print(f"\n=== DEDUP ({model}) ===")
    for a, b, gold, note in DEDUP_CASES:
        out, dt = ask(model, DEDUP_PROMPT.format(a=a, b=b)); lat.append(dt)
        pred = "SI" if out.startswith("SI") or out.startswith("SÌ") else ("NO" if "NO" in out else out)
        hit = (pred == gold)
        ok += hit
        flag = "✅" if hit else "❌"
        tag = " ⚠" if note.startswith("⚠") else ""
        print(f"  {flag} gold={gold:3s} pred={pred:3s} {dt:.2f}s{tag}  «{a[:35]}» ~ «{b[:35]}»")
    print(f"  → accuratezza {ok}/{len(DEDUP_CASES)} = {100*ok/len(DEDUP_CASES):.0f}%  |  latenza media {sum(lat)/len(lat):.2f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--task", choices=["intent", "dedup", "all"], default="all")
    args = ap.parse_args()
    print(f"[bench] modello: {args.model}  (chat_client / host Gemma)")
    if args.task in ("intent", "all"):
        run_intent(args.model)
    if args.task in ("dedup", "all"):
        run_dedup(args.model)
    print("\n⚠ = ground-truth da confermare con Stefano prima di fidarsi del punteggio su quel caso.")


if __name__ == "__main__":
    main()
