"""Audit locale dei tentativi del coding agent.

I transcript OpenCode possono contenere il problema aziendale e il codice
generato. Vivono quindi soltanto in ``research_logs/``, esclusi da Git, Redis,
Obsidian e retrieval, con permessi privati e retention esplicita.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

import config


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "unknown"))[:80]


def _tool_events(value: Any) -> list[dict[str, Any]]:
    """Estrae un indice leggibile senza sostituire il payload integrale."""
    events: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if str(node.get("type") or "").lower() == "tool":
                state = node.get("state") if isinstance(node.get("state"), dict) else {}
                events.append({
                    "tool": str(node.get("tool") or node.get("name") or "unknown"),
                    "call_id": str(
                        node.get("callID") or node.get("call_id") or node.get("id") or ""
                    ),
                    "status": str(state.get("status") or node.get("status") or "unknown"),
                    "error": str(state.get("error") or node.get("error") or "")[:2000],
                })
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return events


def _artifact_record(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"present": False}
    try:
        content = path.read_bytes()
        return {
            "present": True,
            "name": path.name,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    except OSError as exc:
        return {"present": True, "name": path.name, "read_error": str(exc)}


def _prune(directory: Path) -> None:
    cutoff = time.time() - max(
        1, int(config.CODE_AGENT_RESEARCH_LOG_RETENTION_DAYS)
    ) * 86400
    for path in directory.glob("coding-attempt-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def capture_coding_attempt(
    *,
    job_id: str,
    attempt: int,
    model: str,
    request_prompt: str,
    reply_text: str,
    reply_raw: Any,
    session_snapshot: Any,
    outcome: str,
    artifact_path: str | Path | None = None,
    execution: dict[str, Any] | None = None,
) -> str:
    """Persiste la ground truth diagnostica di un tentativo, best-effort."""
    if not config.CODE_AGENT_RESEARCH_LOG_ENABLED:
        return ""
    try:
        directory = Path(config.CODE_AGENT_RESEARCH_LOG_DIR)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        _prune(directory)

        captured_at = time.time()
        combined = {"reply": reply_raw, "session": session_snapshot}
        event = {
            "schema_version": 1,
            "event": "coding_agent_attempt",
            "captured_at": captured_at,
            "captured_at_iso": datetime.fromtimestamp(
                captured_at, tz=timezone.utc
            ).isoformat(),
            "job_id": str(job_id),
            "attempt": int(attempt),
            "model": str(model),
            "outcome": str(outcome),
            "request_prompt": str(request_prompt),
            "reply_text": str(reply_text or ""),
            "reply_raw": reply_raw,
            "session_snapshot": session_snapshot,
            "tool_events": _tool_events(combined),
            "artifact": _artifact_record(
                Path(artifact_path) if artifact_path is not None else None
            ),
            "execution": execution or {},
        }
        stamp = datetime.fromtimestamp(captured_at, tz=timezone.utc).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        base = (
            f"coding-attempt-{stamp}-{_safe_name(job_id)}-"
            f"{int(attempt):02d}.json"
        )
        path = directory / base
        suffix = 1
        while path.exists():
            path = directory / f"{base[:-5]}-{suffix:02d}.json"
            suffix += 1
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(event, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
        logger.info(
            "Coding agent audit: job={} tentativo={} outcome={} tools={} file={}",
            job_id,
            attempt,
            outcome,
            len(event["tool_events"]),
            path,
        )
        return str(path)
    except Exception as exc:
        # La ricerca non deve alterare l'esito operativo del coding agent.
        logger.warning("Coding agent audit non disponibile: {}", exc)
        return ""
