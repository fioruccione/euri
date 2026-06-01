"""
Executor Sandbox per Euri.
Il LLM genera un JSON con tool + parametri, il sandbox valida ed esegue.
Nessun codice arbitrario — solo tool pre-approvati in whitelist.
"""
import json
import re
import time
import threading
import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from loguru import logger

import config


# ─────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────

@dataclass
class ToolSpec:
    name: str
    description: str                     # in italiano, usato nel prompt LLM
    parameters_schema: dict              # {"param": {"type": "str|int|float", "required": bool, "values": [...], "min": N, "max": N}}
    handler: Callable
    timeout_seconds: int = 5
    requires_confirm: bool = False       # True per operazioni distruttive


@dataclass
class ToolCall:
    tool_name: str
    parameters: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
    output: str                          # sempre stringa voice-ready
    error: str | None = None
    raw_data: dict = field(default_factory=dict)


# ─────────────────────────────────────────
# SandboxGuard
# ─────────────────────────────────────────

class SandboxGuard:
    def __init__(self):
        self._call_times: dict[str, list[float]] = {}

    def validate_tool_name(self, name: str, registry: dict) -> bool:
        return name in registry

    def validate_parameters(self, spec: ToolSpec, params: dict) -> tuple[bool, str]:
        schema = spec.parameters_schema
        for param_name, rules in schema.items():
            required = rules.get("required", False)
            value = params.get(param_name)

            if value is None:
                if required:
                    return False, f"Parametro obbligatorio mancante: {param_name}"
                continue

            expected_type = rules.get("type", "str")
            if expected_type == "int":
                try:
                    params[param_name] = int(value)
                except (ValueError, TypeError):
                    return False, f"Parametro {param_name} deve essere un intero"
                v = params[param_name]
                if "min" in rules and v < rules["min"]:
                    params[param_name] = rules["min"]
                if "max" in rules and v > rules["max"]:
                    params[param_name] = rules["max"]
            elif expected_type == "str":
                params[param_name] = str(value)

            allowed = rules.get("values")
            if allowed and params[param_name] not in allowed:
                return False, f"Valore non ammesso per {param_name}: {params[param_name]}. Ammessi: {allowed}"

        return True, ""

    def validate_path(self, path_str: str, allowed_roots: list[str]) -> bool:
        try:
            resolved = Path(path_str).resolve()
            return any(
                str(resolved).startswith(str(Path(root).resolve()))
                for root in allowed_roots
            )
        except Exception:
            return False

    def is_rate_limited(self, tool_name: str, max_per_minute: int) -> bool:
        now = time.time()
        times = self._call_times.get(tool_name, [])
        times = [t for t in times if now - t < 60]
        self._call_times[tool_name] = times
        if len(times) >= max_per_minute:
            return True
        times.append(now)
        self._call_times[tool_name] = times
        return False


# ─────────────────────────────────────────
# Executor
# ─────────────────────────────────────────

def build_injected_context(spoken: str, raw_data: dict | None) -> str:
    """
    Costruisce la stringa da iniettare nella history LLM dopo un tool.
    Disaccoppia 'cosa dice' (spoken) da 'cosa ricorda': se il tool ha prodotto
    dati fedeli su file (run_code/read_document → raw_data['context_extra']), li
    accoda coi valori esatti, così le domande quantitative successive li leggono
    invece di confabularli. Funzione pura: nessuna dipendenza da audio/stato.
    (Spostata qui da voice_daemon per condividerla tra canale vocale e testuale.)
    """
    context_extra = (raw_data or {}).get("context_extra", "")
    if not context_extra:
        return spoken
    return (
        f"{spoken}\n\n[DATI ESTRATTI DAL FILE — valori esatti, "
        f"usa questi e non riassumere a memoria]\n{context_extra}"
    )


class Executor:
    def __init__(self):
        self._registry: dict[str, ToolSpec] = {}
        self._guard = SandboxGuard()
        self.stop_event = threading.Event()  # interruzione vocale per processi lunghi
        self._register_default_tools()

    def _register_default_tools(self):
        from agent.tools.system_monitor import (
            tool_cpu_usage, tool_ram_usage, tool_disk_usage,
            tool_top_processes, tool_uptime, tool_gpu_usage,
        )
        from agent.tools.log_reader import tool_read_log
        from agent.tools.math_eval import tool_evaluate_math
        from agent.tools.text_writer import tool_write_text, tool_clipboard_write, tool_clipboard_read, tool_clipboard_analyze

        tools = [
            ToolSpec(
                name="cpu_usage",
                description="Legge l'utilizzo della CPU in percentuale. Parametro opzionale: process_name (str) per filtrare un processo specifico.",
                parameters_schema={"process_name": {"type": "str", "required": False}},
                handler=tool_cpu_usage,
            ),
            ToolSpec(
                name="ram_usage",
                description="Legge l'utilizzo della RAM: totale, usata e libera in gigabyte.",
                parameters_schema={},
                handler=tool_ram_usage,
            ),
            ToolSpec(
                name="disk_usage",
                description="Legge lo spazio disco disponibile. Parametro opzionale: drive (str, es. 'C' o 'D').",
                parameters_schema={"drive": {"type": "str", "required": False, "values": ["C", "D", "E", "F"]}},
                handler=tool_disk_usage,
            ),
            ToolSpec(
                name="top_processes",
                description="Elenca i processi più pesanti. Parametri opzionali: n (int, default 5) e sort_by ('cpu' o 'memory').",
                parameters_schema={
                    "n": {"type": "int", "required": False, "min": 1, "max": 10},
                    "sort_by": {"type": "str", "required": False, "values": ["cpu", "memory"]},
                },
                handler=tool_top_processes,
            ),
            ToolSpec(
                name="uptime",
                description="Legge da quanto tempo è accesa la workstation.",
                parameters_schema={},
                handler=tool_uptime,
            ),
            ToolSpec(
                name="gpu_usage",
                description="Legge l'utilizzo delle GPU NVIDIA: VRAM usata e libera, utilizzo percentuale.",
                parameters_schema={},
                handler=tool_gpu_usage,
            ),
            ToolSpec(
                name="read_log",
                description="Legge le ultime righe del log di Euri. Parametro opzionale: n_lines (int, default 20).",
                parameters_schema={"n_lines": {"type": "int", "required": False, "min": 5, "max": 100}},
                handler=tool_read_log,
            ),
            ToolSpec(
                name="evaluate_math",
                description="USA QUESTO per calcoli numerici. Riceve un'espressione matematica (expression), la valuta e restituisce il risultato. Es: '(450 * 0.15) / 2', 'sqrt(144)', 'round(3.14159, 2)'. NON usare write_text per i calcoli.",
                parameters_schema={"expression": {"type": "str", "required": True}},
                handler=tool_evaluate_math,
                timeout_seconds=2,
            ),
            ToolSpec(
                name="write_text",
                description="USA QUESTO solo per salvare testo dettato verbalmente (appunti, note, documenti). NON usare per calcoli matematici. Parametri: text (testo da salvare, obbligatorio), filename (nome file, opzionale), format ('txt' o 'md', opzionale).",
                parameters_schema={
                    "text": {"type": "str", "required": True},
                    "filename": {"type": "str", "required": False},
                    "format": {"type": "str", "required": False, "values": ["txt", "md"]},
                },
                handler=tool_write_text,
            ),
            ToolSpec(
                name="clipboard_write",
                description="Copia testo negli appunti senza salvare su file. Parametro: text (str, obbligatorio).",
                parameters_schema={"text": {"type": "str", "required": True}},
                handler=tool_clipboard_write,
            ),
            ToolSpec(
                name="clipboard_read",
                description="Legge il contenuto degli appunti e lo riporta vocalmente.",
                parameters_schema={},
                handler=tool_clipboard_read,
            ),
            ToolSpec(
                name="clipboard_analyze",
                description="Analizza il testo o l'immagine negli appunti con il LLM e salva i punti chiave in memoria.",
                parameters_schema={},
                handler=tool_clipboard_analyze,
                timeout_seconds=60,
            ),
        ]

        for spec in tools:
            self._registry[spec.name] = spec
            logger.debug(f"Executor: tool registrato — {spec.name}")

        # Tool CodeRunner (Data Orchestrator) — registrato solo se abilitato
        if getattr(config, 'CODE_RUNNER_ENABLED', False):
            self._register_code_runner_tools()

    def _register_code_runner_tools(self):
        """Registra i tool del CodeRunner (generazione + esecuzione codice)."""
        from agent.code_runner import CodeRunner

        self._code_runner = CodeRunner()

        def _tool_run_code(params: dict, **kwargs) -> ToolResult:
            """Handler per il tool run_code."""
            stop_ev = kwargs.get('stop_event', self.stop_event)
            task = params.get('task', '')
            if not task:
                return ToolResult(success=False, output="Non ho capito cosa devo fare con i file.")

            from core.brain import Brain
            brain = Brain._shared_instance if hasattr(Brain, '_shared_instance') else Brain()

            result = self._code_runner.generate_and_run(
                task=task,
                brain=brain,
                stop_event=stop_ev,
            )
            # artifacts = contenuto fedele dei file prodotti, da iniettare nel
            # contesto LLM (non nel parlato): è la memoria esatta dell'analisi.
            return ToolResult(
                success=result.success,
                output=result.output,
                error=result.error,
                raw_data={"context_extra": result.artifacts} if result.artifacts else {},
            )

        def _tool_read_document(params: dict, **kwargs) -> ToolResult:
            """
            Percorso LETTURA (no code-gen): estrae il testo dai documenti e lo fa
            COMPRENDERE a Gemma. Per "leggi/analizza/estrai dal documento/PDF/
            scheda". run_code resta per "elabora/unisci/calcola/crea".
            """
            from core.brain import Brain
            brain = Brain._shared_instance if hasattr(Brain, '_shared_instance') else Brain()

            input_dir = self._code_runner._input_dir
            if not input_dir.exists() or not any(input_dir.iterdir()):
                return ToolResult(success=False, output="Non ci sono file nella cartella dati.")

            # 1. PDF/DOCX/PPTX/immagini → cascata pre-extract (pypdf → Vision)
            documents = dict(self._code_runner._preextract_files(brain))
            # 2. File testuali strutturati → lettura diretta
            TEXT_EXT = {".csv", ".txt", ".md", ".json", ".tsv"}
            for p in sorted(input_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in TEXT_EXT and p.name not in documents:
                    try:
                        documents[p.name] = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
            if not any((t or "").strip() for t in documents.values()):
                return ToolResult(success=False, output="Non sono riuscito a leggere testo dai documenti.")

            question = params.get("question", "") or ""
            comprehension = brain.read_and_extract(documents, question)

            # context_extra = testo GREZZO dei documenti, iniettato nel contesto
            # (non nel parlato) per il richiamo fedele dei valori esatti nei turn
            # successivi — stesso disaccoppiamento parla/ricorda del fix run_code.
            raw_blob = "\n\n".join(
                f"=== {f} ===\n{t.strip()}" for f, t in documents.items() if t and t.strip()
            )
            return ToolResult(
                success=True,
                output=comprehension,
                raw_data={"context_extra": raw_blob[:6000]} if raw_blob else {},
            )

        def _tool_ingest_documents(params: dict, **kwargs) -> ToolResult:
            """
            INGEST per-documento: legge i file UNO ALLA VOLTA, ne estrae una
            comprensione focalizzata e la SALVA come memoria ancorata (source=teach,
            filename nel contenuto), poi passa al successivo. Scala a N documenti
            senza sfondare il cap del contesto: il richiamo futuro viene dal RAG
            sulla memoria, non da un blob effimero. Per "studia/memorizza i documenti".
            """
            from core.brain import Brain
            brain = Brain._shared_instance if hasattr(Brain, '_shared_instance') else Brain()
            memory = getattr(self, "memory", None)
            stop = kwargs.get("stop_event")

            input_dir = self._code_runner._input_dir
            if not input_dir.exists() or not any(input_dir.iterdir()):
                return ToolResult(success=False, output="Non ci sono file nella cartella dati.")

            documents = dict(self._code_runner._preextract_files(brain))
            TEXT_EXT = {".csv", ".txt", ".md", ".json", ".tsv"}
            for p in sorted(input_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in TEXT_EXT and p.name not in documents:
                    try:
                        documents[p.name] = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
            documents = {f: t for f, t in documents.items() if t and t.strip()}
            if not documents:
                return ToolResult(success=False, output="Non sono riuscito a leggere testo dai documenti.")

            question = params.get("question", "") or ""

            # Dedup per NOME-FILE, non semantico: file diversi (es. ICMA1 vs ICMA2,
            # schede sorelle quasi identiche) vanno salvati entrambi. Re-ingest dello
            # stesso file → skip. (Il dedup semantico globale cannibalizzava i sorelli.)
            already = set()
            if memory is not None:
                try:
                    for k in memory.r.scan_iter("euri:memory:*", count=2000):
                        tg = memory.r.json().get(k, "$.tags")
                        for tag in ((tg[0] if tg else []) or []):
                            if isinstance(tag, str) and "." in tag and not tag.startswith("#"):
                                already.add(tag)
                except Exception:
                    pass

            saved = skipped = 0
            lines = []
            recap_items = []  # (filename, gist) per il recap-in-contesto della sessione
            for fname, text in documents.items():
                if stop is not None and stop.is_set():
                    lines.append("… interrotto.")
                    break
                try:
                    comp = brain.read_and_extract({fname: text}, question)
                except Exception as e:
                    lines.append(f"- {fname}: errore lettura ({e})")
                    continue
                if not comp or not comp.strip():
                    lines.append(f"- {fname}: nessun contenuto")
                    continue
                content = f"[{fname}]\n{comp.strip()}"
                recap_items.append((fname, comp.strip()[:150]))
                if memory is None:
                    lines.append(f"- {fname}: letto (memoria non disponibile)")
                    continue
                if fname in already:
                    skipped += 1
                    lines.append(f"- {fname}: già in memoria (stesso file)")
                    continue
                try:
                    memory.save_memory(content, category="conoscenza",
                                       source="teach", tags=["documento", "ingest", fname])
                    already.add(fname)
                    saved += 1
                    lines.append(f"- {fname}: salvato")
                except Exception as e:
                    lines.append(f"- {fname}: errore salvataggio ({e})")

            head = (f"Ho studiato i documenti uno per uno e li ho archiviati in memoria. "
                    f"Salvati {saved}, già noti {skipped}, su {len(documents)} totali:")
            # Recap compatto iniettato nel contesto di sessione: così i documenti appena
            # studiati sono discutibili SUBITO (Euri sa cosa ha letto) senza dipendere solo
            # dal RAG; i dettagli completi restano in memoria, richiamabili. (Fix recall 01/06.)
            recap = ""
            if recap_items:
                recap = ("Documenti studiati ora in questa sessione (dettagli completi in "
                         "memoria, richiamabili a domanda):\n"
                         + "\n".join(f"- {fn}: {gist}" for fn, gist in recap_items))
            return ToolResult(
                success=True,
                output=head + "\n" + "\n".join(lines),
                raw_data={"context_extra": recap[:4000]} if recap else {},
            )

        def _tool_read_url(params: dict, **kwargs) -> ToolResult:
            """
            Legge una pagina WEB il cui URL è dato ESPLICITAMENTE da Stefano (NON
            navigazione autonoma): fetch del testo + comprensione, iniettata nel
            contesto di sessione. NON salva di default (salva-su-richiesta via
            save_url). Contenuto trattato come fonte esterna/indicativa.
            """
            from core.brain import Brain
            from core.web_search import fetch_page_text
            brain = Brain._shared_instance if hasattr(Brain, '_shared_instance') else Brain()
            text = params.get("url", "") or ""
            m = re.search(r'https?://\S+', text)
            if not m:
                return ToolResult(success=False, output="Non ho trovato un URL nel messaggio. Dammi un link http(s) da leggere.")
            url = m.group(0).rstrip('.,);]\'"')
            page = fetch_page_text(url)
            if not page or not page.strip():
                return ToolResult(success=False, output=f"Non sono riuscito a leggere contenuto da {url} (pagina vuota, protetta o irraggiungibile).")
            question = re.sub(r'https?://\S+', '', text).strip()
            try:
                comp = brain.read_and_extract({url: page}, question)
            except Exception:
                comp = page[:1500]
            self._last_url = url
            self._last_url_text = page
            return ToolResult(
                success=True,
                output=comp,
                raw_data={"context_extra": f"=== PAGINA WEB {url} (fonte esterna, da verificare) ===\n{page[:6000]}"},
            )

        def _tool_save_url(params: dict, **kwargs) -> ToolResult:
            """Salva in memoria l'ULTIMA pagina letta con read_url (salva-su-richiesta).
            source=web → Memory Guard + requires_verification (fonte esterna indicativa)."""
            memory = getattr(self, "memory", None)
            url = getattr(self, "_last_url", None)
            page = getattr(self, "_last_url_text", None)
            if not url or not page:
                return ToolResult(success=False, output="Non ho una pagina letta di recente da salvare. Prima dimmi 'leggi questa pagina [URL]'.")
            if memory is None:
                return ToolResult(success=False, output="Memoria non disponibile.")
            content = f"[pagina web: {url}]\n{page[:4000]}"
            mid = memory.save_memory(content, category="web", source="web", tags=["web", "pagina", url])
            if mid is None:
                return ToolResult(success=False, output="Pagina NON salvata: contenuto sospetto bloccato dal Memory Guard.")
            try:
                memory.r.json().set(f"euri:memory:{mid}", "$.requires_verification", True)
            except Exception:
                pass
            return ToolResult(success=True, output=f"Salvata in memoria la pagina {url} come fonte web (indicativa, da verificare).")

        def _tool_analyze_image(params: dict, **kwargs) -> ToolResult:
            """Handler per analisi immagine via Gemma vision."""
            question = params.get('question', '')
            images = self._code_runner.find_images()
            if not images:
                return ToolResult(
                    success=False,
                    output="Non ho trovato immagini nella cartella dati.",
                )

            from core.brain import Brain
            brain = Brain._shared_instance if hasattr(Brain, '_shared_instance') else Brain()

            # Analizza la prima immagine (o tutte se sono poche)
            if len(images) == 1:
                result = brain.analyze_image(str(images[0]), question)
                return ToolResult(success=True, output=result)
            else:
                # Analizza la più recente
                latest = max(images, key=lambda p: p.stat().st_mtime)
                result = brain.analyze_image(str(latest), question)
                prefix = f"Ho trovato {len(images)} immagini. Analizzo la più recente. "
                return ToolResult(success=True, output=prefix + result)

        def _tool_list_data_files(params: dict, **kwargs) -> ToolResult:
            """Elenca i file nella cartella dati."""
            files = self._code_runner.list_input_files()
            if not files:
                return ToolResult(success=True, output="La cartella dati è vuota.")
            IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
            from pathlib import Path
            img_count = sum(1 for f in files if Path(f).suffix.lower() in IMAGE_EXT)
            other_count = len(files) - img_count
            parts = []
            if img_count:
                parts.append(f"{img_count} immagini")
            if other_count:
                parts.append(f"{other_count} altri file")
            summary = " e ".join(parts)
            hint = " Posso analizzarle con 'analizza immagine'." if img_count and not other_count else ""
            return ToolResult(
                success=True,
                output=f"Nella cartella dati ci sono {summary}.{hint}",
            )

        code_tools = [
            ToolSpec(
                name="run_code",
                description="Genera ed esegue codice Python per elaborare file (CSV, PDF, Excel, immagini). Parametro: task (str) — cosa fare con i file.",
                parameters_schema={"task": {"type": "str", "required": True}},
                handler=_tool_run_code,
                # Tempo totale = pre-extract Vision + code-gen Gemma + execution.
                # V2.18.2: con PDF scansionati o immagini il pre-extract chiama
                # Vision Gemma 4 per ogni pagina (~10s/pagina). Margine ampio.
                timeout_seconds=config.CODE_RUNNER_TOOL_TIMEOUT,
            ),
            ToolSpec(
                name="read_document",
                description="Legge e COMPRENDE un documento (PDF, DOCX, scheda tecnica, CSV, testo) nella cartella dati ed estrae i dati che contiene, senza generare codice. Parametro opzionale: question (str) — cosa cercare nel documento.",
                parameters_schema={"question": {"type": "str", "required": False}},
                handler=_tool_read_document,
                # pre-extract (pypdf, eventuale Vision) + 1 chiamata di comprensione.
                timeout_seconds=config.CODE_RUNNER_TOOL_TIMEOUT,
            ),
            ToolSpec(
                name="ingest_documents",
                description="Studia i documenti nella cartella dati UNO ALLA VOLTA e li salva in memoria a lungo termine (uno per uno, per non perdere dettagli quando i file sono molti). Per 'studia / memorizza / impara dai documenti', 'leggi e salva i file'.",
                parameters_schema={"question": {"type": "str", "required": False}},
                handler=_tool_ingest_documents,
                # N documenti × 1 comprensione Gemma ciascuno → margine ampio.
                timeout_seconds=600,
            ),
            ToolSpec(
                name="read_url",
                description="Legge una pagina web il cui URL è fornito ESPLICITAMENTE da Stefano (es. 'leggi questa pagina https://…') ed estrae i contenuti. NON naviga né cerca da solo. Parametro: url (str) — il messaggio contenente l'URL.",
                parameters_schema={"url": {"type": "str", "required": True}},
                handler=_tool_read_url,
                timeout_seconds=60,
            ),
            ToolSpec(
                name="save_url",
                description="Salva in memoria l'ultima pagina web letta con read_url (come fonte web, da verificare). Per 'salva questa pagina / questo link'.",
                parameters_schema={},
                handler=_tool_save_url,
            ),
            ToolSpec(
                name="analyze_image",
                description="Analizza un'immagine nella cartella dati usando la visione artificiale. Parametro opzionale: question (str) — domanda specifica sull'immagine.",
                parameters_schema={"question": {"type": "str", "required": False}},
                handler=_tool_analyze_image,
                # 60s come clipboard_analyze: la PRIMA chiamata vision a freddo
                # carica Gemma 4 multimodale in VRAM (~35s osservati il 29/05),
                # superando il vecchio cap di 30s. Le successive (caldo) ~5s.
                timeout_seconds=60,
            ),
            ToolSpec(
                name="list_data_files",
                description="Elenca i file presenti nella cartella dati di input (Scrivania/dati_per_Euri).",
                parameters_schema={},
                handler=_tool_list_data_files,
            ),
        ]

        for spec in code_tools:
            self._registry[spec.name] = spec
            logger.debug(f"Executor: tool CodeRunner registrato — {spec.name}")

    def get_tools_description(self) -> str:
        lines = []
        for name, spec in self._registry.items():
            lines.append(f"- {name}: {spec.description}")
        return "\n".join(lines)

    # Patterns ordinati dal più specifico al più generico
    _TOOL_PATTERNS: list[tuple[re.Pattern, str, dict]] = [
        # top_processes by memory
        (re.compile(
            r'\b(processi|app|applicazioni)\b.*\b(memor|ram)\b'
            r'|\b(memor|ram)\b.*\b(processi|app|applicazioni)\b'
            r'|\bconsuma(?:no)?\s+(?:più\s+)?memor'
            r'|\boccupa(?:no)?\s+(?:più\s+)?memor'
            r'|\bpesanti\s+(?:per\s+)?(?:la\s+)?(?:memor|ram)',
            re.IGNORECASE,
        ), "top_processes", {"sort_by": "memory"}),
        # top_processes by cpu
        (re.compile(
            r'\b(processi|app|applicazioni)\b'
            r'|\bconsuma(?:no)?\s+(?:più\s+)?cpu'
            r'|\boccupa(?:no)?\s+(?:più\s+)?cpu'
            r'|\bpesanti\s+(?:per\s+)?(?:la\s+)?cpu',
            re.IGNORECASE,
        ), "top_processes", {"sort_by": "cpu"}),
        # cpu_usage — frasi generiche di sistema mappate qui (tool più rapido come "status overview")
        (re.compile(
            r'\b(cpu|processore|utilizzo\s+del\s+(?:mac|sistema|cpu))\b'
            r'|\b(auto\s*[- ]?check|check\s+(?:del\s+)?sistema'
            r'|controlla\s+(?:il\s+)?sistema|verifica\s+(?:le\s+)?(?:funzionalit|il\s+sistema|il\s+mac)'
            r'|come\s+sta\s+(?:il\s+)?(?:mac|sistema)|stato\s+del\s+sistema)\b',
            re.IGNORECASE,
        ), "cpu_usage", {}),
        # ram_usage
        (re.compile(
            r'\b(ram|memoria\s+(?:libera|usata|totale|disponibile)|quanta\s+memoria|utilizzo\s+della\s+(?:ram|memoria))\b',
            re.IGNORECASE,
        ), "ram_usage", {}),
        # disk_usage — "spazio su disco" anche senza "disco" standalone
        (re.compile(
            r'\b(disco|spazio\s+(?:su\s+disco|sul\s+disco|libero|disponibile)|storage|memoria\s+di\s+massa)\b',
            re.IGNORECASE,
        ), "disk_usage", {}),
        # uptime
        (re.compile(
            r'\b(uptime|da\s+quanto\s+(è\s+)?acceso|quanto\s+tempo\s+è\s+acceso)\b',
            re.IGNORECASE,
        ), "uptime", {}),
        # gpu_usage
        (re.compile(
            r'\b(gpu|grafica|vram|scheda\s+video)\b',
            re.IGNORECASE,
        ), "gpu_usage", {}),
        # read_log
        (re.compile(
            r'\b(log|log\s+di\s+euri|ultime\s+righe\s+del\s+log)\b',
            re.IGNORECASE,
        ), "read_log", {}),
        # clipboard_analyze — PRIMA di clipboard_read (pattern più specifici)
        (re.compile(
            r'\b(analizza|studia|elabora|approfondisci|esamina|riassumi|sintetizza|salva)\s+.*\b(appunti|clipboard)\b',
            re.IGNORECASE,
        ), "clipboard_analyze", {}),
        # clipboard_read — consente parole intermedie tra "leggi" e "clipboard/appunti"
        (re.compile(
            r'\b(cosa\s+c[\'è]\s+negli\s+appunti'
            r'|leggi\s+.{0,25}?(dagli|degli|gli)\s+appunti'
            r'|leggi\s+.{0,25}?(dalla|la)\s+clipboard)\b',
            re.IGNORECASE,
        ), "clipboard_read", {}),
        # ── CodeRunner patterns ──
        # PRIMA: Analisi immagine diretta con Gemma Vision — deve precedere run_code
        # per evitare che "analizza le immagini nella cartella dati" finisca in run_code via "dati"
        (re.compile(
            r'\b(descrivi|guarda|analizza|controlla|visualizza|mostra|esamina)\s+.*(foto|immagin[ei]|screenshot|fotografia)\b'
            r'|\b(cosa\s+vedi|cosa\s+c[\'\`è])\s+.*(foto|immagin[ei]|screenshot)\b',
            re.IGNORECASE,
        ), "analyze_image", {}),
        # Pagina web da URL ESPLICITO (read_url) e salvataggio della pagina (save_url).
        # read_url scatta SOLO se c'è un http(s):// nel messaggio → nessun clash con read_document.
        (re.compile(
            r'\bsalva\b.*\b(pagin|link|url|quello\s+che\s+hai\s+letto|ci[òo]\s+che\s+hai\s+letto)',
            re.IGNORECASE | re.DOTALL,
        ), "save_url", {}),
        (re.compile(
            r'(?=.*https?://)(?=.*\b(leggi|legg|apri|analizz|guard|controll|riassum|studia|esamin|pagin|link|sito|url))',
            re.IGNORECASE | re.DOTALL,
        ), "read_url", {"url": "__USER_TEXT__"}),
        # PRIMA della lettura singola: INGEST per-documento in memoria a lungo termine.
        # "studia/memorizza/impara/archivia i documenti", "leggi e salva i file".
        # Deve precedere read_document, altrimenti "leggi e salva" finirebbe nel read singolo.
        (re.compile(
            r'\b(studia|memorizza|impara|archivia|assimila|acquisisci|incamera)\s+.*\b(document[io]|file|pdf|manual[ei]|sched[ae]|materiale|impiant[oi])\b'
            r'|\b(leggi|carica)\s+e\s+(salva|memorizza|archivia)\b'
            r'|\b(salva|metti)\s+(in|a)\s+memoria\s+.*\b(document[io]|file|pdf|manual[ei]|sched[ae])\b',
            re.IGNORECASE,
        ), "ingest_documents", {}),
        # IN MEZZO: LETTURA/comprensione di un documento (read_document, no code-gen).
        # Verbi di lettura + sostantivo-documento → Gemma LEGGE ed estrae i valori,
        # non scrive un parser. Deve precedere run_code (che ora prende solo i verbi
        # di elaborazione/calcolo). Vedi caso 03PPR100: il code-gen sbagliava colonna.
        (re.compile(
            r'\b(leggi|apri|analizza|esamina|controlla|riassumi|sintetizza|estrai|consulta|guarda|verifica)\s+.*\b(documento|document[io]|pdf|sched[ae]|certificat[oi]|file|foglio|testo|presentazion[ei]|docx|pptx|csv|excel|xlsx|ods|json|relazione|rapporto|report)\b'
            r'|\b(cosa\s+(dice|riporta|c[\'\`è]\s+scritto)|che\s+(dati|valori)\s+ci\s+sono)\b.*\b(documento|pdf|sched[ae]|file|certificat[oi]|csv|excel)\b',
            re.IGNORECASE,
        ), "read_document", {"question": "__USER_TEXT__"}),
        # DOPO: Elaborazione/calcolo/creazione su file (run_code). Solo verbi che
        # TRASFORMANO o PRODUCONO dati — la pura lettura è gestita sopra.
        (re.compile(
            r'\b(unisci|fondi|combina|merge|raggruppa)\s+.*(csv|file|pdf|excel|dati|document[io]|ods|odt|txt|json)\b'
            r'|\b(elabora|processa|converti|trasforma|calcola|filtra|ordina|conta|somma)\s+.*(csv|pdf|excel|dati|document[io]|ods|odt|txt|json|file)\b'
            r'|\b(crea|genera|esporta|salva)\s+.*(csv|file|pdf|excel|grafico|report|tabella|document[io])\b'
            r'|\b(ridimensiona|comprimi|ruota|taglia|converti)\s+.*(foto|immagin[ei])\b',
            re.IGNORECASE,
        ), "run_code", {"task": "__USER_TEXT__"}),   # __USER_TEXT__ sarà sostituito dal voice_daemon
        # Lista file nella cartella dati
        (re.compile(
            r'\b(cosa|quali|quanti)\s+.*(file|document[io]|dati)\s+.*(ci\s+sono|hai|ho|nella|cartella)\b'
            r'|\belenca\s+.*(file|dati|document[io])\b'
            r'|\bcosa\s+c[\'\`è]\s+(nella|in)\s+cartella\b',
            re.IGNORECASE,
        ), "list_data_files", {}),
    ]

    def select_tool_by_regex(self, text: str) -> ToolCall | None:
        """
        Selettore deterministico: evita la chiamata LLM per i tool comuni.
        Ritorna None se nessun pattern corrisponde (si cade sul LLM).
        """
        for pattern, tool_name, params in self._TOOL_PATTERNS:
            if pattern.search(text):
                logger.debug(f"Executor: tool selezionato via regex — {tool_name} {params}")
                return ToolCall(tool_name=tool_name, parameters=dict(params))
        return None

    def parse_llm_response(self, response: str) -> ToolCall | None:
        try:
            # Estrae il JSON dalla risposta anche se c'è testo attorno
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            tool_name = data.get("tool")
            if not tool_name or tool_name == "null":
                return None
            if tool_name not in self._registry:
                logger.warning(f"Executor: tool non in whitelist — {tool_name}")
                return None
            return ToolCall(tool_name=tool_name, parameters=data.get("params", {}))
        except Exception as e:
            logger.error(f"Executor: parse fallito — {e} | risposta: {response[:100]}")
            return None

    def execute(self, call: ToolCall) -> ToolResult:
        spec = self._registry.get(call.tool_name)
        if not spec:
            return ToolResult(success=False, output="Tool non trovato.", error="tool_not_found")

        # Rate limit
        if self._guard.is_rate_limited(call.tool_name, config.EXECUTOR_RATE_LIMIT_PER_MIN):
            return ToolResult(success=False, output="Troppe richieste in poco tempo, aspetta un momento.", error="rate_limited")

        # Validazione parametri
        ok, err_msg = self._guard.validate_parameters(spec, call.parameters)
        if not ok:
            return ToolResult(success=False, output=f"Parametri non validi: {err_msg}", error=err_msg)

        # Esecuzione in thread con timeout
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(
                    spec.handler, call.parameters,
                    stop_event=self.stop_event,
                    brain=getattr(self, "brain", None),
                    memory=getattr(self, "memory", None),
                )
                result = future.result(timeout=spec.timeout_seconds)
            logger.info(f"Executor: {call.tool_name}({call.parameters}) → {'OK' if result.success else 'FAIL'}")
            return result
        except concurrent.futures.TimeoutError:
            logger.error(f"Executor: timeout su {call.tool_name}")
            return ToolResult(success=False, output="Il controllo ha impiegato troppo tempo, riprova.", error="timeout")
        except Exception as e:
            logger.error(f"Executor: eccezione su {call.tool_name} — {e}")
            return ToolResult(success=False, output="Errore interno durante il controllo.", error=str(e))

    def execute_safe(self, call: ToolCall) -> str:
        result = self.execute(call)
        return result.output

    def dispatch_text(self, text: str, llm_fallback: bool = True) -> dict | None:
        """
        Ingresso channel-agnostic (NO TTS): seleziona un tool dal testo
        (regex fast-path → fallback LLM opzionale), lo esegue, inietta il
        risultato FEDELE nella history del Brain e ritorna un dict. Ritorna
        None SOLO se nessun tool corrisponde (il chiamante ricade sulla chat).
        Quando un tool corrisponde ma fallisce/è vuoto, ritorna comunque il dict
        con l'output onesto del tool ("non ci sono file…") — mai sostituirlo con
        una risposta chat, per non riaprire la porta alla confabulazione.

        Stessa logica di selezione/esecuzione di voice_daemon._handle_execute,
        condivisa per non far divergere canale vocale e testuale.
          llm_fallback=False → solo regex (cheap, niente inferenza extra): adatto
          alla Silent Chat, dove va eseguito su ogni messaggio.
        """
        self.stop_event.clear()
        call = self.select_tool_by_regex(text)
        if call is None and llm_fallback and getattr(self, "brain", None) is not None:
            try:
                tools_desc = self.get_tools_description()
                llm_response = self.brain.decide_tool_call(text, tools_desc)
                call = self.parse_llm_response(llm_response)
            except Exception as e:
                logger.debug(f"dispatch_text: fallback LLM fallito — {e}")
                call = None
        if call is None:
            return None

        # Sostituisce __USER_TEXT__ (pattern run_code/read_document) col testo reale
        for _k, _v in call.parameters.items():
            if _v == "__USER_TEXT__":
                call.parameters[_k] = text

        result = self.execute(call)

        # Continuità: inietta il contenuto fedele nella history del Brain, così i
        # turn successivi leggono i valori esatti invece di confabularli.
        if call.tool_name in ("analyze_image", "clipboard_analyze", "run_code", "read_document", "ingest_documents", "read_url") \
                and getattr(self, "brain", None) is not None:
            try:
                self.brain.inject_tool_result(text, build_injected_context(result.output, result.raw_data))
            except Exception as e:
                logger.debug(f"dispatch_text: inject_tool_result fallito — {e}")

        return {
            "tool_name": call.tool_name,
            "output": result.output,
            "raw_data": result.raw_data or {},
            "success": result.success,
        }
