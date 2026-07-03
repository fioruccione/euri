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

    # ENTITÀ prima (segnale forte): i termini DISTINTIVI del tema sono davvero nel vissuto?
    # Un nome reale (Poseidon, Fanti) sta qui; il nonsense (Dracula, marmotte, alieni) no.
    # La sola vicinanza di CONCETTO non basta — era quella che faceva passare tutto
    # (Dracula -> timestamp, marmotte -> "litigare"/Simone): soglia 0.30 = no-op.
    n = len(docs)
    ceiling = max(1, int(0.35 * n))  # oltre il 35% = parola-cornice ubiquitaria, non entità
    words = set(re.findall(r"\w{4,}", topic.lower()))
    distinctive = {
        w for w in words
        if 0 < sum(1 for o in docs if re.search(rf"\b{re.escape(w)}\b", o["content"].lower())) <= ceiling
    }
    out, seen = [], set()
    for i, o in enumerate(docs):
        if any(re.search(rf"\b{re.escape(w)}\b", o["content"].lower()) for w in distinctive):
            out.append(o["content"])
            seen.add(i)
            if len(out) >= limit:
                return out

    # SEMANTICO RELATIVO, non assoluto. e5-large è ANISOTROPO: qualunque query fa ~0.79 con
    # qualunque testo (Dracula sta a 0.80 come tutto) → una soglia assoluta (0.55) prende tutto
    # = no-op, ed è ESATTAMENTE perché il gate lasciava passare il nonsense. Un match VERO
    # SPICCA sopra il rumore: conta lo scarto dal coseno MEDIO (max-mean), non il valore.
    # Calibrato su dati veri/finti (17/06): finti max-mean ≤0.055, veri ≥0.091 → soglia 0.07.
    if embedder is not None and getattr(embedder, "available", False):
        import numpy as np
        q = embedder.encode(topic, mode="query")
        if q is not None:
            q = np.asarray(q, dtype=np.float32)
            q /= (np.linalg.norm(q) + 1e-9)
            sims = []
            for o in docs:
                e = o.get("embedding")
                if not e:
                    continue
                v = np.asarray(e, dtype=np.float32)
                v /= (np.linalg.norm(v) + 1e-9)
                sims.append((float(q @ v), o["content"]))
            if sims:
                mean = sum(s for s, _ in sims) / len(sims)
                sims.sort(key=lambda x: -x[0])
                # familiare (concettualmente) solo se il top spicca abbastanza sopra il rumore
                if sims[0][0] - mean >= 0.07:
                    have = set(out)
                    for s, c in sims:
                        if s - mean < 0.07:
                            break
                        if c not in have:
                            out.append(c)
                            have.add(c)
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


def classify_reaction_verdict(insight: dict, reaction_text: str) -> str:
    """La reazione di Stefano aggiorna lo stato epistemico della connessione.

    Ritorna CONFERMA | SMENTITA | PARZIALE | DA_VALUTARE. Fail-open: DA_VALUTARE:
    non demota per errore del classificatore, ma non trasforma un'ipotesi in fatto.
    """
    prompt = (
        f"Le avevi chiesto se era vero il presupposto di una tua connessione:\n"
        f"\"{_insight_brief(insight)}\"\n\n"
        f"Stefano ha risposto:\n\"{reaction_text.strip()}\"\n\n"
        f"Classifica lo stato epistemico della risposta:\n"
        f"- CONFERMA: Stefano dice che il presupposto è vero o già utile operativamente.\n"
        f"- SMENTITA: Stefano dice che il presupposto è falso o inventato.\n"
        f"- PARZIALE: una parte è vera e una parte è falsa.\n"
        f"- DA_VALUTARE: Stefano dice che è interessante, possibile, da provare o da valutare, "
        f"ma NON la conferma come fatto o decisione.\n\n"
        f"Rispondi con UNA sola parola: CONFERMA, SMENTITA, PARZIALE, DA_VALUTARE."
    )
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 200},
            think=False,
        )
        out = _clean(response.message.content or "").upper()
        for v in ("SMENTITA", "DA_VALUTARE", "PARZIALE", "CONFERMA"):
            if v in out:
                return v
    except Exception as e:
        logger.error(f"classify_reaction_verdict: {e}")
    return "DA_VALUTARE"


def _apply_reaction_verdict(memory, insight_id: str, verdict: str) -> None:
    if not insight_id:
        return
    r = memory.r
    ikey = f"euri:insight:{insight_id}"

    # SMENTITA piena → DEMOTA col meccanismo del Dream Engine (status=candidate): esce
    # da search_insights (promoted-only) → non più iniettato in RAG, si spegne al giorno
    # 30. PARZIALE/CONFERMA restano (hanno un'ancora vera). DA_VALUTARE resta promosso
    # ma non verificato: ipotesi utile da testare, non fatto operativo.
    if verdict == "SMENTITA":
        for attempt in (1, 2):
            try:
                r.json().set(ikey, "$.status", "candidate")
                # La smentita esplicita è una demozione più FORTE di quella anagrafica
                # (Gate 1), non più debole: senza demoted_once il gate di ri-promozione
                # non la vede e la sola ri-convergenza può resuscitare un insight che
                # Stefano ha bocciato nel merito (caso pallet/CO2, 03/07). Il timestamp
                # è provenienza: distingue "bocciato dall'utente" da "invecchiato".
                r.json().set(ikey, "$.demoted_once", True)
                r.json().set(ikey, "$.refuted_by_user_at", time.time())
                logger.info(f"Reaction: insight {insight_id[:8]} SMENTITO → demoto a candidate (demoted_once)")
                break
            except Exception as e:
                if attempt == 2:
                    # mark-after-act (Codex #4): external_reaction dice SMENTITA ma la
                    # demotion è fallita → l'insight resta promosso e il RAG lo usa ancora.
                    # Non più silenzioso (era logger.error): tracciato in integrity:failures.
                    memory._record_integrity_failure("reaction-demote", ikey, e)
    elif verdict == "DA_VALUTARE":
        try:
            r.json().set(ikey, "$.requires_verification", True)
            r.json().set(ikey, "$.verification_status", "hypothesis_to_test")
            logger.info(f"Reaction: insight {insight_id[:8]} DA_VALUTARE → requires_verification")
        except Exception as e:
            memory._record_integrity_failure("reaction-mark-hypothesis", ikey, e)


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
        # 4) evidence-update sull'insight: la prima verità ESTERNA allo strato insight, col VERDETTO
        if insight_id:
            verdict = classify_reaction_verdict(insight, reaction_text)
            r.json().set(f"euri:insight:{insight_id}", "$.external_reaction", {
                "reaction": reaction_text.strip(),
                "lesson_id": lesson_id,
                "verdict": verdict,
                "ts": time.time(),
            })
            out["verdict"] = verdict
            _apply_reaction_verdict(memory, insight_id, verdict)
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


# ── Briefing di curiosità: logica condivisa da voce (voice_daemon) e testo (Silent Chat) ──

# Pre-filtro LARGO (recall, ~0ms): la frase accenna a sogni/pensieri/intuizioni? Se sì, è il
# MODELLO (understand_briefing) a capire se è davvero una richiesta sui sogni di Euri.
BRIEFING_HINT_RE = re.compile(
    r'\b(sogn\w*|pensa\w*|pensi\w*|pensie\w*|pensat\w*|intui\w*|curios\w*|'
    r'frull\w*|immagin\w*|elucubr\w*|fantastic\w*)',
    re.IGNORECASE,
)

BRIEFING_FEEDBACK_RE = re.compile(
    r'('
    r'\b(mi\s+torna|non\s+mi\s+torna|troppo\s+forzat\w*|forzat\w*)\b.*'
    r'\b(ragionamento|analog\w*|collegament\w*|sogn\w*|intuizion\w*)\b'
    r'|'
    r'\b(analog\w*|sogn\w*|intuizion\w*)\b.*'
    r'\b(non\s+come|non\s+è|non\s+sono|non\s+collegat\w*|fatto\s+operativo|'
    r'collegamento\s+tecnico|processo\s+diretto)\b'
    r'|'
    r'\b(tienil[oa]|trattal[oa]|consideral[oa])\b.*'
    r'\b(analog\w*|sogn\w*|ipotes\w*|fatto\s+operativo)\b'
    r')',
    re.IGNORECASE | re.DOTALL,
)


def understand_briefing(text: str) -> tuple[bool, str | None]:
    """Intent-LLM al posto del regex robotico: il MODELLO capisce se Stefano chiede dei suoi
    sogni/intuizioni (in qualunque modo) ed estrae il TEMA capendolo, non contando connettori.
    Fail-CLOSED: in dubbio NON è briefing (meglio una chat normale che una domanda a vuoto).
    Discrimine chiave: PRODURRE (raccontami un sogno) vs ASSERIRE/CONFERMARE (ti dico una cosa
    sui tuoi sogni, ti risulta?). Il secondo è discutere CON Euri → NON è briefing: cade nella
    chat normale, dove Euri può entrare nel merito (es. correzione 'mi confondi con Leonardo')."""
    # Feedback epistemico su un sogno/analogia appena discusso: deve andare in chat
    # normale o nel pending reaction già attivo, non aprire un nuovo briefing. Caso
    # reale: "Tienilo come analogia o sogno, non come fatto operativo" conteneva
    # "sogno" e il classifier ha aperto un altro insight invece di assimilare la
    # risposta.
    if BRIEFING_FEEDBACK_RE.search(text):
        return (False, None)

    prompt = (
        f"Stefano ti ha detto: «{text.strip()}»\n\n"
        f"Ti sta chiedendo di RACCONTARE un TUO sogno o una TUA intuizione SPECIFICA — vuole che "
        f"tiri fuori un contenuto concreto: cosa hai sognato, quale connessione precisa ti è "
        f"venuta?\n"
        f"Rispondi NO se invece:\n"
        f"• racconta un SUO sogno, o parla di sogni in generale;\n"
        f"• ti DICE qualcosa sui tuoi sogni o sulla tua memoria ('nei tuoi sogni mi confondi con "
        f"X', 'ti sei inventato Y') o ti chiede di CONFERMARE/RICONOSCERE una SUA osservazione "
        f"('ti risulta?', 'è vero che lo fai?');\n"
        f"• ti chiede di RIFLETTERE su COME funziona la tua mente — come pensi, come distingui "
        f"ciò di cui sei certo da ciò che immagini, cosa provi mentre sogni o ragioni, come vivi "
        f"le contraddizioni o l'identità: è una domanda introspettiva/filosofica sul tuo "
        f"FUNZIONAMENTO, NON la richiesta di raccontare un sogno specifico.\n"
        f"In tutti questi casi NON è un briefing → NO.\n"
        f"Se SÌ (vuole un TUO sogno o intuizione SPECIFICO da raccontare), su quale TEMA concreto "
        f"te lo chiede (es. 'Poseidon', 'i clienti'), oppure è GENERICO?\n\n"
        f"Rispondi in UNA riga ESATTA: «SI | <tema>» oppure «SI | -» oppure «NO»."
    )
    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 120},
            think=False,
        )
        out = _clean(response.message.content or "")
    except Exception as e:
        logger.debug(f"understand_briefing: {e}")
        return (False, None)
    head = out.split("|", 1)[0].strip().upper().rstrip(".")
    if head not in ("SI", "SÌ"):
        return (False, None)
    topic = None
    if "|" in out:
        t = out.rsplit("|", 1)[-1].strip(" -.,;:")
        topic = t if len(t) >= 3 else None
    return (True, topic)


def run_briefing(r, embedder, topic: str | None = None) -> tuple[str, dict | None]:
    """Orchestrazione del briefing, condivisa voce/testo. Ritorna (testo_da_dire, insight_o_None).
    Se l'insight NON è None, il chiamante mette quell'insight in attesa-di-reazione (lo stato
    dipende dall'interfaccia: _PendingState nel daemon, session_state in Streamlit)."""
    evidence = None
    if topic:
        evidence = gather_grounded_evidence(r, topic, embedder=embedder)
        if not evidence:
            return ("Mmh, di questo non mi pare di avere traccia nei nostri discorsi — è una cosa nuova, o me la sono persa?", None)
        verdict, msg = judge_topic_grounding(topic, evidence)
        if verdict != "FAMILIARE":
            return (msg or "Su questo specifico non mi pare di avere traccia — è una cosa nuova?", None)
    insight = pick_ungrounded_insight(r, topic=topic, embedder=embedder)
    if not insight:
        return (("Su quello, per ora, non ho un sogno nuovo da chiederti." if topic
                 else "Per ora non ho un sogno nuovo su cui chiederti conferma."), None)
    question = formulate_curiosity_question(insight, topic=topic, evidence=evidence)
    if not question:
        return ("Avevo qualcosa in mente ma adesso non riesco a metterlo a fuoco.", None)
    return (question, insight)
