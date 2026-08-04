"""Interpretazione semantica condivisa dei turni conversazionali.

Whisper resta la fonte verbatim. Questo modulo produce una rappresentazione
operativa separata e, soltanto quando il proprietario corregge esplicitamente
un'entita', conserva l'identita' canonica per i turni successivi. Non contiene
nomi o regex di aziende: le equivalenze nascono dalla comprensione del turno.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

import config
from core.memory_scope import normalize_scope
from core.ollama_client import chat_client
from core.pulse import cognitive_emit


SEMANTIC_FRAME_VERSION = 2
ENTITY_SCHEMA_VERSION = 1
_ALIAS_KEY_PREFIX = "euri:semantic:entity_aliases:"
_ENTITY_KEY_PREFIX = "euri:semantic:entity:"

_ALLOWED_INTENTS = frozenset({
    "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
    "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "EXECUTE", "COMPLETE",
    "RESCHEDULE", "ACTION_REASONING", "TRANSLATE", "DICTATION",
})
_ALLOWED_SPEECH_ACTS = frozenset({
    "INFORM", "ASK", "REQUEST_WEB_SEARCH", "REQUEST_MEMORY_SEARCH",
    "REQUEST_SAVE", "REQUEST_ACTION", "CORRECT_ENTITY", "CORRECT_FACT",
    "CONFIRM", "REJECT", "DICTATE", "TRANSLATE",
})
_ALLOWED_MEMORY_DISPOSITIONS = frozenset({
    "candidate", "ephemeral", "no_store", "unspecified",
})


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _alias_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _identity_token(value: str) -> str:
    """Equipara una parola scandita (A-B-C) alla sua forma ricomposta (ABC)."""
    parts = _alias_token(value).split()
    result: list[str] = []
    index = 0
    while index < len(parts):
        if len(parts[index]) == 1:
            end = index
            while end < len(parts) and len(parts[end]) == 1:
                end += 1
            if end - index >= 2:
                result.append("".join(parts[index:end]))
                index = end
                continue
        result.append(parts[index])
        index += 1
    return " ".join(result)


def _compact_spelled_name(value: str) -> str:
    """Toglie i separatori da sequenze di lettere scandite nel nome finale."""
    pattern = re.compile(r"(?<!\w)(?:[^\W\d_]\s*[-.]\s*)+[^\W\d_](?!\w)", re.UNICODE)
    return pattern.sub(
        lambda match: re.sub(r"[\s.-]+", "", match.group(0)),
        str(value or ""),
    ).strip()


def _confidence(value, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _json_object(text: str) -> dict:
    cleaned = str(text or "").strip()
    if "<channel|>" in cleaned:
        cleaned = cleaned.split("<channel|>", 1)[-1]
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("nessun oggetto JSON nella risposta")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("il frame non e' un oggetto")
    return value


@dataclass
class SemanticTurnFrame:
    turn_id: str
    raw_text: str
    interpreted_text: str
    primary_intent: str = ""
    speech_acts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    web_query: str = ""
    preservation_mode: str = "semantic"
    requires_clarification: bool = False
    confidence: float = 0.0
    meaning_preserved: bool = False
    status: str = "fallback"
    memory_scope: str = "personal"
    memory_disposition: str = "unspecified"
    memory_reason: str = ""
    addressed_to_assistant: bool = False
    address_relation: str = "unclear"
    address_confidence: float = 0.0
    canonicalizations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SEMANTIC_FRAME_VERSION,
            "turn_id": self.turn_id,
            "raw_text": self.raw_text,
            "interpreted_text": self.interpreted_text,
            "primary_intent": self.primary_intent,
            "speech_acts": list(self.speech_acts),
            "entities": list(self.entities),
            "facts": list(self.facts),
            "actions": list(self.actions),
            "web_query": self.web_query,
            "preservation_mode": self.preservation_mode,
            "requires_clarification": self.requires_clarification,
            "confidence": self.confidence,
            "meaning_preserved": self.meaning_preserved,
            "status": self.status,
            "memory_scope": self.memory_scope,
            "memory_disposition": self.memory_disposition,
            "memory_reason": self.memory_reason,
            "addressed_to_assistant": self.addressed_to_assistant,
            "address_relation": self.address_relation,
            "address_confidence": self.address_confidence,
            "canonicalizations": list(self.canonicalizations),
        }


class EntityRegistry:
    """Registro locale delle identita' corrette esplicitamente dall'utente."""

    def __init__(self, redis_client):
        self.r = redis_client

    @staticmethod
    def _alias_key(scope: str) -> str:
        return f"{_ALIAS_KEY_PREFIX}{normalize_scope(scope)}"

    @staticmethod
    def _entity_key(scope: str, entity_id: str) -> str:
        return f"{_ENTITY_KEY_PREFIX}{normalize_scope(scope)}:{entity_id}"

    def alias_records(self, scope: str) -> dict[str, dict]:
        try:
            raw = self.r.hgetall(self._alias_key(scope)) or {}
        except Exception as exc:
            logger.debug("Registro entita': alias non disponibili ({})", exc)
            return {}
        records: dict[str, dict] = {}
        for key, value in raw.items():
            try:
                doc = json.loads(_decode(value))
                if isinstance(doc, dict) and doc.get("canonical_name"):
                    records[_decode(key)] = doc
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def canonicalize(self, text: str, scope: str) -> str:
        """Applica alias appresi, usando confini generici e mai nomi cablati."""
        result = str(text or "")
        records = self.alias_records(scope)
        aliases = sorted(
            (
                (record.get("observed_form") or alias, record.get("canonical_name") or "")
                for alias, record in records.items()
            ),
            key=lambda item: len(str(item[0])),
            reverse=True,
        )
        for observed, canonical in aliases:
            observed = str(observed or "").strip()
            canonical = str(canonical or "").strip()
            if (
                len(_alias_token(observed)) < 3
                or not canonical
                or _alias_token(observed) == _alias_token(canonical)
            ):
                continue
            result = re.sub(
                rf"(?<!\w){re.escape(observed)}(?!\w)",
                lambda _match, value=canonical: value,
                result,
                flags=re.IGNORECASE,
            )
        return result

    def context(self, scope: str, limit: int = 24) -> list[dict]:
        unique: dict[str, dict] = {}
        for record in self.alias_records(scope).values():
            entity_id = str(record.get("entity_id") or "")
            if entity_id:
                unique[entity_id] = record
        ordered = sorted(
            unique.values(),
            key=lambda item: float(item.get("updated_at") or 0.0),
            reverse=True,
        )
        return [
            {
                "entity_id": item.get("entity_id"),
                "canonical_name": item.get("canonical_name"),
                "entity_type": item.get("entity_type"),
                "aliases": item.get("aliases", []),
            }
            for item in ordered[:max(1, int(limit))]
        ]

    def remember_correction(self, entity: dict, scope: str) -> dict | None:
        observed = str(entity.get("observed_form") or "").strip()
        canonical = _compact_spelled_name(
            str(entity.get("canonical_name") or "").strip()
        )
        observed_token = _alias_token(observed)
        canonical_token = _alias_token(canonical)
        if (
            len(observed_token) < 3
            or len(canonical_token) < 3
            or observed_token == canonical_token
        ):
            return None

        scope = normalize_scope(scope)
        # Se la forma osservata era gia' stata risolta e il modello restituisce
        # soltanto la versione scandita dello stesso nome, conserva la grafia
        # canonica confermata in precedenza invece di creare una nuova identita'.
        known = self.alias_records(scope).get(observed_token) or {}
        known_canonical = str(known.get("canonical_name") or "").strip()
        if (
            known_canonical
            and _identity_token(known_canonical) == _identity_token(canonical)
        ):
            canonical = known_canonical
            canonical_token = _alias_token(canonical)
        entity_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"euri:{scope}:{canonical_token}"))
        entity_key = self._entity_key(scope, entity_id)
        aliases = [observed]
        try:
            existing_raw = self.r.get(entity_key)
            existing = json.loads(_decode(existing_raw)) if existing_raw else {}
            if isinstance(existing, dict):
                aliases = list(existing.get("aliases") or []) + aliases
        except Exception:
            existing = {}
        aliases = list(dict.fromkeys(
            alias for alias in aliases if _alias_token(alias) != canonical_token
        ))
        record = {
            "schema_version": ENTITY_SCHEMA_VERSION,
            "entity_id": entity_id,
            "canonical_name": canonical,
            "entity_type": str(entity.get("entity_type") or "unknown")[:64],
            "observed_form": observed,
            "aliases": aliases,
            "memory_scope": scope,
            "source": "explicit_owner_correction",
            "confidence": _confidence(entity.get("confidence")),
            "updated_at": time.time(),
        }
        try:
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            self.r.set(entity_key, encoded)
            mapping = {}
            for alias in aliases:
                mapping[_alias_token(alias)] = encoded
            # Anche il canonico risolve a se stesso e rende disponibile il contesto.
            canonical_record = dict(record, observed_form=canonical)
            mapping[canonical_token] = json.dumps(
                canonical_record, ensure_ascii=False, separators=(",", ":")
            )
            self.r.hset(self._alias_key(scope), mapping=mapping)
        except Exception as exc:
            logger.warning("Registro entita': correzione non persistita ({})", exc)
            return None
        return record


class SemanticTurnService:
    """Crea un solo frame e lo rende riusabile da tutti i consumer del turno."""

    def __init__(
        self,
        redis_client,
        *,
        model_call: Callable[[str], str] | None = None,
    ):
        self.r = redis_client
        self.registry = EntityRegistry(redis_client)
        self._model_call = model_call
        self._model_lock = threading.Lock()

    def _call_model(self, prompt: str) -> str:
        if self._model_call is not None:
            return self._model_call(prompt)
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 700},
            think=False,
        )
        return str(response.message.content or "")

    @staticmethod
    def _history_context(recent_history: list[dict], scope: str) -> list[dict]:
        rows = []
        for message in recent_history[-8:]:
            if normalize_scope(message.get("memory_scope")) != scope:
                continue
            content = str(message.get("content") or "").strip()
            if content:
                rows.append({
                    "role": str(message.get("role") or ""),
                    "content": content[:300],
                })
        return rows

    @staticmethod
    def _prompt(
        raw_text: str,
        baseline: str,
        history: list[dict],
        known: list[dict],
        *,
        session_bootstrap: bool = False,
    ) -> str:
        payload = {
            "raw_text": raw_text,
            "known_entity_state": known,
            "recent_conversation": history,
            "precanonicalized_text": baseline,
            "session_bootstrap": bool(session_bootstrap),
        }
        return (
            "Sei l'interprete semantico unico di un assistente personale. Analizza il "
            "turno nel suo contesto e restituisci SOLO JSON valido. Non eseguire azioni.\n"
            "Il testo raw e' evidenza immutabile. interpreted_text deve restare quasi "
            "verbatim: correggi soltanto identita'/ortografia quando il contesto lo "
            "giustifica; non cambiare fatti, numeri, negazioni, tempi o intenzioni.\n"
            "Una entity con status explicit_correction e' ammessa SOLO se l'utente sta "
            "correggendo esplicitamente una forma osservata con un nome canonico. Una "
            "semplice menzione non e' una correzione.\n"
            "Se l'utente scandisce le lettere per spiegare l'ortografia, canonical_name "
            "deve contenere la grafia finale normale senza trattini, punti o spazi tra "
            "le singole lettere: la sillabazione e' evidenza, non il nome canonico.\n"
            "Distingui identita' (CORRECT_ENTITY) da correzioni fattuali (CORRECT_FACT). "
            "Se chiede una ricerca Internet, compila web_query con termini canonici. "
            "Se detta/traduce/cita testo da preservare, usa preservation_mode=verbatim.\n"
            "Distingui rigorosamente una RISPOSTA da un'ESECUZIONE. Chiedere cosa sai, "
            "ricordi, ricostruisci o puoi riferire dal contesto/memoria e' SEARCH con ASK "
            "e REQUEST_MEMORY_SEARCH: produrre una risposta verbale NON e' un'azione. "
            "REQUEST_ACTION ed EXECUTE/ACTION_REASONING sono ammessi solo quando l'utente "
            "vuole un effetto operativo distinto dalla risposta (mutare stato, creare o "
            "modificare un artefatto, usare un tool sul sistema o pianificare tale effetto). "
            "In quel caso actions deve essere non vuoto e descrivere concretamente effetto, "
            "target e capability_class. Se non puoi descrivere tale effetto, non usare "
            "REQUEST_ACTION ne' un intent operativo.\n"
            "Classifica anche la destinazione mnemonica del turno. Usa candidate SOLO "
            "per fatti, preferenze, clienti, progetti o impegni dell'utente che potrebbero "
            "essere utili in conversazioni future. Usa ephemeral per stato temporaneo o "
            "metaconversazionale (riavvii, test, stato corrente dell'assistente o del codice). "
            "In particolare, una modifica appena fatta al software dell'assistente, un suo "
            "riavvio o l'esito del test corrente RESTANO ephemeral anche se potrebbero essere "
            "utili al debugging: non sono fatti personali durevoli. Diventano candidate solo "
            "se l'utente chiede esplicitamente di ricordarli/salvarli oppure li presenta come "
            "decisione tecnica stabile di un progetto. "
            "Usa no_store per domande, comandi, conferme, convenevoli e pure correzioni di "
            "identita' gia' gestite dal registro. Questa classificazione puo' soltanto "
            "candidare o impedire l'estrazione passiva: non salva direttamente nulla.\n"
            "Valuta inoltre se il turno e' linguisticamente rivolto all'assistente. Non "
            "presumerlo dal fatto che il parlante sia il proprietario. Usa direct_address "
            "solo per un saluto, una domanda, un comando o un riferimento diretto allo "
            "stato/capacita' dell'assistente; direct_followup solo se dipende dal dialogo "
            "recente; ambient per parlato autonomo rivolto verosimilmente ad altre persone; "
            "unclear nel dubbio. Se session_bootstrap=true, il riferimento al ritorno, al "
            "riavvio o all'aver fermato/modificato l'assistente e' evidenza contestuale, ma "
            "la sola presenza del proprietario non lo e'. Nel bootstrap applica una soglia "
            "molto severa: un saluto, un imperativo o la seconda persona da soli NON bastano. "
            "Comandi fisici o frasi plausibilmente rivolte a un collega (portare/spostare "
            "oggetti, lavorazioni in reparto, commenti autonomi) sono ambient o unclear, "
            "perche' l'assistente software non puo' eseguirli. Accetta direct_address solo "
            "quando il contenuto richiama specificamente il dialogo, lo stato o le capacita' "
            "software dell'assistente; nel dubbio addressed_to_assistant=false.\n"
            "Schema obbligatorio:\n"
            "{\"interpreted_text\":\"\",\"primary_intent\":\"CHAT|WEB_SEARCH|SEARCH|"
            "SAVE_MEMORY|SAVE_TODO|SAVE_NOTE|SAVE_LAST|READ_BACK|EXECUTE|COMPLETE|"
            "RESCHEDULE|ACTION_REASONING|TRANSLATE|DICTATION\","
            "\"speech_acts\":[\"INFORM|ASK|REQUEST_WEB_SEARCH|REQUEST_MEMORY_SEARCH|"
            "REQUEST_SAVE|REQUEST_ACTION|CORRECT_ENTITY|CORRECT_FACT|CONFIRM|REJECT|"
            "DICTATE|TRANSLATE\"],\"entities\":[{\"observed_form\":\"\","
            "\"canonical_name\":\"\",\"entity_type\":\"person|organization|product|"
            "place|project|other\",\"status\":\"mentioned|resolved|explicit_correction\","
            "\"evidence\":\"\",\"confidence\":0.0}],\"facts\":[],\"actions\":[],"
            "\"web_query\":\"\",\"preservation_mode\":\"semantic|verbatim\","
            "\"requires_clarification\":false,\"meaning_preserved\":true,"
            "\"confidence\":0.0,\"memory_disposition\":\"candidate|ephemeral|no_store\","
            "\"memory_reason\":\"\",\"addressed_to_assistant\":false,"
            "\"address_relation\":\"direct_address|direct_followup|ambient|unclear\","
            "\"address_confidence\":0.0}\n"
            "INPUT_JSON:\n" + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _validated_frame(data: dict, raw_text: str, baseline: str, scope: str) -> SemanticTurnFrame:
        intent = str(data.get("primary_intent") or "").upper()
        if intent not in _ALLOWED_INTENTS:
            intent = ""
        speech_acts = []
        for item in data.get("speech_acts") or []:
            value = str(item or "").upper()
            if value in _ALLOWED_SPEECH_ACTS and value not in speech_acts:
                speech_acts.append(value)
        operational_acts = {
            "REQUEST_WEB_SEARCH", "REQUEST_MEMORY_SEARCH", "REQUEST_SAVE",
            "REQUEST_ACTION", "DICTATE", "TRANSLATE",
        }
        # Un turno informativo/interrogativo e' comunque conversazione. Senza
        # questo default un primary_intent vuoto puo' cadere nel controller fuzzy.
        if not intent and speech_acts and not (set(speech_acts) & operational_acts):
            intent = "CHAT"
        entities = [item for item in (data.get("entities") or []) if isinstance(item, dict)][:16]
        preservation = str(data.get("preservation_mode") or "semantic").lower()
        if preservation not in {"semantic", "verbatim"}:
            preservation = "semantic"
        confidence = _confidence(data.get("confidence"))
        meaning_preserved = bool(data.get("meaning_preserved"))
        memory_disposition = str(
            data.get("memory_disposition") or "unspecified"
        ).strip().lower()
        if memory_disposition not in _ALLOWED_MEMORY_DISPOSITIONS:
            memory_disposition = "unspecified"
        # Compatibilita' conservativa con output prodotti senza il nuovo campo:
        # richieste e correzioni non diventano memorie passive per duplicazione.
        if memory_disposition == "unspecified" and (
            set(speech_acts) & (operational_acts | {"CORRECT_ENTITY", "CORRECT_FACT"})
        ):
            memory_disposition = "no_store"
        address_relation = str(
            data.get("address_relation") or "unclear"
        ).strip().lower()
        if address_relation not in {
            "direct_address", "direct_followup", "ambient", "unclear",
        }:
            address_relation = "unclear"
        interpreted = str(data.get("interpreted_text") or "").strip()
        ratio = len(interpreted) / max(1, len(raw_text))
        safe_interpretation = (
            preservation == "semantic"
            and meaning_preserved
            and confidence >= 0.60
            and 0.35 <= ratio <= 2.5
        )
        if preservation == "verbatim":
            interpreted = raw_text
        elif not safe_interpretation:
            interpreted = baseline
        return SemanticTurnFrame(
            turn_id=str(uuid.uuid4()),
            raw_text=raw_text,
            interpreted_text=interpreted or baseline or raw_text,
            primary_intent=intent,
            speech_acts=speech_acts,
            entities=entities,
            facts=[item for item in (data.get("facts") or []) if isinstance(item, dict)][:24],
            actions=[item for item in (data.get("actions") or []) if isinstance(item, dict)][:12],
            web_query=str(data.get("web_query") or "").strip()[:240],
            preservation_mode=preservation,
            requires_clarification=bool(data.get("requires_clarification")),
            confidence=confidence,
            meaning_preserved=meaning_preserved,
            status="interpreted",
            memory_scope=scope,
            memory_disposition=memory_disposition,
            memory_reason=str(data.get("memory_reason") or "").strip()[:240],
            addressed_to_assistant=data.get("addressed_to_assistant") is True,
            address_relation=address_relation,
            address_confidence=_confidence(data.get("address_confidence")),
        )

    @staticmethod
    def _frame_value(frame: SemanticTurnFrame | dict, name: str, default=None):
        if isinstance(frame, dict):
            return frame.get(name, default)
        return getattr(frame, name, default)

    @classmethod
    def _explicit_entity_corrections(
        cls, frame: SemanticTurnFrame | dict
    ) -> list[dict]:
        speech_acts = list(cls._frame_value(frame, "speech_acts", []) or [])
        if "CORRECT_ENTITY" not in speech_acts:
            return []
        corrections = []
        raw_token = _alias_token(cls._frame_value(frame, "raw_text", ""))
        for entity in cls._frame_value(frame, "entities", []) or []:
            if str(entity.get("status") or "").lower() != "explicit_correction":
                continue
            observed = str(entity.get("observed_form") or "").strip()
            canonical = str(entity.get("canonical_name") or "").strip()
            evidence = str(entity.get("evidence") or "").strip()
            if (
                observed
                and canonical
                and evidence
                and _alias_token(observed) in raw_token
                and _alias_token(observed) != _alias_token(canonical)
                and _confidence(entity.get("confidence")) >= 0.80
            ):
                corrections.append(entity)
        return corrections

    def _commit_corrections(
        self, frame: SemanticTurnFrame | dict
    ) -> SemanticTurnFrame | dict:
        canonicalizations = self._frame_value(frame, "canonicalizations", None)
        if canonicalizations:
            return frame
        if canonicalizations is None:
            canonicalizations = []
            if isinstance(frame, dict):
                frame["canonicalizations"] = canonicalizations
            else:
                frame.canonicalizations = canonicalizations
        scope = normalize_scope(self._frame_value(frame, "memory_scope", "personal"))
        turn_id = str(self._frame_value(frame, "turn_id", "") or uuid.uuid4())
        for entity in self._explicit_entity_corrections(frame):
            record = self.registry.remember_correction(entity, scope)
            if record is None:
                continue
            canonicalizations.append(record)
            cognitive_emit(
                self.r,
                "entity",
                "extero",
                "canonicalized",
                producer="semantic_turn",
                trace_id=turn_id,
                logical_event_id=f"entity-canonicalized:{record['entity_id']}:{int(record['updated_at'])}",
                entity_refs=[{
                    "type": record.get("entity_type") or "entity",
                    "id": record["entity_id"],
                    "role": "corrected",
                }],
                payload={
                    "entity_id": record["entity_id"],
                    "observed_form": record["observed_form"],
                    "canonical_name": record["canonical_name"],
                    "memory_scope": scope,
                    "turn_id": turn_id,
                },
                epistemic_before="stt_observed_form",
                epistemic_after="owner_corrected_identity",
                salience=0.85,
            )
        return frame

    def commit_precomputed(self, frame: dict) -> dict:
        """Applica gli effetti espliciti solo dopo che il gate ha accettato il turno."""
        if not isinstance(frame, dict) or frame.get("status") != "interpreted":
            return frame
        return self._commit_corrections(frame)

    def interpret(
        self,
        raw_text: str,
        *,
        recent_history: list[dict] | None = None,
        memory_scope: str = "personal",
        session_bootstrap: bool = False,
        persist_corrections: bool = True,
    ) -> dict:
        raw_text = str(raw_text or "")
        scope = normalize_scope(memory_scope)
        baseline = self.registry.canonicalize(raw_text, scope)
        frame = SemanticTurnFrame(
            turn_id=str(uuid.uuid4()),
            raw_text=raw_text,
            interpreted_text=baseline,
            memory_scope=scope,
        )
        if not getattr(config, "SEMANTIC_TURN_ENABLED", True) or not raw_text.strip():
            return frame.as_dict()

        history = self._history_context(list(recent_history or []), scope)
        prompt = self._prompt(
            raw_text,
            baseline,
            history,
            self.registry.context(scope),
            session_bootstrap=session_bootstrap,
        )
        started = time.perf_counter()
        try:
            with self._model_lock:
                data = _json_object(self._call_model(prompt))
            frame = self._validated_frame(data, raw_text, baseline, scope)
        except Exception as exc:
            logger.warning("Turno semantico: fallback al testo canonico ({})", exc)
            return frame.as_dict()

        if persist_corrections:
            self._commit_corrections(frame)

        logger.info(
            "[TIMING] Turno semantico: {:.0f}ms -> {} acts={} canonicalizzazioni={}",
            (time.perf_counter() - started) * 1000,
            frame.primary_intent or "UNKNOWN",
            ",".join(frame.speech_acts) or "-",
            len(frame.canonicalizations),
        )
        return frame.as_dict()


def _frame_has_concrete_action(frame: dict | None) -> bool:
    if not isinstance(frame, dict):
        return False
    for item in frame.get("actions") or []:
        if not isinstance(item, dict):
            continue
        if any(
            str(item.get(field) or "").strip()
            for field in (
                "effect", "target", "capability_class", "capability",
                "action", "type", "goal", "description",
            )
        ):
            return True
    return False


def semantic_intent(frame: dict | None, *, minimum_confidence: float = 0.72) -> str:
    """Intent operativo del frame, solo quando abbastanza sicuro."""
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return ""
    if frame.get("requires_clarification"):
        return ""
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return ""
    value = str(frame.get("primary_intent") or "").upper()
    if value not in _ALLOWED_INTENTS:
        return ""
    # Il modello propone; questo contratto minimo autorizza il routing solo se
    # l'atto linguistico coerente e' presente nello stesso frame.
    acts = set(frame.get("speech_acts") or [])
    required = {
        "WEB_SEARCH": {"REQUEST_WEB_SEARCH"},
        "SEARCH": {"REQUEST_MEMORY_SEARCH", "ASK"},
        "SAVE_MEMORY": {"REQUEST_SAVE"},
        "SAVE_TODO": {"REQUEST_SAVE"},
        "SAVE_NOTE": {"REQUEST_SAVE"},
        "SAVE_LAST": {"REQUEST_SAVE"},
        "READ_BACK": {"REQUEST_MEMORY_SEARCH", "ASK"},
        "EXECUTE": {"REQUEST_ACTION"},
        "COMPLETE": {"REQUEST_ACTION", "INFORM"},
        "RESCHEDULE": {"REQUEST_ACTION"},
        "ACTION_REASONING": {"REQUEST_ACTION"},
        "TRANSLATE": {"TRANSLATE"},
        "DICTATION": {"DICTATE"},
    }.get(value)
    if required is not None and not (acts & required):
        return ""
    if value in {"EXECUTE", "ACTION_REASONING"} and "CORRECT_ENTITY" in acts:
        # La canonicalizzazione e' gia' l'effetto autorizzato del frame. Una
        # richiesta come "correggi il nome" non deve avviare un secondo
        # controller generico o fingere una mutazione di memoria distinta.
        return "CHAT"
    if value in {"EXECUTE", "ACTION_REASONING"} and not _frame_has_concrete_action(frame):
        # Un modello non puo' autorizzare il controller con la sola etichetta:
        # deve anche rappresentare l'effetto operativo richiesto.
        return ""
    return value


def frame_is_correction(frame: dict | None) -> bool:
    acts = set(frame.get("speech_acts") or []) if isinstance(frame, dict) else set()
    return bool(acts & {"CORRECT_ENTITY", "CORRECT_FACT"})


def frame_vetoes_contextual_action(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.72,
) -> bool:
    """Vieta il controller fuzzy quando il frame affidabile non chiede azioni.

    Gli intent deterministici gia' riconosciuti dal router restano indipendenti:
    questo veto protegge soltanto il fallback basato sul lessico del testo.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return False
    if frame.get("requires_clarification"):
        return False
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return False
    acts = {str(item or "").upper() for item in (frame.get("speech_acts") or [])}
    if not acts:
        return False
    intent = str(frame.get("primary_intent") or "").upper()
    if "REQUEST_ACTION" in acts:
        # Etichetta d'azione senza alcun effetto rappresentato: il frame non e'
        # abbastanza grounded per giustificare il fallback fuzzy.
        return not (
            intent in {"EXECUTE", "COMPLETE", "RESCHEDULE", "ACTION_REASONING"}
            and _frame_has_concrete_action(frame)
        )
    return intent not in {"EXECUTE", "COMPLETE", "RESCHEDULE", "ACTION_REASONING"}


def frame_blocks_passive_memory(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.72,
) -> bool:
    """Applica il blocco solo a frame affidabili; in dubbio lascia il learner invariato."""
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return False
    if frame.get("requires_clarification"):
        return False
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return False
    return str(frame.get("memory_disposition") or "").lower() in {
        "ephemeral", "no_store",
    }


def frame_bootstraps_owner_session(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.92,
    minimum_frame_confidence: float = 0.72,
) -> bool:
    """Apre la prima lease solo su evidenza semantica diretta e forte."""
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return False
    if frame.get("requires_clarification"):
        return False
    if _confidence(frame.get("confidence")) < minimum_frame_confidence:
        return False
    if frame.get("addressed_to_assistant") is not True:
        return False
    if str(frame.get("address_relation") or "").lower() != "direct_address":
        return False
    return _confidence(frame.get("address_confidence")) >= minimum_confidence
