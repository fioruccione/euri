"""
core/reaction.py — Cattura-reazione (Euri Pulse, primo mattone EFFERENTE).

Quando Euri porta a Stefano una sua connessione (un insight sognato) e Stefano
reagisce, la reazione NON finisce in una tabella di voti morta. Diventa una LEZIONE:
un nodo di memoria *normale*, nel dominio dell'insight — quindi RI-SOGNABILE dal
Loop 2b, che pesca i semi via `_get_random_memory_from_domain`. Così si chiude il loop:

    generazione (sogno) → selezione esterna (reazione di Stefano) → lezione → di nuovo sogno

La lezione "potrebbe ridiventare un sogno che avrà ancora bisogno di essere riaffrontato"
(Stefano, 16/06): la ri-sognabilità non è un canale speciale, viene gratis dal fatto che
la lezione è un nodo normale nel dominio giusto.

In termini della primitiva cognitiva (15/06) la reazione è DUE cose insieme:
  (a) un evidence-update sullo stato epistemico dell'insight  → campo `external_reaction`
      (la PRIMA verità esterna che arriva allo strato insight, distinta dal grounding-per-uso);
  (b) un nuovo nodo con provenienza `reacted_to` → l'insight da cui nasce.

Fail-open e additivo: non tocca i loop esistenti. Se la sintesi LLM fallisce, salva
comunque la reazione grezza come lezione (non si perde mai il segnale).
"""

import re
import time

from loguru import logger

from core.ollama_client import chat_client, dream_client
from core.operational_context import load_operational_context
from core.pulse import pulse_emit
import config


def _clean(text: str) -> str:
    """Rimuove il reasoning interno (Gemma 4) — gemello di Brain._clean."""
    if not text:
        return ""
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _insight_brief(insight: dict) -> str:
    """Riduce un insight alla connessione che Euri aveva portato a Stefano."""
    da = insight.get("domain_a", "?")
    db = insight.get("domain_b", "?")
    body = re.sub(r"^.*?succede:\s*", "", insight.get("content", "") or "", flags=re.I)
    return f"[{da} × {db}] {body.strip()}"


def pick_ungrounded_insight(r, topic: str | None = None, embedder=None) -> dict | None:
    """
    Pesca un insight `promoted` ANCORA NON GROUNDATO (senza `external_reaction`): è una
    convinzione che Euri tiene sul mondo di Stefano ma non ha mai confermato — il materiale
    naturale della curiosità. Fail-open: mai solleva.

    - `topic` None → sceglie il più recente (briefing libero, "cosa hai sognato?").
    - `topic` valorizzato → richiesta STRUTTURATA ("hai sognato sul pallet Poseidon?"):
      sceglie il non-groundato più VICINO al tema (semantico se c'è l'embedder caldo,
      altrimenti keyword). Ritorna None se niente è abbastanza vicino → l'handler dirà
      "su quello non ho un sogno nuovo". Resta un trigger dirigibile, non la primitiva.
    """
    from redis.commands.search.query import Query
    try:
        res = r.ft("idx:insights").search(Query("@status:{promoted}").paging(0, 200))
    except Exception as e:
        logger.error(f"pick_ungrounded_insight: {e}")
        return None
    free = []
    for doc in res.docs:
        try:
            o = r.json().get(doc.id)
        except Exception:
            continue
        if o and not o.get("external_reaction"):
            free.append(o)
    if not free:
        return None

    if not topic:
        free.sort(key=lambda o: -_ts(o.get("created_at")))
        return free[0]

    # Richiesta strutturata: semantico se l'embedder è caldo
    if embedder is not None and getattr(embedder, "available", False):
        import numpy as np
        q = embedder.encode(topic, mode="query")
        if q is not None:
            q = np.asarray(q, dtype=np.float32)
            q /= (np.linalg.norm(q) + 1e-9)
            best, best_s = None, -1.0
            for o in free:
                e = o.get("embedding")
                if not e:
                    continue
                v = np.asarray(e, dtype=np.float32)
                v /= (np.linalg.norm(v) + 1e-9)
                s = float(q @ v)
                if s > best_s:
                    best, best_s = o, s
            return best if best_s >= 0.30 else None

    # Fallback keyword (embedder freddo): match sui termini del tema
    words = re.findall(r"\w{4,}", topic.lower())  # parole piene: evita match spuri su "che", "di"
    for o in free:
        c = (o.get("content", "") + o.get("domain_a", "") + o.get("domain_b", "")).lower()
        if any(re.search(rf"\b{re.escape(w)}\b", c) for w in words):
            return o
    return None


def _ts(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# Sorgenti VISSUTE (ground truth di ciò che Stefano ha detto/insegnato) — NON i sogni di
# Euri (insight/reflection: coerenti ma non vissuti). La familiarità si misura QUI.
_GROUNDED_SRC = "user|teach|passive|episode|conversation"


def gather_grounded_evidence(r, topic: str, embedder=None, limit: int = 6) -> list[str]:
    """
    Recupera dalla memoria VISSUTA (user/teach/passive/episode/conversation) gli snippet che
    toccano il tema — l'evidenza groundata da METTERE DAVANTI al modello (RAG sulla
    familiarità). Lista vuota = niente di vissuto su quel tema → caxxata/ignoto (gate GRATIS,
    senza scomodare l'LLM). Semantico se embedder caldo; altrimenti keyword DISTINTIVA
    (IDF-like: i termini-cornice ubiquitari come "cliente"/"materiale" non contano — niente
    stop-list cucita a mano). Non è verità, è evidenza vissuta da far leggere a chi giudica.
    """
    from redis.commands.search.query import Query
    try:
        res = r.ft("idx:memories").search(Query(f"@source:{{{_GROUNDED_SRC}}}").paging(0, 800))
    except Exception as e:
        logger.error(f"gather_grounded_evidence: {e}")
        return []
    docs = []
    for d in res.docs:
        try:
            o = r.json().get(d.id)
        except Exception:
            continue
        if o and o.get("content"):
            docs.append(o)
    if not docs:
        return []

    # Semantico (embedder caldo): snippet concettualmente vicini al tema
    if embedder is not None and getattr(embedder, "available", False):
        import numpy as np
        q = embedder.encode(topic, mode="query")
        if q is not None:
            q = np.asarray(q, dtype=np.float32)
            q /= (np.linalg.norm(q) + 1e-9)
            scored = []
            for o in docs:
                e = o.get("embedding")
                if not e:
                    continue
                v = np.asarray(e, dtype=np.float32)
                v /= (np.linalg.norm(v) + 1e-9)
                s = float(q @ v)
                if s >= 0.30:
                    scored.append((s, o["content"]))
            scored.sort(key=lambda x: -x[0])
            return [c for _, c in scored[:limit]]

    # Keyword distintiva (IDF-like): termini presenti ma NON ubiquitari (entità, non cornice)
    n = len(docs)
    ceiling = max(1, int(0.35 * n))
    words = set(re.findall(r"\w{4,}", topic.lower()))
    distinctive = set()
    for w in words:
        cnt = sum(1 for o in docs if re.search(rf"\b{re.escape(w)}\b", o["content"].lower()))
        if 0 < cnt <= ceiling:
            distinctive.add(w)
    if not distinctive:
        return []
    out = []
    for o in docs:
        cl = o["content"].lower()
        if any(re.search(rf"\b{re.escape(w)}\b", cl) for w in distinctive):
            out.append(o["content"])
            if len(out) >= limit:
                break
    return out


def judge_topic_grounding(topic: str, evidence: list[str]) -> tuple[str, str | None]:
    """
    Mette l'evidenza vissuta DAVANTI a Gemma e le fa giudicare se il tema SPECIFICO è reale
    per Stefano — ragionando SOLO sull'evidenza, non dalla sua testa (= RAG, non il muro del
    plausibility-gate). Risolve il mix che il meccanismo non sa sciogliere: "conosco i clienti
    ma non un Rossi". Ritorna (verdetto, frase-da-dire). Fail-open: FAMILIARE se non parsa,
    per non zittire Euri su un dubbio.
    """
    block = "\n".join(f"- {e[:220]}" for e in evidence) or "(niente)"
    prompt = (
        f"Stefano ti ha chiesto dei tuoi 'sogni' su questo tema: «{topic}».\n\n"
        f"Ecco SOLO ciò che Stefano ti ha davvero detto o insegnato in passato che potrebbe "
        f"riguardarlo — la tua memoria VISSUTA, non la cultura generale:\n{block}\n\n"
        f"Basandoti ESCLUSIVAMENTE su questa evidenza vissuta, il tema «{topic}» fa parte del "
        f"mondo reale di Stefano?\n"
        f"- L'evidenza lo conferma nello specifico → FAMILIARE\n"
        f"- L'evidenza parla del tema generale ma NON dello specifico nominato (es. parla di "
        f"clienti ma di nessun 'Rossi') → PARZIALE\n"
        f"- L'evidenza non lo riguarda davvero → IGNOTO\n\n"
        f"Rispondi in UNA riga così:  VERDETTO | una frase naturale e onesta da dire a Stefano. "
        f"Per PARZIALE/IGNOTO la frase ammette con onestà cosa NON sai e gli chiede se è cosa nuova."
    )
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 800},
            think=False,
        )
        out = _clean(response.message.content or "")
    except Exception as e:
        logger.error(f"judge_topic_grounding: {e}")
        return ("FAMILIARE", None)
    # Parse robusto all'eco del template ("VERDETTO | PARZIALE | frase"): il verdetto si
    # cerca nella parte PRIMA dell'ultimo pipe (così le parole-verdetto dentro la frase non
    # ingannano), la frase è dopo l'ultimo pipe e si ripulisce di un eventuale verdetto in testa.
    if "|" in out:
        head, msg = out.rsplit("|", 1)
        msg = msg.strip()
    else:
        head, msg = out, None
    up = head.upper()
    verdict = "FAMILIARE"
    for v in ("IGNOTO", "PARZIALE", "FAMILIARE"):  # il più cauto vince se ambiguo
        if re.search(rf"\b{v}\b", up):
            verdict = v
            break
    if msg:
        msg = re.sub(r"^\s*(VERDETTO|FAMILIARE|PARZIALE|IGNOTO)\b[\s:|\-]*", "", msg, flags=re.I).strip()
    return (verdict, msg or None)


def formulate_curiosity_question(insight: dict, topic: str | None = None,
                                 evidence: list[str] | None = None) -> str | None:
    """
    Trasforma un insight ANCORA NON GROUNDATO (una connessione che Euri ha sognato sul
    mondo di Stefano ma mai confermato) nella DOMANDA che le verrebbe da fargli per
    scoprire se è vera — registro naturale, curioso, un filo esitante, come un bambino:
    "Babbo, una cosa: ma è vero che...?". NON un prompt di rating, NON "valuta questo
    insight": una domanda viva.

    Gira su Gemma (voce, think=False): è un atto conversazionale, breve e naturale — non
    riflessivo come synthesize_lesson (Qwen). NB: questa è solo la FORMA della curiosità;
    il DRIVE (quando Euri si incuriosisce DA SOLA) è la primitiva ancora da imparare —
    per ora il trigger fa da bootstrap (Stefano 16/06: trigger ≠ primitiva di curiosità).
    """
    # Se Stefano ha chiesto di un TEMA preciso ma i sogni l'hanno astratto via (es. "Poseidon"
    # → "sostituzione legno/plastica", e nessun insight nomina Poseidon), àncora la domanda a
    # QUEL tema usando l'evidenza vissuta che il nome ce l'ha → niente domanda generica.
    anchor = ""
    if topic:
        ev = "\n".join(f"- {e[:180]}" for e in (evidence or [])[:4]) or "(poco)"
        anchor = (
            f"\n\nNB: Stefano ti ha chiesto proprio di «{topic}» — i tuoi sogni l'hanno astratto, "
            f"ma la domanda deve restare RICONOSCIBILMENTE su «{topic}» (nominalo), non generica. "
            f"Ecco cosa sai davvero di «{topic}» dai tuoi ricordi:\n{ev}\n"
            f"Àncora la domanda a questo reale, ma chiedi comunque conferma del PRESUPPOSTO "
            f"della connessione che hai sognato."
        )
    prompt = (
        f"Hai sognato questa connessione sul mondo di Stefano, ma non sai se è davvero vera:\n"
        f"\"{_insight_brief(insight)}\"{anchor}\n\n"
        f"Dentro c'è un PRESUPPOSTO concreto sul mondo reale di Stefano — qualcosa che lui "
        f"potrebbe aver fatto, usato o vissuto — che hai dato per scontato senza confermarlo "
        f"(esempio: che costruisse antenne con stampi di alluminio). Trova QUEL presupposto "
        f"fattuale e chiedigli se è vero — la domanda va al FATTO, non alla teoria che ci hai "
        f"costruito sopra. Curiosa, naturale, un filo esitante, come quando una cosa ti "
        f"incuriosisce: \"Stefano, una cosa: ma è vero che...?\". Se NON c'è un fatto concreto "
        f"da verificare, chiedi invece se è davvero un suo pensiero o se lo stai leggendo tu "
        f"di tua testa. Una frase, parlando a Stefano."
    )
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.6, "num_predict": 800},
            think=False,
        )
        return _clean(response.message.content or "") or None
    except Exception as e:
        logger.error(f"Errore formulate_curiosity_question: {e}")
        return None


def synthesize_lesson(insight: dict, reaction_text: str) -> str | None:
    """
    Euri sintetizza la LEZIONE da (sua connessione + reazione di Stefano).
    È QUI che si vede se il loop è meccanico o naturale: deve tenere il filo
    (citare la connessione e cosa ha detto Stefano), essere onesta se ha sbagliato,
    e nominare il punto che potrebbe dover riaffrontare. Ritorna None se l'LLM tace.

    Gira su GEMMA (già in VRAM per la voce), NON su Qwen: la sintesi avviene nel mezzo di
    una sessione vocale viva, e caricare Qwen 35B lì faceva OOM (Qwen 20GB + Gemma 18GB +
    Whisper > 2×16GB — Stefano 16/06). Concettualmente la sintesi è parente del sogno (Qwen
    sarebbe più profondo), ma il Qwen va DIFFERITO all'idle (disaccoppia cattura/consumo),
    non eseguito durante la voce. Antepone EURI_CONTEXT. (Prompt = lever da lavorare.)
    """
    prompt = (
        f"Avevi portato a Stefano questa tua connessione — un insight che hai sognato:\n"
        f"\"{_insight_brief(insight)}\"\n\n"
        f"Stefano ti ha risposto:\n\"{reaction_text.strip()}\"\n\n"
        f"PRIMA capisci cosa ha toccato la sua risposta, alla base della connessione:\n"
        f"– ha confermato che poggia su qualcosa di VERO? allora riconoscilo prima di andare "
        f"oltre — il sogno aveva un'ancora reale — e poi di' cosa resta comunque aperto.\n"
        f"– l'ha smentita perché ne avevi INVENTATO la base? allora è un errore di FONDAMENTO "
        f"(hai costruito su un fatto che non esisteva), NON di stile o di come l'hai detta. "
        f"Chiamalo col suo nome, senza addolcirlo.\n"
        f"– ne ha confermato una parte e lasciato aperta un'altra? tieni separate le due.\n\n"
        f"POI scrivi in prima persona la LEZIONE che ne ricavi: cosa cambia adesso nella tua "
        f"comprensione e cosa potresti dover riaffrontare. NON un ringraziamento, NON \"ok ho "
        f"capito\". Tieni il filo (cita la connessione e cosa ti ha detto Stefano). Max 3 frasi."
    )
    messages = [{"role": "user", "content": prompt}]
    op_ctx = load_operational_context()
    if op_ctx:
        messages = [{"role": "system", "content": op_ctx}, *messages]
    try:
        response = chat_client.chat(  # Gemma (già caricato), NON Qwen → niente OOM in sessione viva
            model=config.OLLAMA_MODEL,
            messages=messages,
            # think=False: l'output diretto è affidabile (think=True a volte consuma il budget
            # nel reasoning e torna VUOTO → None → fallback grezzo, visto dal vivo) ed è veloce
            # (~8s vs ~40s) → meno contesa con la voce in background.
            options={"temperature": 0.4, "num_predict": 1500},
            think=False,
        )
        return _clean(response.message.content or "") or None
    except Exception as e:
        logger.error(f"Errore synthesize_lesson: {e}")
        return None


def capture_reaction(memory, insight: dict, reaction_text: str, *, emit: bool = True) -> dict:
    """
    Cattura la reazione di Stefano a un insight e la trasforma in lezione ri-sognabile.

    Ritorna un dict di esito (mai solleva): {lesson_id, lesson, persisted, ...}.
    """
    r = memory.r
    insight_id = insight.get("id", "")
    domain = insight.get("domain_a") or insight.get("domain") or "generale"

    # 1) Euri sintetizza la lezione (fail-open → reazione grezza se l'LLM tace)
    lesson = synthesize_lesson(insight, reaction_text)
    fallback = lesson is None
    if fallback:
        lesson = f"Su «{_insight_brief(insight)}» Stefano ha reagito: «{reaction_text.strip()}»."

    out = {"insight_id": insight_id, "lesson": lesson, "persisted": False, "lesson_id": None}

    # 2) salva la lezione come nodo NORMALE (riusa save_memory: embedding, indice, TTL, dominio auto)
    try:
        lesson_id = memory.save_memory(lesson, category="lezione", source="reaction")
    except Exception as e:
        logger.error(f"capture_reaction: save lezione fallito: {e}")
        return out
    if not lesson_id:
        return out  # probabile duplicato — niente di rotto, solo nulla di nuovo
    out["lesson_id"] = lesson_id

    key = f"euri:memory:{lesson_id}"
    try:
        # 3) forza il dominio = dominio dell'insight → la lezione rientra nel POOL-SOGNO giusto
        #    (Loop 2b pesca per @domain); + provenienza e reazione grezza per audit.
        r.json().set(key, "$.domain", domain)
        r.json().set(key, "$.reacted_to", insight_id)
        r.json().set(key, "$.reaction_raw", reaction_text.strip())
        db = insight.get("domain_b")
        if db:
            r.json().set(key, "$.tags", [db])  # pescabile anche dall'altro polo
        # 4) evidence-update sull'insight: la prima verità ESTERNA allo strato insight
        if insight_id:
            r.json().set(f"euri:insight:{insight_id}", "$.external_reaction", {
                "reaction": reaction_text.strip(),
                "lesson_id": lesson_id,
                "ts": time.time(),
            })
        out["persisted"] = True
    except Exception as e:
        logger.error(f"capture_reaction: arricchimento nodo fallito (lezione salvata): {e}")

    # 5) evento sul polso: extero, è il mondo (Stefano) che risponde all'iniziativa di Euri
    if emit:
        pulse_emit(
            r, "reaction", "extero", "rated",
            payload={"insight": insight_id, "lesson": lesson_id, "domain": domain},
            salience=0.7,  # ipotesi grezza: la verità esterna spicca più dell'housekeeping interno
        )

    return out
