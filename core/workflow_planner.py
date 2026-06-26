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

# Verbi-azione GENERICI (non modi di dire di settore — vedi feedback no-overfit):
# pre-gate economico per decidere se vale la pena chiamare il planner. Non è
# classificazione, solo "sembra una richiesta operativa-composta?". Sostituibile
# dal modello quando le regex toccano il soffitto (pipeline_model_routing).
_ACTION_VERBS = re.compile(
    r"\b(legg\w*|riassum\w*|sintetizz\w*|prepar\w*|scriv\w*|bozz\w*|mail|email|"
    r"mand\w*|invi\w*|salv\w*|controll\w*|verific\w*|analizz\w*|estra\w*|"
    r"revision\w*|rived\w*|rispond\w*|rispost\w*)\b",
    re.IGNORECASE,
)


def looks_like_workflow(text: str) -> bool:
    """
    Pre-gate economico: True se la frase contiene ≥2 verbi-azione DISTINTI
    (= probabile richiesta multi-step). Heuristica fail-safe: se sbaglia, il
    planner ritorna comunque [] o un piano da 1 step e si torna al dispatch.
    """
    hits = {m.group(0).lower() for m in _ACTION_VERBS.finditer(text or "")}
    return len(hits) >= 2


# ──────────────────────────────────────────
# PLANNER (puro)
# ──────────────────────────────────────────

def _plan_prompt(utterance: str) -> str:
    cat = "\n".join(f"- {k}: {v}" for k, v in CAPABILITIES.items())
    return (
        "Sei il pianificatore operativo di Euri. Trasforma la richiesta dell'utente "
        "in una sequenza ORDINATA di passi, usando SOLO queste capability:\n"
        f"{cat}\n\n"
        f'Richiesta: "{utterance}"\n\n'
        "Regole:\n"
        "- Usa solo le capability elencate, in ordine logico.\n"
        '- Ogni passo prende in input l\'output del passo precedente con "$N" '
        "(N = numero del passo, 1-based), oppure null se non serve input.\n"
        "- Se la richiesta è una sola azione semplice, restituisci un solo passo.\n"
        "- DRAFT non invia mai; se l'utente vuole una mail, è sempre una bozza.\n"
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
    if not isinstance(steps, list) or not steps:
        return []
    clean = []
    for s in steps:
        if not isinstance(s, dict):
            return []
        cap = str(s.get("cap", "")).upper()
        if cap not in _VALID_CAPS:
            return []
        args = s.get("args")
        clean.append({
            "cap": cap,
            "args": args if isinstance(args, dict) else {},
            "input": s.get("input"),
        })
    return clean


def plan(utterance: str, *, chat=None, model: str = None) -> list[dict]:
    """
    1 LLM-call → piano. PURO (nessun effetto). Fail-open: [] su errore/incertezza.
    `chat`/`model` injectabili per test (default: chat_client + config.OLLAMA_MODEL).
    """
    if not utterance or not utterance.strip():
        return []
    try:
        if chat is None:
            from core.ollama_client import chat_client
            chat = chat_client
        resp = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": _plan_prompt(utterance)}],
            options={"temperature": 0.1, "num_predict": 400, "num_ctx": 4096},
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

    def __init__(self, executor, brain):
        self._executor = executor
        self._brain = brain

    def run(self, steps: list[dict]) -> dict:
        """
        Esegue gli step in ordine. Ritorna:
            {"ok": bool, "text": str, "path": str|None, "spoken": str}
        Si ferma al primo errore bloccante (conservativo).
        """
        outputs: dict[str, dict] = {}
        for i, step in enumerate(steps, 1):
            cap = step["cap"]
            src = self._resolve_input(step.get("input"), outputs)
            try:
                outputs[f"${i}"] = self._run_cap(cap, step.get("args") or {}, src)
            except Exception as e:
                logger.error(f"WorkflowEngine: step {i} {cap} fallito: {e}")
                return {
                    "ok": False, "text": "", "path": None,
                    "spoken": f"Mi sono fermata: {cap.lower()} non è riuscito. {e}",
                }

        last = outputs.get(f"${len(steps)}", {})
        return {
            "ok": True,
            "text": last.get("text", ""),
            "path": last.get("path"),
            "spoken": self._spoken(steps, last),
        }

    # ── input chaining ──
    @staticmethod
    def _resolve_input(ref, outputs) -> str:
        if not ref:
            return ""
        out = outputs.get(ref)
        if isinstance(out, dict):
            return out.get("text", "") or ""
        return str(out or "")

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
            return {"text": self._llm(
                f"Scrivi una bozza di {kind} in italiano basata su quanto segue. "
                "Solo la bozza, pronta da revisionare, senza commenti né preamboli:\n\n"
                + src), "path": None}
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
        r = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": num_predict, "num_ctx": 16384},
            think=False,
        )
        return self._brain._clean(r.message.content or "")

    # ── voce finale ──
    @staticmethod
    def _spoken(steps: list[dict], last: dict) -> str:
        path = last.get("path")
        if path:
            return (f"Fatto. Bozza pronta in {path.split('/')[-1]}, "
                    "non l'ho inviata: è lì per la revisione.")
        text = (last.get("text") or "").strip()
        return text[:350] if text else "Fatto."
