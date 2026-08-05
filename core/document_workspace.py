"""Workspace documentale temporaneo condiviso fra UI, voce e mobile.

Redis e' il control plane (selezione, hash, versione e ricevute); il filesystem
resta il data plane. Questo stato non e' memoria cognitiva e scade da solo.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import config
from core.memory_scope import current_scope, normalize_scope


WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_KEY_PREFIX = "euri:document_workspace:v1:"


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class DocumentWorkspace:
    """Manifest temporaneo owner-scoped, visibile da processi distinti."""

    def __init__(
        self,
        redis_client,
        *,
        ttl_s: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.r = redis_client
        self.ttl_s = int(
            ttl_s
            if ttl_s is not None
            else getattr(config, "DOCUMENT_WORKSPACE_TTL_SECONDS", 30 * 60)
        )
        self._clock = clock

    @staticmethod
    def _key(memory_scope: str | None = None) -> str:
        return f"{WORKSPACE_KEY_PREFIX}{normalize_scope(memory_scope or current_scope())}"

    @staticmethod
    def _receipt_key(memory_scope: str | None = None) -> str:
        return DocumentWorkspace._key(memory_scope) + ":receipts"

    @staticmethod
    def _operation_key(memory_scope: str | None = None) -> str:
        return DocumentWorkspace._key(memory_scope) + ":operation"

    def _load_manifest(self, memory_scope: str | None = None) -> dict:
        raw = self.r.get(self._key(memory_scope))
        if not raw:
            return {}
        try:
            value = json.loads(_decode(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if int(value.get("schema_version") or 0) != WORKSPACE_SCHEMA_VERSION:
            return {}
        if self._clock() >= float(value.get("expires_at") or 0):
            return {}
        return value

    @staticmethod
    def _normalise_document(raw: dict, *, now: float) -> dict | None:
        content = str(raw.get("content") or "")
        if not content.strip():
            return None
        filename = str(raw.get("filename") or "documento").strip() or "documento"
        source_path = str(raw.get("source_path") or "").strip()
        digest = str(raw.get("sha256") or "").strip()
        if not digest:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "id": str(raw.get("id") or f"artifact:{uuid.uuid4()}"),
            "filename": filename,
            "source_path": source_path,
            "source": str(raw.get("source") or source_path or "document_workspace"),
            "kind": str(raw.get("kind") or "document"),
            "content": content,
            "sha256": digest,
            "bytes": int(raw.get("bytes") or len(content.encode("utf-8"))),
            "captured_at": float(raw.get("captured_at") or now),
            "version": max(1, int(raw.get("version") or 1)),
        }

    def publish_documents(
        self,
        documents: list[dict],
        *,
        active_filename: str = "",
        source_channel: str = "",
        preserve_existing: bool = False,
        allowed_existing_paths: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> dict:
        now = self._clock()
        previous = self._load_manifest(memory_scope)
        old_by_identity = {}
        for item in previous.get("documents") or []:
            identity = str(item.get("source_path") or item.get("filename") or "").casefold()
            if identity:
                old_by_identity[identity] = item
        cleaned = [self._normalise_document(item, now=now) for item in documents]
        cleaned = [item for item in cleaned if item]
        if not cleaned:
            self.clear(memory_scope)
            return {}
        for item in cleaned:
            identity = str(item.get("source_path") or item.get("filename") or "").casefold()
            old = old_by_identity.get(identity)
            if not old:
                continue
            item["id"] = str(old.get("id") or item["id"])
            item["version"] = int(old.get("version") or 1) + int(
                str(old.get("sha256") or "") != item["sha256"]
            )
        if preserve_existing:
            # Gli upload Streamlit formano una coda: i documenti nuovi sostituiscono
            # l'eventuale versione omonima e finiscono in fondo (piu' recenti). Non
            # trasciniamo invece sorgenti eterogenee come clipboard o scansioni libere.
            incoming = {
                str(item.get("source_path") or item.get("filename") or "").casefold()
                for item in cleaned
            }
            allowed = (
                {str(path).casefold() for path in allowed_existing_paths}
                if allowed_existing_paths is not None
                else None
            )
            queued = []
            for item in previous.get("documents") or []:
                identity = str(
                    item.get("source_path") or item.get("filename") or ""
                ).casefold()
                if (
                    identity
                    and identity not in incoming
                    and (allowed is None or identity in allowed)
                    and not str(item.get("filename") or "").startswith(".")
                    and str(item.get("kind") or "") == "uploaded_document"
                ):
                    queued.append(item)
            cleaned = queued + cleaned
        active_fold = active_filename.casefold().strip()
        active_id = ""
        if active_fold:
            for item in cleaned:
                if item["filename"].casefold() == active_fold:
                    active_id = item["id"]
                    break
        elif len(cleaned) == 1:
            active_id = cleaned[0]["id"]
        manifest = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "memory_scope": normalize_scope(memory_scope or current_scope()),
            "source_channel": str(source_channel or ""),
            "updated_at": now,
            "expires_at": now + self.ttl_s,
            "active_artifact_id": active_id,
            "documents": cleaned[-12:],
        }
        self.r.set(
            self._key(memory_scope),
            json.dumps(manifest, ensure_ascii=False),
            ex=self.ttl_s,
        )
        # Una nuova lettura apre un nuovo lavoro: non mostrare come corrente la
        # ricevuta eventualmente legata al documento precedente.
        self.r.delete(self._receipt_key(memory_scope))
        return _json_copy(manifest)

    def snapshot(self, memory_scope: str | None = None) -> dict:
        manifest = self._load_manifest(memory_scope)
        operation = self.get_operation(memory_scope=memory_scope)
        if not manifest:
            return {
                "documents": [],
                "receipts": [],
                "active_artifact_id": "",
                "operation": operation or {},
            }
        receipts = []
        try:
            raw_items = self.r.lrange(self._receipt_key(memory_scope), 0, 9)
            for raw in raw_items:
                item = json.loads(_decode(raw))
                if isinstance(item, dict):
                    receipts.append(item)
        except Exception:
            receipts = []
        result = _json_copy(manifest)
        result["receipts"] = receipts
        result["operation"] = operation or {}
        return result

    def get_operation(
        self,
        *,
        max_age_seconds: int | None = None,
        memory_scope: str | None = None,
    ) -> dict | None:
        """Ultima operazione documentale, condivisa fra processi ma non memorizzata."""
        raw = self.r.get(self._operation_key(memory_scope))
        if not raw:
            return None
        try:
            operation = json.loads(_decode(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(operation, dict):
            return None
        now = self._clock()
        if now >= float(operation.get("expires_at") or 0):
            return None
        if max_age_seconds is not None and (
            now - float(operation.get("updated_at") or 0) > max_age_seconds
        ):
            return None
        return _json_copy(operation)

    def start_operation(
        self,
        kind: str,
        *,
        source_channel: str,
        filename: str = "",
        tool_name: str = "",
        memory_scope: str | None = None,
    ) -> dict:
        """Pubblica subito un lavoro in corso, prima dell'inferenza o del tool."""
        now = self._clock()
        operation = {
            "id": f"document-operation:{uuid.uuid4()}",
            "kind": str(kind or "document_operation"),
            "status": "running",
            "source_channel": str(source_channel or "unknown"),
            "filename": str(filename or ""),
            "tool_name": str(tool_name or ""),
            "message": "",
            "started_at": now,
            "updated_at": now,
            "expires_at": now + self.ttl_s,
        }
        self.r.set(
            self._operation_key(memory_scope),
            json.dumps(operation, ensure_ascii=False),
            ex=self.ttl_s,
        )
        return _json_copy(operation)

    def claim_operation(
        self,
        operation_id: str,
        *,
        tool_name: str,
        memory_scope: str | None = None,
    ) -> dict | None:
        """Associa il tool reale a un lavoro UI già annunciato."""
        operation = self.get_operation(memory_scope=memory_scope)
        if (
            not operation
            or operation.get("status") != "running"
            or str(operation.get("id") or "") != str(operation_id or "")
        ):
            return None
        now = self._clock()
        operation["tool_name"] = str(tool_name or "")
        operation["updated_at"] = now
        operation["expires_at"] = now + self.ttl_s
        self.r.set(
            self._operation_key(memory_scope),
            json.dumps(operation, ensure_ascii=False),
            ex=self.ttl_s,
        )
        return _json_copy(operation)

    def finish_operation(
        self,
        operation_id: str,
        *,
        success: bool,
        message: str = "",
        memory_scope: str | None = None,
    ) -> dict | None:
        """Chiude soltanto l'operazione ancora corrente; esiti vecchi non la sovrascrivono."""
        operation = self.get_operation(memory_scope=memory_scope)
        if not operation or str(operation.get("id") or "") != str(operation_id or ""):
            return None
        now = self._clock()
        operation.update({
            "status": "completed" if success else "failed",
            "message": str(message or "")[:800],
            "updated_at": now,
            "finished_at": now,
            "expires_at": now + self.ttl_s,
        })
        self.r.set(
            self._operation_key(memory_scope),
            json.dumps(operation, ensure_ascii=False),
            ex=self.ttl_s,
        )
        return _json_copy(operation)

    def get_active(
        self,
        *,
        max_age_seconds: int | None = None,
        memory_scope: str | None = None,
    ) -> dict | None:
        manifest = self._load_manifest(memory_scope)
        active_id = str(manifest.get("active_artifact_id") or "")
        if not active_id:
            return None
        for item in manifest.get("documents") or []:
            if str(item.get("id") or "") != active_id:
                continue
            if max_age_seconds is not None and (
                self._clock() - float(item.get("captured_at") or 0) > max_age_seconds
            ):
                return None
            artifact = _json_copy(item)
            artifact["filenames"] = [artifact["filename"]]
            artifact["workspace_updated_at"] = manifest.get("updated_at")
            return artifact
        return None

    def select(self, artifact_id: str, memory_scope: str | None = None) -> bool:
        manifest = self._load_manifest(memory_scope)
        if not manifest or artifact_id not in {
            str(item.get("id") or "") for item in manifest.get("documents") or []
        }:
            return False
        manifest["active_artifact_id"] = artifact_id
        manifest["updated_at"] = self._clock()
        manifest["expires_at"] = self._clock() + self.ttl_s
        self.r.set(
            self._key(memory_scope),
            json.dumps(manifest, ensure_ascii=False),
            ex=self.ttl_s,
        )
        return True

    def record_receipt(self, receipt: dict, memory_scope: str | None = None) -> None:
        if not isinstance(receipt, dict) or not receipt.get("filepath"):
            return
        payload = _json_copy({**receipt, "recorded_at": self._clock()})
        key = self._receipt_key(memory_scope)
        pipe = self.r.pipeline(transaction=True)
        pipe.lpush(key, json.dumps(payload, ensure_ascii=False))
        pipe.ltrim(key, 0, 9)
        pipe.expire(key, self.ttl_s)
        pipe.execute()

    def clear(self, memory_scope: str | None = None) -> None:
        self.r.delete(
            self._key(memory_scope),
            self._receipt_key(memory_scope),
            self._operation_key(memory_scope),
        )

    @staticmethod
    def file_document(path: Path, content: str, *, kind: str = "uploaded_document") -> dict:
        payload = path.read_bytes() if path.is_file() else content.encode("utf-8")
        return {
            "filename": path.name,
            "source_path": str(path.resolve()) if path.exists() else str(path),
            "source": str(path.parent),
            "kind": kind,
            "content": content,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
