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
