"""Confine durevole tra memoria personale e scenari sperimentali.

Lo scope non decide se una frase è vera: stabilisce in quale mondo epistemico
può essere recuperata e trasformata. I documenti legacy senza campo
``memory_scope`` appartengono alla memoria personale.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import re
import time
import unicodedata
from dataclasses import dataclass


PERSONAL_SCOPE = "personal"
EXPERIMENT_PREFIX = "experiment_"
INVALID_SCOPE = "invalid_scope"
ACTIVE_SCOPE_KEY = "euri:memory_scope:active"
DEFAULT_EXPERIMENT_TTL_SECONDS = 24 * 3600

_CURRENT_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "euri_memory_scope",
    default=PERSONAL_SCOPE,
)
_START_RE = re.compile(
    r"^\s*(?:euri[\s,;:!-]*)?"
    r"(?:inizia|avvia|apri|attiva|cominciamo|facciamo)\s+"
    r"(?:una\s+)?(?:sessione\s+)?(?:sperimentale|di\s+test|test)\b"
    r"(?:\s+(?:chiamata|denominata|nome|su|per|:|-)\s*)?(?P<label>.*)$",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^\s*(?:euri[\s,;:!-]*)?"
    r"(?:chiudi|termina|concludi|ferma|esci\s+dalla|disattiva)\s+"
    r"(?:la\s+)?(?:sessione\s+)?(?:sperimentale|di\s+test|test)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^\s*(?:euri[\s,;:!-]*)?"
    r"(?:in\s+che\s+modalit[aà]\s+(?:di\s+memoria\s+)?(?:sei|siamo)|"
    r"qual\s+[eè]\s+lo\s+scope|stato\s+della\s+sessione\s+sperimentale)\s*[?.!]*$",
    re.IGNORECASE,
)


def normalize_scope(value: str | None) -> str:
    """Normalizza solo valori già canonici; il resto va in quarantena.

    ``None``/vuoto resta ``personal`` per la migrazione dei documenti legacy.
    La trasformazione di un'etichetta umana in slug appartiene esclusivamente
    a :func:`experiment_scope`: ripulire qui un valore corrotto potrebbe farlo
    collidere con uno scope valido o, peggio, riportarlo nel personale.
    """
    raw = str(value or "").strip().lower()
    if not raw or raw == PERSONAL_SCOPE:
        return PERSONAL_SCOPE
    if re.fullmatch(r"experiment_[a-z0-9]+(?:_[a-z0-9]+)*", raw):
        suffix = raw[len(EXPERIMENT_PREFIX):]
        return raw if len(suffix) <= 64 else INVALID_SCOPE
    return INVALID_SCOPE


def experiment_scope(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(label or ""))
    ascii_label = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_label)
    slug = re.sub(r"_+", "_", slug).strip("_")[:64]
    if not slug:
        raise ValueError("serve un nome per la sessione sperimentale")
    return f"{EXPERIMENT_PREFIX}{slug}"


def scope_of(document: dict | None) -> str:
    if not isinstance(document, dict):
        return PERSONAL_SCOPE
    return normalize_scope(document.get("memory_scope"))


def is_experimental(scope: str | None) -> bool:
    return normalize_scope(scope).startswith(EXPERIMENT_PREFIX)


def current_scope() -> str:
    return normalize_scope(_CURRENT_SCOPE.get())


def bind_memory_scope(scope: str | None) -> str:
    """Imposta lo scope per il contesto sincrono corrente (es. un rerun UI)."""
    normalized = normalize_scope(scope)
    _CURRENT_SCOPE.set(normalized)
    return normalized


@contextlib.contextmanager
def use_memory_scope(scope: str | None):
    token = _CURRENT_SCOPE.set(normalize_scope(scope))
    try:
        yield current_scope()
    finally:
        _CURRENT_SCOPE.reset(token)


@dataclass(frozen=True)
class ScopeCommand:
    action: str
    label: str = ""


def parse_scope_command(text: str) -> ScopeCommand | None:
    value = str(text or "").strip()
    if not value:
        return None
    if _STOP_RE.match(value):
        return ScopeCommand("stop")
    if _STATUS_RE.match(value):
        return ScopeCommand("status")
    match = _START_RE.match(value)
    if not match:
        return None
    label = (match.group("label") or "").strip(" .,:;-")
    return ScopeCommand("start", label=label)


def _decode(raw) -> str:
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")


def active_scope_state(redis_client) -> dict:
    try:
        raw = redis_client.get(ACTIVE_SCOPE_KEY)
        data = json.loads(_decode(raw)) if raw else {}
    except Exception:
        return {
            "scope": PERSONAL_SCOPE,
            "label": "memoria personale",
            "active": False,
        }
    scope = normalize_scope(data.get("scope"))
    expires_at = float(data.get("expires_at") or 0.0)
    if not is_experimental(scope) or (expires_at and expires_at <= time.time()):
        try:
            redis_client.delete(ACTIVE_SCOPE_KEY)
        except Exception:
            pass
        return {
            "scope": PERSONAL_SCOPE,
            "label": "memoria personale",
            "active": False,
        }
    return {
        "scope": scope,
        "label": str(data.get("label") or scope.removeprefix(EXPERIMENT_PREFIX)),
        "active": True,
        "activated_at": float(data.get("activated_at") or 0.0),
        "expires_at": expires_at,
    }


def get_active_scope(redis_client) -> str:
    return active_scope_state(redis_client)["scope"]


def start_experiment(
    redis_client,
    label: str,
    *,
    ttl_seconds: int = DEFAULT_EXPERIMENT_TTL_SECONDS,
) -> dict:
    scope = experiment_scope(label)
    now_ts = time.time()
    ttl = max(300, int(ttl_seconds))
    state = {
        "schema_version": 1,
        "scope": scope,
        "label": str(label).strip(),
        "active": True,
        "activated_at": now_ts,
        "expires_at": now_ts + ttl,
    }
    redis_client.set(
        ACTIVE_SCOPE_KEY,
        json.dumps(state, ensure_ascii=False, sort_keys=True),
        ex=ttl,
    )
    return state


def stop_experiment(redis_client) -> dict:
    previous = active_scope_state(redis_client)
    redis_client.delete(ACTIVE_SCOPE_KEY)
    return previous


def derive_scope(documents: list[dict]) -> str | None:
    """Ritorna lo scope comune oppure None: i derivati cross-scope falliscono chiusi."""
    scopes = {scope_of(document) for document in documents if isinstance(document, dict)}
    if len(scopes) != 1 or INVALID_SCOPE in scopes:
        return None
    return next(iter(scopes))


def redis_tag_value(value: str) -> str:
    """Escape conservativo per valori TAG RediSearch."""
    return "".join(
        char if char.isalnum() or char == "_" else f"\\{char}"
        for char in str(value)
    )


def scope_clause(scope: str | None = None) -> str:
    return f"@memory_scope:{{{redis_tag_value(normalize_scope(scope or current_scope()))}}}"
