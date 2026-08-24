"""Adapter locale e limitato per OpenCode.

OpenCode viene avviato in un workspace temporaneo dedicato al singolo job. Il
processo puo' costruire/modificare i file del workspace, ma non riceve shell,
rete, sub-agent o accesso a directory esterne. L'esecuzione del codice prodotto
non avviene qui: resta responsabilita' del CodeRunner di Euri.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

import config


class OpenCodeError(RuntimeError):
    """Errore operativo dell'adapter, gia' sicuro da riportare al chiamante."""


@dataclass
class OpenCodeReply:
    text: str
    raw: dict[str, Any]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _response_text(payload: Any) -> str:
    """Estrae i blocchi testuali dalla risposta strutturata del server."""
    if isinstance(payload, dict):
        parts = payload.get("parts")
        if isinstance(parts, list):
            chunks = [
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if any(chunks):
                return "\n".join(chunk for chunk in chunks if chunk)
        for key in ("text", "content", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, (dict, list)):
                nested = _response_text(value)
                if nested:
                    return nested
    elif isinstance(payload, list):
        chunks = [_response_text(item) for item in payload]
        return "\n".join(chunk for chunk in chunks if chunk)
    return ""


class OpenCodeAdapter:
    """Gestisce un server OpenCode effimero, radicato nel workspace del job."""

    AGENT_NAME = "euri-tool-builder"

    def __init__(
        self,
        workspace: str | Path,
        *,
        binary: str | None = None,
        model: str | None = None,
        ollama_base_url: str | None = None,
        max_steps: int | None = None,
        start_timeout: int | None = None,
        prompt_timeout: int | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.binary = binary or config.CODE_AGENT_OPENCODE_BIN
        self.model = model or config.CODE_AGENT_MODEL
        self.ollama_base_url = ollama_base_url or config.CODE_AGENT_OLLAMA_BASE_URL
        self.max_steps = int(max_steps or config.CODE_AGENT_MAX_STEPS)
        self.start_timeout = int(start_timeout or config.CODE_AGENT_SERVER_START_TIMEOUT)
        self.prompt_timeout = int(prompt_timeout or config.CODE_AGENT_PROMPT_TIMEOUT)
        self.port = 0
        self.password = ""
        self.base_url = ""
        self.session_id = ""
        self._process: subprocess.Popen | None = None
        self._log_stream = None

    def _agent_prompt(self) -> str:
        prompt_path = (
            Path(__file__).resolve().parent.parent
            / "resources" / "opencode" / "euri_tool_builder_prompt.txt"
        )
        return prompt_path.read_text(encoding="utf-8").strip()

    def _write_config(self) -> None:
        permissions = {
            "*": "deny",
            "read": "allow",
            "edit": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "bash": "deny",
            "task": "deny",
            "external_directory": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "lsp": "deny",
            "skill": "deny",
            "question": "deny",
            "doom_loop": "deny",
        }
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"ollama/{self.model}",
            "provider": {
                "ollama": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Ollama locale Euri",
                    "options": {"baseURL": self.ollama_base_url},
                    "models": {self.model: {"name": self.model}},
                }
            },
            "permission": permissions,
            "agent": {
                self.AGENT_NAME: {
                    "description": (
                        "Costruisce un singolo strumento Python temporaneo per "
                        "verificare un problema numerico proposto da Euri."
                    ),
                    "mode": "primary",
                    "model": f"ollama/{self.model}",
                    "temperature": 0.15,
                    "steps": self.max_steps,
                    "prompt": self._agent_prompt(),
                    "permission": permissions,
                }
            },
        }
        (self.workspace / "opencode.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _check_local_model(self) -> None:
        """Fallisce presto se Ollama non espone il modello come tool-capable."""
        root = self.ollama_base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        request = urllib.request.Request(
            root.rstrip("/") + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise OpenCodeError(f"Ollama locale non raggiungibile: {exc}") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        match = next(
            (
                item for item in (models or [])
                if str(item.get("name") or item.get("model") or "") == self.model
            ),
            None,
        )
        if match is None:
            raise OpenCodeError(f"Modello locale non trovato in Ollama: {self.model}")
        capabilities = {
            str(item).strip().lower() for item in (match.get("capabilities") or [])
        }
        if "tools" not in capabilities:
            raise OpenCodeError(
                f"Il modello {self.model} non espone tool calling in Ollama "
                f"(capabilities={sorted(capabilities)})."
            )

    def start(self) -> None:
        if self._process is not None:
            return
        binary_path = shutil.which(self.binary)
        if not binary_path:
            raise OpenCodeError(
                f"OpenCode non e' installato o non e' nel PATH ({self.binary})."
            )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._check_local_model()
        self._write_config()
        self.port = _free_loopback_port()
        self.password = secrets.token_urlsafe(24)
        self.base_url = f"http://127.0.0.1:{self.port}"
        log_path = self.workspace / "opencode-server.log"
        self._log_stream = log_path.open("ab")
        env = os.environ.copy()
        env.update({
            "OPENCODE_SERVER_USERNAME": "euri",
            "OPENCODE_SERVER_PASSWORD": self.password,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        })
        self._process = subprocess.Popen(
            [
                binary_path,
                "serve",
                "--hostname", "127.0.0.1",
                "--port", str(self.port),
            ],
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + self.start_timeout
        last_error = "server non raggiungibile"
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                health = self._request("GET", "/global/health", timeout=2)
                if isinstance(health, dict) and health.get("healthy"):
                    logger.info(
                        "OpenCodeAdapter: server pronto pid={} port={} model={}",
                        self._process.pid, self.port, self.model,
                    )
                    return
            except OpenCodeError as exc:
                last_error = str(exc)
            time.sleep(0.2)
        self.close()
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError:
            pass
        raise OpenCodeError(
            f"OpenCode non si e' avviato entro {self.start_timeout}s: {last_error}"
            + (f" | log: {tail}" if tail else "")
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
    ) -> Any:
        if not self.base_url:
            raise OpenCodeError("Server OpenCode non avviato.")
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        token = base64.b64encode(f"euri:{self.password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.prompt_timeout
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-2000:]
            raise OpenCodeError(f"OpenCode HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenCodeError(f"OpenCode non raggiungibile: {exc}") from exc
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCodeError("Risposta OpenCode non valida.") from exc

    def create_session(self, title: str) -> str:
        payload = self._request("POST", "/session", {"title": title})
        session_id = str((payload or {}).get("id") or "")
        if not session_id:
            raise OpenCodeError("OpenCode non ha restituito l'id della sessione.")
        self.session_id = session_id
        return session_id

    def session_snapshot(self) -> dict[str, Any]:
        """Legge transcript e metadati prima del cleanup della sessione.

        L'audit e' best-effort: un endpoint diagnostico non disponibile non deve
        trasformare un tentativo riuscito in un errore operativo.
        """
        if not self.session_id or not self.base_url:
            return {"available": False, "reason": "session_not_active"}
        session = urllib.parse.quote(self.session_id, safe="")
        snapshot: dict[str, Any] = {
            "available": True,
            "session_id": self.session_id,
        }
        try:
            snapshot["session"] = self._request(
                "GET", f"/session/{session}", timeout=5
            )
        except OpenCodeError as exc:
            snapshot["session_error"] = str(exc)
        try:
            snapshot["messages"] = self._request(
                "GET", f"/session/{session}/message", timeout=5
            )
        except OpenCodeError as exc:
            snapshot["messages_error"] = str(exc)
        if "session" not in snapshot and "messages" not in snapshot:
            snapshot["available"] = False
        return snapshot

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        """Firma cheap per osservare una materializzazione senza leggere il contenuto."""
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return None
        if not path.is_file():
            return None
        return stat.st_mtime_ns, stat.st_size

    def prompt(
        self,
        message: str,
        *,
        completion_path: str | Path | None = None,
        stop_event: threading.Event | None = None,
        no_artifact_timeout: float | None = None,
    ) -> OpenCodeReply:
        """Invia un turno e riprende il controllo appena l'artefatto e' stabile.

        L'endpoint ``/message`` termina soltanto quando il modello chiude anche la
        propria risposta narrativa. Per un coding job il vero contratto, invece,
        e' la materializzazione del file: aspettare il monologo successivo puo'
        consumare l'intero timeout pur avendo gia' ottenuto ``main.py``. Quando
        ``completion_path`` cambia e resta stabile per alcuni poll, abortiamo il
        turno OpenCode e consegniamo il file al CodeRunner.
        """
        if not self.session_id:
            self.create_session("Euri temporary computational tool")
        session = urllib.parse.quote(self.session_id, safe="")
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        artifact_path = (
            Path(completion_path).resolve() if completion_path is not None else None
        )
        initial_signature = (
            self._file_signature(artifact_path) if artifact_path is not None else None
        )

        def _request_prompt() -> None:
            try:
                payload = self._request(
                    "POST",
                    f"/session/{session}/message",
                    {
                        "agent": self.AGENT_NAME,
                        "parts": [{"type": "text", "text": message}],
                    },
                    timeout=self.prompt_timeout,
                )
                result_queue.put((payload, None))
            except Exception as exc:  # inoltrato sul thread chiamante
                result_queue.put((None, exc))

        worker = threading.Thread(
            target=_request_prompt,
            name="opencode-prompt",
            daemon=True,
        )
        worker.start()
        started = time.monotonic()
        deadline = started + self.prompt_timeout
        no_artifact_deadline = None
        if artifact_path is not None and no_artifact_timeout is not None:
            no_artifact_deadline = started + max(
                1.0, min(float(no_artifact_timeout), float(self.prompt_timeout))
            )
        observed_signature: tuple[int, int] | None = None
        stable_since = 0.0
        while True:
            if stop_event is not None and stop_event.is_set():
                self.abort()
                raise OpenCodeError("Richiesta OpenCode interrotta.")

            try:
                payload, error = result_queue.get(timeout=0.2)
                break
            except queue.Empty:
                pass

            now = time.monotonic()
            if artifact_path is not None:
                signature = self._file_signature(artifact_path)
                if signature is not None and signature != initial_signature:
                    if signature != observed_signature:
                        observed_signature = signature
                        stable_since = now
                    elif now - stable_since >= 0.8:
                        elapsed = self.prompt_timeout - max(0.0, deadline - now)
                        logger.info(
                            "OpenCodeAdapter: artefatto {} stabile dopo {:.1f}s; "
                            "chiusura anticipata del turno",
                            artifact_path.name,
                            elapsed,
                        )
                        self.abort()
                        return OpenCodeReply(
                            text="artefatto materializzato",
                            raw={
                                "stopped_after_artifact": True,
                                "artifact": artifact_path.name,
                            },
                        )
                else:
                    observed_signature = None
                    stable_since = 0.0

                if (
                    no_artifact_deadline is not None
                    and observed_signature is None
                    and now >= no_artifact_deadline
                ):
                    elapsed = now - started
                    logger.warning(
                        "OpenCodeAdapter: nessun artefatto {} dopo {:.1f}s; "
                        "interruzione del tentativo",
                        artifact_path.name,
                        elapsed,
                    )
                    self.abort()
                    worker.join(timeout=3)
                    return OpenCodeReply(
                        text=(
                            f"Nessun {artifact_path.name} materializzato "
                            f"entro {elapsed:.1f}s."
                        ),
                        raw={
                            "stopped_no_artifact": True,
                            "artifact": artifact_path.name,
                            "elapsed_s": round(elapsed, 1),
                        },
                    )

            if now >= deadline:
                # Il timeout di urllib non e' un budget totale: durante uno stream
                # lungo puo' ripartire a ogni lettura. Qui imponiamo una deadline a
                # parete e fermiamo il server, spezzando anche la richiesta pendente.
                self.abort()
                self.delete_session()
                self._terminate_server()
                raise OpenCodeError(
                    f"OpenCode ha superato il timeout totale di {self.prompt_timeout}s."
                )
        if error is not None:
            if isinstance(error, OpenCodeError):
                raise error
            raise OpenCodeError(f"Richiesta OpenCode fallita: {error}") from error
        raw = payload if isinstance(payload, dict) else {"payload": payload}
        return OpenCodeReply(text=_response_text(payload), raw=raw)

    def abort(self) -> None:
        if not self.session_id or not self.base_url:
            return
        try:
            session = urllib.parse.quote(self.session_id, safe="")
            self._request("POST", f"/session/{session}/abort", {}, timeout=3)
        except OpenCodeError:
            pass

    def delete_session(self) -> None:
        """Elimina transcript e diff dal datastore globale di OpenCode."""
        if not self.session_id or not self.base_url:
            return
        session = urllib.parse.quote(self.session_id, safe="")
        try:
            self._request("DELETE", f"/session/{session}", timeout=3)
        except OpenCodeError as exc:
            # Il workspace resta comunque effimero, ma rendiamo osservabile un
            # eventuale residuo nel database di OpenCode.
            logger.warning(
                "OpenCodeAdapter: sessione {} non eliminata ({})",
                self.session_id,
                exc,
            )
            return
        self.session_id = ""

    def reset_session(self, title: str) -> str:
        """Chiude il tentativo corrente e riparte senza ereditarne il monologo."""
        self.abort()
        self.delete_session()
        return self.create_session(title)

    def close(self) -> None:
        self.abort()
        self.delete_session()
        self._terminate_server()

    def _terminate_server(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_stream is not None:
            try:
                self._log_stream.close()
            except OSError:
                pass
            self._log_stream = None
        self.base_url = ""

    def __enter__(self) -> "OpenCodeAdapter":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
