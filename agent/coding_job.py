"""Job temporaneo OpenCode -> CodeRunner per strumenti di calcolo ad hoc."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger

import config
from agent.code_runner import CodeResult, CodeRunner
from agent.coding_research_log import capture_coding_attempt
from agent.opencode_adapter import OpenCodeAdapter, OpenCodeError


@dataclass
class CodingAttempt:
    number: int
    code_sha256: str = ""
    success: bool = False
    exit_code: int = -1
    error: str = ""
    diagnostic_log: str = ""


@dataclass
class CodingJobResult:
    success: bool
    output: str
    error: str | None = None
    job_id: str = ""
    attempts: list[CodingAttempt] = field(default_factory=list)
    artifacts: str = ""
    code_sha256: str = ""
    model: str = ""
    workspace_kept: str = ""

    def trace(self) -> dict:
        return {
            "job_id": self.job_id,
            "attempts": [asdict(item) for item in self.attempts],
            "code_sha256": self.code_sha256,
            "model": self.model,
            "workspace_kept": self.workspace_kept,
        }


class CodingJob:
    """Costruisce, esegue e corregge un programma con budget espliciti."""

    def __init__(
        self,
        *,
        code_runner: CodeRunner | None = None,
        adapter_factory: Callable[..., OpenCodeAdapter] = OpenCodeAdapter,
        workspace_root: str | Path | None = None,
        max_attempts: int | None = None,
        no_artifact_timeout: int | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        diagnostic_recorder: Callable[..., str] = capture_coding_attempt,
    ):
        self.code_runner = code_runner or CodeRunner()
        self.adapter_factory = adapter_factory
        self.workspace_root = Path(
            workspace_root or config.CODE_AGENT_WORKSPACE_ROOT
        ).resolve()
        self.max_attempts = int(max_attempts or config.CODE_AGENT_MAX_ATTEMPTS)
        self.no_artifact_timeout = int(
            no_artifact_timeout or config.CODE_AGENT_NO_ARTIFACT_TIMEOUT
        )
        self.progress_callback = progress_callback
        self.diagnostic_recorder = diagnostic_recorder
        self._progress_started = 0.0

    @staticmethod
    def _session_snapshot(adapter) -> dict:
        if not hasattr(adapter, "session_snapshot"):
            return {"available": False, "reason": "adapter_has_no_snapshot"}
        try:
            return adapter.session_snapshot()
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    def _record_attempt(
        self,
        *,
        adapter,
        job_id: str,
        attempt: int,
        model: str,
        request_prompt: str,
        reply,
        outcome: str,
        artifact_path: Path,
        execution: CodeResult | None = None,
    ) -> str:
        try:
            execution_record = {}
            if execution is not None:
                execution_record = {
                    "success": bool(execution.success),
                    "exit_code": int(execution.exit_code),
                    "output": str(execution.output or ""),
                    "error": str(execution.error or ""),
                    "stdout_chars": execution.stdout_chars,
                    "artifacts_chars": len(str(execution.artifacts or "")),
                }
            return self.diagnostic_recorder(
                job_id=job_id,
                attempt=attempt,
                model=model,
                request_prompt=request_prompt,
                reply_text=str(getattr(reply, "text", "") or ""),
                reply_raw=getattr(reply, "raw", {}),
                session_snapshot=self._session_snapshot(adapter),
                outcome=outcome,
                artifact_path=artifact_path,
                execution=execution_record,
            )
        except Exception as exc:
            logger.warning("CodingJob {}: audit tentativo {} fallito ({})", job_id, attempt, exc)
            return ""

    def _emit(self, phase: str, label: str, **details) -> None:
        """Pubblica avanzamento osservabile senza renderlo parte del risultato."""
        callback = self.progress_callback
        if callback is None:
            return
        payload = {
            "phase": phase,
            "label": label,
            "elapsed_s": round(
                max(0.0, time.monotonic() - self._progress_started), 1
            ),
            **details,
        }
        try:
            callback(payload)
        except Exception as exc:
            # La telemetria non deve mai cambiare l'esito del calcolo.
            logger.debug("CodingJob: progress callback ignorata ({})", exc)

    def _make_workspace(self) -> Path:
        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Il contenuto del problema e il codice generato possono essere
        # sensibili: anche una directory gia' esistente resta privata.
        self.workspace_root.chmod(0o700)
        return Path(tempfile.mkdtemp(prefix="euri-code-", dir=self.workspace_root)).resolve()

    def _safe_cleanup(self, workspace: Path) -> None:
        try:
            if workspace.parent != self.workspace_root or not workspace.name.startswith("euri-code-"):
                logger.error("CodingJob: cleanup rifiutato per path inatteso {}", workspace)
                return
            shutil.rmtree(workspace)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("CodingJob: cleanup workspace fallito {}", exc)

    def _initial_prompt(self, task: str) -> str:
        files = self.code_runner.list_input_files()
        file_list = "\n".join(f"- {item}" for item in files) or "- nessun file"
        return f"""Costruisci uno strumento Python temporaneo per verificare questo problema:

{task.strip()}

Contratto di esecuzione:
- devi creare o sostituire main.py nel workspace corrente;
- il programma sara' eseguito da Euri in una sandbox separata;
- input leggibili in sola lettura: {config.CODE_RUNNER_INPUT_DIR}
- output persistenti consentiti: {config.CODE_RUNNER_OUTPUT_DIR}
- file attualmente disponibili:
{file_list}
- librerie ammesse: json, csv, math, statistics, decimal, fractions,
  itertools, collections, pathlib, pandas, numpy, openpyxl, odf,
  PyPDF2/pypdf, docx, pptx, PIL, matplotlib, tabulate;
- niente rete, shell, subprocess, installazione pacchetti o input interattivo;
- stampa valori concreti, assunzioni, unita' e almeno un controllo di coerenza;
- se mancano dati indispensabili, il programma deve dichiararlo chiaramente
  senza inventarli.

Non modificare altri file e non rispondere soltanto con una spiegazione:
materializza main.py usando il tool di scrittura.
"""

    @staticmethod
    def _repair_prompt(attempt: int, result: CodeResult) -> str:
        evidence = (result.error or result.output or "errore non specificato")
        evidence = evidence[-int(config.CODE_AGENT_MAX_ERROR_CHARS):]
        return f"""L'esecuzione controllata del tentativo {attempt} e' fallita.

Evidenza reale restituita dalla sandbox:
---
{evidence}
---

Correggi main.py senza aggirare scanner, sandbox o limiti. Mantieni invariati
obiettivo, dati e unita'. Usa il tool di modifica del file, poi fermati.
"""

    @staticmethod
    def _require_observable_result(result: CodeResult) -> CodeResult:
        """Rifiuta i falsi successi del coding agent privi di un risultato.

        ``exit(0)`` prova soltanto che Python ha terminato senza eccezioni. Per
        uno strumento computazionale temporaneo serve anche un'evidenza utile:
        stdout reale oppure un artefatto persistente prodotto nella cartella
        di output. ``stdout_chars is None`` conserva la compatibilita' con
        runner storici che non espongono ancora questa telemetria.
        """
        if not result.success or result.stdout_chars != 0:
            return result
        if str(result.artifacts or "").strip():
            return result
        return CodeResult(
            success=False,
            output=(
                "Il programma e' terminato senza produrre un risultato "
                "osservabile."
            ),
            error=(
                "missing_observable_result: exit code 0, ma nessun stdout "
                "reale e nessun artefatto di output; completa il calcolo e "
                "stampa i risultati oppure salva un artefatto consentito."
            ),
            exit_code=result.exit_code,
            interrupted=result.interrupted,
            script_path=result.script_path,
            artifacts=result.artifacts,
            stdout_chars=0,
        )

    def run(self, task: str, stop_event: threading.Event) -> CodingJobResult:
        task = str(task or "").strip()
        if not task:
            return CodingJobResult(False, "Non ho ricevuto il problema da verificare.", "empty_task")

        self._progress_started = time.monotonic()
        self._emit("preparing", "Preparo l'ambiente temporaneo sicuro")
        workspace = self._make_workspace()
        job_id = workspace.name.removeprefix("euri-code-")
        attempts: list[CodingAttempt] = []
        final_code_hash = ""
        success = False
        keep_workspace = False
        adapter = self.adapter_factory(workspace)
        model_name = str(getattr(adapter, "model", config.CODE_AGENT_MODEL))
        deadline = time.monotonic() + float(config.CODE_AGENT_TOOL_TIMEOUT)
        try:
            self._emit("backend_starting", "Avvio OpenCode e collego il modello locale")
            adapter.start()
            adapter.create_session(f"Euri coding job {job_id}")
            self._emit(
                "backend_ready", "Coding agent pronto",
                job_id=job_id, model=model_name,
            )
            next_prompt = self._initial_prompt(task)
            for attempt_no in range(1, self.max_attempts + 1):
                if stop_event.is_set():
                    adapter.abort()
                    return CodingJobResult(
                        False, "Costruzione dello strumento interrotta.", "interrupted",
                        job_id=job_id, attempts=attempts, model=model_name,
                    )

                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    adapter.abort()
                    return CodingJobResult(
                        False,
                        "Il coding agent ha esaurito il tempo totale disponibile.",
                        "job_timeout",
                        job_id=job_id,
                        attempts=attempts,
                        model=model_name,
                    )
                if hasattr(adapter, "prompt_timeout"):
                    adapter.prompt_timeout = max(
                        1,
                        min(int(adapter.prompt_timeout), remaining),
                    )

                logger.info(
                    "CodingJob {}: OpenCode tentativo {}/{}",
                    job_id, attempt_no, self.max_attempts,
                )
                self._emit(
                    "generating",
                    f"OpenCode costruisce main.py — tentativo {attempt_no}/{self.max_attempts}",
                    attempt=attempt_no,
                    max_attempts=self.max_attempts,
                    model=model_name,
                )
                main_path = workspace / "main.py"
                reply = adapter.prompt(
                    next_prompt,
                    completion_path=main_path,
                    stop_event=stop_event,
                    no_artifact_timeout=min(self.no_artifact_timeout, remaining),
                )
                no_artifact_watchdog = bool(
                    isinstance(getattr(reply, "raw", None), dict)
                    and reply.raw.get("stopped_no_artifact")
                )
                if no_artifact_watchdog or not main_path.is_file():
                    self._emit(
                        "missing_artifact",
                        (
                            "main.py non creato entro il watchdog — "
                            f"passo al tentativo {attempt_no + 1}"
                            if no_artifact_watchdog
                            else f"main.py non creato — preparo il tentativo {attempt_no + 1}"
                        ),
                        attempt=attempt_no,
                        max_attempts=self.max_attempts,
                        watchdog=no_artifact_watchdog,
                    )
                    diagnostic_log = self._record_attempt(
                        adapter=adapter,
                        job_id=job_id,
                        attempt=attempt_no,
                        model=model_name,
                        request_prompt=next_prompt,
                        reply=reply,
                        outcome=(
                            "watchdog_no_artifact"
                            if no_artifact_watchdog
                            else "completed_without_artifact"
                        ),
                        artifact_path=main_path,
                    )
                    attempts.append(CodingAttempt(
                        attempt_no,
                        error=(reply.text or "main.py non creato")[-2000:],
                        diagnostic_log=diagnostic_log,
                    ))
                    if attempt_no < self.max_attempts and hasattr(adapter, "reset_session"):
                        adapter.reset_session(
                            f"Euri coding job {job_id} retry {attempt_no + 1}"
                        )
                    # Il retry usa una sessione pulita: deve quindi ricevere di
                    # nuovo il problema completo, non soltanto una correzione.
                    next_prompt = self._initial_prompt(task) + (
                        "\nIl tentativo precedente non ha creato main.py entro il "
                        "watchdog. Inizia usando subito il tool di scrittura e "
                        "fermati appena il file e' completo."
                    )
                    continue

                try:
                    size = main_path.stat().st_size
                    self._emit(
                        "artifact_ready",
                        f"main.py materializzato ({size} byte) — controllo ed esecuzione",
                        attempt=attempt_no,
                        size_bytes=size,
                    )
                    if size > int(config.CODE_AGENT_MAX_CODE_BYTES):
                        execution = CodeResult(
                            False,
                            "Il programma generato supera il limite consentito.",
                            "code_too_large",
                        )
                        code = ""
                    else:
                        code = main_path.read_text(encoding="utf-8")
                        execution_remaining = int(deadline - time.monotonic())
                        if execution_remaining <= 0:
                            return CodingJobResult(
                                False,
                                "Il coding agent ha esaurito il tempo prima dell'esecuzione.",
                                "job_timeout",
                                job_id=job_id,
                                attempts=attempts,
                                code_sha256=final_code_hash,
                                model=model_name,
                            )
                        self._emit(
                            "executing",
                            "SecurityScanner superato: eseguo nella sandbox",
                            attempt=attempt_no,
                        )
                        execution = self.code_runner.execute_generated_code(
                            code,
                            stop_event,
                            timeout=max(
                                1,
                                min(int(config.CODE_RUNNER_TIMEOUT), execution_remaining),
                            ),
                            workspace_dir=workspace,
                            require_bwrap=True,
                        )
                except OSError as exc:
                    code = ""
                    execution = CodeResult(False, "Non riesco a leggere main.py.", str(exc))

                # Un processo Python concluso con exit code 0 puo' comunque
                # essere un programma incompleto (per esempio main() = pass).
                # Questo gate appartiene al solo coding agent: il run_code
                # storico mantiene il proprio contratto invariato.
                execution = self._require_observable_result(execution)

                final_code_hash = (
                    hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
                )
                diagnostic_log = self._record_attempt(
                    adapter=adapter,
                    job_id=job_id,
                    attempt=attempt_no,
                    model=model_name,
                    request_prompt=next_prompt,
                    reply=reply,
                    outcome=("executed_ok" if execution.success else "execution_failed"),
                    artifact_path=main_path,
                    execution=execution,
                )
                attempts.append(CodingAttempt(
                    number=attempt_no,
                    code_sha256=final_code_hash,
                    success=execution.success,
                    exit_code=execution.exit_code,
                    error=(execution.error or "")[-int(config.CODE_AGENT_MAX_ERROR_CHARS):],
                    diagnostic_log=diagnostic_log,
                ))
                if execution.success:
                    success = True
                    self._emit(
                        "completed", "Strumento eseguito: risultato disponibile",
                        attempt=attempt_no,
                    )
                    return CodingJobResult(
                        True,
                        execution.output,
                        job_id=job_id,
                        attempts=attempts,
                        artifacts=execution.artifacts,
                        code_sha256=final_code_hash,
                        model=model_name,
                    )
                if execution.error == "sandbox_unavailable":
                    self._emit("failed", "Sandbox sicura non disponibile")
                    return CodingJobResult(
                        False,
                        "Non ho eseguito il programma: la sandbox sicura non e' disponibile.",
                        execution.error,
                        job_id=job_id,
                        attempts=attempts,
                        code_sha256=final_code_hash,
                        model=model_name,
                    )
                self._emit(
                    "repairing",
                    (
                        "Nessun risultato osservabile — OpenCode completa lo strumento"
                        if execution.error and execution.error.startswith(
                            "missing_observable_result"
                        )
                        else "L'esecuzione ha trovato un errore — OpenCode lo corregge"
                    ),
                    attempt=attempt_no,
                    max_attempts=self.max_attempts,
                )
                next_prompt = self._repair_prompt(attempt_no, execution)

            keep_workspace = bool(config.CODE_AGENT_KEEP_FAILED_WORKSPACE)
            return CodingJobResult(
                False,
                f"Non sono riuscito a ottenere un programma valido dopo {self.max_attempts} tentativi.",
                attempts[-1].error if attempts else "no_candidate",
                job_id=job_id,
                attempts=attempts,
                code_sha256=final_code_hash,
                model=model_name,
                workspace_kept=str(workspace) if keep_workspace else "",
            )
        except OpenCodeError as exc:
            self._emit("failed", "Coding agent non disponibile", error=str(exc))
            keep_workspace = bool(config.CODE_AGENT_KEEP_FAILED_WORKSPACE)
            return CodingJobResult(
                False,
                f"Il coding agent locale non e' disponibile: {exc}",
                str(exc),
                job_id=job_id,
                attempts=attempts,
                code_sha256=final_code_hash,
                model=model_name,
                workspace_kept=str(workspace) if keep_workspace else "",
            )
        except Exception as exc:
            self._emit("failed", "Errore interno nel coding agent", error=str(exc))
            logger.exception("CodingJob {}: errore inatteso", job_id)
            keep_workspace = bool(config.CODE_AGENT_KEEP_FAILED_WORKSPACE)
            return CodingJobResult(
                False,
                "Errore interno durante la costruzione dello strumento temporaneo.",
                str(exc),
                job_id=job_id,
                attempts=attempts,
                code_sha256=final_code_hash,
                model=model_name,
                workspace_kept=str(workspace) if keep_workspace else "",
            )
        finally:
            try:
                adapter.close()
            except Exception as exc:
                logger.debug("CodingJob {}: chiusura adapter fallita ({})", job_id, exc)
            if success or not keep_workspace:
                self._safe_cleanup(workspace)
