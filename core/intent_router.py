"""
Classifica l'intent dell'input vocale con regex deterministiche.
Niente LLM qui — <10ms, deve essere snappy.
"""
import re
import unicodedata
from enum import Enum


class Intent(str, Enum):
    SAVE_MEMORY = "SAVE_MEMORY"
    SAVE_TODO = "SAVE_TODO"
    SAVE_NOTE = "SAVE_NOTE"
    SAVE_LAST = "SAVE_LAST"
    WEB_SEARCH = "WEB_SEARCH"
    TRANSLATE = "TRANSLATE"
    SEARCH = "SEARCH"
    LIST_TODAY = "LIST_TODAY"
    COMPLETE = "COMPLETE"
    SILENCE_MODE = "SILENCE_MODE"
    RESTORE_ALERTS = "RESTORE_ALERTS"
    STATUS = "STATUS"
    EXECUTE = "EXECUTE"
    TEACH = "TEACH"
    ENROLL_VOICE = "ENROLL_VOICE"
    AUDIT_MEMORY = "AUDIT_MEMORY"
    DICTATION = "DICTATION"
    SHUTDOWN = "SHUTDOWN"
    CHAT = "CHAT"


# Ordine importante: i pattern più specifici prima
_PATTERNS: list[tuple[Intent, list[str]]] = [
    (Intent.SILENCE_MODE, [
        r"modalit[àa]\s+silenziosa",
        r"\bsilenzio\b",
        r"non\s+disturbare",
        r"muto\b",
        r"silenzio\s+fino\s+(a|alle?)",
    ]),
    (Intent.RESTORE_ALERTS, [
        r"ripristina\s+(alert|avvisi|notifiche|tutto)",
        r"riattiva\s+(alert|avvisi|notifiche)",
        r"togli\s+(il\s+)?silenzio",
    ]),
    (Intent.STATUS, [
        r"\bche\s+stato\s+(hai|c[''è])\b",
        r"\bdammi\s+(uno\s+)?stato\b",
        r"\bstato\s+(del\s+sistema|di\s+euri)\b",
        r"quant[ie]\s+(todo|ricordi|appunti|scadenz)",
        r"riassumimi",
    ]),
    (Intent.LIST_TODAY, [
        r"cosa\s+(devo\s+fare|ho\s+da\s+fare)\s+oggi",
        r"agenda\s+(di\s+)?oggi",
        r"lista\s+di\s+oggi",
        r"promemoria\s+di\s+oggi",
        r"cosa\s+c[''è]\s+oggi",
        r"cose\s+da\s+fare\s+oggi",
        r"riepilogo\s+(del\s+)?giorno",
    ]),
    (Intent.COMPLETE, [
        r"^(fatto|completato|finito)[.!]?\s*$",   # utterance isolata — inequivocabile
        r"cancella\s+(il\s+)?(promemoria|todo|task)",
        r"segna\s+(come\s+)?(fatto|completato)",
        r"rimuovi\s+(il\s+)?promemoria",
    ]),
    (Intent.SAVE_TODO, [
        r"ricordami\s+(?:\w+\s+){0,3}(?:di|del|dello|della|dei|degli)\b",
        r"ricordami\s+di\b",
        r"devo\s+(fare|chiamare|mandare|inviare|comprare|pagare|andare|contattare)",
        r"non\s+dimenticare\s+di\b",
        r"promemoria\s+(per|di)\b",
        r"metti\s+(in\s+agenda|un\s+promemoria)",
        r"aggiungi\s+(un\s+)?todo",
    ]),
    (Intent.SAVE_NOTE, [
        r"^idea\s*[:\-]",
        r"^appunto\s*[:\-]",
        r"^nota\s*[:\-]",
        r"^tecnico\s*[:\-]",
        r"^progetto\s*[:\-]",
        r"scrivi\s+(un\s+)?(appunto|nota)\b",
        r"segna\s+(un\s+)?(appunto|nota)\b",
        r"annotami\b",
    ]),
    (Intent.TRANSLATE, [
        r"\btraduci\b",
        r"\bmodalit[àa]\s+trad\w*\b",
        r"\bmodalit[àa]\s+interprete\b",
        r"\bcome\s+si\s+dice\s+(in\s+)?(inglese|francese|spagnolo|tedesco|cinese|giapponese|russo|arabo|portoghese)\b",
        r"\btraduzione\s+(in\s+)?tempo\s+reale\b",
        r"\binizia\s+a\s+tradurre\b",
        r"\bfacciamo\s+(?:una\s+)?traduzione\b",
        r"\bvoglio\s+tradurre\b",
        r"\bmodalit[àa]\s+(conversazione\s+)?bilingue\b",
        r"\btraduzione\s+bidirezionale\b",
        r"\bfai\s+(da\s+)?interprete\b",
        r"\bfai\s+l['\u2019]?\s*interprete\b",
        r"\binterprete\b",
        r"\bdall[\u2019']?(?:italian[ao]|ingles[ei]|frances[ei]|spagnol[oi]|tedesc[oi])\s+all[\u2019']?(?:italian[ao]|ingles[ei]|frances[ei]|spagnol[oi]|tedesc[oi])\b",
    ]),
    (Intent.WEB_SEARCH, [
        r"cercami\s+(online|sul\s+web|su\s+internet|in\s+rete)\b",
        r"cerca(?:re)?\s+(online|sul\s+web|nel\s+web|su\s+internet|in\s+rete)\b",
        r"cerca\s+su\s+internet\b",
        r"cosa\s+dice\s+il\s+web\s+(su|di|riguardo)",
        r"\b(guarda|controlla|prova\s+a\s+(guardare|cercare|controllare))\s+(nel|sul|in)\s+web\b",
        r"\b(guarda|controlla|prova\s+a\s+(guardare|cercare|controllare))\s+(online|su\s+internet|in\s+rete)\b",
        r"vai\s+(online|sul\s+web|su\s+internet)\s+(e\s+)?(cerca|guarda|controlla)\b",
        r"\bse\s+trovi\s+(online|sul\s+web|su\s+internet|nel\s+web)\b",
        r"\bvedi\s+(online|sul\s+web|nel\s+web|su\s+internet)\b",
        r"\bmi\s+fai\s+(una\s+)?ricerca\s+(nel|sul|in|su)\s+(web|internet)\b",
        r"\bfai\s+(una\s+)?ricerca\s+(nel|sul|in|su)\s+(web|internet)\b",
        r"\b(fai|fa')\s+(una\s+)?ricerca\s+online\b",
        r"\bricerca\s+(nel|sul|in|su)\s+(web|internet)\s+(per|su|di)\b",
        r"\bcerca\s+online\b",
    ]),
    (Intent.SAVE_LAST, [
        r"salva\s+(quello\s+che\s+abbiamo\s+(detto|parlato)|la\s+conversazione|questo\s+argomento|tutto\s+questo|questa\s+conversazione|quello\s+di\s+(prima|cui\s+sopra))",
        r"ricordati\s+(di\s+questa\s+conversazione|quello\s+che\s+abbiamo\s+detto|di\s+questo\s+argomento|quest[io]|gli|questi|queste)",
        r"metti\s+(in\s+memoria|a\s+memoria)\s+(quello|questa|tutto\s+questo)",
        r"salva\s+(il\s+discorso|l[''\']\s*argomento)\s+(di\s+prima|appena\s+fatto)",
        r"\bsalva\s+tutto\b",
        r"\bsalvo\s+in\s+memoria\b",
        r"\bsalva\s+in\s+memoria\b",
    ]),
    (Intent.EXECUTE, [
        r"\b(controlla|verifica|monitora|dimmi|mostrami)\s+(cpu|ram|memoria|disco|processi?|gpu|vram|temperatura|carico)\b",
        r"\b(controlla|verifica|monitora|dimmi|mostrami)\s+(i\s+)?processi\s+(del\s+)?(pc|sistema|workstation|linux)\b",
        r"\b(controlla|verifica|monitora|dimmi|mostrami)\s+(i\s+)?processi\s+(attivi|aperti|pesanti|in\s+esecuzione)\b",
        r"\b(quanta|quanto)\s+(ram|memoria|cpu|disco|vram)\s+(usa|c[''è]|ho|è\s+liber[ao])\b",
        r"\bche\s+(processi?|programmi?)\s+(sono|stanno)\s+(aperti|girando|in\s+esecuzione)\b",
        r"\bstato\s+(del\s+)?(sistema|workstation|pc|server)\b",
        r"\bquanta\s+vram\b",
        r"\buso\s+(della\s+)?(cpu|ram|gpu|memoria|disco)\b",
        r"\bprocessi\s+(più\s+)?(pesanti|attivi)\b",
        r"\bda\s+quanto\s+(è\s+accesa|gira|è\s+su)\b",
        r"\buptime\b",
        r"\bleggi\s+(il\s+)?log\b",
        r"\bultimi?\s+(errori?|log|righe\s+del\s+log)\b",
        r"\bche\s+processi\b",
        r"\bmonitor(a|aggio)\s+(la\s+)?(cpu|ram|gpu|sistema|workstation|pc)\b",
        r"\bquanto\s+fa\b",
        r"\bcalcola\b",
        r"\bfa\s+il\s+calcolo\b",
        r"\bquant[oaie]\s+(è|sono|fa|viene)\s+[\d\(]",
        r"\brisultat[oi]\s+di\s+[\d\(]",
        r"\bpercentuale\s+di\s+[\d\(]",
        r"\bscrivi\s+(un\s+)?(testo|documento|file|appunto\s+su\s+file)\b",
        r"\bsalva\s+(il\s+)?testo\b",
        r"\bdetta\s+(un\s+)?(testo|documento|appunto)\b",
        r"\bcopia\s+(questo|il\s+testo|negli\s+appunti)\b",
        r"\bmetti\s+(questo\s+)?(negli\s+appunti|in\s+clipboard)\b",
        r"\bcosa\s+c[''è]\s+(negli\s+appunti|in\s+clipboard)\b",
        r"\bleggi\s+.{0,25}?((dagli|degli|gli)\s+appunti|(dalla|la)\s+clipboard)\b",
        r"\b(analizza|studia|elabora|approfondisci|esamina)\s+((dagli?|degli?|gli?|dalla?|la|da)\s+)?(appunti|clipboard)\b",
        r"\b(cosa\s+dice|dimmi\s+cosa\s+c[''è]|riassumi|sintetizza)\s+(negli?\s+appunti|in\s+clipboard|dalla\s+clipboard)\b",
        r"\bsalva\s+(dagli?\s+appunti|dalla\s+clipboard)\b",
        r"\bcopiami\b",
        # CodeRunner — operazioni su file/dati (espansione massiva)
        r"\b(unisci|fondi|combina|merge|raggruppa)\s+.*(csv|file|pdf|excel|dati|document[io]|docx?|txt|json|xml|markdown|md|ods|odt|odp|libreoffice|calc|writer)\b",
        r"\b(leggi|apri|elabora|processa|converti|trasforma|analizza|controlla|riassumi|estrai|cerca\s+nel)\s+.*(csv|file|pdf|excel|dati|document[io]|docx?|txt|json|xml|markdown|md|testo|presentazion[ei]|ppt|ods|odt|odp|libreoffice|calc|writer)\b",
        r"\b(crea|genera|esporta|salva|scrivi)\s+.*(csv|file|pdf|excel|grafico|report|tabella|txt|ods|odt|odp)\b",
        r"\b(salva|scrivi|metti|esporta).*(cartella\s+(di\s+)?scambio|scambio_dati)\b",
        r"\b(ridimensiona|comprimi|ruota|taglia)\s+.*(foto|immagin[ei])\b",
        r"\bcartella\s+dati\b",
        r"\b(descrivi|analizza|controlla|guarda|leggi)\s+.*(foto|immagin[ei]|screenshot)\b",
        r"\b(cosa|quali|quanti)\s+.*(file|dati)\s+.*(ci\s+sono|nella|cartella)\b",
        r"\belenca\s+.*(file|dati)\b",
    ]),
    (Intent.TEACH, [
        r"\bti\s+(racconto|spiego|insegno)\b",
        r"\bvoglio\s+(spiegarti|raccontarti|insegnarti)\b",
        r"\blascia\s+che\s+ti\s+(spieghi|racconti)\b",
        r"\bvolendo\s+te\s+lo\s+posso\s+(raccontare|spiegare)\b",
        r"\bte\s+lo\s+(spiego|racconto)\b",
        r"\bposso\s+(spiegarti|raccontarti)\b",
    ]),
    (Intent.AUDIT_MEMORY, [
        r"\baudit\s+(della\s+)?(memoria|memorie)\b",
        r"\bcontrolla\s+(la\s+)?(memoria|memorie)\b",
        r"\bpulisci\s+(la\s+)?(memoria|memorie)\b",
        r"\bquante\s+memorie\s+(hai|ho|ci\s+sono)\b",
        r"\bcosa\s+(hai|ho)\s+in\s+memoria\b",
        r"\bcosa\s+sai\s+di\s+me\b",
        r"\bstato\s+(della\s+)?memoria\b",
        r"\banalizza\s+(la\s+)?(memoria|memorie)\b",
    ]),
    (Intent.DICTATION, [
        r"\bmodalit[àa]\s+dettatura\b",
        r"\binizia\s+dettatura\b",
        r"\bmodo\s+dettatura\b",
        r"\binizia\s+a\s+dettare\b",
        r"\bvoglio\s+dettare\b",
        r"\bdetta\s+un\s+documento\b",
    ]),
    (Intent.SHUTDOWN, [
        r"\bspegniti\b",
        r"\bspegni\s+euri\b",
        r"\bchiudi\s+euri\b",
        r"\bshutdown\b",
        r"\bvai\s+offline\b",
        r"\bspegni\s+tutto\b",
        r"\bchiudi\s+tutto\b",
        r"\bfermati\s+e\s+(chiudi|spegni)\b",
    ]),
    (Intent.ENROLL_VOICE, [
        r"\bregistra\s+(la\s+mia\s+voce|il\s+mio\s+profilo\s+vocale)\b",
        r"\bmemorizza\s+la\s+mia\s+voce\b",
        r"\bimpara\s+(a\s+riconoscermi|la\s+mia\s+voce)\b",
        r"\bcalibra\s+(la\s+)?voce\b",
        r"\bsetup\s+vocale\b",
    ]),
    (Intent.SAVE_MEMORY, [
        r"ricordami\s+che\b",
        r"segna\s+che\b",
        r"ricorda\s+che\b",
        r"nota\s+che\b",
        r"tieni\s+a\s+mente\s+che\b",
        r"non\s+dimenticare\s+che\b",
        r"memorizza\b",
        r"aggiungi\s+(?:anche\s+)?in\s+memoria\s+che\b",
        r"aggiungi\s+(?:anche\s+)?che\b",
        r"metti\s+in\s+memoria\s+che\b",
        r"salva\s+in\s+memoria\s+che\b",
    ]),
    (Intent.SEARCH, [
        r"cosa\s+(avevo|ho)\s+detto\s+(su|di|del|della|dei|degli)",
        r"che\s+(appunti|ricordi|note|todo)\s+(ho|avevo|ci\s+sono)",
        r"cercami\b",
        r"cerca\s+(tra|nei|nei)\b",
        r"dimmi\s+(tutto\s+su|cosa\s+so\s+su)",
        r"cosa\s+sai\s+(su|di)\b",
        r"hai\s+(qualcosa|appunti|note|informazioni)\s+(su|di|riguardo)\b",
        r"ricordi\s+(qualcosa\s+)?(su|di|del|della)\b",
        r"ricordi\s+\w",         # "ricordi il liceo...?", "ricordi Roberta...?"
        r"hai\s+memoria\s+(di|su|del|della)\b",
        r"c[''è]\s+(qualcosa|niente)\s+(in\s+memoria|nei\s+ricordi|salvato)\b",
        r"cosa\s+c[''è]\s+su\b",
        r"nella\s+tua\s+memoria\b",
        r"nei\s+tuoi\s+ricordi\b",
    ]),
]

_COMPILED: list[tuple[Intent, list[re.Pattern]]] = [
    (intent, [re.compile(p, re.IGNORECASE) for p in patterns])
    for intent, patterns in _PATTERNS
]

_COMPLETE_NARRATIVE = re.compile(
    r"\b(raccont[oa]|spieg[oa]|ricordare|evolver|ho\s+creato|ho\s+sviluppato"
    r"|come\s+ho\s+(fatto|creato|sviluppato)|come\s+l['’]\s*ho|te\s+lo\s+posso)\b",
    re.IGNORECASE
)

# Condizionali e contesti narrativi che rendono "fatto/finito" non un completamento
_COMPLETE_CONDITIONAL = re.compile(
    r'\b(saresti|sarei|sarebbe|sareste|sarebbero'
    r'|potrei|potresti|potrebbe|potremmo|potreste|potrebbero'
    r'|avrei|avresti|avrebbe|avremmo|avreste|avrebbero'
    r'|farei|faresti|farebbe|faremmo|fareste|farebbero'
    r'|dovrei|dovresti|dovrebbe'
    r'|era\s+il\s+software|era\s+il\s+programma|era\s+il\s+codice'
    r'|ti\s+ho\s+fatto|che\s+ti\s+ho\s+fatto|che\s+ho\s+fatto'
    r'|ho\s+fatto\s+bene'
    r'|ho\s+(fatto|finito|completato)\s+(anche|pure|oltre|diverse?|alcune?|molte?|pi[uù]|tante?|varie?|gi[àa])\b'
    r'|abbiamo\s+(fatto|risolto|completato|finito)\b)\b',
    re.IGNORECASE
)


def classify(text: str) -> tuple[Intent, dict]:
    """
    Ritorna (intent, metadata).
    Rileva tutti gli intent possibili e assegna la vittoria a quello
    il cui trigger compare per PRIMO nella frase pronunciata.
    """
    text = text.strip()
    # Versione senza diacritici per il matching regex — "intérprete" → "interprete"
    text_norm = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    is_question = text.endswith("?")

    matches = []

    # 1. Raccoglie tutti i possibili match e la loro posizione
    for intent, patterns in _COMPILED:
        for pattern in patterns:
            match = pattern.search(text_norm)
            if match:
                # Logica specifica per evitare falsi positivi su COMPLETE narrativi
                if intent == Intent.COMPLETE:
                    if _COMPLETE_NARRATIVE.search(text_norm):
                        continue
                    if _COMPLETE_CONDITIONAL.search(text_norm):
                        continue
                    if is_question:
                        continue

                matches.append({
                    "intent": intent,
                    "pattern": pattern.pattern,
                    "start_index": match.start()
                })
                break  # Passa al prossimo intent, basta un pattern matchato per categoria

    if not matches:
        return Intent.CHAT, {}

    # 2. Filtra i match non validi (es. domande retoriche)
    valid_matches = []
    for m in matches:
        if is_question and m["intent"] in (Intent.SAVE_TODO, Intent.SAVE_MEMORY, Intent.SAVE_NOTE):
            if not re.match(r"^(ricordami|segna|memorizza|nota|appunto|idea|devo\b)", text_norm, re.IGNORECASE):
                continue
        valid_matches.append(m)

    if not valid_matches:
        return Intent.CHAT, {}

    # 3. Il vincitore è il trigger che compare PRIMA nella frase
    valid_matches.sort(key=lambda x: x["start_index"])

    winner = valid_matches[0]
    return winner["intent"], {"matched_pattern": winner["pattern"]}

def extract_content_after_trigger(text: str, triggers: list[str]) -> str:
    """
    Rimuove il trigger dal testo per estrarre il contenuto utile.
    Es: "ricordami che il PP è salito" → "il PP è salito"
    """
    for trigger in triggers:
        match = re.search(trigger, text, re.IGNORECASE)
        if match:
            return text[match.end():].strip(" ,.-")
    return text


SAVE_MEMORY_TRIGGERS = [
    r"ricordami\s+che\b", r"segna\s+che\b", r"ricorda\s+che\b",
    r"nota\s+che\b", r"tieni\s+a\s+mente\s+che\b", r"memorizza\b",
    r"aggiungi\s+(?:anche\s+)?in\s+memoria\s+che\b",
    r"aggiungi\s+(?:anche\s+)?che\b",
    r"metti\s+in\s+memoria\s+che\b",
    r"salva\s+in\s+memoria\s+che\b",
]
SAVE_TODO_TRIGGERS = [
    r"ricordami\s+di\b", r"devo\s+", r"non\s+dimenticare\s+di\b",
    r"promemoria\s+(per|di)\b", r"metti\s+(in\s+agenda|un\s+promemoria)\b",
]
SAVE_NOTE_TRIGGERS = [
    r"^idea\s*[:\-]\s*", r"^appunto\s*[:\-]\s*", r"^nota\s*[:\-]\s*",
    r"^tecnico\s*[:\-]\s*", r"^progetto\s*[:\-]\s*",
    r"scrivi\s+(un\s+)?(appunto|nota)\b\s*[:\-]?\s*",
]
SEARCH_TRIGGERS = [
    r"cosa\s+(avevo|ho)\s+detto\s+(su|di|del|della|dei|degli)\s*",
    r"cercami\b\s*", r"dimmi\s+tutto\s+su\s*",
    r"cosa\s+sai\s+(su|di)\s*",
]


TEACH_END_SIGNALS = re.compile(
    r'(?<!\bnon\s)'  # negative lookbehind per "non basta"
    r'\b(basta(?!\s+che\b)|stop|finiamo|finita|finito|basta\s+così|ok\s+basta|ho\s+finito'
    r'|è\s+tutto|tutto\s+qui|per\s+ora\s+basta|finiamola|smettila'
    r'|fermati|fermiamoci|interrompi|ok\s+fermati|adesso\s+fermati|ora\s+fermati'
    r'|ti\s+devi\s+fermare|devi\s+fermarti|voglio\s+fermarmi|smetti\s+di\s+chiedere'
    r'|finiamo\s+(qui|per\s+ora|con\s+questo|la\s+discussione|il\s+discorso)'
    r'|chiudiamo\s+(qui|il\s+discorso|la\s+discussione)'
    r'|lascia\s+perdere|lasciamo\s+perdere)\b',
    re.IGNORECASE
)


DICTATION_END_SIGNALS = re.compile(
    r'\b(fine\s+dettatura|stop\s+dettatura|esci\s+(dalla\s+)?dettatura|termina\s+dettatura)\b',
    re.IGNORECASE
)

# Azioni di chiusura dettatura — determinano cosa fare con il testo accumulato
DICTATION_SAVE_FILE = re.compile(
    r'\b(salva(\s+(su\s+)?file)?|salva\s+(il\s+)?documento)\b',
    re.IGNORECASE
)

DICTATION_COPY_CLIPBOARD = re.compile(
    r'\b(copia(\s+negli\s+appunti)?|metti\s+(negli\s+appunti|in\s+clipboard)'
    r'|negli\s+appunti|clipboard|clip)\b',
    re.IGNORECASE
)

TRANSLATE_END_SIGNALS = re.compile(
    r'\b(esci|fine|stop|basta|termina|chiudi)\s+(dalla?\s+)?traduzione\b'
    r'|\bfine\s+traduzione\b|\bstop\s+traduzione\b'
    r'|\b(arresta|ferma|disattiva|esci\s+da[l\s]+)?(?:il\s+)?(traduttore|interprete)\b'
    r'|\bchiudi\s+(il\s+)?(traduttore|interprete)\b',
    re.IGNORECASE
)

# Mappa lingua italiana → nome completo per il prompt LLM
LANGUAGE_MAP = {
    r"ingles[ei]": "inglese",
    r"frances[ei]": "francese",
    r"spagnol[oi]": "spagnolo",
    r"tedesc[oi]": "tedesco",
    r"cines[ei]": "cinese",
    r"portoghesel?": "portoghese",
    r"russ[oa]": "russo",
    r"giappones[ei]": "giapponese",
    r"arabo": "arabo",
    r"italian[oa]": "italiano",
}


def extract_target_language(text: str) -> str:
    """Estrae la lingua target dal testo. Ritorna 'inglese' come default."""
    for pattern, lang in LANGUAGE_MAP.items():
        if re.search(pattern, text, re.IGNORECASE):
            return lang
    return "inglese"


def is_translate_mode_continuous(text: str) -> bool:
    """True se la frase indica modalità traduzione continua monodirezionale."""
    return bool(re.search(
        r'\bmodalit[àa]\s+traduzione\b|\btraduzione\s+in\s+tempo\s+reale\b|\binizia\s+a\s+tradurre\b',
        text, re.IGNORECASE
    ))


def is_translate_mode_bidir(text: str) -> bool:
    """True se la frase indica modalità conversazione bidirezionale (interprete)."""
    return bool(re.search(
        r'\bmodalit[àa]\s+(conversazione\s+)?bilingue\b'
        r'|\btraduzione\s+bidirezionale\b'
        r'|\bfai\s+(da\s+)?interprete\b'
        r'|\bmodalit[àa]\s+interprete\b'
        r"|\bfai\s+l['\u2019]?\s*interprete\b"
        r'|\binterprete\s+(italiano[\s\-]inglese|it[\s\-]en)\b'
        r'|\bmodalit[àa]\s+traduzione\s+(italiano[\s\-]inglese\s+)?(bidirezionale|due\s+lingue|entrambe)\b',
        text, re.IGNORECASE
    ))


def detect_note_category(text: str) -> str:
    text_lower = text.lower()
    if re.search(r"\bidea\b", text_lower):
        return "idea"
    if re.search(r"\btecnico\b|\bbug\b|\bcodice\b|\bprogramma\b", text_lower):
        return "tecnico"
    if re.search(r"\bprogetto\b", text_lower):
        return "progetto"
    if re.search(r"\blavoro\b|\bfornitore\b|\bclient[ei]\b|\bprezzo\b", text_lower):
        return "lavoro"
    return "personale"
