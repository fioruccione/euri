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
    artifacts: str = ""       # contenuto FEDELE dei file prodotti in output_dir,
                              # riletto dal disco per iniettarlo nel contesto LLM
                              # (la "memoria del collega": valori esatti, non il
                              # riassunto che lo script ha scelto di stampare).


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
        "PyPDF2", "pypdf", "reportlab", "pdf2image",
        # Word / PowerPoint (V2.18.2)
        "docx", "pptx",
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
        # NB: 'compile(' rimosso dalla blacklist testuale (catturava anche
        # re.compile() innocuo). Il check sul compile() builtin è ora via AST.
        "eval(", "exec(", "__import__(",
        "open('/etc", "open('/sys", "open('/proc", "open('/dev",
        'open("/etc', 'open("/sys', 'open("/proc', 'open("/dev',
        "socket", "http.client", "urllib", "requests",
        "smtplib", "ftplib", "telnetlib",
        "ctypes", "cffi",
        "signal.SIG",
        "input(",
        # Deserializzazione = RCE che bypassa l'AST: pickle/joblib eseguono
        # codice arbitrario al load; np.load con allow_pickle idem. (np.loadtxt
        # resta consentito: il pattern ha la parentesi, "np.load(" non matcha
        # "np.loadtxt(".)
        "pickle", "read_parquet", "joblib", "np.load(", "numpy.load(", ".to_pickle",
        # Path sensibili: chiavi/credenziali/traversal. I path di lavoro
        # (input/output) arrivano come VARIABILI, non come queste stringhe —
        # quindi bloccarle non tocca il codice legittimo.
        ".ssh", "id_rsa", "id_ed25519", ".gnupg", ".aws", ".netrc",
        "authorized_keys", ".bash_history", "../",
    ]

    # Funzioni/metodi che aprono file: i loro argomenti string-literal assoluti
    # devono cadere nelle cartelle di lavoro (check in scan(), via allowed_roots).
    _FILE_FUNCS = frozenset({"open", "Path"})
    _FILE_METHODS = frozenset({
        "open", "write_text", "read_text", "write_bytes", "read_bytes",
        "read_csv", "read_excel", "read_json", "read_table",
        "to_csv", "to_excel", "to_json", "savefig", "save",
    })

    @staticmethod
    def _path_outside_roots(p: str, allowed_roots: list[str]) -> bool:
        """
        True se `p` è un path che esce dalle cartelle di lavoro.
        - path relativi → ok (il subprocess gira con cwd nella sandbox);
        - `~...` → bloccato (espansione home);
        - path assoluti → ammessi solo se contenuti in una root permessa.
        """
        if not p:
            return False
        if p.startswith("~"):
            return True
        if not p.startswith("/"):
            return False
        norm = os.path.normpath(p)
        for r in allowed_roots:
            rn = os.path.normpath(r)
            if norm == rn or norm.startswith(rn + os.sep):
                return False
        return True

    def scan(self, code: str, allowed_roots: list[str] | None = None) -> tuple[bool, str]:
        """
        Analizza il codice generato.
        allowed_roots: se passato, i path string-literal assoluti nelle chiamate
        di apertura file devono cadere in una di queste cartelle (input/output/
        sandbox). I path costruiti da variabili non sono toccati.
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

            # 4. Blocca chiamate a builtin pericolose: __import__, compile
            # (eval/exec sono già coperti dalla blacklist testuale).
            # NB: re.compile() qui passa perché node.func è ast.Attribute,
            # non ast.Name — l'AST distingue correttamente fra builtin e metodo.
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id == "__import__":
                        return False, "Chiamata __import__() bloccata"
                    if node.func.id == "compile":
                        return False, "Chiamata compile() builtin bloccata"
                # Blocca os.system, os.popen, ecc.
                if isinstance(node.func, ast.Attribute):
                    if (isinstance(node.func.value, ast.Name) and
                        node.func.value.id == "os" and
                        node.func.attr in ("system", "popen", "execvp", "fork",
                                           "remove", "unlink", "rmdir", "removedirs")):
                        return False, f"Chiamata os.{node.func.attr}() bloccata"

                # Path letterali assoluti fuori dalle cartelle di lavoro: blocca
                # le chiamate di apertura file con una stringa-literal sospetta
                # (es. open('/home/fio/.ssh/id_rsa')). I path da variabili passano.
                if allowed_roots is not None:
                    fn = node.func
                    is_file_call = (
                        (isinstance(fn, ast.Name) and fn.id in self._FILE_FUNCS)
                        or (isinstance(fn, ast.Attribute) and fn.attr in self._FILE_METHODS)
                    )
                    if is_file_call:
                        for arg in node.args:
                            if (isinstance(arg, ast.Constant)
                                    and isinstance(arg.value, str)
                                    and self._path_outside_roots(arg.value, allowed_roots)):
                                return False, f"Path fuori dalle cartelle di lavoro: '{arg.value}'"

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

    def _preextract_files(self, brain) -> dict[str, str]:
        """
        Pre-estrae il testo da tutti i file gestiti nella cartella di input
        (PDF, DOCX, PPTX, immagini). Per i formati strutturati direttamente
        leggibili (csv, xlsx, json, txt, md) non si fa pre-estrazione: Gemma
        li legge nel codice generato via pandas/open().

        Cascata per formato:
        - PDF: pypdf → fallback Vision (Gemma 4 multimodale)
        - DOCX: python-docx (nessun fallback Vision: i .docx sono strutturati)
        - PPTX: python-pptx → fallback Vision (per slide grafiche)
        - Immagini: Vision diretta (è l'unico canale)

        Restituisce dict {filename: testo_estratto}. File senza testo
        estraibile hanno valore stringa vuota.
        """
        if not self._input_dir.exists():
            return {}

        from agent.file_extractors import extract_any, IMAGE_EXTS

        HANDLED = {".pdf", ".docx", ".pptx"} | IMAGE_EXTS
        files = [f for f in self._input_dir.iterdir()
                 if f.is_file() and f.suffix.lower() in HANDLED]
        if not files:
            return {}

        # vision_callback wrappa Brain.analyze_image così file_extractors
        # non ha dipendenza diretta su Brain (modulo isolato).
        vision_cb = brain.analyze_image if brain is not None else None

        out = {}
        for f in files:
            try:
                text = extract_any(f, vision_callback=vision_cb)
                out[f.name] = text
                logger.info(f"Pre-extract '{f.name}' ({f.suffix}): {len(text)} char")
            except Exception as e:
                logger.warning(f"Pre-extract fallito su {f.name}: {e}")
                out[f.name] = ""
        return out

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

        # 1b. Pre-estrazione testo da PDF/DOCX/PPTX/immagini
        # Cascata testo-nativo → Vision (Gemma 4 multimodale) per i casi
        # scansionati. Formati testuali (csv/xlsx/json/txt/md) NON vengono
        # pre-estratti: Gemma li legge direttamente da disco.
        file_contents = self._preextract_files(brain)

        # 1c. Salva i file_contents su disco come JSON così lo script generato
        # li carica via json.load() invece di hardcodare 2-8 KB di testo come
        # stringhe multilinea (osservato 28/05: Gemma duplicava il content e
        # superava num_predict, troncando lo script a metà stringa).
        #
        # Path FISSO e CORTO (V2.18.3 fix 28/05 16:35): con nome dinamico
        # 'euri_file_contents_<timestamp>.json' Gemma a volte tronca il
        # path quando lo ricopia come stringa, generando SyntaxError. Path
        # fisso = 0 caratteri da sbagliare. È sicuro perché il file viene
        # sovrascritto ad ogni richiesta (operazioni seriali nel daemon).
        contents_path = None
        if file_contents:
            import json
            self._sandbox_dir.mkdir(parents=True, exist_ok=True)
            contents_path = self._sandbox_dir / "file_contents.json"
            with open(contents_path, "w", encoding="utf-8") as f:
                json.dump(file_contents, f, ensure_ascii=False)
            logger.debug(f"CodeRunner: file_contents salvati in {contents_path.name}")

        # 2. Genera il codice con l'LLM (retry su SyntaxError)
        # Gemma a temperature=0.2 non è deterministica: stessa task può produrre
        # codice corretto o codice con bug di sintassi. Osservato 28/05: 2 falsi
        # consecutivi, poi successo. Retry trasforma fallimenti intermittenti
        # in successi affidabili (al prezzo di max 2× latenza nel peggior caso).
        setup_block = ""
        if contents_path is not None:
            setup_block = (
                "import json\n"
                f"FILE_CONTENTS = json.load(open({str(contents_path)!r}, encoding='utf-8'))\n"
                "\n"
            )

        MAX_ATTEMPTS = 2
        last_reason = "code_generation_failed"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info(
                f"CodeRunner: generazione codice per '{task[:60]}...'"
                + (f" (tentativo {attempt}/{MAX_ATTEMPTS})" if attempt > 1 else "")
            )
            code = brain.generate_code(
                task=task,
                available_files=available_files,
                input_dir=str(self._input_dir),
                output_dir=str(self._output_dir),
                file_contents=file_contents,
                file_contents_path=str(contents_path) if contents_path else None,
            )
            if not code or len(code.strip()) < 10:
                last_reason = "code_generation_failed"
                continue

            # Prepend setup block (FILE_CONTENTS già definito)
            full_code = setup_block + code

            logger.debug(f"CodeRunner: codice generato ({len(code)} chars, tentativo {attempt})")

            # 3. Scansione sicurezza (con confinamento path alle cartelle di lavoro)
            is_safe, reason = self._scanner.scan(
                full_code,
                allowed_roots=[
                    str(self._input_dir), str(self._output_dir),
                    str(self._sandbox_dir), "/tmp",
                ],
            )
            if is_safe:
                # 4. Esecuzione
                return self._execute_code(full_code, stop_event, timeout)

            last_reason = reason
            logger.warning(
                f"CodeRunner: codice BLOCCATO al tentativo {attempt}/{MAX_ATTEMPTS} — {reason}"
            )

        # Tutti i tentativi falliti
        return CodeResult(
            success=False,
            output=f"Ho generato il codice ma l'ho bloccato per sicurezza: {last_reason}",
            error=f"security: {last_reason}"
        )

    def _read_output_artifacts(self, before_outputs: dict,
                               max_total: int = 4000) -> str:
        """
        Rilegge dal disco i file che lo script ha prodotto/aggiornato in
        output_dir durante questa run, per iniettarli FEDELMENTE nel contesto
        LLM. È la "memoria del collega": ciò che Euri ricorda dell'analisi sono
        i valori esatti scritti su file, non il riassunto che lo script ha
        scelto di stampare a voce (che era lossy → confabulazioni su numeri).

        Solo file testuali (csv/json/txt/md/tsv). Cap a max_total caratteri
        totali per non saturare il contesto.

        TODO (debito noto, non urgente): output_dir (scambio_dati/) cresce
        all'infinito e non viene mai svuotata. Il filtro mtime qui sotto isola
        i file di QUESTA run, quindi nell'uso normale `produced` contiene 1-2
        file e il problema non morde. MA se una singola run producesse molti
        file, `sorted(iterdir())` li ordina alfabeticamente e il cap a 4KB
        rileggerebbe i primi per nome, non i più pertinenti. Da chiudere con
        una pulizia periodica della cartella o un sotto-folder per-run con
        timestamp (es. scambio_dati/<run_ts>/). Solo allora ordinare per mtime.
        """
        if not self._output_dir.exists():
            return ""
        TEXT_EXT = {".csv", ".json", ".txt", ".md", ".tsv"}
        produced = []
        for p in sorted(self._output_dir.iterdir()):
            if not p.is_file():
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            prev = before_outputs.get(str(p))
            if prev is None or mtime > prev:  # nuovo o modificato in questa run
                produced.append(p)
        if not produced:
            return ""

        blocks = []
        budget = max_total
        for p in produced:
            if budget <= 0:
                break
            if p.suffix.lower() not in TEXT_EXT:
                blocks.append(f"=== {p.name} === (file non testuale, {p.stat().st_size} byte)")
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            if len(txt) > budget:
                txt = txt[:budget] + " ...[troncato]"
            blocks.append(f"=== {p.name} ===\n{txt}")
            budget -= len(txt)
        return "\n\n".join(blocks)

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

            # Snapshot dei file già presenti in output_dir: dopo il run ci serve
            # per capire QUALI file lo script ha prodotto/aggiornato e rileggerli
            # fedelmente nel contesto (memoria del collega — vedi CodeResult.artifacts).
            before_outputs = {}
            if self._output_dir.exists():
                for p in self._output_dir.iterdir():
                    if p.is_file():
                        try:
                            before_outputs[str(p)] = p.stat().st_mtime
                        except OSError:
                            pass

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
                artifacts = self._read_output_artifacts(before_outputs)
                if artifacts:
                    logger.debug(f"CodeRunner: artefatti riletti per il contesto ({len(artifacts)} char)")
                return CodeResult(
                    success=True,
                    output=output,
                    exit_code=exit_code,
                    script_path=str(script_path),
                    artifacts=artifacts,
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
