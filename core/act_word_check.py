"""
Verifica atto-parola (N2) — P-GT applicato al linguaggio.

Un LLM non distingue dall'interno tra aver compiuto un'azione e produrre il
racconto di averla compiuta (confabulazione di agency: 11/06 17:18, "Ho aggiornato
la nota" con intent CHAT e nessun salvataggio). Non curabile dall'interno: si
confronta il CLAIM con la GROUND TRUTH del turno (gli handler eseguiti).

Check BINARIO (decisione di Stefano, 12/06): scatta solo se la risposta afferma
un'azione in prima persona compiuta MA in quel turno nessuna azione è stata
eseguita. Non distingue il verbo (update≠create): lascia passare il claim impreciso
su un'azione realmente avvenuta (17:43) — è il bias conservativo, non erodere
fiducia negando azioni vere.

Il discriminante è il participio passato in prima persona ("ho salvato"): offerte
("vuoi che salvi?"), condizionali ("lo salvo se…") e descrizioni ("di solito salvo")
NON usano questa forma, quindi non scattano. Lessico CHIUSO e generico (azioni, non
modi di dire di dominio).
"""
import re

# Verbi-azione che Euri può affermare di aver compiuto (participio passato).
_VERBS = r"(?:salvat|aggiornat|memorizzat|segnat|annotat|registrat|creat|elimina|cancellat|rimoss|complet)\w*"

# Claim = prima persona passata: "ho salvato", "l'ho aggiornato", "li ho memorizzati".
# Ammette avverbi tra ausiliare e participio ("ho appena/già/anche salvato").
_ADV = r"(?:gi[àa]|appena|anche|poi|subito|pure|ora|adesso|comunque|quindi|già)"
_CLAIM_RE = re.compile(
    rf"\b(?:ho|l['’ ]ho|li ho|le ho|gli ho)\s+(?:{_ADV}\s+){{0,2}}{_VERBS}\b",
    re.IGNORECASE,
)

# Prefisso di conferma a partecipio: "Salvato:", "Fatto, memorizzato.", "Aggiornato:"
_CLAIM_PREFIX_RE = re.compile(rf"(?:^|[.,]\s+)(?:fatto[,.]?\s+)?{_VERBS}\b\s*[:.]", re.IGNORECASE)

# Negazione esplicita: "non ho salvato" NON è un claim d'azione compiuta.
_NEG_CLAIM_RE = re.compile(rf"\bnon\s+(?:ho|l['’ ]ho|li ho|le ho|gli ho)\s+{_VERBS}\b", re.IGNORECASE)

# Distanza temporale: "l'ho salvata IERI" è il racconto di un'azione passata vera,
# non un claim sul turno corrente → non scattare (eviterebbe di negare un'azione
# reale di un altro turno: "hai salvato la nota di ieri?" → "sì, l'ho salvata ieri").
# NB: "appena", "adesso", "ora" NON sono distanza → un claim su azione di questo
# turno deve ancora scattare se nessun handler ha agito.
_PAST_DISTANCE_RE = re.compile(
    r"\b(ieri|prima|stamattina|stasera|poco fa|tempo fa|giorni fa|"
    r"l['’ ]altr[oa]\s+(?:volta|giorno|settimana)|(?:settimana|mese|anno)\s+scors[oa]|"
    r"in passato|gi[àa] allora|ti avevo|già)\b",
    re.IGNORECASE,
)


def claims_completed_action(text: str) -> bool:
    """True se il testo afferma un'azione COMPIUTA in prima persona NEL turno corrente
    (negazioni e azioni passate-distanti escluse)."""
    if not text:
        return False
    if not (_CLAIM_RE.search(text) or _CLAIM_PREFIX_RE.search(text)):
        return False
    # Rimuovi le forme negate ("non ho salvato niente") e ricontrolla: resta un claim?
    stripped = _NEG_CLAIM_RE.sub("", text)
    if not (_CLAIM_RE.search(stripped) or _CLAIM_PREFIX_RE.search(stripped)):
        return False
    # Azione passata-distante → racconto, non claim sul turno corrente.
    if _PAST_DISTANCE_RE.search(text):
        return False
    return True


def needs_honest_correction(reply: str, turn_actions: set) -> bool:
    """
    True (mismatch) se la risposta afferma un'azione compiuta ma nel turno NON è
    stata eseguita alcuna azione. `turn_actions` = azioni reali del turno (vuoto
    se nessun handler ha agito). Check binario: qualsiasi azione "copre" il claim.
    """
    return claims_completed_action(reply) and not turn_actions


def honest_correction() -> str:
    """Riga onesta da pronunciare al posto del claim falso."""
    return ("Aspetta — in realtà non ho eseguito quell'azione in questo turno. "
            "Vuoi che la faccia adesso?")
