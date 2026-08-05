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
import uuid
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
    effect: str = "local_write"          # contratto per ActionController
    contextual: bool = False             # proponibile da una frase CHAT contestuale


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
        unknown = sorted(set(params) - set(schema))
        if unknown:
            return False, f"Parametri non ammessi: {', '.join(unknown)}"
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
        self._session_artifact_lock = threading.Lock()
        self._session_artifact: dict | None = None
        # Iniettato da UI/Voice quando Redis e' disponibile. Il fallback RAM
        # mantiene Executor utilizzabile nei test puri e in modalita' offline.
        self.document_workspace = None
        self.stop_event = threading.Event()  # interruzione vocale per processi lunghi
        self._register_default_tools()

    def _capture_session_artifact(self, tool_name: str, result: ToolResult) -> None:
        """Conserva la sorgente completa dell'ultimo documento letto nella sessione.

        ``context_extra`` puo' restare corto per il prompt conversazionale; il campo
        separato ``artifact_content`` e' invece la sorgente operativa integra usata
        dai tool documentali successivi.
        """
        raw = result.raw_data or {}
        receipt = raw.get("artifact_receipt")
        workspace = getattr(self, "document_workspace", None)
        if isinstance(receipt, dict) and workspace is not None:
            try:
                workspace.record_receipt(receipt)
            except Exception as exc:
                logger.warning("Workspace documentale: ricevuta non condivisa ({})", exc)

        documents = raw.get("artifact_documents")
        if isinstance(documents, list) and documents:
            active_filename = str(raw.get("artifact_active_filename") or "")
            if workspace is not None:
                try:
                    workspace.publish_documents(
                        documents,
                        active_filename=active_filename,
                        source_channel=str(raw.get("artifact_source_channel") or ""),
                        preserve_existing=bool(
                            raw.get("artifact_preserve_existing", False)
                        ),
                        allowed_existing_paths=list(
                            raw.get("artifact_allowed_source_paths") or []
                        ),
                    )
                except Exception as exc:
                    logger.warning("Workspace documentale: pubblicazione fallita ({})", exc)
            active = next(
                (
                    item for item in documents
                    if str(item.get("filename") or "").casefold()
                    == active_filename.casefold()
                ),
                documents[0] if len(documents) == 1 else None,
            )
            if active:
                active = {
                    **active,
                    "id": str(active.get("id") or f"artifact:{uuid.uuid4()}"),
                    "filenames": [str(active.get("filename") or "documento")],
                    "captured_at": float(active.get("captured_at") or time.time()),
                }
            with self._session_artifact_lock:
                self._session_artifact = dict(active) if active else None
            logger.info(
                "Executor: workspace documentale acquisito ({} file, attivo={})",
                len(documents), active_filename or "ambiguo",
            )
            return

        content = raw.get("artifact_content")
        if not isinstance(content, str) or not content.strip():
            if tool_name in {
                "clipboard_read", "clipboard_analyze", "clipboard_analyze_save",
                "read_document",
            }:
                # Una nuova lettura vuota/fallita invalida la sorgente precedente:
                # e' più sicuro chiedere di rileggere che modificare il file sbagliato.
                with self._session_artifact_lock:
                    self._session_artifact = None
                logger.info(
                    "Executor: artefatto di sessione invalidato dopo {} senza sorgente",
                    tool_name,
                )
            return
        artifact = {
            "id": f"artifact:{uuid.uuid4()}",
            "kind": str(raw.get("artifact_kind") or tool_name),
            "source": str(raw.get("artifact_source") or tool_name),
            "filenames": list(raw.get("artifact_filenames") or []),
            "content": content,
            "captured_at": time.time(),
        }
        with self._session_artifact_lock:
            self._session_artifact = artifact
        if workspace is not None:
            try:
                workspace.publish_documents(
                    [{
                        **artifact,
                        "filename": (
                            artifact["filenames"][0]
                            if len(artifact["filenames"]) == 1
                            else artifact["kind"]
                        ),
                        "source_path": str(raw.get("artifact_path") or ""),
                    }],
                    source_channel=str(raw.get("artifact_source_channel") or tool_name),
                )
            except Exception as exc:
                logger.warning("Workspace documentale: sorgente non condivisa ({})", exc)
        logger.info(
            "Executor: artefatto di sessione acquisito da {} ({} caratteri, id={})",
            tool_name, len(content), artifact["id"].split(":", 1)[-1][:8],
        )

    def get_session_artifact(self, *, max_age_seconds: int = 1800) -> dict | None:
        """Ritorna una copia dell'artefatto recente, senza esporre stato mutabile."""
        workspace = getattr(self, "document_workspace", None)
        if workspace is not None:
            try:
                shared = workspace.get_active(
                    max_age_seconds=max_age_seconds
                )
                if shared:
                    return shared
                # Un manifest valido ma ambiguo e' una decisione esplicita: non
                # riutilizzare l'ultimo documento RAM di questo processo.
                if workspace.snapshot().get("documents"):
                    return None
            except Exception as exc:
                logger.warning("Workspace documentale: lettura fallita ({})", exc)
        with self._session_artifact_lock:
            artifact = dict(self._session_artifact) if self._session_artifact else None
        if not artifact:
            return None
        if time.time() - float(artifact.get("captured_at") or 0) > max_age_seconds:
            return None
        return artifact

    def _recent_document_context(self, max_chars: int = 7000) -> str:
        """Estratto recente utile a risolvere 'applica le modifiche suggerite'."""
        brain = getattr(self, "brain", None)
        if brain is None:
            return ""
        try:
            with brain.history_lock:
                messages = list(brain._conversation_history)[-8:]
            rendered = "\n".join(
                f"{str(item.get('role') or '').upper()}: {item.get('content') or ''}"
                for item in messages
            )
            return rendered[-max_chars:]
        except Exception:
            return ""

    @staticmethod
    def _streamlit_upload_paths(input_dir: Path) -> list[Path]:
        """Coda autorevole dei soli file acquisiti dalla Silent Chat.

        La cartella dati e' un data plane condiviso e puo' contenere file estranei
        al lavoro corrente. Il registro UI, invece, descrive esplicitamente gli
        upload dell'utente e il suo ordine e' la precedenza (ultimo = attivo).
        """
        registry = input_dir / ".silent_chat_uploads.json"
        try:
            entries = json.loads(registry.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(entries, list):
            return []
        try:
            root = input_dir.resolve()
        except Exception:
            return []
        ordered: list[tuple[float, int, Path]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            try:
                path = Path(str(entry.get("path") or "")).expanduser().resolve()
                path.relative_to(root)
            except Exception:
                continue
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                uploaded_at = float(entry.get("uploaded_at") or 0)
            except (TypeError, ValueError):
                uploaded_at = 0.0
            ordered.append((uploaded_at, index, path))
        ordered.sort(key=lambda item: (item[0], item[1]))
        deduped: dict[str, Path] = {}
        for _uploaded_at, _index, path in ordered:
            key = str(path).casefold()
            deduped.pop(key, None)
            deduped[key] = path
        return list(deduped.values())[-12:]

    def resolve_document_format(self, requested: str, instruction: str) -> str:
        """Formato esplicito, altrimenti conserva quello del documento attivo."""
        if requested in {"txt", "docx", "pdf"}:
            return requested
        lowered = str(instruction or "").lower()
        if re.search(r"\b(word|docx)\b", lowered):
            return "docx"
        if re.search(r"\bpdf\b", lowered):
            return "pdf"
        artifact = self.get_session_artifact()
        filename = str(
            (artifact or {}).get("filename")
            or ((artifact or {}).get("filenames") or [""])[0]
            or (artifact or {}).get("source_path")
            or ""
        )
        suffix = Path(filename).suffix.lower().lstrip(".")
        return suffix if suffix in {"txt", "docx", "pdf"} else "txt"

    def _register_default_tools(self):
        from agent.tools.system_monitor import (
            tool_cpu_usage, tool_ram_usage, tool_disk_usage,
            tool_top_processes, tool_uptime, tool_gpu_usage,
        )
        from agent.tools.log_reader import tool_read_log
        from agent.tools.math_eval import tool_evaluate_math
        from agent.tools.text_writer import (
            tool_write_text,
            tool_clipboard_write,
            tool_clipboard_read,
            tool_clipboard_analyze,
            tool_clipboard_analyze_save,
        )

        tools = [
            ToolSpec(
                name="cpu_usage",
                description="Legge l'utilizzo della CPU in percentuale. Parametro opzionale: process_name (str) per filtrare un processo specifico.",
                parameters_schema={"process_name": {"type": "str", "required": False}},
                handler=tool_cpu_usage,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="ram_usage",
                description="Legge l'utilizzo della RAM: totale, usata e libera in gigabyte.",
                parameters_schema={},
                handler=tool_ram_usage,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="disk_usage",
                description="Legge lo spazio disco disponibile. Parametro opzionale: drive (str, es. 'C' o 'D').",
                parameters_schema={"drive": {"type": "str", "required": False, "values": ["C", "D", "E", "F"]}},
                handler=tool_disk_usage,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="top_processes",
                description="Elenca i processi più pesanti. Parametri opzionali: n (int, default 5) e sort_by ('cpu' o 'memory').",
                parameters_schema={
                    "n": {"type": "int", "required": False, "min": 1, "max": 10},
                    "sort_by": {"type": "str", "required": False, "values": ["cpu", "memory"]},
                },
                handler=tool_top_processes,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="uptime",
                description="Legge da quanto tempo è accesa la workstation.",
                parameters_schema={},
                handler=tool_uptime,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="gpu_usage",
                description="Legge l'utilizzo delle GPU NVIDIA: VRAM usata e libera, utilizzo percentuale.",
                parameters_schema={},
                handler=tool_gpu_usage,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="read_log",
                description="Legge le ultime righe del log di Euri. Parametro opzionale: n_lines (int, default 20).",
                parameters_schema={"n_lines": {"type": "int", "required": False, "min": 5, "max": 100}},
                handler=tool_read_log,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="evaluate_math",
                description="USA QUESTO per calcoli numerici. Riceve un'espressione matematica (expression), la valuta e restituisce il risultato. Es: '(450 * 0.15) / 2', 'sqrt(144)', 'round(3.14159, 2)'. NON usare write_text per i calcoli.",
                parameters_schema={"expression": {"type": "str", "required": True}},
                handler=tool_evaluate_math,
                timeout_seconds=2,
                effect="read_only",
                contextual=True,
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
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="clipboard_analyze",
                description="Analizza il testo o l'immagine negli appunti con il LLM per la sessione corrente. Non salva nulla nella memoria permanente.",
                parameters_schema={},
                handler=tool_clipboard_analyze,
                timeout_seconds=60,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="clipboard_analyze_save",
                description="Analizza il testo o l'immagine negli appunti e salva la sintesi nella memoria permanente. Usalo solo se l'utente chiede esplicitamente di salvare o memorizzare.",
                parameters_schema={},
                handler=tool_clipboard_analyze_save,
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

            question = params.get("question", "") or ""
            upload_queue = self._streamlit_upload_paths(input_dir)
            q_fold = question.casefold()
            named_uploads = [
                path for path in upload_queue
                if path.name.casefold() in q_fold
                or (
                    len(path.stem) >= 5
                    and path.stem.casefold() in q_fold
                )
            ]
            shared_active_name = ""
            workspace = getattr(self, "document_workspace", None)
            if workspace is not None:
                try:
                    active = workspace.get_active()
                    shared_active_name = str((active or {}).get("filename") or "")
                except Exception:
                    shared_active_name = ""
            active_upload = next(
                (
                    path for path in upload_queue
                    if path.name.casefold() == shared_active_name.casefold()
                ),
                None,
            )
            # Se il turno arriva dalla UI, il prompt contiene i nomi appena
            # caricati. Nei follow-up vocali senza nome resta attivo il piu'
            # recente della coda Streamlit. Nessun altro file della cartella entra.
            selected_paths = named_uploads or (
                [active_upload]
                if active_upload is not None
                else (upload_queue[-1:] if upload_queue else [])
            )
            scan_paths = selected_paths or [
                path for path in input_dir.iterdir()
                if path.is_file() and not path.name.startswith(".")
            ]

            # 1. PDF/DOCX/PPTX/immagini → cascata pre-extract (pypdf → Vision)
            documents = dict(
                self._code_runner._preextract_files(brain, paths=scan_paths)
            )
            # 2. File testuali strutturati → lettura diretta
            TEXT_EXT = {".csv", ".txt", ".md", ".json", ".tsv"}
            for p in scan_paths:
                if p.is_file() and p.suffix.lower() in TEXT_EXT and p.name not in documents:
                    try:
                        documents[p.name] = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
            if not any((t or "").strip() for t in documents.values()):
                return ToolResult(success=False, output="Non sono riuscito a leggere testo dai documenti.")

            # Ogni file resta un artefatto distinto. La domanda puo' nominare
            # esplicitamente un file; se non lo fa, un solo file e' selezionabile
            # automaticamente, mentre N file restano deliberatamente ambigui.
            selected_names = []
            for filename in documents:
                name_fold = filename.casefold()
                stem_fold = Path(filename).stem.casefold()
                if name_fold in q_fold or (len(stem_fold) >= 5 and stem_fold in q_fold):
                    selected_names.append(filename)
            if not selected_names and len(documents) == 1:
                selected_names = list(documents)
            active_filename = selected_names[-1] if selected_names else ""
            comprehension_docs = (
                {active_filename: documents[active_filename]}
                if active_filename else documents
            )
            comprehension = brain.read_and_extract(comprehension_docs, question)

            # context_extra = testo GREZZO dei documenti, iniettato nel contesto
            # (non nel parlato) per il richiamo fedele dei valori esatti nei turn
            # successivi — stesso disaccoppiamento parla/ricorda del fix run_code.
            raw_blob = "\n\n".join(
                f"=== {f} ===\n{t.strip()}"
                for f, t in comprehension_docs.items() if t and t.strip()
            )
            from core.document_workspace import DocumentWorkspace
            artifact_documents = []
            for filename, text in documents.items():
                if not text or not text.strip():
                    continue
                path = input_dir / filename
                artifact_documents.append(DocumentWorkspace.file_document(path, text))
            ambiguity_note = ""
            if len(artifact_documents) > 1 and not active_filename:
                ambiguity_note = (
                    " Ho registrato i file separatamente, ma prima di modificarne uno "
                    "devi indicarmi quale documento vuoi usare."
                )
            return ToolResult(
                success=True,
                output=comprehension + ambiguity_note,
                raw_data={
                    "context_extra": raw_blob[:6000],
                    "artifact_documents": artifact_documents,
                    "artifact_active_filename": active_filename,
                    "artifact_source_channel": (
                        "silent_chat" if upload_queue else "read_document"
                    ),
                    "artifact_preserve_existing": bool(upload_queue),
                    "artifact_allowed_source_paths": [
                        str(path.resolve()) for path in upload_queue
                    ],
                } if raw_blob else {},
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

        def _tool_teach_text(params: dict, **kwargs) -> ToolResult:
            """
            Il proprietario INSEGNA esplicitamente un testo/elenco INCOLLATO in chat
            ("memorizza questo: …", "impara quanto segue: …", "tieni a mente: …") e
            lo salva come memoria PERMANENTE (source=teach): niente TTL, intoccabile
            dai cicli notturni. È il gemello di ingest_documents — che però studia i
            FILE nella cartella — per il testo battuto/incollato al volo. Chiude il
            buco per cui un elenco incollato finiva in chat (passive, 90gg) mentre
            Euri dichiarava di averlo salvato "permanentemente": qui il salvataggio
            è reale e la conferma scatta solo se è andato a buon fine.
            """
            memory = getattr(self, "memory", None)
            if memory is None:
                return ToolResult(success=False, output="Memoria non disponibile: non posso salvare adesso.")
            raw = params.get("text", "") or ""
            # Toglie la frase-comando iniziale e tiene il CONTENUTO da imparare.
            # Caso 1: c'è il due punti ("memorizza questo: <testo>") → taglia fino ai ":".
            body = re.sub(
                r'^\s*(memorizza|impara|imprimiti|assimila|tieni\s+a\s+mente|segnati|annota|prendi\s+nota)\b'
                r'[^:\n]{0,40}?:\s*',
                '', raw, count=1, flags=re.IGNORECASE)
            # Caso 2: nessun due punti → toglie il comando + eventuali determinanti e
            # nomi generici ("queste informazioni", "questi dati", "la lista") finché
            # non resta il contenuto vero. Se l'utente scrive solo il comando senza
            # incollare nulla ("memorizza queste informazioni"), il corpo collassa a
            # vuoto e sotto scatta l'avviso "manca il testo" invece di salvare un guscio.
            if body == raw:
                body = re.sub(
                    r'^\s*(memorizza|impara|imprimiti|assimila|tieni\s+a\s+mente|segnati|annota|prendi\s+nota)\b'
                    r"(\s+(quest[oaei]|quei|quegli|quell[ao]|il|i|lo|gli|la|le)\b|\s+l['’])*"
                    r"(\s+(seguent[ei]|informazion[ei]|info|dat[oi]|cos[ae]|roba|lista|elenco|testo|nota|appunt[oi]|nozion[ei])\b|\s+quanto\s+segue\b)*"
                    r'\s*', '', raw, count=1, flags=re.IGNORECASE)
            body = body.strip()
            # Ripulisce punteggiatura e vocativo iniziali ("memorizza questo, Euri, …"
            # lasciava ", Euri, …"): toglie virgole/spazi e un eventuale "Euri" iniziale.
            body = re.sub(r"^[\s,;:.–—-]*(euri\b[\s,;:.–—-]*)?", "",
                          body, count=1, flags=re.IGNORECASE).strip()
            if len(body) < 3:
                return ToolResult(success=False, output=(
                    "Non ho trovato il contenuto da memorizzare dopo il comando. "
                    "Scrivilo così: «memorizza questo: …» seguito dal testo o dall'elenco."))
            mid = memory.save_memory(body, category="conoscenza", source="teach",
                                     tags=["teach", "insegnamento-testo"])
            if mid is None:
                return ToolResult(success=False, output=(
                    "Contenuto NON salvato: il Memory Guard l'ha bloccato come sospetto."))
            preview = body[:140].replace("\n", " ")
            ell = "…" if len(body) > 140 else ""
            return ToolResult(
                success=True,
                output=(f"Memorizzato in modo permanente — non scadrà e i cicli notturni "
                        f"non lo toccheranno: «{preview}{ell}»"),
                raw_data={"context_extra": f"=== APPRESO ORA (permanente, source=teach) ===\n{body[:6000]}"},
            )

        def _tool_read_url(params: dict, **kwargs) -> ToolResult:
            """
            Legge una pagina WEB il cui URL è dato ESPLICITAMENTE dall'utente (NON
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
            mid = memory.save_memory(
                content,
                category="web",
                source="web",
                tags=["web", "pagina", url],
                final_fields={"requires_verification": True},
            )
            if mid is None:
                return ToolResult(success=False, output="Pagina NON salvata: contenuto sospetto bloccato dal Memory Guard.")
            return ToolResult(success=True, output=f"Salvata in memoria la pagina {url} come fonte web (indicativa, da verificare).")

        def _tool_run_eval(params: dict, **kwargs) -> ToolResult:
            """Lancia l'INTERA suite di benchmark (scripts/eval.py: calibrazione + recall)
            in un subprocess isolato e riporta il punteggio. Pesante (~18 chiamate al
            modello, alcuni minuti, contende la GPU col daemon): on-demand."""
            import subprocess
            import os as _os
            import sys as _sys
            from pathlib import Path as _Path
            repo = _Path(__file__).resolve().parents[1]
            script = repo / "scripts" / "eval.py"
            if not script.exists():
                return ToolResult(success=False, output="Non trovo scripts/eval.py.")
            try:
                env = {**_os.environ, "PYTHONPATH": str(repo)}
                proc = subprocess.run(
                    [_sys.executable, str(script)],
                    cwd=str(repo), env=env, capture_output=True, text=True, timeout=900,
                )
                out = proc.stdout or ""
                lines = [ln for ln in out.splitlines()
                         if any(k in ln for k in ("PASS", "FAIL", "WARN", "PUNTEGGIO", "TOTALE", "#  "))]
                summary = ("\n".join(lines))[-3500:] or out[-3500:] or (proc.stderr or "")[-1000:]
                return ToolResult(success=True, output="Risultato della suite di eval:\n" + summary)
            except subprocess.TimeoutExpired:
                return ToolResult(success=False, output="La suite di eval ha impiegato troppo (timeout 900s).")
            except Exception as e:
                return ToolResult(success=False, output=f"Errore nel lancio degli eval: {e}")

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

        def _tool_compose_document(params: dict, **kwargs) -> ToolResult:
            """Crea un file reale a partire dall'ultima sorgente letta in sessione."""
            from agent.tools.document_composer import compose_document_tool
            return compose_document_tool(
                params,
                artifact=self.get_session_artifact(),
                recent_context=self._recent_document_context(),
                brain=getattr(self, "brain", None),
                output_dir=Path(config.CODE_RUNNER_OUTPUT_DIR),
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
                description="Legge e COMPRENDE il documento attivo caricato dalla UI (PDF, DOCX, scheda tecnica, CSV, testo) ed estrae i dati che contiene, senza generare codice. Se non esiste una coda UI usa il percorso legacy della cartella dati. Parametro opzionale: question (str) — cosa cercare nel documento.",
                parameters_schema={"question": {"type": "str", "required": False}},
                handler=_tool_read_document,
                # pre-extract (pypdf, eventuale Vision) + 1 chiamata di comprensione.
                timeout_seconds=config.CODE_RUNNER_TOOL_TIMEOUT,
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="compose_document",
                description=(
                    "Revisiona DAVVERO il documento attivo condiviso fra UI e voce, oppure "
                    "crea un nuovo documento strutturato dagli appunti. Su un DOCX sorgente "
                    "modifica una copia preservando layout e struttura; produce TXT, Word o "
                    "PDF verificati in Scrivania/scambio_dati e li rende scaricabili nella UI. "
                    "Parametri: instruction (richiesta completa), format (txt, docx o pdf), "
                    "filename opzionale."
                ),
                parameters_schema={
                    "instruction": {"type": "str", "required": True},
                    "format": {
                        "type": "str", "required": False,
                        "values": ["txt", "docx", "pdf"],
                    },
                    "filename": {"type": "str", "required": False},
                },
                handler=_tool_compose_document,
                timeout_seconds=180,
                effect="local_write",
                contextual=True,
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
                name="teach_text",
                description=f"Salva in memoria a lungo termine (permanente) un testo o un elenco che {config.OWNER_DISPLAY_NAME} INCOLLA in chat. Per 'memorizza questo: …', 'impara quanto segue: …', 'tieni a mente: …'. Gemello di ingest_documents ma per testo incollato al volo, non per file.",
                parameters_schema={"text": {"type": "str", "required": True}},
                handler=_tool_teach_text,
            ),
            ToolSpec(
                name="read_url",
                description=f"Legge una pagina web il cui URL è fornito ESPLICITAMENTE da {config.OWNER_DISPLAY_NAME} (es. 'leggi questa pagina https://…') ed estrae i contenuti. NON naviga né cerca da solo. Parametro: url (str) — il messaggio contenente l'URL.",
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
                name="run_eval",
                description="Lancia l'intera suite di benchmark di Euri (calibrazione + recall) e riporta il punteggio. Per 'eval' / 'lancia gli eval' / 'auto-test'. Operazione pesante, alcuni minuti.",
                parameters_schema={},
                handler=_tool_run_eval,
                timeout_seconds=900,
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
                effect="read_only",
                contextual=True,
            ),
            ToolSpec(
                name="list_data_files",
                description="Elenca i file presenti nella cartella dati di input (Scrivania/dati_per_Euri).",
                parameters_schema={},
                handler=_tool_list_data_files,
                effect="read_only",
                contextual=True,
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

    def get_contextual_capabilities(self) -> list[dict]:
        """Catalogo read-only/whitelist per il ponte intenzione→azione.

        Gli handler restano nell'Executor: il controller vede soltanto nome,
        descrizione, schema ed effetto e non puo' inventare nuovi tool.
        """
        return [
            {
                "name": name,
                "description": spec.description,
                "parameters_schema": spec.parameters_schema,
                "effect": spec.effect,
                "requires_confirm": spec.requires_confirm,
            }
            for name, spec in self._registry.items()
            if spec.contextual
        ]

    def document_action_state_context(self) -> str:
        """Stato operativo esposto al controller, non alla memoria cognitiva."""
        workspace = getattr(self, "document_workspace", None)
        if workspace is None:
            return ""
        try:
            snapshot = workspace.snapshot()
        except Exception:
            return ""
        documents = list(snapshot.get("documents") or [])
        operation = dict(snapshot.get("operation") or {})
        state_lines = []
        if operation:
            status = str(operation.get("status") or "")
            channel = str(operation.get("source_channel") or "sconosciuto")
            filename = str(operation.get("filename") or "documento")
            tool_name = str(operation.get("tool_name") or "")
            if status == "running":
                state_lines.append(
                    f"- operazione_documentale: IN_CORSO sul canale {channel}; "
                    f"file={filename}; tool={tool_name or 'in selezione'}. "
                    "Se rispondi da un altro canale, descrivi il lavoro come eseguito "
                    "dalla UI/canale indicato: non affermare di averlo già completato."
                )
            elif status in {"completed", "failed"}:
                outcome = "COMPLETATA" if status == "completed" else "FALLITA"
                message = str(operation.get("message") or "")[:300]
                state_lines.append(
                    f"- operazione_documentale: {outcome} sul canale {channel}; "
                    f"file={filename}; tool={tool_name or 'non indicato'}; esito={message or '-'}"
                )
        if not documents:
            return "\n".join(state_lines)
        active_id = str(snapshot.get("active_artifact_id") or "")
        active = next(
            (
                item for item in documents
                if str(item.get("id") or "") == active_id
            ),
            None,
        )
        if active is None:
            state_lines.append(
                f"- workspace_documenti: {len(documents)} documenti, nessuno attivo; "
                "serve una selezione prima di modificarne uno"
            )
            return "\n".join(state_lines)
        queue = ", ".join(str(item.get("filename") or "documento") for item in documents)
        state_lines.append(
            f"- workspace_documenti: documento attivo={active.get('filename')} | "
            f"origine={snapshot.get('source_channel') or 'sconosciuta'} | coda={queue}. "
            "I riferimenti 'questo documento'/'il documento' indicano il documento "
            "attivo; non indicano la clipboard."
        )
        return "\n".join(state_lines)

    def dispatch_contextual_action(
        self,
        text: str,
        *,
        previous_euri_turn: str = "",
        controller=None,
    ) -> dict:
        """Percorso semantico channel-agnostic per un REQUEST_ACTION gia' accertato.

        E' usato dalla Silent Chat, che possiede gia' il frame semantico ma non il
        daemon vocale. Nessuna regex decide la capability: il controller puo'
        scegliere soltanto dal catalogo Executor e la policy rivalida l'effetto.
        Qualunque incertezza ritorna un esito fail-closed, mai una risposta CHAT.
        """
        from core.action_controller import (
            ActionController, ActionDisposition, build_capability_snapshot,
        )

        action_controller = controller or ActionController()
        capabilities, state_context, targets = build_capability_snapshot(
            [], self.get_contextual_capabilities()
        )
        document_state = self.document_action_state_context()
        state_context = "\n".join(
            part for part in (state_context, document_state) if part
        )
        proposal = action_controller.propose(
            text,
            previous_euri_turn=previous_euri_turn,
            capabilities=capabilities,
            state_context=state_context,
            targets_by_id=targets,
        )
        decision = action_controller.decide(proposal, capabilities)
        if decision.disposition != ActionDisposition.EXECUTE or proposal is None:
            logger.info(
                "Executor contextual: {} cap={} reason={}",
                decision.disposition.value,
                proposal.capability if proposal else "-",
                decision.reason,
            )
            return {
                "tool_name": proposal.capability if proposal else "",
                "output": (
                    "Non ho eseguito nulla: ho capito la richiesta operativa, ma "
                    "non riesco a collegarla con sufficiente certezza a uno strumento reale."
                ),
                "raw_data": {},
                "success": False,
                "fail_closed": True,
            }

        tool_name = proposal.capability.removeprefix("executor.")
        parameters = dict(proposal.args)
        if tool_name == "compose_document":
            parameters["instruction"] = text
            parameters["format"] = self.resolve_document_format(
                str(parameters.get("format") or ""), text
            )
        result = self.execute(ToolCall(tool_name=tool_name, parameters=parameters))
        if getattr(self, "brain", None) is not None:
            try:
                self.brain.inject_tool_result(
                    text, build_injected_context(result.output, result.raw_data)
                )
            except Exception as exc:
                logger.debug(f"dispatch_contextual_action: inject fallito — {exc}")
        return {
            "tool_name": tool_name,
            "output": result.output,
            "raw_data": result.raw_data or {},
            "success": result.success,
        }

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
            r'\b(processi|app|applicazioni)\b.{0,40}\b(pesanti|attiv[ie]|apert[ie]|girando|esecuzione|cpu|sistema|pc|workstation|linux)\b'
            r'|\b(che|quali|mostra|controlla|verifica)\b.{0,25}\b(processi|app|applicazioni)\b.{0,40}\b(pesanti|attiv[ie]|apert[ie]|girando|esecuzione|cpu|sistema|pc|workstation|linux)\b'
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
        # read_log — richiede un verbo vicino a "log" (non la parola "log" nuda, che
        # compariva in frasi normali tipo "il MES, i log..." facendo partire il tool).
        (re.compile(
            r'\b(leggi|mostra|fammi\s+veder|vedi|controlla|guarda|apri|stampa)\b[^.\n]{0,25}\blog\b'
            r'|\bultim[ei]\s+(righe\s+del\s+log|errori)\b'
            r'|\blog\s+di\s+euri\b',
            re.IGNORECASE,
        ), "read_log", {}),
        # Salvataggio clipboard: deve essere ESPLICITO e precedere l'analisi temporanea.
        (re.compile(
            r'\b(salva|memorizza|ricorda)\s+.*\b(appunti|clipboard)\b'
            r'|\b(analizza|studia|elabora|approfondisci|esamina|riassumi|sintetizza)\b'
            r'.*\b(salva|memorizza)\b.*\b(appunti|clipboard)\b'
            r'|\b(analizza|studia|elabora|approfondisci|esamina|riassumi|sintetizza)\b'
            r'.*\b(appunti|clipboard)\b.*\b(salva|memorizza)\b',
            re.IGNORECASE,
        ), "clipboard_analyze_save", {}),
        # Analisi temporanea — PRIMA di clipboard_read (pattern più specifico).
        (re.compile(
            r'\b(analizza|studia|elabora|approfondisci|esamina|riassumi|sintetizza)\s+.*\b(appunti|clipboard)\b',
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
        # Suite di eval/benchmark: la parola "eval" (o "lancia gli eval", "auto-test") la avvia.
        (re.compile(
            r'\beval\b|\bbenchmark\b|\bauto-?test\b|\bautodiagnosi\b'
            r'|\b(lancia|esegui|gira|fai|avvia)\b.{0,25}\b(eval|test|benchmark)\b',
            re.IGNORECASE,
        ), "run_eval", {}),
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
        # teach_text — Stefano INSEGNA un testo/elenco INCOLLATO in chat → salvataggio
        # PERMANENTE (source=teach). Trigger esplicito ("memorizza questo:", "impara
        # quanto segue:", "tieni a mente:") per non confondersi con la chat normale.
        # DOPO ingest_documents: se c'è un sostantivo-file ("memorizza i documenti")
        # vince l'ingest; senza, il testo incollato finisce qui invece che nel limbo
        # passive (90gg). Bypassa la guardia 300-char in dispatch_text: insegnare un
        # elenco lungo è proprio il suo caso d'uso (vedi guardia).
        (re.compile(
            r'\b(memorizza|impara|imprimiti|assimila|tieni\s+a\s+mente|segnati|annota|prendi\s+nota)\b'
            r"[^.\n]{0,30}?(\bquest[oae]\b|\bquanto\s+segue\b|\bil\s+seguente\b|\bi\s+seguenti\b|\bla\s+lista\b|\bl['’]elenco\b|:)",
            re.IGNORECASE,
        ), "teach_text", {"text": "__USER_TEXT__"}),
        # Spreadsheet: anche quando l'utente dice "leggi", il formato non è testo
        # lineare. Passa dal CodeRunner/pandas invece del lettore documentale.
        (re.compile(
            r'\b(leggi|apri|analizza|esamina|controlla|riassumi|sintetizza|estrai|consulta|guarda|verifica)\s+.*\b(excel|xlsx|xls|ods|fogli[oa](\s+di\s+calcolo)?|spreadsheet)\b'
            r'|\b(cosa\s+(dice|riporta|c[\'\`è]\s+scritto)|che\s+(dati|valori)\s+ci\s+sono)\b.*\b(excel|xlsx|xls|ods|fogli[oa](\s+di\s+calcolo)?|spreadsheet)\b',
            re.IGNORECASE,
        ), "run_code", {"task": "__USER_TEXT__"}),
        # IN MEZZO: LETTURA/comprensione di un documento (read_document, no code-gen).
        # Verbi di lettura + sostantivo-documento → Gemma LEGGE ed estrae i valori,
        # non scrive un parser. Deve precedere run_code (che ora prende solo i verbi
        # di elaborazione/calcolo). Vedi caso 03PPR100: il code-gen sbagliava colonna.
        (re.compile(
            r'\b(leggi|apri|analizza|esamina|controlla|riassumi|sintetizza|estrai|consulta|guarda|verifica)\s+.*\b(documento|document[io]|pdf|sched[ae]|certificat[oi]|file|testo|presentazion[ei]|docx|pptx|csv|json|relazione|rapporto|report)\b'
            r'|\b(cosa\s+(dice|riporta|c[\'\`è]\s+scritto)|che\s+(dati|valori)\s+ci\s+sono)\b.*\b(documento|pdf|sched[ae]|file|certificat[oi]|csv)\b',
            re.IGNORECASE,
        ), "read_document", {"question": "__USER_TEXT__"}),
        # DOPO: Elaborazione/calcolo/creazione su file (run_code). Solo verbi che
        # TRASFORMANO o PRODUCONO dati — la pura lettura è gestita sopra.
        (re.compile(
            r'\b(unisci|fondi|combina|merge|raggruppa)\s+.*(csv|file|pdf|excel|xlsx|xls|dati|document[io]|ods|odt|txt|json)\b'
            r'|\b(elabora|processa|converti|trasforma|calcola|filtra|ordina|conta|somma)\s+.*(csv|pdf|excel|xlsx|xls|dati|document[io]|ods|odt|txt|json|file)\b'
            r'|\b(crea|genera|esporta|salva)\s+.*(csv|file|pdf|excel|xlsx|xls|grafico|report|tabella|document[io])\b'
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
        # "Analizza gli appunti senza salvarli" nega soltanto la persistenza,
        # non l'analisi. Va risolto prima della guardia generale, che altrimenti
        # potrebbe interpretare "senza salvare" come negazione dell'intero tool.
        clipboard_analysis = re.search(
            r'\b(analizza|studia|elabora|approfondisci|esamina|riassumi|sintetizza)\b'
            r'.*\b(appunti|clipboard)\b',
            text,
            re.IGNORECASE,
        )
        persistence_negated = re.search(
            r'\b(?:senza|non)\s+(?:salvar\w*|memorizzar\w*|ricordar\w*)\b',
            text,
            re.IGNORECASE,
        )
        if clipboard_analysis and persistence_negated:
            return ToolCall(tool_name="clipboard_analyze", parameters={})

        if self._is_negated_tool_request(text):
            return None
        for pattern, tool_name, params in self._TOOL_PATTERNS:
            if pattern.search(text):
                logger.debug(f"Executor: tool selezionato via regex — {tool_name} {params}")
                return ToolCall(tool_name=tool_name, parameters=dict(params))
        return None

    @staticmethod
    def _is_negated_tool_request(text: str) -> bool:
        """Blocca trigger tool esplicitamente negati: 'non leggere il log', 'non cercare online'."""
        return bool(re.search(
            r'\b(non|senza)\s+.{0,20}\b('
            r'leggere?|aprire?|analizzare?|studiare?|controllare?|verificare?|'
            r'cercare?|calcolare?|scrivere?|salvare?|copiare?|mostrare?|'
            r'processare?|elaborare?|eseguire?|lanciare?|avviare?'
            r')\b',
            text,
            re.IGNORECASE,
        ))

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

        # Le operazioni documentali sono visibili anche all'altro processo mentre
        # sono in corso. Un upload UI può aver pubblicato il lavoro prima che il
        # controller abbia finito di scegliere il tool: in quel caso lo prendiamo
        # in carico invece di aprire una seconda operazione.
        document_operation_id = ""
        workspace = getattr(self, "document_workspace", None)
        if call.tool_name in {"read_document", "compose_document"} and workspace is not None:
            try:
                current = workspace.get_operation(max_age_seconds=300)
                if (
                    call.tool_name == "read_document"
                    and current
                    and current.get("status") == "running"
                    and current.get("kind") == "document_analysis"
                    and not current.get("tool_name")
                ):
                    document_operation_id = str(current.get("id") or "")
                    workspace.claim_operation(
                        document_operation_id, tool_name=call.tool_name
                    )
                else:
                    active = workspace.get_active()
                    operation = workspace.start_operation(
                        call.tool_name,
                        source_channel=str(
                            getattr(self, "operation_channel", "executor") or "executor"
                        ),
                        filename=str((active or {}).get("filename") or ""),
                        tool_name=call.tool_name,
                    )
                    document_operation_id = str(operation.get("id") or "")
            except Exception as exc:
                logger.warning("Workspace documentale: stato operazione non avviato ({})", exc)

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
            self._capture_session_artifact(call.tool_name, result)
            if document_operation_id and workspace is not None:
                workspace.finish_operation(
                    document_operation_id,
                    success=bool(result.success),
                    message=result.output,
                )
            logger.info(f"Executor: {call.tool_name}({call.parameters}) → {'OK' if result.success else 'FAIL'}")
            return result
        except concurrent.futures.TimeoutError:
            logger.error(f"Executor: timeout su {call.tool_name}")
            if document_operation_id and workspace is not None:
                workspace.finish_operation(
                    document_operation_id,
                    success=False,
                    message="Timeout durante l'operazione documentale.",
                )
            return ToolResult(success=False, output="Il controllo ha impiegato troppo tempo, riprova.", error="timeout")
        except Exception as e:
            logger.error(f"Executor: eccezione su {call.tool_name} — {e}")
            if document_operation_id and workspace is not None:
                workspace.finish_operation(
                    document_operation_id,
                    success=False,
                    message="Errore interno durante l'operazione documentale.",
                )
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
        if not text:
            return None
        call = self.select_tool_by_regex(text)
        # Guardia anti-falso-positivo: un comando-tool è una frase BREVE ("leggi il log",
        # "studia i documenti", "eval"). Un messaggio lungo è chat o insegnamento e NON va
        # instradato a un tool anche se contiene una parola-trigger ("log", "file", "dati"…).
        # Senza questo, una spiegazione di 3000 char con dentro "i log" faceva partire read_log.
        # ECCEZIONE: teach_text con trigger esplicito ("memorizza questo: <elenco>") È
        # proprio l'insegnamento di un testo lungo incollato → deve passare la guardia.
        if len(text) > 300 and not (call is not None and call.tool_name == "teach_text"):
            return None
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
        if call.tool_name in ("analyze_image", "clipboard_read", "clipboard_analyze", "clipboard_analyze_save", "run_code", "read_document", "ingest_documents", "read_url", "teach_text", "compose_document") \
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
