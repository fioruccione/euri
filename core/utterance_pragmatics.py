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

# Marcatori FORTI di non-comprensione: bastano da soli, anche senza punto interrogativo.
_STRONG = re.compile(
    r"\b(non\s+ho\s+(capito|compreso)|non\s+(ho\s+)?cap\w*\s+(cosa|di\s+cosa|quale)|"
    r"non\s+so\s+di\s+(cosa|che)|a\s+cosa\s+(ti\s+)?riferisc\w*|"
    r"di\s+(che|cosa)\s+(stai\s+)?parl\w*)\b",
    re.IGNORECASE,
)

# Marcatori "QUALE/cosa intendi": valgono come chiarimento solo se il turno è una domanda.
_WHICH = re.compile(
    r"\b(qual[ei]|che\s+insight|quale\s+insight|cosa\s+intend\w*|"
    r"che\s+cosa\s+intend\w*|in\s+che\s+senso|puoi\s+(ri)?spiegar\w*)\b",
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
_CREATED_REF = re.compile(
    r"\b((la\s+)?bozza|"
    r"(il\s+|quel\s+)?(file|documento)\s+(appena\s+)?(creat\w+|salvat\w+|fatt\w+)|"
    r"(quello|ci[oò])\s+che\s+(hai|abbiamo)\s+(appena\s+)?(creat\w+|fatt\w+|salvat\w+)|"
    r"appena\s+(creat\w+|salvat\w+|fatt\w+))\b", re.IGNORECASE)


def is_open_created_file_request(text: str) -> bool:
    """
    True se il turno chiede di APRIRE l'ultimo file creato da Euri. Conservativo
    per non rubare 'apri il documento' generico a read_document.
    """
    if not text:
        return False
    t = text.strip()
    if _OPEN_CLITIC.search(t):
        return True
    return bool(_OPEN_VERB.search(t) and _CREATED_REF.search(t))


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
