"""
Workflow Planner — strato sottile sopra i tool che ESISTONO già.

Trasforma una richiesta operativa NATURALE e composta
    "leggi il documento, riassumilo e preparami una bozza di mail, non inviarla"
in un PIANO ordinato di poche capability, e lo esegue incatenando gli output.
Non aggiunge tool nuovi: ogni capability è un adapter sottile su un primitivo
che esiste già (Executor.read_document, sintesi LLM, draft_writer).

Due pezzi separati e debuggabili:
  - plan(utterance) -> [step]     PURO: 1 LLM-call, nessun effetto.
  - WorkflowEngine.run(steps)     esegue, mappa capability→primitivo, incatena.

Il planner NON esegue e gli adapter NON ragionano: la separazione tiene il
sistema debuggabile e "bozza-non-inviare" è un invariante strutturale, non
una regex.

Kill switch: config.WORKFLOW_PLANNER_ENABLED.
Fail-open: plan() ritorna [] su qualunque incertezza → il caller torna al
dispatch attuale (nessuna regressione).
"""
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from loguru import logger

import config


# Catalogo capability — piccolo per scelta. Ogni voce mappa su un primitivo vivo.
CAPABILITIES = {
    "READ":            "Legge i documenti nella cartella dati di Euri e ne estrae il testo.",
    "SUMMARIZE":       "Riassume un testo ricevuto in input.",
    "CHECK":           "Verifica cosa MANCA o è incoerente in un testo o in una richiesta.",
    "DRAFT":           "Scrive una BOZZA (mail, risposta, testo) dal contenuto in input. NON invia nulla.",
    "SAVE_FOR_REVIEW": "Salva il risultato in una cartella di revisione e ne comunica il percorso.",
}

_VALID_CAPS = set(CAPABILITIES)
MAX_WORKFLOW_STEPS = 8


@dataclass(frozen=True)
class ActionContract:
    """Confine deterministico di una capability del workflow.

    Il modello sceglie il piano una sola volta; questi contratti stabiliscono
    invece quando un passo e' applicabile e quale risultato osservabile deve
    produrre. Non contengono prompt e non possono introdurre nuove azioni.
    """

    requires_text: bool = False
    produces_text: bool = True
    produces_path: bool = False
    max_attempts: int = 1


ACTION_CONTRACTS = {
    "READ": ActionContract(),
    "SUMMARIZE": ActionContract(requires_text=True),
    "CHECK": ActionContract(requires_text=True),
    "DRAFT": ActionContract(requires_text=True),
    "SAVE_FOR_REVIEW": ActionContract(
        requires_text=True,
        produces_path=True,
    ),
}


class WorkflowStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkflowGoal:
    """Goal chiuso: completare esattamente il piano validato e osservarne l'esito."""

    steps: tuple[dict, ...]

    @classmethod
    def from_steps(cls, steps: list[dict]) -> "WorkflowGoal":
        return cls(tuple(dict(step) for step in steps))


@dataclass
class WorkflowState:
    """Stato effimero del singolo run; non e' memoria cognitiva né Redis."""

    goal: WorkflowGoal
    status: WorkflowStatus = WorkflowStatus.READY
    outputs: dict[str, dict] = field(default_factory=dict)
    completed_steps: list[int] = field(default_factory=list)
    attempts: dict[int, int] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    failure: str = ""

    @property
    def reached(self) -> bool:
        return len(self.completed_steps) == len(self.goal.steps)

# Il gate deve riconoscere un ATTO OPERATIVO, non parole vagamente simili a verbi.
# Il vecchio `legg\w*` contava "leggero" come "leggere" e una spiegazione tecnica
# diventava un workflow. Le famiglie sotto usano forme lessicali finite e contano
# CAPACITA' distinte, non due flessioni dello stesso verbo.
_ACTION_FAMILIES = {
    "READ": re.compile(r"\b(?:leggi(?:mi)?|leggere)\b", re.IGNORECASE),
    "SUMMARIZE": re.compile(
        r"\b(?:riassumi(?:mi)?|riassumere|sintetizza(?:mi)?|sintetizzare)\b",
        re.IGNORECASE,
    ),
    "CHECK": re.compile(
        r"\b(?:controlla(?:mi)?|controllare|verifica(?:mi)?|verificare|"
        r"analizza(?:mi)?|analizzare|estrai|estrarre|rivedi|rivedere|"
        r"revisiona(?:mi)?|revisionare)\b",
        re.IGNORECASE,
    ),
    "DRAFT": re.compile(
        r"\b(?:prepara(?:mi)?|preparare|scrivi(?:mi)?|scrivere|bozza|"
        r"rispondi|rispondere)\b",
        re.IGNORECASE,
    ),
    "SAVE_FOR_REVIEW": re.compile(
        r"\b(?:salva(?:mi)?|salvare|manda(?:mi)?|mandare|invia(?:mi)?|inviare)\b",
        re.IGNORECASE,
    ),
}

_DIRECTIVE = re.compile(
    r"\b(?:leggi(?:mi)?|riassumi(?:mi)?|sintetizza(?:mi)?|controlla(?:mi)?|"
    r"verifica(?:mi)?|analizza(?:mi)?|estrai|rivedi|revisiona(?:mi)?|"
    r"prepara(?:mi)?|scrivi(?:mi)?|rispondi|salva(?:mi)?|manda(?:mi)?|"
    r"invia(?:mi)?|puoi|potresti|vorrei|voglio|fammi)\b",
    re.IGNORECASE,
)

_TEXT_ARTIFACT = re.compile(
    r"\b(?:document\w*|file|allegat\w*|testo|mail|email|bozza|risposta|"
    r"relazione|report|contenuto|cartella|nota|appunti)\b|"
    r"\b(?:quello|quanto)\s+che\s+(?:ho|abbiamo)\s+detto\b|"
    r"\bquesto\s+discorso\b",
    re.IGNORECASE,
)


def looks_like_workflow(text: str) -> bool:
    """
    True solo per una richiesta esplicita su testo/documenti con almeno due
    capability distinte. E' intenzionalmente precision-first: una falsa azione
    crea effetti reali, un falso negativo torna al normale dispatch conversazionale.
    """
    utterance = text or ""
    if not _DIRECTIVE.search(utterance) or not _TEXT_ARTIFACT.search(utterance):
        return False
    families = {name for name, pattern in _ACTION_FAMILIES.items() if pattern.search(utterance)}
    return len(families) >= 2


# ──────────────────────────────────────────
# PLANNER (puro)
# ──────────────────────────────────────────

def _plan_prompt(utterance: str, history_brief: str = "") -> str:
    cat = "\n".join(f"- {k}: {v}" for k, v in CAPABILITIES.items())
    convo = ""
    if history_brief.strip():
        convo = (
            "\nConversazione recente (la FONTE se la richiesta si riferisce a "
            "'questo'/'quanto detto'):\n" + history_brief.strip() + "\n"
        )
    return (
        "Sei il pianificatore operativo di Euri. Trasforma la richiesta dell'utente "
        "in una sequenza ORDINATA di passi, usando SOLO queste capability:\n"
        f"{cat}\n"
        f"{convo}\n"
        f'Richiesta: "{utterance}"\n\n'
        "Regole:\n"
        "- Se il turno corrente e' una spiegazione, una constatazione, una domanda di "
        "opinione o il racconto di cosa l'utente ha/non ha fatto, restituisci []. Non "
        "inventare un'azione basandoti sulla conversazione precedente.\n"
        "- Pianifica soltanto comandi operativi ESPLICITI presenti nel turno corrente.\n"
        "- Usa solo le capability elencate, in ordine logico.\n"
        '- Ogni passo prende in input l\'output del passo precedente con "$N" '
        "(N = numero del passo, 1-based), oppure null se non serve input.\n"
        "- Se la richiesta si riferisce a quanto appena detto nella conversazione "
        "(es. 'scrivimi una mail su questo', 'riassumi quello che ho detto'), la FONTE "
        "è la CONVERSAZIONE: usa direttamente DRAFT/SUMMARIZE con input null (l'engine "
        "inietta la conversazione), NON READ né CHECK (non c'è un documento da leggere).\n"
        "- READ/CHECK servono solo quando c'è un DOCUMENTO/file da elaborare.\n"
        "- Se la richiesta è una sola azione semplice, restituisci un solo passo.\n"
        "- DRAFT non invia mai; se l'utente vuole una mail, è sempre una bozza.\n"
        "- Dopo una DRAFT aggiungi sempre SAVE_FOR_REVIEW come ultimo passo, "
        "tranne se l'utente chiede esplicitamente di leggerla ad alta voce.\n"
        '- Per DRAFT puoi indicare il tipo in args, es. {"kind": "mail"}.\n\n'
        "Rispondi SOLO con un array JSON, niente altro. Esempio:\n"
        '[{"cap":"READ","args":{},"input":null},'
        '{"cap":"SUMMARIZE","args":{},"input":"$1"},'
        '{"cap":"DRAFT","args":{"kind":"mail"},"input":"$2"},'
        '{"cap":"SAVE_FOR_REVIEW","args":{},"input":"$3"}]'
    )


def _extract_json_array(raw: str) -> list:
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def _validate(steps) -> list[dict]:
    """Tutto-o-niente: un solo step invalido → [] (fail-open, niente piano monco)."""
    if (
        not isinstance(steps, list)
        or not steps
        or len(steps) > MAX_WORKFLOW_STEPS
    ):
        return []
    clean = []
    for index, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            return []
        cap = str(s.get("cap", "")).upper()
        if cap not in _VALID_CAPS:
            return []
        input_ref = s.get("input")
        if input_ref is not None:
            if not isinstance(input_ref, str):
                return []
            match = re.fullmatch(r"\$([1-9][0-9]*)", input_ref.strip())
            # Un piano ordinato puo' leggere soltanto un risultato gia' osservato:
            # riferimenti futuri/ciclici non arrivano mai all'engine.
            if not match or int(match.group(1)) >= index:
                return []
            input_ref = input_ref.strip()
        args = s.get("args")
        clean.append({
            "cap": cap,
            "args": args if isinstance(args, dict) else {},
            "input": input_ref,
        })
    return clean


def plan(utterance: str, *, history_brief: str = "", chat=None, model: str = None) -> list[dict]:
    """
    1 LLM-call → piano. PURO (nessun effetto). Fail-open: [] su errore/incertezza.
    `history_brief` = conversazione recente, così il planner sa che la FONTE può essere
    il discorso (non un documento). `chat`/`model` injectabili per test.
    """
    if not utterance or not utterance.strip():
        return []
    try:
        if chat is None:
            from core.ollama_client import chat_client
            chat = chat_client
        resp = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": _plan_prompt(utterance, history_brief)}],
            options={
                "temperature": 0.1,
                "num_predict": 400,
                "num_ctx": config.CHAT_OLLAMA_NUM_CTX,
            },
            think=False,
        )
        raw = resp.message.content or ""
    except Exception as e:
        logger.warning(f"workflow_planner.plan: LLM fallito ({e}) → fallback dispatch")
        return []
    return _validate(_extract_json_array(raw))


# ──────────────────────────────────────────
# ENGINE (esegue, incatena, effetti)
# ──────────────────────────────────────────

class WorkflowEngine:
    """
    Esegue un piano incatenando gli output. Riceve i componenti VIVI
    (executor, brain) — non li crea — così è testabile con dei fake.
    Ogni step produce un dict {"text": str, "path": str|None}; "$N" referenzia
    l'output dello step N.
    """

    def __init__(self, executor, brain, conversation: str = ""):
        self._executor = executor
        self._brain = brain
        self._conversation = conversation or ""   # fonte quando non c'è un documento

    def run(self, steps: list[dict]) -> dict:
        """
        Esegue un passo alla volta e rivaluta lo stato dopo ogni osservazione.
        Il replanning e' deterministico: non richiama il modello e non puo'
        aggiungere capability al piano validato. Ritorna:
            {"ok": bool, "text": str, "path": str|None, "spoken": str}
        Si ferma al primo errore bloccante (conservativo).
        """
        steps = _validate(steps)
        if not steps:
            state = WorkflowState(WorkflowGoal.from_steps([]))
            return self._failed_result(state, "piano vuoto o non valido")
        steps = self._ensure_review(steps)
        if len(steps) > MAX_WORKFLOW_STEPS:
            state = WorkflowState(WorkflowGoal.from_steps(steps))
            return self._failed_result(state, "piano oltre il limite di passi")
        state = WorkflowState(WorkflowGoal.from_steps(steps))
        state.status = WorkflowStatus.RUNNING

        while not state.reached:
            selected = self._select_next_action(state)
            if selected is None:
                return self._failed_result(
                    state,
                    state.failure or "nessuna azione applicabile per raggiungere il goal",
                )
            index, step, src = selected
            cap = step["cap"]
            state.attempts[index] = state.attempts.get(index, 0) + 1
            state.trace.append({
                "step": index,
                "cap": cap,
                "event": "started",
                "attempt": state.attempts[index],
            })
            try:
                output = self._run_cap(cap, step.get("args") or {}, src)
            except Exception as e:
                return self._failed_result(state, str(e), index=index, cap=cap)

            postcondition_error = self._postcondition_error(cap, output)
            if postcondition_error:
                return self._failed_result(
                    state,
                    postcondition_error,
                    index=index,
                    cap=cap,
                )

            state.outputs[f"${index}"] = output
            state.completed_steps.append(index)
            state.trace.append({
                "step": index,
                "cap": cap,
                "event": "completed",
                "attempt": state.attempts[index],
            })
            logger.info(
                "WorkflowEngine: stato {}/{} dopo {}",
                len(state.completed_steps),
                len(state.goal.steps),
                cap,
            )

        state.status = WorkflowStatus.COMPLETED
        last = state.outputs.get(f"${len(steps)}", {})
        return {
            "ok": True,
            "text": last.get("text", ""),
            "path": last.get("path"),
            "spoken": self._spoken(steps, last),
            "goal_status": state.status.value,
            "completed_steps": len(state.completed_steps),
            "trace": state.trace,
        }

    def _select_next_action(
        self,
        state: WorkflowState,
    ) -> tuple[int, dict, str] | None:
        """Sceglie il prossimo passo applicabile dallo stato osservato corrente."""
        for index, step in enumerate(state.goal.steps, 1):
            if index in state.completed_steps:
                continue
            # Il piano e' ordinato: non saltare un predecessore fallito o mancante.
            if any(previous not in state.completed_steps for previous in range(1, index)):
                state.failure = f"step {index} bloccato da un predecessore incompleto"
                return None

            cap = step.get("cap", "")
            contract = ACTION_CONTRACTS.get(cap)
            if contract is None:
                state.failure = f"capability non registrata: {cap or '?'}"
                return None
            if state.attempts.get(index, 0) >= contract.max_attempts:
                state.failure = f"budget tentativi esaurito per {cap.lower()}"
                return None

            input_ref = step.get("input")
            if input_ref and input_ref not in state.outputs:
                state.failure = f"input {input_ref} non disponibile per {cap.lower()}"
                return None
            src = self._resolve_input(input_ref, state.outputs)
            src = self._effective_source(cap, src)
            if contract.requires_text and not src.strip():
                state.failure = f"input testuale mancante per {cap.lower()}"
                return None
            return index, step, src
        state.failure = "piano incompleto ma senza passi disponibili"
        return None

    @staticmethod
    def _postcondition_error(cap: str, output) -> str:
        contract = ACTION_CONTRACTS[cap]
        if not isinstance(output, dict):
            return f"{cap.lower()} non ha prodotto un'osservazione strutturata"
        if contract.produces_text and not str(output.get("text") or "").strip():
            return f"{cap.lower()} ha prodotto testo vuoto"
        if contract.produces_path:
            raw_path = str(output.get("path") or "").strip()
            if not raw_path:
                return f"{cap.lower()} non ha restituito il percorso dell'artefatto"
            if not Path(raw_path).is_file():
                return f"{cap.lower()} ha dichiarato un artefatto che non esiste"
        return ""

    @staticmethod
    def _failed_result(
        state: WorkflowState,
        reason: str,
        *,
        index: int | None = None,
        cap: str = "",
    ) -> dict:
        state.status = WorkflowStatus.FAILED
        state.failure = reason
        if index is not None:
            state.trace.append({
                "step": index,
                "cap": cap,
                "event": "failed",
                "attempt": state.attempts.get(index, 0),
                "reason": reason[:240],
            })
        label = cap.lower() if cap else "workflow"
        logger.error(f"WorkflowEngine: {label} fallito: {reason}")
        return {
            "ok": False,
            "text": "",
            "path": None,
            "spoken": f"Mi sono fermata: {label} non è riuscito. {reason}",
            "goal_status": state.status.value,
            "completed_steps": len(state.completed_steps),
            "trace": state.trace,
        }

    # ── normalizzazione del piano ──
    @staticmethod
    def _ensure_review(steps: list[dict]) -> list[dict]:
        """
        Una bozza è un artefatto DA RIVEDERE: se il piano finisce con DRAFT e non
        salva, appendi SAVE_FOR_REVIEW d'ufficio. Il modello è incostante su questo
        passo (stesso input → a volte lo mette, a volte no): qui diventa un
        invariante strutturale dell'engine, non una decisione del planner. Evita
        anche che Euri legga ad alta voce una bozza lunga invece di salvarla.
        """
        if not steps:
            return steps
        caps = [s["cap"] for s in steps]
        if caps[-1] == "DRAFT":
            return steps + [{"cap": "SAVE_FOR_REVIEW", "args": {}, "input": f"${len(steps)}"}]
        return steps

    # ── input chaining ──
    @staticmethod
    def _resolve_input(ref, outputs) -> str:
        if not ref:
            return ""
        out = outputs.get(ref)
        if isinstance(out, dict):
            return out.get("text", "") or ""
        return ""

    def _effective_source(self, cap: str, src: str) -> str:
        """Materializza la conversazione soltanto per le capability previste."""
        if not src and self._conversation and cap in ("DRAFT", "SUMMARIZE", "CHECK"):
            return self._conversation
        return src

    # ── dispatch capability → primitivo esistente ──
    def _run_cap(self, cap: str, args: dict, src: str) -> dict:
        if cap == "READ":
            return {"text": self._read(), "path": None}
        if cap == "SUMMARIZE":
            return {"text": self._summarize(src), "path": None}
        if cap == "CHECK":
            return {"text": self._llm(
                "Elenca in modo puntuale cosa MANCA o è incompleto qui, "
                "senza riscrivere il contenuto:\n\n" + src), "path": None}
        if cap == "DRAFT":
            kind = (args or {}).get("kind", "testo")
            # Budget largo: una bozza è il PENSIERO INTERO di Euri, non un riassunto —
            # con 800 token la relazione del 03/07 si è troncata a metà frase.
            return {"text": self._llm(
                f"Scrivi una bozza di {kind} in italiano basata su quanto segue. "
                "Solo la bozza, pronta da revisionare, senza commenti né preamboli:\n\n"
                + src, num_predict=2500), "path": None}
        if cap == "SAVE_FOR_REVIEW":
            from agent.tools.draft_writer import save_review
            return {"text": src, "path": save_review(src)}
        return {"text": src, "path": None}

    # ── adapters sui primitivi vivi ──
    def _read(self) -> str:
        from agent.executor import ToolCall
        try:
            self._executor.stop_event.clear()
        except Exception:
            pass
        result = self._executor.execute(ToolCall(tool_name="read_document", parameters={"question": ""}))
        if not getattr(result, "success", False):
            raise RuntimeError(getattr(result, "output", "lettura fallita"))
        raw = (getattr(result, "raw_data", None) or {}).get("context_extra")
        return raw or getattr(result, "output", "") or ""

    def _summarize(self, text: str) -> str:
        if not text.strip():
            return ""
        try:
            from agent.tools.text_writer import _analyze_text_full
            return _analyze_text_full(text, config, self._brain)
        except Exception as e:
            logger.debug(f"WorkflowEngine._summarize: fallback respond ({e})")
            return self._llm("Riassumi in modo denso e diretto, in italiano:\n\n" + text)

    def _llm(self, prompt: str, *, num_predict: int = 800) -> str:
        from core.ollama_client import chat_client
        base = [{"role": "user", "content": prompt}]
        parts: list[str] = []
        # Il budget token è un tetto tecnico, non la fine del pensiero: se Ollama
        # taglia (done_reason=length) si CONTINUA da dove si è fermato invece di
        # consegnare un moncone (bozza troncata a metà frase, 03/07). Max 2 riprese:
        # oltre, meglio un documento lungo interrotto con traccia nel log che un loop.
        for _round in range(3):
            msgs = base if not parts else base + [
                {"role": "assistant", "content": "".join(parts)},
                {"role": "user", "content": "Continua ESATTAMENTE da dove ti sei "
                                            "interrotto, senza ripetere nulla e senza commenti."},
            ]
            r = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=msgs,
                options={
                    "temperature": 0.3,
                    "num_predict": num_predict,
                    "num_ctx": config.CHAT_OLLAMA_NUM_CTX,
                },
                think=False,
            )
            parts.append(r.message.content or "")
            if getattr(r, "done_reason", None) != "length":
                break
            logger.info(f"WorkflowEngine._llm: output al tetto di {num_predict} token → continuo la scrittura")
        else:
            logger.warning("WorkflowEngine._llm: ancora troncato dopo 2 riprese — consegno com'è")
        return self._brain._clean("".join(parts))

    # ── voce finale ──
    @staticmethod
    def _spoken(steps: list[dict], last: dict) -> str:
        path = last.get("path")
        if path:
            return (f"Fatto. Bozza pronta in {path.split('/')[-1]}, "
                    "non l'ho inviata: è lì per la revisione.")
        text = (last.get("text") or "").strip()
        return text[:350] if text else "Fatto."
