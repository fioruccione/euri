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
