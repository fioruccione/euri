"""Ground truth di trasporto per i prompt realtime inviati a Ollama.

Il client Python invia ``messages`` come JSON a ``/api/chat``; il renderer
``gemma4`` trasforma poi quel payload nel prompt tokenizzato all'interno di
Ollama. Questo modulo intercetta i byte HTTP effettivi prima dell'invio e li
scrive asincronamente in un archivio di ricerca separato da Redis e Obsidian.

Ollama espone ``prompt_eval_count`` nella risposta, ma non espone né gli offset
token dei sottoblocchi, né il flag di troncamento. Quei campi restano quindi
esplicitamente ``unavailable``: non vengono sostituiti con stime.
"""
from __future__ import annotations

import atexit
import contextvars
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from loguru import logger


_ACTIVE_CAPTURE: contextvars.ContextVar["PromptCapture | None"] = (
    contextvars.ContextVar("euri_prompt_research_capture", default=None)
)
_HOOKED_CLIENT_IDS: set[int] = set()
_HOOK_LOCK = threading.Lock()
_WRITER: "_ResearchWriter | None" = None
_WRITER_LOCK = threading.Lock()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _response_metric(response: Any, name: str) -> int | None:
    value = getattr(response, name, None)
    if value is None and isinstance(response, dict):
        value = response.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rag_blocks(context: str) -> list[tuple[int, int, str]]:
    """Ritorna i blocchi separati da righe vuote preservandone gli offset."""
    blocks: list[tuple[int, int, str]] = []
    for match in re.finditer(r"(?:\A|\n\n)(.*?)(?=\n\n|\Z)", context or "", re.DOTALL):
        value = match.group(1)
        if not value.strip():
            continue
        start = match.start(1)
        blocks.append((start, start + len(value), value))
    return blocks


def _message_content_offsets(
    messages: list[dict],
    value: str,
) -> dict[str, Any] | None:
    if not value:
        return None
    stream_before = 0
    for index, message in enumerate(messages):
        content = str(message.get("content") or "")
        start = content.find(value)
        if start >= 0:
            absolute = stream_before + start
            total = sum(len(str(item.get("content") or "")) for item in messages)
            return {
                "message_index": index,
                "role": str(message.get("role") or ""),
                "char_offset_in_message": start,
                "char_offset_from_message_end": len(content) - (start + len(value)),
                "content_stream_char_offset_from_start": absolute,
                "content_stream_char_offset_from_end": total - (absolute + len(value)),
            }
        stream_before += len(content)
    return None


def analyze_transport_body(
    body: bytes,
    *,
    identity_block: str = "",
    rag_context: str = "",
) -> dict[str, Any]:
    """Analisi pura del body HTTP; utile anche per regressioni senza I/O."""
    body_text = body.decode("utf-8")
    payload = json.loads(body_text)
    messages = [dict(item) for item in (payload.get("messages") or [])]
    total_content_chars = sum(len(str(item.get("content") or "")) for item in messages)

    identity_location = _message_content_offsets(messages, identity_block)
    identity = {
        "present": identity_location is not None,
        "sha256": _sha256_text(identity_block) if identity_block else None,
        "chars": len(identity_block),
        "location": identity_location,
        "token_offset_from_start": None,
        "token_offset_from_end": None,
        "token_offset_status": "unavailable_ollama_renderer_internal",
    }

    rag_location = _message_content_offsets(messages, rag_context)
    rag_entries: list[dict[str, Any]] = []
    for ordinal, (start, end, block) in enumerate(_rag_blocks(rag_context), 1):
        entry = {
            "ordinal": ordinal,
            "label": block.splitlines()[0][:180],
            "sha256": _sha256_text(block),
            "chars": len(block),
            "char_offset_in_rag_context": start,
            "char_offset_from_rag_context_end": len(rag_context) - end,
            "token_offset_from_start": None,
            "token_offset_from_end": None,
            "token_offset_status": "unavailable_ollama_renderer_internal",
        }
        if rag_location:
            entry["message_index"] = rag_location["message_index"]
            entry["char_offset_in_message"] = (
                rag_location["char_offset_in_message"] + start
            )
        rag_entries.append(entry)

    return {
        "model": payload.get("model"),
        "message_count": len(messages),
        "message_roles": [str(item.get("role") or "") for item in messages],
        "message_content_chars": total_content_chars,
        "identity_block": identity,
        "rag_context": {
            "present": rag_location is not None if rag_context else False,
            "sha256": _sha256_text(rag_context) if rag_context else None,
            "chars": len(rag_context),
            "location": rag_location,
            "blocks": rag_entries,
        },
        "compiled_prompt": {
            "status": "unavailable_ollama_renderer_internal",
            "reason": (
                "il client invia messages JSON; il renderer gemma4 compila e "
                "tokenizza il prompt dentro Ollama"
            ),
        },
    }


class _ResearchWriter:
    def __init__(self, directory: Path, retention_days: int, max_file_mb: int):
        self.directory = Path(directory)
        self.retention_days = max(1, int(retention_days))
        self.max_file_bytes = max(1, int(max_file_mb)) * 1024 * 1024
        self.events: queue.Queue[dict | None] = queue.Queue(maxsize=512)
        self.thread = threading.Thread(
            target=self._run,
            name="prompt-research-log",
            daemon=True,
        )
        self.thread.start()

    def submit(self, event: dict) -> bool:
        try:
            self.events.put_nowait(event)
            return True
        except queue.Full:
            logger.warning("Prompt research log: coda piena, evento non persistito")
            return False

    def close(self) -> None:
        try:
            self.events.put_nowait(None)
        except queue.Full:
            return
        self.thread.join(timeout=2.0)

    def _prune(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        for path in self.directory.glob("prompt-capture-*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _target(self, stamp: float) -> Path:
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
        base = self.directory / f"prompt-capture-{day}.jsonl"
        if not base.exists() or base.stat().st_size < self.max_file_bytes:
            return base
        index = 1
        while True:
            candidate = self.directory / f"prompt-capture-{day}-{index:02d}.jsonl"
            if not candidate.exists() or candidate.stat().st_size < self.max_file_bytes:
                return candidate
            index += 1

    def _run(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            self._prune()
        except OSError as exc:
            logger.warning(f"Prompt research log non disponibile: {exc}")
            return
        written = 0
        while True:
            event = self.events.get()
            if event is None:
                return
            try:
                path = self._target(float(event.get("captured_at") or time.time()))
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
                written += 1
                if written % 100 == 0:
                    self._prune()
            except OSError as exc:
                logger.warning(f"Prompt research log: scrittura fallita ({exc})")


def _writer() -> _ResearchWriter | None:
    global _WRITER
    if not getattr(config, "PROMPT_RESEARCH_LOG_ENABLED", False):
        return None
    with _WRITER_LOCK:
        if _WRITER is None:
            _WRITER = _ResearchWriter(
                Path(config.PROMPT_RESEARCH_LOG_DIR),
                config.PROMPT_RESEARCH_LOG_RETENTION_DAYS,
                config.PROMPT_RESEARCH_LOG_MAX_FILE_MB,
            )
    return _WRITER


def _close_writer() -> None:
    global _WRITER
    with _WRITER_LOCK:
        writer, _WRITER = _WRITER, None
    if writer is not None:
        writer.close()


atexit.register(_close_writer)


def _request_hook(request) -> None:
    capture = _ACTIVE_CAPTURE.get()
    if capture is None or not str(request.url.path).endswith("/api/chat"):
        return
    try:
        started_ns = time.perf_counter_ns()
        body = bytes(request.content)
        event = {
            "schema_version": 1,
            "event": "request",
            "request_id": capture.request_id,
            "captured_at": time.time(),
            "capture_label": capture.capture_label,
            "conversation_id": capture.conversation_id,
            "memory_scope": capture.memory_scope,
            "thinking_reason": capture.thinking_reason,
            "research_storage": {
                "retrieval_eligible": False,
                "redis_key": None,
                "obsidian_path": None,
            },
            "http": {
                "method": request.method,
                "url_path": request.url.path,
                "body_utf8": body.decode("utf-8"),
                "body_bytes": len(body),
                "body_sha256": _sha256_bytes(body),
            },
            "analysis": analyze_transport_body(
                body,
                identity_block=capture.identity_block,
                rag_context=capture.rag_context,
            ),
        }
        event["capture_hook_us"] = round(
            (time.perf_counter_ns() - started_ns) / 1000, 3
        )
        writer = _writer()
        if writer is not None:
            capture.transport_captured = writer.submit(event)
    except Exception as exc:
        # L'osservatore non può mai impedire la richiesta che sta osservando.
        logger.warning(f"Prompt research log: cattura fallita, invio invariato ({exc})")


def _install_hook(client: Any) -> bool:
    http_client = getattr(client, "_client", None)
    hooks = getattr(http_client, "event_hooks", None)
    if not isinstance(hooks, dict):
        return False
    client_id = id(http_client)
    with _HOOK_LOCK:
        if client_id in _HOOKED_CLIENT_IDS:
            return True
        hooks.setdefault("request", []).append(_request_hook)
        _HOOKED_CLIENT_IDS.add(client_id)
    return True


@dataclass
class PromptCapture:
    client: Any
    identity_block: str = ""
    rag_context: str = ""
    capture_label: str = "brain_primary"
    conversation_id: str = ""
    memory_scope: str = ""
    thinking_reason: str = ""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transport_captured: bool = False
    _token: contextvars.Token | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "PromptCapture":
        if not getattr(config, "PROMPT_RESEARCH_LOG_ENABLED", False):
            return self
        try:
            if not _install_hook(self.client) or _writer() is None:
                return self
            self._token = _ACTIVE_CAPTURE.set(self)
        except Exception as exc:
            logger.warning(f"Prompt research log: setup fallito, invio invariato ({exc})")
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        if self._token is not None:
            _ACTIVE_CAPTURE.reset(self._token)
        if exc is not None:
            self.fail(exc)

    def complete(self, response: Any, *, latency_ms: float) -> None:
        if not self.transport_captured:
            return
        try:
            writer = _writer()
            if writer is None:
                return
            writer.submit({
                "schema_version": 1,
                "event": "completion",
                "request_id": self.request_id,
                "captured_at": time.time(),
                "capture_label": self.capture_label,
                "transport_captured": self.transport_captured,
                "latency_ms": round(float(latency_ms), 3),
                "prompt_eval_count": _response_metric(response, "prompt_eval_count"),
                "eval_count": _response_metric(response, "eval_count"),
                "done_reason": getattr(response, "done_reason", None),
                "truncation": {
                    "status": "unavailable_in_ollama_chat_response",
                    "cause": None,
                },
            })
        except Exception as exc:
            logger.warning(f"Prompt research log: completion non salvata ({exc})")

    def fail(self, exc: BaseException) -> None:
        if not self.transport_captured:
            return
        try:
            writer = _writer()
            if writer is None:
                return
            writer.submit({
                "schema_version": 1,
                "event": "failure",
                "request_id": self.request_id,
                "captured_at": time.time(),
                "capture_label": self.capture_label,
                "transport_captured": self.transport_captured,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            })
        except Exception as log_exc:
            logger.warning(f"Prompt research log: failure non salvata ({log_exc})")


def capture_final_prompt(
    client: Any,
    *,
    identity_block: str = "",
    rag_context: str = "",
    capture_label: str = "brain_primary",
    conversation_id: str = "",
    memory_scope: str = "",
    thinking_reason: str = "",
) -> PromptCapture:
    return PromptCapture(
        client=client,
        identity_block=identity_block,
        rag_context=rag_context,
        capture_label=capture_label,
        conversation_id=conversation_id,
        memory_scope=memory_scope,
        thinking_reason=thinking_reason,
    )
