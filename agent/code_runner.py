"""
CodeRunner — Sandbox per esecuzione codice Python generato dall'LLM.

Flusso:
  1. L'LLM (Gemma) genera uno script Python basato sulla richiesta vocale
  2. SecurityScanner valida il codice con analisi AST (whitelist import)
  3. Il codice viene eseguito in subprocess isolato con timeout
  4. stdout/stderr catturati e restituiti come risultato vocale

Cartelle I/O:
  - Input:  ~/Scrivania/dati_per_Euri/
  - Output: ~/Scrivania/scambio_dati/
"""
import ast
import os
import sys
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from loguru import logger

import config


# ─────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────

@dataclass
class CodeResult:
    success: bool
    output: str               # stdout (voice-ready, max 10KB)
    error: str | None = None  # stderr o messaggio di errore
    exit_code: int = -1
    interrupted: bool = False
    script_path: str = ""     # per debug


# ─────────────────────────────────────────
# SecurityScanner — Validazione AST
# ─────────────────────────────────────────

class SecurityScanner:
    """
    Analisi statica del codice generato dall'LLM.
    Usa il modulo `ast` per ispezionare l'albero sintattico
    e bloccare import e pattern pericolosi PRIMA dell'esecuzione.
    """

    # Moduli consentiti (e i loro sotto-moduli)
    ALLOWED_MODULES = frozenset({
        # Core Python
        "os", "os.path", "pathlib", "json", "csv", "math", "re",
        "datetime", "collections", "itertools", "statistics",
        "io", "base64", "hashlib", "textwrap", "unicodedata",
        "string", "decimal", "fractions", "copy", "pprint",
        "typing", "dataclasses", "enum", "functools",
        "glob", "fnmatch", "tempfile", "time",
        # Data Science
        "pandas", "numpy", "openpyxl", "xlsxwriter", "odf", "odfpy",
        # PDF
        "PyPDF2", "pypdf", "reportlab",
        # Immagini
        "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
        "PIL.ImageFilter", "PIL.ImageEnhance", "PIL.ExifTags",
        # Grafici
        "matplotlib", "matplotlib.pyplot", "matplotlib.figure",
        # Tabelle
        "tabulate",
    })

    # Pattern testuali bloccati (catch-all per cose che ast potrebbe non catturare)
    BLOCKED_PATTERNS = [
        "subprocess", "shutil.rmtree", "shutil.move",
        "os.system(", "os.remove(", "os.unlink(", "os.rmdir(",
        "os.removedirs(", "os.rename(",
        "eval(", "exec(", "compile(", "__import__(",
        "open('/etc", "open('/sys", "open('/proc", "open('/dev",
        'open("/etc', 'open("/sys', 'open("/proc', 'open("/dev',
        "socket", "http.client", "urllib", "requests",
        "smtplib", "ftplib", "telnetlib",
        "ctypes", "cffi",
        "signal.SIG",
        "input(",
    ]

    def scan(self, code: str) -> tuple[bool, str]:
        """
        Analizza il codice generato.
        Returns: (is_safe, reason)
        """
        # 1. Check testuale veloce (pattern blacklist)
        code_lower = code.lower()
        for pattern in self.BLOCKED_PATTERNS:
            if pattern.lower() in code_lower:
                return False, f"Pattern bloccato: '{pattern}'"

        # 2. Parse AST
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Errore di sintassi nel codice generato: {e}"

        # 3. Controlla tutti gli import
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self._is_allowed_module(alias.name):
                        return False, f"Import bloccato: '{alias.name}'"

            elif isinstance(node, ast.ImportFrom):
                if node.module and not self._is_allowed_module(node.module):
                    return False, f"Import bloccato: 'from {node.module}'"

            # 4. Blocca chiamate a __import__
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    return False, "Chiamata __import__() bloccata"
                # Blocca os.system, os.popen, ecc.
                if isinstance(node.func, ast.Attribute):
                    if (isinstance(node.func.value, ast.Name) and
                        node.func.value.id == "os" and
                        node.func.attr in ("system", "popen", "execvp", "fork",
                                           "remove", "unlink", "rmdir", "removedirs")):
                        return False, f"Chiamata os.{node.func.attr}() bloccata"

        return True, "OK"

    def _is_allowed_module(self, module_name: str) -> bool:
        """Controlla se il modulo è nella whitelist (inclusi sotto-moduli)."""
        parts = module_name.split(".")
        # Controlla ogni prefisso: "matplotlib.pyplot" matcha "matplotlib"
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.ALLOWED_MODULES:
                return True
        return False


# ─────────────────────────────────────────
# CodeRunner — Generazione + Esecuzione
# ─────────────────────────────────────────

class CodeRunner:
    """
    Genera codice Python tramite l'LLM ed eseguilo in un subprocess isolato.
    Il subprocess è interrompibile tramite stop_event (comando vocale "Stop").
    """

    def __init__(self):
        self._scanner = SecurityScanner()
        self._input_dir = Path(config.CODE_RUNNER_INPUT_DIR)
        self._output_dir = Path(config.CODE_RUNNER_OUTPUT_DIR)
        self._sandbox_dir = Path(config.CODE_RUNNER_SANDBOX_DIR)
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Crea le cartelle I/O se non esistono."""
        for d in (self._input_dir, self._output_dir, self._sandbox_dir):
            d.mkdir(parents=True, exist_ok=True)

    def list_input_files(self) -> list[str]:
        """Elenca i file nella cartella di input."""
        if not self._input_dir.exists():
            return []
        files = []
        for f in sorted(self._input_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                size = f.stat().st_size
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024*1024):.1f} MB"
                files.append(f"{f.name} ({size_str})")
        return files

    def find_images(self) -> list[Path]:
        """Trova file immagine nella cartella di input."""
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff"}
        if not self._input_dir.exists():
            return []
        return [f for f in self._input_dir.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS]

    def generate_and_run(self, task: str, brain,
                         stop_event: threading.Event,
                         timeout: int = None) -> CodeResult:
        """
        Pipeline completa: genera codice → valida → esegui.
        
        Args:
            task: richiesta dell'utente in linguaggio naturale
            brain: istanza di Brain per chiamare Gemma
            stop_event: threading.Event per interruzione vocale
            timeout: secondi max di esecuzione (default da config)
        """
        timeout = timeout or config.CODE_RUNNER_TIMEOUT

        # 1. Elenca i file disponibili
        available_files = self.list_input_files()
        if not available_files:
            return CodeResult(
                success=False,
                output="Non ci sono file nella cartella dati. Metti i file in Scrivania, dati per Euri.",
                error="no_input_files"
            )

        # 2. Genera il codice con l'LLM
        logger.info(f"CodeRunner: generazione codice per '{task[:60]}...'")
        code = brain.generate_code(
            task=task,
            available_files=available_files,
            input_dir=str(self._input_dir),
            output_dir=str(self._output_dir),
        )

        if not code or len(code.strip()) < 10:
            return CodeResult(
                success=False,
                output="Non sono riuscito a generare il codice per questa richiesta.",
                error="code_generation_failed"
            )

        logger.debug(f"CodeRunner: codice generato ({len(code)} chars)")

        # 3. Scansione sicurezza
        is_safe, reason = self._scanner.scan(code)
        if not is_safe:
            logger.warning(f"CodeRunner: codice BLOCCATO — {reason}")
            return CodeResult(
                success=False,
                output=f"Ho generato il codice ma l'ho bloccato per sicurezza: {reason}",
                error=f"security: {reason}"
            )

        # 4. Esecuzione
        return self._execute_code(code, stop_event, timeout)

    def _execute_code(self, code: str, stop_event: threading.Event,
                      timeout: int) -> CodeResult:
        """Esegue il codice in subprocess isolato."""
        # Salva lo script in un file temporaneo nella sandbox
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)
        script_path = self._sandbox_dir / f"euri_script_{int(time.time())}.py"

        try:
            script_path.write_text(code, encoding="utf-8")
            logger.info(f"CodeRunner: esecuzione {script_path.name} (timeout={timeout}s)")

            # Ambiente: eredita l'env del processo padre (necessario per venv/site-packages),
            # ma rimuove variabili sensibili
            env = dict(os.environ)
            # Rimuovi credenziali/token se presenti
            for sensitive_key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN",
                                  "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                                  "GOOGLE_API_KEY", "REDIS_PASSWORD"):
                env.pop(sensitive_key, None)
            env["PYTHONIOENCODING"] = "utf-8"

            # Lancia il subprocess
            process = subprocess.Popen(
                [sys.executable, "-u", str(script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._sandbox_dir),
                env=env,
                preexec_fn=os.setsid,  # nuovo process group per kill pulito
            )

            # Polling non-bloccante con check stop_event
            start_time = time.monotonic()
            while process.poll() is None:
                # Check interruzione vocale
                if stop_event.is_set():
                    logger.warning("CodeRunner: INTERRUPT — killing subprocess")
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    process.wait(timeout=3)
                    return CodeResult(
                        success=False,
                        output="Esecuzione interrotta.",
                        interrupted=True,
                        script_path=str(script_path),
                    )

                # Check timeout
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    logger.warning(f"CodeRunner: TIMEOUT dopo {timeout}s")
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    process.wait(timeout=3)
                    return CodeResult(
                        success=False,
                        output=f"Lo script ha impiegato troppo tempo, l'ho fermato dopo {timeout} secondi.",
                        error="timeout",
                        script_path=str(script_path),
                    )

                time.sleep(0.2)  # poll ogni 200ms

            # Processo terminato normalmente
            stdout = process.stdout.read().decode("utf-8", errors="replace")
            stderr = process.stderr.read().decode("utf-8", errors="replace")

            # Limita output
            max_out = config.CODE_RUNNER_MAX_OUTPUT_BYTES
            if len(stdout) > max_out:
                stdout = stdout[:max_out] + f"\n... (troncato, {len(stdout)} caratteri totali)"

            exit_code = process.returncode
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            if exit_code == 0:
                logger.success(f"CodeRunner: completato in {elapsed_ms}ms")
                # Restituisci l'output oppure un messaggio di default
                output = stdout.strip() if stdout.strip() else "Operazione completata senza errori."
                return CodeResult(
                    success=True,
                    output=output,
                    exit_code=exit_code,
                    script_path=str(script_path),
                )
            else:
                logger.error(f"CodeRunner: errore (exit={exit_code})")
                # Estraiamo l'errore più utile
                error_msg = stderr.strip() if stderr.strip() else "Errore sconosciuto"
                # Prendiamo solo l'ultima riga dell'errore (di solito la più utile)
                last_error = error_msg.splitlines()[-1] if error_msg else "Errore"
                return CodeResult(
                    success=False,
                    output=f"Lo script ha dato errore: {last_error}",
                    error=error_msg,
                    exit_code=exit_code,
                    script_path=str(script_path),
                )

        except Exception as e:
            logger.error(f"CodeRunner: eccezione — {e}")
            return CodeResult(
                success=False,
                output="Errore interno durante l'esecuzione dello script.",
                error=str(e),
            )
        finally:
            # Pulisce il file temporaneo (in caso di successo)
            try:
                if script_path.exists():
                    script_path.unlink()
            except Exception:
                pass
