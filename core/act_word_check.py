"""
Verifica atto-parola (N2) — P-GT applicato al linguaggio.

Un LLM non distingue dall'interno tra aver compiuto un'azione e produrre il
racconto di averla compiuta (confabulazione di agency: 11/06 17:18, "Ho aggiornato
la nota" con intent CHAT e nessun salvataggio). Non curabile dall'interno: si
confronta il CLAIM con la GROUND TRUTH del turno (gli handler eseguiti).

Check BINARIO (decisione di Stefano, 12/06): scatta se la risposta afferma
un'azione in prima persona compiuta, oppure promette di iniziarla autonomamente
subito, MA in quel turno nessuna azione è stata eseguita. Non distingue il verbo
(update≠create): lascia passare il claim impreciso su un'azione realmente avvenuta
(17:43) — è il bias conservativo, non erodere fiducia negando azioni vere.

Il discriminante è il participio passato in prima persona ("ho salvato"): offerte
("vuoi che salvi?"), condizionali ("lo salvo se…") e descrizioni ("di solito salvo")
NON usano questa forma, quindi non scattano. Lessico CHIUSO e generico (azioni, non
modi di dire di dominio).
"""
import re

# Verbi-azione che Euri può affermare di aver compiuto (participio passato).
_VERBS = (
    r"(?:salvat|aggiornat|modificat|memorizzat|segnat|annotat|registrat|"
    r"creat|generat|prodot|esportat|preparat|scritt|elimina|cancellat|rimoss|complet|"
    r"analizzat|esaminat|consultat|lett)\w*"
)

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

# Impegno operativo immediato senza tool: "vado a studiare il codice", "ora
# controllo e ti dico". Non cattura futuri condizionati/offerte ("se vuoi provo",
# "lo memorizzerò appena confermi"), che non fingono lavoro autonomo già avviato.
_ACTION_INFINITIVE = (
    r"(?:studiare|analizzare|controllare|verificare|leggere|guardare|esaminare|"
    r"approfondire|cercare|elaborare|sistemare|aggiornare|salvare|memorizzare|"
    r"creare|generare|eseguire|avviare|lanciare)"
)
_ACTION_PRESENT = (
    r"(?:studio|analizzo|controllo|verifico|leggo|guardo|esamino|approfondisco|"
    r"cerco|elaboro|sistemo|aggiorno|salvo|memorizzo|creo|genero|eseguo|avvio|lancio)"
)
_IMMEDIATE_COMMITMENT_RE = re.compile(
    rf"\b(?:vado|provo|inizio|comincio|procedo)\s+"
    rf"(?:subito\s+|ora\s+|adesso\s+)?a\s+"
    rf"(?:{_ACTION_INFINITIVE}|dare\s+un['’ ]occhiata)\b"
    rf"|\b(?:ora|adesso|intanto)\s+(?:mi\s+metto\s+a\s+{_ACTION_INFINITIVE}|{_ACTION_PRESENT})\b",
    re.IGNORECASE,
)
# Claim presenti e transitivi che nei dialoghi agenda suonano come un esito
# immediato (caso live 21/07: "Lo tolgo dai sospesi" dopo routing CHAT). Sono
# ancorati a inizio frase/periodo e a un oggetto, per non catturare spiegazioni
# generiche come "quando chiudo un progetto...".
_DIRECT_PRESENT_ACTION_RE = re.compile(
    r"(?:^|(?<=[.!?])\s+)(?:ricevuto[,.]?\s*)?"
    r"(?:(?:lo|la|li|le|questo|questa)\s+"
    r"(?:tolgo|chiudo|completo|cancello|rimuovo|sospendo|sposto|riprogrammo)\b"
    r"|lascio\b[^.!?\n]{0,50}\bin\s+sospeso\b)",
    re.IGNORECASE,
)
_IN_PROGRESS_COMMITMENT_RE = re.compile(
    r"\b(?:sto|stiamo)\s+(?:ora\s+|adesso\s+|già\s+)?"
    r"(?:preparando|generando|creando|scrivendo|riscrivendo|esportando|salvando|"
    r"modificando|aggiornando|analizzando|controllando|verificando)\b",
    re.IGNORECASE,
)
_CONDITIONAL_OFFER_RE = re.compile(
    r"\b(?:se\s+vuoi|se\s+preferisci|se\s+mi\s+dici|quando\s+vuoi|"
    r"appena\s+confermi|dimmi\s+e)\b",
    re.IGNORECASE,
)
_ARTIFACT_AVAILABILITY_RE = re.compile(
    r"\b(?:il\s+(?:file|documento)\s+(?:si\s+trova|è\s+(?:già\s+)?"
    r"(?:pronto|disponibile|salvato))|lo\s+trovi\b|la\s+trovi\b|"
    r"puoi\s+(?:scaricarlo|scaricarla|aprirlo|aprirla)\b|ecco\s+il\s+file\b)",
    re.IGNORECASE,
)

# Se una lettura/analisi di una sorgente non è avvenuta, anche le conclusioni che
# seguono nello stesso draft sono prive di fondamento. In quel caso non basta
# eliminare la sola frase "ho letto": va ritirata l'intera risposta dipendente.
_SOURCE_ACCESS_OBJECT = (
    r"(?:document[oi]|file|pdf|allegat[oi]|testo|appunt[oi]|clipboard|log|"
    r"pagina|sito|offert[ae]|preventiv[oi]|sched[ae]|report)"
)
_UNBACKED_SOURCE_ACCESS_CLAIM_RE = re.compile(
    rf"\b(?:ho|l['’ ]ho|li ho|le ho|gli ho)\s+"
    rf"(?:{_ADV}\s+){{0,2}}(?:analizzat|esaminat|consultat|lett)\w*\b"
    rf"[^.!?\n]{{0,120}}\b{_SOURCE_ACCESS_OBJECT}\b",
    re.IGNORECASE,
)

# Un aggiornamento esplicitamente cognitivo non è un effetto operativo. Caso
# live 06/08: "Ho aggiornato mentalmente il dato" apriva inutilmente il
# controller e produceva una falsa smentita d'azione. La regola è grammaticale,
# non legata al contenuto: rimuove soltanto la breve clausola interna; un secondo
# claim reale nella stessa frase ("... e ho salvato il file") resta rilevabile.
_COGNITIVE_UPDATE_OBJECT = (
    r"(?:mentalmente(?:\s+(?:il|questo|quel)\s+"
    r"(?:dato|quadro|contesto|informazione))?"
    r"|(?:la\s+)?mia\s+(?:comprensione|interpretazione)"
    r"|(?:il|questo|quel)\s+(?:dato|quadro|contesto|informazione)\s+"
    r"(?:mentalmente|nella\s+mia\s+(?:comprensione|interpretazione)|"
    r"nel\s+contesto\s+della\s+conversazione))"
)
_COGNITIVE_UPDATE_RE = re.compile(
    rf"\b(?:"
    rf"(?:ho|l['’ ]ho|li ho|le ho|gli ho)\s+(?:{_ADV}\s+){{0,2}}aggiornat\w*"
    rf"|(?:ora|adesso|intanto)\s+aggiorno"
    rf"|sto\s+(?:ora\s+|adesso\s+|già\s+)?aggiornando"
    rf")\s+{_COGNITIVE_UPDATE_OBJECT}\b",
    re.IGNORECASE,
)


def _without_cognitive_updates(text: str) -> str:
    return _COGNITIVE_UPDATE_RE.sub("", text or "")


def claims_completed_action(text: str) -> bool:
    """True se il testo afferma un'azione COMPIUTA in prima persona NEL turno corrente
    (negazioni e azioni passate-distanti escluse)."""
    if not text:
        return False
    text = _without_cognitive_updates(text)
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


def claims_immediate_action_commitment(text: str) -> bool:
    """True se Euri promette lavoro autonomo immediato senza attendere un tool."""
    text = _without_cognitive_updates(text)
    if not text or _CONDITIONAL_OFFER_RE.search(text):
        return False
    return bool(
        _IMMEDIATE_COMMITMENT_RE.search(text)
        or _DIRECT_PRESENT_ACTION_RE.search(text)
        or _IN_PROGRESS_COMMITMENT_RE.search(text)
    )


def needs_honest_correction(reply: str, turn_actions: set) -> bool:
    """
    True (mismatch) se la risposta afferma un'azione compiuta ma nel turno NON è
    stata eseguita alcuna azione. `turn_actions` = azioni reali del turno (vuoto
    se nessun handler ha agito). Check binario: qualsiasi azione "copre" il claim.
    """
    return (
        claims_completed_action(reply) or claims_immediate_action_commitment(reply)
    ) and not turn_actions


def unbacked_action_claim_details(reply: str, turn_actions: set) -> list[dict[str, str]]:
    """Restituisce categoria e frase dei claim non coperti osservati nel draft.

    È diagnostica, non decide se eseguire o correggere: il chiamante può così
    distinguere un effetto dichiarato come già compiuto da una promessa futura
    e lasciare nei log la porzione esatta che ha attivato il guard.
    """
    if turn_actions or not reply:
        return []
    details: list[dict[str, str]] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", reply.strip()):
        if claims_completed_action(sentence):
            details.append({"category": "completed_action", "sentence": sentence})
        elif claims_immediate_action_commitment(sentence):
            details.append({"category": "immediate_commitment", "sentence": sentence})
    return details


def honest_correction() -> str:
    """Riga onesta da pronunciare al posto del claim falso."""
    return ("Aspetta — in realtà non ho eseguito quell'azione in questo turno. "
            "Vuoi che la faccia adesso?")


def honest_commitment_correction() -> str:
    """Correzione per una promessa di lavoro futuro non sostenuta da un tool."""
    return ("Per essere preciso: non posso continuare quell'azione in background. "
            "In questo turno non è partito alcun tool reale.")


def strip_leading_stage_direction(text: str) -> str:
    """Rimuove una sola didascalia parentesizzata posta prima del testo parlato.

    La funzione non interpreta il contenuto e non tocca parentesi interne. Va usata
    soltanto quando il frame semantico ha gia' stabilito che il turno e' una
    performance linguistica (presentazione, discorso, recitazione).
    """
    value = str(text or "")
    stripped = value.lstrip()
    if not stripped.startswith("("):
        return value
    depth = 0
    for index, char in enumerate(stripped):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                remainder = stripped[index + 1 :].lstrip()
                return remainder if remainder else value
        if depth < 0:
            break
    return value


def scrub_unbacked_action_claim(
    reply: str,
    turn_actions: set,
    *,
    semantic_action_veto: bool = False,
) -> str:
    """
    Pavimento di onestà sull'AZIONE — fratello più largo di
    honesty.scrub_unbacked_save_claim (che copre il solo salvataggio).

    In un handler che NON esegue azioni mutanti (`turn_actions` vuoto): se la
    risposta rivendica un'azione compiuta in questo turno (creato/eliminato/
    aggiornato/completato...), rimuove le frasi che la rivendicano e aggiunge la
    correzione onesta. Altrimenti ritorna invariato.

    Specchia la forma di scrub_unbacked_save_claim: si applica a valle, frase per
    frase, così le frasi vere restano e solo il claim infondato cade.
    """
    completed = claims_completed_action(reply)
    commitment = claims_immediate_action_commitment(reply)
    if turn_actions or not (completed or commitment):
        return reply
    if completed and _UNBACKED_SOURCE_ACCESS_CLAIM_RE.search(
        _without_cognitive_updates(reply)
    ):
        return honest_correction()
    sentences = re.split(r"(?<=[.!?…])\s+", reply.strip())
    kept = [
        s for s in sentences
        if not claims_completed_action(s)
        and not claims_immediate_action_commitment(s)
        and not _ARTIFACT_AVAILABILITY_RE.search(s)
    ]
    cleaned = " ".join(kept).strip()
    # Un frame affidabile può avere già stabilito che il turno richiede solo
    # ragionamento/linguaggio. In quel caso una frase intercettata dal pattern
    # morbido (es. "provo a elaborare il parallelo...") non deve produrre una
    # falsa coda su tool/background. La frase sospetta cade comunque; i claim
    # forti su azioni già compiute conservano sempre la correzione.
    if semantic_action_veto and commitment and not completed and cleaned:
        return cleaned
    tail = honest_correction() if completed else honest_commitment_correction()
    return f"{cleaned}\n\n{tail}" if cleaned else tail


def emit_unbacked_action_commitment(r, reply: str, turn_actions: set, *, channel: str = "") -> bool:
    """
    Afferente puro: se Euri rivendica un'azione non coperta da un handler reale,
    traccia il tentativo su Pulse come commitment/intero.

    Non riconcilia, non corregge, non agisce. Lo scrub resta responsabilità di
    scrub_unbacked_action_claim(). Qui registriamo solo il segnale osservabile.
    """
    if not needs_honest_correction(reply, turn_actions):
        return False
    try:
        from core.pulse import pulse_emit
        pulse_emit(
            r,
            "commitment",
            "intero",
            "unbacked_action_claim",
            payload={
                "channel": channel,
                "claim": reply[:700],
                "turn_actions": sorted(str(a) for a in (turn_actions or set())),
            },
            salience=0.65,
        )
    except Exception:
        pass
    return True
