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
        from agent.tools.text_writer import tool_write_text, tool_clipboard_write, tool_clipboard_read

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
            return ToolResult(
                success=result.success,
                output=result.output,
                error=result.error,
            )

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
                prefix = f"Ho trovato {len(images)} immagini. Analizzo la più recente, {latest.name}. "
                return ToolResult(success=True, output=prefix + result)

        def _tool_list_data_files(params: dict, **kwargs) -> ToolResult:
            """Elenca i file nella cartella dati."""
            files = self._code_runner.list_input_files()
            if not files:
                return ToolResult(success=True, output="La cartella dati è vuota.")
            file_list = ", ".join(files[:10])
            extra = f" e altri {len(files) - 10}" if len(files) > 10 else ""
            return ToolResult(
                success=True,
                output=f"Nella cartella dati ci sono {len(files)} file: {file_list}{extra}.",
            )

        code_tools = [
            ToolSpec(
                name="run_code",
                description="Genera ed esegue codice Python per elaborare file (CSV, PDF, Excel, immagini). Parametro: task (str) — cosa fare con i file.",
                parameters_schema={"task": {"type": "str", "required": True}},
                handler=_tool_run_code,
                timeout_seconds=config.CODE_RUNNER_TIMEOUT + 10,  # margine per generazione
            ),
            ToolSpec(
                name="analyze_image",
                description="Analizza un'immagine nella cartella dati usando la visione artificiale. Parametro opzionale: question (str) — domanda specifica sull'immagine.",
                parameters_schema={"question": {"type": "str", "required": False}},
                handler=_tool_analyze_image,
                timeout_seconds=30,
            ),
            ToolSpec(
                name="list_data_files",
                description="Elenca i file presenti nella cartella dati di input (Desktop/dati_per_Euri).",
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
        # clipboard_read
        (re.compile(
            r'\b(appunti|clipboard|cosa\s+c[\'è]\s+negli\s+appunti|leggi\s+gli\s+appunti)\b',
            re.IGNORECASE,
        ), "clipboard_read", {}),
        # ── CodeRunner patterns ──
        # Operazioni su file/dati
        (re.compile(
            r'\b(unisci|fondi|combina|merge)\s+.*(csv|file|pdf|excel|dati)\b'
            r'|\b(leggi|apri|elabora|processa|converti|trasforma)\s+.*(csv|file|pdf|excel|dati)\b'
            r'|\b(crea|genera|esporta)\s+.*(csv|file|pdf|excel|grafico|report)\b'
            r'|\b(ridimensiona|comprimi|ruota|taglia)\s+.*(foto|immagin[ei])\b'
            r'|\bcartella\s+dati\b',
            re.IGNORECASE,
        ), "run_code", {}),
        # Analisi immagine diretta (Gemma vision)
        (re.compile(
            r'\b(descrivi|analizza|guarda|cosa\s+vedi)\s+.*(foto|immagin[ei]|screenshot)\b'
            r'|\b(analizza|descrivi)\s+.*(nella|dalla)\s+cartella\b',
            re.IGNORECASE,
        ), "analyze_image", {}),
        # Lista file nella cartella dati
        (re.compile(
            r'\b(cosa|quali|quanti)\s+.*(file|document[io]|dati)\s+.*(ci\s+sono|hai|ho|nella|cartella)\b'
            r'|\belenca\s+.*(file|dati)\b',
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
                # Passa stop_event ai tool che lo supportano (es. run_code)
                future = ex.submit(spec.handler, call.parameters, stop_event=self.stop_event)
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
