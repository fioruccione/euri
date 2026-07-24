"""
Pragmatica dell'enunciato — distingue il TIPO di un turno utente (risposta vs
domanda/chiarimento), un problema che ricorre in più punti (reazione a un
insight, correzione). Per ora euristica GENERICA e leggera; è il seme del
classificatore pragmatico locale previsto in pipeline_model_routing: quando le
euristiche toccano il soffitto, qui entra un modellino al posto delle regex.

Niente modi di dire di settore (no overfit a Lucy Plast): solo struttura
pragmatica generale.
"""
import re

from loguru import logger

# Marcatori FORTI di non-comprensione: bastano da soli, anche senza punto
# interrogativo (la STT spesso lo perde). Volutamente larghi: meglio ri-chiedere
# di troppo che consumare un chiarimento come verdetto (il fallimento osservato).
_STRONG = re.compile(
    r"\b(non\s+(ho\s+)?(capi\w*|compren\w*|compres\w*)"   # non capisco / non ho capito / non comprendo
    r"|non\s+(mi\s+)?[èe]\s+chiar\w*"                      # non mi è chiaro
    r"|cosa\s+intend\w*|che\s+(cosa\s+)?intend\w*"         # cosa/che intendi
    r"|a\s+cosa\s+(ti\s+)?riferisc\w*"                     # a cosa ti riferisci
    r"|di\s+(che|cosa)\s+(stai\s+)?parl\w*"                # di cosa parli
    r"|non\s+so\s+di\s+(cosa|che))\b",
    re.IGNORECASE,
)

# Marcatori "QUALE": valgono come chiarimento solo se il turno è una domanda.
_WHICH = re.compile(
    r"\b(qual[ei]|che\s+insight|quale\s+insight|in\s+che\s+senso|"
    r"puoi\s+(ri)?spiegar\w*)\b",
    re.IGNORECASE,
)


# ── "apri il file che hai appena creato" ──
# Verbo di apertura + riferimento all'artefatto appena prodotto. Conservativo:
# "apri il documento" generico (senza 'creato'/'bozza') NON scatta → resta
# read_document sulla cartella di input. Solo il riferimento esplicito al
# creato, o il clitico ("aprilo/aprila"), apre l'ultimo artefatto.
_OPEN_VERB = re.compile(
    r"\b(apri\w*|mostra\w*|visualizz\w*|fammi\s+(veder\w*|apri\w*))\b", re.IGNORECASE)
_OPEN_CLITIC = re.compile(r"\b(apri|mostra)(lo|la|melo|mela|telo|tela)\b", re.IGNORECASE)
# Target apribile. NB: il verbo è di APERTURA (apri/mostra/visualizza), non di
# lettura — "leggi il documento" resta read_document. La disambiguazione vera
# (vs un doc in input) è la RECENCY dell'ultimo file creato, gestita nel dispatch.
_OPEN_TARGET = re.compile(
    r"\b(bozza|document\w+|file|allegat\w+|lettera|present\w+|mail)\b", re.IGNORECASE)


def is_open_created_file_request(text: str) -> bool:
    """
    True se il turno chiede di APRIRE un documento/file (verbo di apertura, non
    'leggi'). Il dispatch applica questo solo quando esiste un file creato di
    recente, così non ruba 'apri il documento' a read_document fuori contesto.
    """
    if not text:
        return False
    t = text.strip()
    if _OPEN_CLITIC.search(t):
        return True
    return bool(_OPEN_VERB.search(t) and _OPEN_TARGET.search(t))


def is_clarification_request(text: str) -> bool:
    """
    True se il turno è (probabilmente) una RICHIESTA DI CHIARIMENTO e non una
    risposta. Conservativo per non scambiare risposte reali per domande:
      - marcatore FORTE di non-comprensione → True (anche senza '?');
      - marcatore QUALE/cosa-intendi → True solo se il turno è una domanda ('?').
    Euristica fail-safe: se sbaglia, al massimo Euri ri-chiede invece di catturare.
    """
    if not text:
        return False
    t = text.strip()
    if _STRONG.search(t):
        return True
    if t.endswith("?") and _WHICH.search(t):
        return True
    return False


# ── Classificatore pragmatico via Gemma (non embedding) ──
# I tentativi falliti col classificatore intent erano con gli EMBEDDING (e5-large
# anisotropo + Welford, selection bias): servivano a EVITARE Gemma per latenza.
# Gemma come classificatore funziona già (è il maestro dell'harvest). Per decisioni
# RARE (il chiarimento scatta solo dopo una domanda di curiosità) la latenza si paga
# pochissime volte → usiamo Gemma direttamente. Il regex resta come fast-path
# (chiarimento ovvio → niente latenza) e come fallback se Gemma non risponde.
def classify_reply_type(question: str, reply: str, *, chat=None, model: str = None) -> str:
    """
    Ritorna ANSWER, CLARIFICATION o OFF_TOPIC: la replica dell'utente è una risposta
    alla domanda, una richiesta di chiarimento o la continuazione di un altro filo?
    Fast-path: chiarimento ovvio via regex (zero latenza). Poi Gemma. Fallback su
    regex se Gemma è giù.
    """
    if is_clarification_request(reply):
        return "CLARIFICATION"
    try:
        if chat is None:
            from core.ollama_client import chat_client
            chat = chat_client
        import config
        prompt = (
            "Euri (un assistente) ha fatto una domanda all'utente per validare una sua idea.\n"
            f'DOMANDA DI EURI: "{question or "(una domanda su una sua intuizione)"}"\n'
            f'REPLICA DELL\'UTENTE: "{reply}"\n\n'
            "Classifica la replica:\n"
            "- RISPOSTA: affronta davvero la domanda, anche con conferma, smentita o dubbio.\n"
            "- CHIARIMENTO: l'utente non ha capito e chiede di rispiegare.\n"
            "- FUORI_TEMA: continua un altro discorso o non fornisce evidenza sulla domanda.\n"
            "Una frase non diventa RISPOSTA solo perché contiene parole vagamente analoghe.\n"
            "Rispondi con UNA sola parola: RISPOSTA, CHIARIMENTO oppure FUORI_TEMA."
        )
        r = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 8},
            think=False,
        )
        out = (r.message.content or "").strip().upper()
        if "CHIARIMENT" in out:
            return "CLARIFICATION"
        if "FUORI" in out or "OFF_TOPIC" in out:
            return "OFF_TOPIC"
        if "RISPOST" in out:
            return "ANSWER"
        logger.debug(f"classify_reply_type: output Gemma ambiguo '{out}' → OFF_TOPIC")
    except Exception as e:
        logger.warning(f"classify_reply_type: Gemma fallito ({e}) → OFF_TOPIC")
    return "OFF_TOPIC"


def classify_memory_verification_reply(
    question: str,
    claim: str,
    reply: str,
    *,
    chat=None,
    model: str = None,
) -> str:
    """Classifica una risposta alla verifica di una memoria passiva.

    Ritorna CONFIRM, REFUTE, CORRECT, CLARIFICATION oppure OFF_TOPIC. La decisione
    resta semantica: nessun vocabolario di settore o formula obbligatoria.
    """
    if is_clarification_request(reply):
        return "CLARIFICATION"
    try:
        if chat is None:
            from core.ollama_client import chat_client
            chat = chat_client
        import config
        prompt = (
            "Euri ha chiesto all'utente di verificare una memoria passiva.\n"
            f'MEMORIA: "{claim}"\n'
            f'DOMANDA: "{question}"\n'
            f'RISPOSTA UTENTE: "{reply}"\n\n'
            "Classifica il significato della risposta:\n"
            "- CONFERMA: conferma la memoria, anche aggiungendo dettagli coerenti\n"
            "- SMENTITA: dice che la memoria è falsa o non applicabile\n"
            "- CORREZIONE: conferma solo in parte e modifica un dettaglio sostanziale\n"
            "- CHIARIMENTO: chiede di rispiegare la domanda\n"
            "- FUORI_TEMA: non risponde alla verifica\n"
            "Rispondi con UNA sola parola: CONFERMA, SMENTITA, CORREZIONE, "
            "CHIARIMENTO oppure FUORI_TEMA."
        )
        result = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 8},
            think=False,
        )
        out = (result.message.content or "").strip().upper()
        if "CHIARIMENT" in out:
            return "CLARIFICATION"
        if "FUORI" in out or "OFF_TOPIC" in out:
            return "OFF_TOPIC"
        if "CORREZ" in out:
            return "CORRECT"
        if "SMENT" in out or "REFUT" in out:
            return "REFUTE"
        if "CONFER" in out:
            return "CONFIRM"
        logger.debug(f"classify_memory_verification_reply: output ambiguo '{out}'")
    except Exception as e:
        logger.warning(f"classify_memory_verification_reply: Gemma fallito ({e})")
    return "OFF_TOPIC"


# ── Rilettura → cura (READ_BACK pending) ──────────────────────────────────────
# Cosa vuole l'utente dopo che Euri gli ha riletto una memoria? I prefissi-ack a
# regex non reggono la varietà del parlato (caso live 14/07: "Tutto perfetto, hai
# capito benissimo" finiva nel ramo correzione e creava un nodo spurio). Regex solo
# sui casi ovvi; Gemma per il resto; fallback CONSERVATIVO = OK: nel dubbio la
# memoria NON si tocca (l'utente può ridirlo esplicito), mai il contrario.
_READBACK_ACK_RE = re.compile(
    r"^(ok|va bene|giusto|perfetto|esatto|niente|nulla|corretta|lascia|annulla|basta"
    r"|tutto\s+(bene|ok|perfetto|giusto|corretto)|bravo|brava|ottimo|benissimo)\b",
    re.IGNORECASE,
)
_READBACK_ADD_RE = re.compile(r"\b(aggiungi|aggiungici|integra|completa\s+con)\b", re.IGNORECASE)


def classify_readback_reply(reply: str, *, chat=None, model: str = None) -> str:
    """Ritorna 'OK', 'CORREZIONE' o 'AGGIUNTA'. Capisce, non matcha parole."""
    low = (reply or "").strip().lower()
    if not low or len(low) < 12 or _READBACK_ACK_RE.match(low):
        return "OK"
    if _READBACK_ADD_RE.search(low):
        return "AGGIUNTA"
    try:
        if chat is None:
            from core.ollama_client import chat_client
            chat = chat_client
        import config
        prompt = (
            "Euri (un'assistente) ha appena riletto all'utente una memoria che aveva "
            "salvato, chiedendo se c'è da correggere o aggiungere qualcosa.\n"
            f'REPLICA DELL\'UTENTE: "{reply}"\n\n'
            "Classifica la replica:\n"
            "- OK: approva, va bene così, complimenti, nessuna modifica richiesta\n"
            "- CORREZIONE: dice che qualcosa nella memoria è sbagliato/impreciso e indica come dev'essere\n"
            "- AGGIUNTA: vuole integrare informazioni nuove nella memoria\n"
            "Rispondi con UNA sola parola: OK oppure CORREZIONE oppure AGGIUNTA."
        )
        r = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 8},
            think=False,
        )
        out = (r.message.content or "").strip().upper()
        if "CORREZ" in out:
            return "CORREZIONE"
        if "AGGIUNT" in out:
            return "AGGIUNTA"
        return "OK"
    except Exception as e:
        logger.warning(f"classify_readback_reply: Gemma fallito ({e}) → OK conservativo")
        return "OK"
