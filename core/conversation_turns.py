"""Archivio durevole dei turni conversazionali originali.

I turni non sono memorie cognitive e non partecipano ai loop: sono evidenza
locale, immutabile e indirizzabile. Le memorie passive possono riferirli tramite
``turn_ref`` e il retrieval dual-channel può idratarli senza usare la parafrasi
come substrato di risposta.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from loguru import logger
from utils.date_utils import format_datetime_full, from_timestamp
from core.memory_scope import PERSONAL_SCOPE, normalize_scope


TURN_KEY_PREFIX = "euri:turn:"
TURN_SCHEMA_VERSION = 1
TURN_RENDER_VERSION = "absolute-time-auth-channel-v1"
VERBATIM_LIFECYCLE_REPORT_KEY = "euri:verbatim:lifecycle:latest"
VERBATIM_LIFECYCLE_PENDING_KEY = "euri:verbatim:lifecycle:review_pending"
LEGACY_VOICE_BACKFILL_KEY = "euri:verbatim:legacy_voice_backfill:v1"
_TURN_REF_RE = re.compile(r"^(?P<conversation>[^:\s]+):(?P<seq>[1-9]\d*)$")
_LOG_TIME_RE = re.compile(r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
_LOG_STT_RE = re.compile(r"\bSTT:\s+'(?P<content>.*)'\s+\(lang=", re.DOTALL)
_LOG_INTENT_RE = re.compile(
    r"\bIntent:\s+[A-Z_]+\s+[—-]\s+'(?P<content>.*)'\s*$",
    re.DOTALL,
)
_CHRONOLOGY_STOP_WORDS = {
    "a", "al", "alla", "alle", "anche", "che", "chi", "come", "con", "cosa",
    "data", "del", "della", "delle", "detto", "di", "e", "euri", "gli", "hai",
    "ho", "il", "in", "la", "le", "lo", "mi", "nominato", "parlato", "per",
    "prima", "quando", "raccontato", "ricordi", "su", "ti", "ultima", "ultimo",
    "una", "volta",
}


def make_turn_ref(conversation_id: str, seq: int) -> str:
    conversation = str(conversation_id or "").strip()
    turn_seq = int(seq)
    if not conversation or ":" in conversation or turn_seq < 1:
        raise ValueError("conversation_id/seq non validi per turn_ref")
    return f"{conversation}:{turn_seq}"


def turn_key(turn_ref: str) -> str:
    match = _TURN_REF_RE.fullmatch(str(turn_ref or "").strip())
    if not match:
        raise ValueError(f"turn_ref non valido: {turn_ref!r}")
    return (
        f"{TURN_KEY_PREFIX}{match.group('conversation')}:{int(match.group('seq'))}"
    )


@dataclass(frozen=True)
class ArchivedTurn:
    turn_ref: str
    conversation_id: str
    seq: int
    role: str
    speaker: str
    content: str
    trusted: bool
    observed_at: float
    segment_id: int | None
    memory_scope: str = PERSONAL_SCOPE

    def render(self) -> str:
        # Il verbatim prova che la frase è stata pronunciata in quel momento,
        # non che descriva ancora lo stato presente. Data assoluta e canale
        # restano accanto alla fonte quando entra nel prompt.
        observed = format_datetime_full(from_timestamp(self.observed_at))
        channel = "canale autenticato" if self.trusted else "canale non autenticato"
        scope = (
            f"; scenario sperimentale {self.memory_scope.removeprefix('experiment_')}"
            if self.memory_scope.startswith("experiment_")
            else ""
        )
        return (
            f"[Turno originale del {observed}; {channel}{scope}] "
            f"{self.speaker}: {self.content}"
        )


class ConversationTurnStore:
    """Persistenza esatta, idempotente e senza TTL dei turni sorgente."""

    def __init__(self, redis_client):
        self.r = redis_client
        from core.conversation_continuity import ConversationContinuityStore
        self.continuity = ConversationContinuityStore(redis_client)

    @staticmethod
    def _speaker(role: str) -> str:
        if role == "user":
            return config.OWNER_DISPLAY_NAME
        if role == "assistant":
            return config.ASSISTANT_DISPLAY_NAME
        return role or "?"

    def persist(self, message: dict) -> str:
        ref = str(message.get("turn_ref") or "").strip()
        if not ref:
            ref = make_turn_ref(message["conversation_id"], message["seq"])
        key = turn_key(ref)
        doc = {
            "schema_version": TURN_SCHEMA_VERSION,
            "turn_ref": ref,
            "conversation_id": str(message.get("conversation_id") or ""),
            "seq": int(message.get("seq")),
            "role": str(message.get("role") or ""),
            "speaker": self._speaker(str(message.get("role") or "")),
            # L'archivio e' la prova verbatim. La versione interpretata resta
            # additiva e non sostituisce mai cio' che l'utente ha pronunciato.
            "content": str(message.get("raw_content", message.get("content")) or ""),
            "trusted": bool(message.get("trusted")),
            "observed_at": float(message.get("observed_at")),
            "segment_id": message.get("segment_id"),
            "memory_scope": normalize_scope(message.get("memory_scope")),
        }
        if message.get("raw_content") is not None:
            doc["interpreted_content"] = str(message.get("content") or "")
        if message.get("semantic_frame"):
            doc["semantic_frame"] = message["semantic_frame"]
        if message.get("archive_origin"):
            doc["archive_origin"] = str(message["archive_origin"])
        if message.get("source_locator"):
            doc["source_locator"] = str(message["source_locator"])
        # Lo stesso ref identifica lo stesso turno: la riscrittura è idempotente.
        self.r.json().set(key, "$", doc)
        try:
            self.continuity.record(doc)
        except Exception as exc:
            # L'archivio verbatim ha precedenza assoluta: una cache temporanea
            # non deve mai trasformare una scrittura riuscita in un fallimento.
            logger.debug(f"Continuità conversazionale non aggiornata per {ref} ({exc})")
        return ref

    def restore_into(self, brain, memory_scope: str | None = None) -> int:
        """Reidrata il Brain da una capsule recente, senza journal o nuove scritture."""
        try:
            snapshot = self.continuity.load(memory_scope)
        except Exception as exc:
            logger.debug(f"Continuità conversazionale non disponibile ({exc})")
            return 0
        if snapshot is None:
            return 0
        restored = brain.restore_continuity(
            list(snapshot.turns),
            memory_scope=snapshot.memory_scope,
            prompt_context=snapshot.render_for_prompt(),
        )
        if restored:
            logger.info(
                "Continuità conversazionale: {} turni, {} entità, {} fili aperti "
                "ripristinati per scope {}",
                restored,
                len(snapshot.active_entities),
                len(snapshot.open_loops),
                snapshot.memory_scope,
            )
        return restored

    def sync_into(self, brain, memory_scope: str | None = None) -> int:
        """Pull idempotente dei turni recenti creati da UI/voce dopo il boot."""
        try:
            snapshot = self.continuity.load(memory_scope)
        except Exception as exc:
            logger.debug(f"Sync continuità non disponibile ({exc})")
            return 0
        if snapshot is None:
            return 0
        synced = brain.sync_continuity(
            list(snapshot.turns),
            memory_scope=snapshot.memory_scope,
            prompt_context=snapshot.render_for_prompt(),
        )
        if synced:
            logger.info(
                "Continuità conversazionale live: {} nuovi turni importati per scope {}",
                synced,
                snapshot.memory_scope,
            )
        return synced

    def persist_many(self, messages: list[dict]) -> int:
        persisted = 0
        for message in messages:
            try:
                self.persist(message)
                persisted += 1
            except Exception as exc:
                logger.error(
                    "Archivio turni: persistenza fallita per {}: {}",
                    message.get("turn_ref") or message.get("seq"),
                    exc,
                )
                raise
        return persisted

    def get(self, turn_ref: str) -> ArchivedTurn | None:
        try:
            raw = self.r.json().get(turn_key(turn_ref), "$")
        except (TypeError, ValueError):
            return None
        except Exception as exc:
            logger.debug(f"Archivio turni: lettura {turn_ref} fallita ({exc})")
            return None
        if not raw:
            return None
        doc = raw[0] if isinstance(raw, list) else raw
        try:
            return ArchivedTurn(
                turn_ref=str(doc["turn_ref"]),
                conversation_id=str(doc["conversation_id"]),
                seq=int(doc["seq"]),
                role=str(doc.get("role") or ""),
                speaker=str(doc.get("speaker") or self._speaker(doc.get("role") or "")),
                content=str(doc.get("content") or ""),
                trusted=bool(doc.get("trusted")),
                observed_at=float(doc["observed_at"]),
                segment_id=(
                    int(doc["segment_id"])
                    if doc.get("segment_id") is not None
                    else None
                ),
                memory_scope=normalize_scope(doc.get("memory_scope")),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Archivio turni: documento malformato per {turn_ref}")
            return None

    def render(self, turn_ref: str) -> str:
        turn = self.get(turn_ref)
        return turn.render() if turn else ""

    @staticmethod
    def chronology_terms(subject: str) -> list[str]:
        """Termini sicuri e distintivi per la ricerca full-text sui turni.

        Il soggetto arriva dal classificatore semantico. Qui non se ne deduce il
        significato: si eliminano soltanto parole di servizio e caratteri che
        potrebbero alterare la sintassi RediSearch.
        """
        terms = []
        for raw in re.findall(r"[^\W_]+", str(subject or "").casefold()):
            if (
                len(raw) >= 2
                and raw not in _CHRONOLOGY_STOP_WORDS
                and raw not in terms
            ):
                terms.append(raw)
        return terms[:6]

    def _chronological_query(
        self,
        subject: str,
        memory_scope: str | None = None,
    ) -> tuple[str, list[str], str]:
        """Query condivisa da ricerca e conteggio: due forme diverse mentirebbero."""
        from core.memory_scope import current_scope, redis_tag_value, scope_clause

        terms = self.chronology_terms(subject)
        scope = normalize_scope(memory_scope or current_scope())
        if not terms:
            return "", [], scope
        query_text = (
            f"({scope_clause(scope)}) "
            f"(@role:{{{redis_tag_value('user')}}}) "
            f"(@content:({' '.join(terms)}))"
        )
        return query_text, terms, scope

    def count_chronological(
        self,
        subject: str,
        *,
        memory_scope: str | None = None,
    ) -> int | None:
        """Quante occorrenze esistono, indipendentemente da quante ne mostriamo.

        Serve a impedire che un `limit` venga verbalizzato come unicità: mostrare
        un turno perché ne è stato chiesto uno non autorizza a dire «l'unica».
        Il totale viene dall'indice, quindi non rivalida la fiducia sul documento
        idratato ed è un limite superiore. ``None`` significa "non lo so", che è
        diverso da zero e va trattato come tale.
        """
        query_text, terms, _scope = self._chronological_query(subject, memory_scope)
        if not terms:
            return None

        from redis.commands.search.query import Query

        try:
            return int(
                self.r.ft("idx:turns").search(Query(query_text).paging(0, 0)).total
            )
        except Exception as exc:
            logger.debug(f"Archivio turni: conteggio cronologico non disponibile ({exc})")
            return None

    def search_chronological(
        self,
        subject: str,
        *,
        order: str,
        limit: int = 3,
        memory_scope: str | None = None,
        trusted_only: bool = True,
    ) -> list[ArchivedTurn]:
        """Cerca occorrenze verbatim e le ordina sul tempo osservato.

        ``observed_at`` è la data del turno, non la data dell'evento raccontato.
        Il filtro user+scope avviene nell'indice prima del paging; la fiducia
        viene verificata sul documento idratato. Su indice assente o query non
        valida il metodo fallisce chiuso restituendo nessuna evidenza.
        """
        if order not in {"first", "last"}:
            raise ValueError("order deve essere first o last")
        query_text, terms, scope = self._chronological_query(subject, memory_scope)
        if not terms:
            return []

        from redis.commands.search.query import Query

        try:
            result = self.r.ft("idx:turns").search(
                Query(query_text)
                .sort_by("observed_at", asc=order == "first")
                .paging(0, max(20, int(limit) * 5))
                .return_fields("turn_ref", "observed_at")
            )
        except Exception as exc:
            logger.warning(f"Archivio turni: ricerca cronologica non disponibile ({exc})")
            return []

        matches: list[ArchivedTurn] = []
        seen = set()
        for row in result.docs:
            ref = str(getattr(row, "turn_ref", "") or "").strip()
            if not ref or ref in seen:
                continue
            turn = self.get(ref)
            if (
                turn is None
                or turn.role != "user"
                or turn.memory_scope != scope
                or (trusted_only and not turn.trusted)
            ):
                continue
            seen.add(ref)
            matches.append(turn)
            if len(matches) >= max(1, int(limit)):
                break
        return matches


def _log_timestamp(line: str) -> float | None:
    match = _LOG_TIME_RE.match(line)
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group("stamp"), "%Y-%m-%d %H:%M:%S.%f")
        if hasattr(config.TIMEZONE, "localize"):
            return config.TIMEZONE.localize(naive).timestamp()
        return naive.replace(tzinfo=config.TIMEZONE).timestamp()
    except (TypeError, ValueError):
        return None


def iter_accepted_voice_turns(path: Path):
    """Estrae dai log soltanto STT che hanno raggiunto un Intent.

    Le righe STT ignorate dal wake guard o provenienti dal parlato ambientale non
    entrano quindi nello storico personale. Quando possibile si conserva il
    timestamp STT; l'Intent è la prova che quel testo è stato accettato.
    """
    pending_stt: tuple[str, float] | None = None
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            timestamp = _log_timestamp(line)
            if timestamp is None:
                continue
            stt = _LOG_STT_RE.search(line)
            if stt:
                pending_stt = (stt.group("content"), timestamp)
                continue
            intent = _LOG_INTENT_RE.search(line)
            if not intent:
                continue
            content = intent.group("content")
            observed_at = timestamp
            if (
                pending_stt is not None
                and pending_stt[0] == content
                and 0 <= timestamp - pending_stt[1] <= 120
            ):
                observed_at = pending_stt[1]
            pending_stt = None
            yield {
                "content": content,
                "observed_at": observed_at,
                "line_no": line_no,
            }


def _earliest_archived_turn(redis_client) -> float | None:
    earliest = None
    for raw_key in redis_client.scan_iter(match=f"{TURN_KEY_PREFIX}*"):
        doc = _json_doc(redis_client, _decode_key(raw_key))
        try:
            observed_at = float(doc.get("observed_at"))
        except (TypeError, ValueError):
            continue
        earliest = observed_at if earliest is None else min(earliest, observed_at)
    return earliest


def backfill_legacy_voice_turns(
    redis_client,
    *,
    log_paths: list[Path] | None = None,
) -> dict:
    """Importa una volta i turni vocali accettati precedenti all'archivio.

    È una migrazione di provenienza, non estrazione di memoria: conserva la frase
    pronunciata e il suo timestamp. I riferimenti sono hash deterministici,
    quindi un'interruzione prima del marker resta idempotente.
    """
    try:
        existing = redis_client.get(LEGACY_VOICE_BACKFILL_KEY)
    except Exception:
        existing = None
    if existing:
        try:
            return json.loads(_decode_key(existing))
        except Exception:
            return {"status": "already_completed"}

    paths = (
        [Path(path) for path in log_paths]
        if log_paths is not None
        else sorted((config.BASE_DIR / "logs").glob("voice_daemon*.log"))
    )
    cutoff = _earliest_archived_turn(redis_client)
    store = ConversationTurnStore(redis_client)
    imported = parsed = skipped_after_cutoff = 0
    for path in paths:
        if not path.is_file():
            continue
        for item in iter_accepted_voice_turns(path):
            parsed += 1
            if cutoff is not None and item["observed_at"] >= cutoff:
                skipped_after_cutoff += 1
                continue
            identity = (
                f"{item['observed_at']:.6f}\0{item['content']}"
            ).encode("utf-8")
            conversation_id = (
                "legacy-voice-" + hashlib.sha256(identity).hexdigest()[:20]
            )
            store.persist({
                "conversation_id": conversation_id,
                "seq": 1,
                "role": "user",
                "content": item["content"],
                "trusted": True,
                "observed_at": item["observed_at"],
                "segment_id": None,
                "memory_scope": PERSONAL_SCOPE,
                "archive_origin": "legacy_voice_log",
                "source_locator": f"{path.name}:{item['line_no']}",
            })
            imported += 1

    report = {
        "status": "completed",
        "schema_version": 1,
        "completed_at": time.time(),
        "files": [path.name for path in paths if path.is_file()],
        "cutoff_observed_at": cutoff,
        "parsed": parsed,
        "imported": imported,
        "skipped_after_cutoff": skipped_after_cutoff,
    }
    redis_client.set(
        LEGACY_VOICE_BACKFILL_KEY,
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )
    if imported:
        logger.info(
            "Archivio turni: backfill storico completato — {} turni accettati "
            "importati da {} log",
            imported,
            len(report["files"]),
        )
    return report


def _decode_key(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _json_doc(redis_client, key: str) -> dict:
    try:
        raw = redis_client.json().get(key, "$")
    except Exception:
        return {}
    if not raw:
        return {}
    doc = raw[0] if isinstance(raw, list) else raw
    return dict(doc) if isinstance(doc, dict) else {}


def audit_verbatim_lifecycle(
    redis_client,
    *,
    reference_at: float | None = None,
    grace_days: int | None = None,
) -> dict:
    """Mark-and-sweep audit-only dell'archivio verbatim.

    Marca come raggiungibile ogni turno referenziato da una memoria Redis
    esistente. Lo sweep produce soltanto candidati: non modifica TTL, memorie o
    turni. È deliberatamente conservativo anche verso memorie superseded, che
    restano parte dell'audit storico finché il loro documento esiste.
    """
    now_ts = time.time() if reference_at is None else float(reference_at)
    grace = int(
        getattr(config, "VERBATIM_UNREFERENCED_GRACE_DAYS", 180)
        if grace_days is None else grace_days
    )
    if grace < 1:
        raise ValueError("grace_days deve essere positivo")

    reverse_refs: dict[str, set[str]] = {}
    memory_docs = malformed_memories = 0
    for raw_key in redis_client.scan_iter(match="euri:memory:*"):
        key = _decode_key(raw_key)
        doc = _json_doc(redis_client, key)
        if not doc:
            malformed_memories += 1
            continue
        memory_docs += 1
        memory_id = str(doc.get("id") or key.removeprefix("euri:memory:"))
        temporal = doc.get("temporal_context") or {}
        for raw_ref in temporal.get("source_turn_refs") or []:
            ref = str(raw_ref or "").strip()
            if ref:
                reverse_refs.setdefault(ref, set()).add(memory_id)

    turns = []
    malformed_turns = 0
    for raw_key in redis_client.scan_iter(match=f"{TURN_KEY_PREFIX}*"):
        key = _decode_key(raw_key)
        ref = key.removeprefix(TURN_KEY_PREFIX)
        try:
            turn = ConversationTurnStore(redis_client).get(ref)
        except Exception:
            turn = None
        if turn is None:
            malformed_turns += 1
            continue
        age_days = max(0.0, (now_ts - turn.observed_at) / 86400)
        referenced_by = sorted(reverse_refs.get(ref, set()))
        turns.append(
            {
                "turn_ref": ref,
                "role": turn.role,
                "observed_at": turn.observed_at,
                "age_days": round(age_days, 3),
                "referenced_by": referenced_by,
                "referenced": bool(referenced_by),
            }
        )

    known_refs = {item["turn_ref"] for item in turns}
    missing_source_refs = [
        {
            "turn_ref": ref,
            "referenced_by": sorted(memory_ids),
        }
        for ref, memory_ids in sorted(reverse_refs.items())
        if ref not in known_refs
    ]
    orphan_candidates = [
        item for item in turns
        if not item["referenced"] and item["age_days"] >= grace
    ]
    recent_unreferenced = [
        item for item in turns
        if not item["referenced"] and item["age_days"] < grace
    ]
    referenced = [item for item in turns if item["referenced"]]
    return {
        "schema_version": 1,
        "mode": "audit_only",
        "reference_at": now_ts,
        "grace_days": grace,
        "counts": {
            "turns": len(turns),
            "referenced": len(referenced),
            "recent_unreferenced": len(recent_unreferenced),
            "orphan_candidates": len(orphan_candidates),
            "missing_source_refs": len(missing_source_refs),
            "memory_documents_scanned": memory_docs,
            "malformed_turns": malformed_turns,
            "non_json_memory_keys_skipped": malformed_memories,
        },
        "orphan_candidates": orphan_candidates,
        "missing_source_refs": missing_source_refs,
    }


def _decode_json_value(value) -> dict:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def get_verbatim_lifecycle_pending(redis_client) -> dict:
    """Restituisce l'avviso durevole di revisione, se presente."""
    try:
        return _decode_json_value(redis_client.get(VERBATIM_LIFECYCLE_PENDING_KEY))
    except Exception:
        return {}


def run_verbatim_lifecycle_maintenance(
    redis_client,
    *,
    reference_at: float | None = None,
    grace_days: int | None = None,
    emit_pulse: bool = True,
) -> dict:
    """Esegue e persiste l'audit, senza modificare né cancellare i turni.

    Un problema crea un avviso durevole. L'avviso viene rimosso soltanto quando
    un audit successivo torna pulito; il Pulse è emesso solo quando cambia
    l'insieme dei problemi, per non produrre lo stesso evento ogni giorno.
    """
    report = audit_verbatim_lifecycle(
        redis_client,
        reference_at=reference_at,
        grace_days=grace_days,
    )
    counts = report["counts"]
    needs_review = bool(
        counts["orphan_candidates"]
        or counts["missing_source_refs"]
        or counts["malformed_turns"]
    )
    report["review_required"] = needs_review
    report["persisted_at"] = time.time()
    redis_client.set(
        VERBATIM_LIFECYCLE_REPORT_KEY,
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )

    if not needs_review:
        redis_client.delete(VERBATIM_LIFECYCLE_PENDING_KEY)
        logger.info(
            "Lifecycle verbatim: audit automatico pulito — {} turni, "
            "{} referenziati, {} recenti non referenziati",
            counts["turns"],
            counts["referenced"],
            counts["recent_unreferenced"],
        )
        return report

    fingerprint_payload = {
        "orphan_candidates": [
            item["turn_ref"] for item in report["orphan_candidates"]
        ],
        "missing_source_refs": [
            item["turn_ref"] for item in report["missing_source_refs"]
        ],
        "malformed_turns": counts["malformed_turns"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    previous = get_verbatim_lifecycle_pending(redis_client)
    pending = {
        "schema_version": 1,
        "status": "review_pending",
        "fingerprint": fingerprint,
        "first_detected_at": (
            previous.get("first_detected_at")
            if previous.get("fingerprint") == fingerprint
            else report["reference_at"]
        ),
        "last_detected_at": report["reference_at"],
        "grace_days": report["grace_days"],
        "counts": {
            "orphan_candidates": counts["orphan_candidates"],
            "missing_source_refs": counts["missing_source_refs"],
            "malformed_turns": counts["malformed_turns"],
        },
        "report_key": VERBATIM_LIFECYCLE_REPORT_KEY,
        "automatic_deletion": False,
    }
    redis_client.set(
        VERBATIM_LIFECYCLE_PENDING_KEY,
        json.dumps(pending, ensure_ascii=False, sort_keys=True),
    )
    logger.warning(
        "Lifecycle verbatim: REVISIONE PENDENTE — {} candidati orfani, "
        "{} riferimenti mancanti, {} turni malformati; nessuna cancellazione",
        counts["orphan_candidates"],
        counts["missing_source_refs"],
        counts["malformed_turns"],
    )

    if emit_pulse and previous.get("fingerprint") != fingerprint:
        from core.pulse import pulse_emit

        pulse_emit(
            redis_client,
            "memory",
            "intero",
            "verbatim_lifecycle_review_needed",
            payload=pending,
            salience=0.75,
            producer="conversation_turns.lifecycle",
            logical_event_id=f"verbatim-lifecycle:{fingerprint}",
            experiment_version="verbatim-lifecycle-v1",
        )
    return report
