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
from difflib import SequenceMatcher
from typing import Callable

from loguru import logger

import config
from core.memory_scope import normalize_scope
from core.ollama_client import chat_client
from core.pulse import cognitive_emit


SEMANTIC_FRAME_VERSION = 9
ENTITY_SCHEMA_VERSION = 1
_ALIAS_KEY_PREFIX = "euri:semantic:entity_aliases:"
_ENTITY_KEY_PREFIX = "euri:semantic:entity:"

_ALLOWED_INTENTS = frozenset({
    "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
    "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "EXECUTE", "COMPLETE",
    "RESCHEDULE", "ACTION_REASONING", "TRANSLATE", "DICTATION", "TEACH",
})
_ALLOWED_SPEECH_ACTS = frozenset({
    "INFORM", "ASK", "REQUEST_WEB_SEARCH", "REQUEST_MEMORY_SEARCH",
    "REQUEST_SAVE", "REQUEST_ACTION", "CORRECT_ENTITY", "CORRECT_FACT",
    "CONFIRM", "REJECT", "DICTATE", "TRANSLATE", "INITIATE_TEACHING",
    "REQUEST_DELIBERATION",
})
_ALLOWED_MEMORY_DISPOSITIONS = frozenset({
    "candidate", "ephemeral", "no_store", "unspecified",
})
_ALLOWED_MEMORY_POLICY_ACTIONS = frozenset({"none", "suppress", "resume"})
_ALLOWED_MEMORY_POLICY_SCOPES = frozenset({"turn", "episode"})
_ALLOWED_FACT_MODALITIES = frozenset({
    "asserted", "probable", "planned", "pending", "counterfactual", "unspecified",
})
_ALLOWED_FACT_DURABILITY = frozenset({
    "reusable", "session_only", "unspecified",
})
_ALLOWED_ACTION_EFFECT_SCOPES = frozenset({
    "response", "read", "write", "state_change", "external", "unspecified",
})
_ALLOWED_ACTION_POLARITIES = frozenset({
    "requested", "negated", "hypothetical", "unspecified",
})
_OPERATIONAL_ACTION_EFFECT_SCOPES = frozenset({
    "read", "write", "state_change", "external",
})
_ALLOWED_DOCUMENT_SOURCE_KINDS = frozenset({
    "active_document", "recent_conversation", "instruction_only", "unspecified",
})
_ALLOWED_DOCUMENT_SOURCE_SCOPES = frozenset({
    "last_exchange", "current_thread", "recent_turns", "unspecified",
})
_ALLOWED_EVIDENCE_DEPENDENCIES = frozenset({"none", "optional", "required"})
_ALLOWED_EVIDENCE_SOURCES = frozenset({
    "current_user", "company_documents", "web",
})
_ALLOWED_DELIBERATION_MODES = frozenset({"none", "explicit", "suggest"})
_ALLOWED_DELIBERATION_REASONS = frozenset({
    "tradeoff", "multiple_hypotheses", "high_impact", "uncertainty", "other",
})
_MIN_CURRENT_ENTITY_SURFACE_SIMILARITY = 0.72


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


def semantic_entity_key(value: str) -> str:
    """Chiave strutturale per confrontare entita' gia' estratte semanticamente."""
    return _identity_token(value)


def _surface_name_similarity(observed: str, canonical: str) -> float:
    """Somiglianza generica fra due grafie, non risoluzione di coreferenze."""
    left = "".join(_alias_token(observed).split())
    right = "".join(_alias_token(canonical).split())
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


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
    web_search_request: dict = field(default_factory=dict)
    preservation_mode: str = "semantic"
    requires_clarification: bool = False
    confidence: float = 0.0
    meaning_preserved: bool = False
    status: str = "fallback"
    memory_scope: str = "personal"
    memory_disposition: str = "unspecified"
    memory_reason: str = ""
    memory_policy: dict = field(default_factory=dict)
    passive_memory_blocked: bool = False
    passive_memory_block_scope: str = ""
    passive_memory_block_reason: str = ""
    passive_memory_block_source_turn_id: str = ""
    memory_retrieval: dict = field(default_factory=dict)
    evidence_request: dict = field(default_factory=dict)
    teaching_session: dict = field(default_factory=dict)
    deliberation_request: dict = field(default_factory=dict)
    addressed_to_assistant: bool = False
    address_relation: str = "unclear"
    address_confidence: float = 0.0
    canonical_projections: list[dict] = field(default_factory=list)
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
            "web_search_request": dict(self.web_search_request),
            "preservation_mode": self.preservation_mode,
            "requires_clarification": self.requires_clarification,
            "confidence": self.confidence,
            "meaning_preserved": self.meaning_preserved,
            "status": self.status,
            "memory_scope": self.memory_scope,
            "memory_disposition": self.memory_disposition,
            "memory_reason": self.memory_reason,
            "memory_policy": dict(self.memory_policy),
            "passive_memory_blocked": self.passive_memory_blocked,
            "passive_memory_block_scope": self.passive_memory_block_scope,
            "passive_memory_block_reason": self.passive_memory_block_reason,
            "passive_memory_block_source_turn_id": (
                self.passive_memory_block_source_turn_id
            ),
            "memory_retrieval": dict(self.memory_retrieval),
            "evidence_request": dict(self.evidence_request),
            "teaching_session": dict(self.teaching_session),
            "deliberation_request": dict(self.deliberation_request),
            "addressed_to_assistant": self.addressed_to_assistant,
            "address_relation": self.address_relation,
            "address_confidence": self.address_confidence,
            "canonical_projections": list(self.canonical_projections),
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
        self._last_model_meta: dict = {}

    def _call_model(self, prompt: str) -> str:
        if self._model_call is not None:
            return self._model_call(prompt)
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 1000},
            format="json",
            think=False,
        )
        self._last_model_meta = {
            "done_reason": str(getattr(response, "done_reason", "") or ""),
            "eval_count": int(getattr(response, "eval_count", 0) or 0),
        }
        return str(response.message.content or "")

    def _call_routing_recovery_model(self, raw_text: str) -> str:
        """Recupera solo il contratto operativo se il frame ricco non e' JSON.

        Non indovina una capability e non esegue: rappresenta soltanto il gesto
        espresso nel turno. L'ActionController applichera' poi catalogo,
        grounding, autorita' e policy come nel percorso ordinario.
        """
        prompt = f"""Il frame semantico completo non era decodificabile. Classifica
SOLTANTO il gesto operativo del turno corrente, senza rispondere al contenuto.

Turno corrente:
---
{raw_text}
---

Restituisci un solo oggetto JSON compatto con questa forma:
{{"interpreted_text":"testo fedele", "primary_intent":"CHAT|EXECUTE|ACTION_REASONING",
"speech_acts":["INFORM|ASK|REQUEST_ACTION"],
"actions":[{{"effect":"azione richiesta", "target":"oggetto esplicito",
"capability_class":"classe generica", "effect_scope":"read|write|state_change|external|none",
"polarity":"requested|proposed|described|negated"}}],
"requires_clarification":false, "meaning_preserved":true, "confidence":0.0,
"memory_disposition":"candidate|ephemeral|no_store",
"memory_policy":{{"action":"none|suppress|resume", "scope":"episode|turn",
"evidence":"", "scope_evidence":"", "confidence":0.0}}}}

Regole:
- REQUEST_ACTION solo se l'utente chiede davvero di eseguire/costruire/modificare;
- per REQUEST_ACTION compila effect, target, capability_class, effect_scope e
  polarity=requested usando soltanto elementi del turno;
- non scegliere il nome di un tool;
- memory_policy richiede evidenza letterale nel turno corrente;
- niente markdown e nessun campo aggiuntivo."""
        if self._model_call is not None:
            return self._model_call(prompt)
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 350},
            format="json",
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
        runtime_context: str = "",
    ) -> str:
        payload = {
            "raw_text": raw_text,
            "known_entity_state": known,
            "recent_conversation": history,
            "precanonicalized_text": baseline,
            "session_bootstrap": bool(session_bootstrap),
            "runtime_context": str(runtime_context or "")[:4000],
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
            "Quando observed_form e' un pronome, un'anafora o una descrizione come loro, "
            "lei, quello, il cliente o l'officina, puoi rappresentare la coreferenza nelle "
            "entities ma NON sostituirla con canonical_name dentro interpreted_text. La "
            "proiezione ortografica e' riservata a una forma nominale realmente pronunciata "
            "che sia una grafia plausibile dello stesso nome; la conoscenza di un'entita' "
            "pregressa, da sola, non autorizza la sostituzione.\n"
            "Se l'utente scandisce le lettere per spiegare l'ortografia, canonical_name "
            "deve contenere la grafia finale normale senza trattini, punti o spazi tra "
            "le singole lettere: la sillabazione e' evidenza, non il nome canonico.\n"
            "Distingui identita' (CORRECT_ENTITY) da correzioni fattuali (CORRECT_FACT). "
            "Se chiede una ricerca Internet, compila web_query con termini canonici. "
            "Produci anche web_search_request: explicit=true e source=web soltanto "
            "quando il turno CORRENTE autorizza chiaramente una fonte Internet/Web "
            "esterna. Cita in evidence le parole letterali che forniscono tale "
            "autorizzazione. Una ricerca allargata, associativa, approfondita o fra "
            "memorie/progetti resta REQUEST_MEMORY_SEARCH: il verbo cercare non "
            "autorizza da solo l'esterno. Un riferimento ellittico come 'controlla "
            "nel web' e' esplicito perche' nomina comunque la fonte esterna. "
            "Se detta/traduce/cita testo da preservare, usa preservation_mode=verbatim.\n"
            "Distingui rigorosamente una RISPOSTA da un'ESECUZIONE. Chiedere cosa sai, "
            "ricordi, ricostruisci o puoi riferire dal contesto/memoria e' SEARCH con ASK "
            "e REQUEST_MEMORY_SEARCH: produrre una risposta verbale NON e' un'azione. "
            "Anche rispondere, spiegare, descrivere, argomentare, confrontare, elencare, "
            "valutare o fare un'autovalutazione resta CHAT/SEARCH e, se vuoi rappresentare "
            "il gesto linguistico in actions, usa effect_scope=response. Un imperativo non "
            "implica da solo l'uso di strumenti. Una frase che vieta o nega l'esecuzione "
            "deve usare polarity=negated e non deve diventare un intent operativo. "
            "REQUEST_ACTION ed EXECUTE/ACTION_REASONING sono ammessi solo quando l'utente "
            "vuole un effetto operativo distinto dalla risposta (mutare stato, creare o "
            "modificare un artefatto, usare un tool sul sistema o pianificare tale effetto). "
            "In quel caso actions deve essere non vuoto e descrivere concretamente effetto, "
            "target, capability_class, effect_scope=read|write|state_change|external e "
            "polarity=requested. Se non puoi descrivere tale effetto, non usare "
            "REQUEST_ACTION ne' un intent operativo.\n"
            "Per creare o revisionare un documento, indica anche source_kind: "
            "active_document se la sorgente e' il file attivo, recent_conversation se "
            "l'utente vuole trasformare cio' che vi siete appena detti, instruction_only "
            "se la richiesta corrente contiene gia' tutto il contenuto necessario. Per "
            "recent_conversation usa source_scope=last_exchange|current_thread|recent_turns. "
            "Usa current_thread per 'questa conversazione', 'la traccia della conversazione' "
            "o 'quanto ci siamo detti'; last_exchange soltanto per 'l'ultimo scambio' o "
            "'la tua ultima risposta'; recent_turns per un intervallo recente generico. "
            "La semplice presenza di una conversazione o di un file non sceglie la sorgente: "
            "conta il riferimento intenzionale dell'utente.\n"
            "runtime_context descrive soltanto lo stato disponibile (per esempio il documento "
            "attivo): aiuta a risolvere i riferimenti, ma non costituisce un comando ne' "
            "autorizza da solo alcuna azione.\n"
            "Rappresenta ogni fatto con claim, modality e durability. modality descrive "
            "quanto afferma davvero l'utente: asserted per un fatto dichiarato, probable "
            "per un'ipotesi o una valutazione incerta, planned per una decisione futura, "
            "pending per un esito ancora atteso, counterfactual per uno scenario non reale. "
            "Non trasformare mai probabile, pianificato o in attesa in asserted. durability "
            "e' reusable se il fatto puo' servire in una conversazione futura: comprende "
            "clienti, prodotti, materiali, prove industriali, processi, progetti e decisioni "
            "future concrete, anche quando sono probabili, preliminari o pending. Usa "
            "session_only soltanto per lo stato transitorio della conversazione o del runtime "
            "dell'assistente (riavvio, modifica software appena testata, codice o test corrente). "
            "La parola test non rende session_only una prova tecnica del cliente, del prodotto "
            "o del processo: conta il referente semantico, non il vocabolo.\n"
            "Classifica poi la destinazione mnemonica globale in modo coerente con i fatti. "
            "Se il turno INFORMA almeno un fatto reusable usa candidate. Se tutti i fatti "
            "utili sono session_only usa ephemeral. Usa no_store quando non ci sono fatti "
            "riutilizzabili (domande, comandi, conferme, convenevoli e correzioni di identita') "
            "e per una richiesta esplicita di salvataggio, gia' gestita dal flusso attivo e "
            "da non duplicare passivamente. Questa classificazione puo' soltanto candidare o "
            "impedire l'estrazione passiva: non salva direttamente nulla.\n"
            "Produci inoltre memory_policy per le istruzioni ESPLICITE dell'utente sul "
            "non memorizzare. Per action=suppress usa episode come scelta prudente. Usa "
            "scope=turn soltanto se il contenuto protetto e' gia' tutto nel turno corrente "
            "e l'utente limita esplicitamente il divieto a quel singolo messaggio; usa "
            "scope=episode se introduce una serie, scenario o "
            "blocco di dati che seguira' nella conversazione. Se il turno stabilisce il "
            "divieto ma non contiene ancora i dati sostanziali da proteggere, scope=turn "
            "non avrebbe oggetto: usa episode, perché il contenuto protetto arrivera' nei "
            "turni successivi. Usa action=resume solo quando "
            "l'utente revoca esplicitamente il divieto. In tutti gli altri casi usa none. "
            "Non dedurre questa policy dal fatto che un esempio sia ipotetico: serve una "
            "volonta' esplicita sul salvataggio. evidence deve citare letteralmente il "
            "passaggio del turno corrente che esprime tale volonta'. scope_evidence deve "
            "citare il passaggio che limita esplicitamente il divieto al solo turno; "
            "lascialo vuoto per una policy episodica. Non ereditare qui la "
            "policy dai turni precedenti, perche' lo fara' il sistema dopo la validazione.\n"
            "Produci anche memory_retrieval: e' un piano semantico per decidere se la "
            "risposta corrente beneficia della memoria durevole, non un comando di ricerca. "
            "needed=true soltanto quando recuperare conoscenza pregressa aiuta davvero a "
            "rispondere; una semplice menzione incidentale di un nome non basta. In focus "
            "inserisci esclusivamente entita' gia' presenti in entities, usando il nome "
            "canonico e una relevance continua. role descrive il ruolo discorsivo: focus "
            "per il soggetto cercato, comparison per un termine di confronto, context per "
            "un riferimento ambientale e modifier per una proprieta'/prodotto che restringe "
            "un altro soggetto. relation riassume liberamente il rapporto compreso. "
            "evidence_goal indica overview, fact, comparison, provenance, timeline, "
            "continuity oppure other. Per domande sull'origine di un ricordo usa provenance. "
            "Non inventare entita' o bisogni mnemonici assenti dal turno e dal contesto.\n"
            "Produci anche evidence_request, che descrive quali fatti esterni alle sole "
            "premesse del turno servono davvero per rispondere. Non e' un comando e non "
            "autorizza strumenti. dependency=none se basta ragionare sulle premesse date "
            "dall'utente o se la risposta non richiede fatti ulteriori; optional se puoi "
            "dare una risposta condizionale utile ma alcuni dettagli renderebbero il "
            "confronto piu' preciso; required soltanto se senza quei dati una risposta "
            "specifica sarebbe inventata. In entities usa esclusivamente nomi gia' "
            "presenti in entities. premises conserva, senza rafforzarle, le assunzioni "
            "esplicite o implicate dalla domanda. missing_facts nomina informazioni "
            "concrete mancanti, non temi generici. acceptable_sources ordina le fonti "
            "semanticamente adatte: current_user se il proprietario puo' conoscere il "
            "dato, company_documents per dati aziendali/documentali, web per fatti "
            "pubblici verificabili. Il web non e' una fonte automatica e non va sempre "
            "proposto. Se la richiesta impone di usare soltanto le memorie, usa "
            "memory_only=true e non proporre fonti esterne. Una normale analogia basata "
            "su premesse gia' sufficienti (per esempio due marche che hanno entrambe una "
            "birra bionda) non crea un vuoto informativo. In un confronto aziendale, una "
            "premessa dell'utente puo' essere usata come premessa, ma non autorizza a "
            "inventare processi, valori o caratteristiche dell'altra azienda.\n"
            "Se il turno corrente autorizza esplicitamente una ricerca web con un "
            "riferimento ellittico come 'controlla nel web', risolvi dal dialogo recente "
            "l'entita' e i missing_facts appena discussi, usa WEB_SEARCH e "
            "REQUEST_WEB_SEARCH e costruisci una web_query autonoma e precisa. Non "
            "completare la query con dettagli che non compaiono nel turno o nel dialogo.\n"
            "Distingui una vera SESSIONE DI INSEGNAMENTO da una normale spiegazione. "
            "Usa primary_intent=TEACH e speech act INITIATE_TEACHING soltanto quando "
            "l'utente vuole trasferire conoscenza all'assistente in un dialogo guidato: "
            "l'assistente ascoltera', fara' domande e proporra' un riepilogo da salvare "
            "solo dopo conferma. Compila teaching_session con recipient=assistant, "
            "goal=knowledge_transfer, interaction=guided_session e cita in evidence un "
            "passaggio letterale del turno che dimostri questa intenzione. Chiedere "
            "all'assistente di spiegare, presentare o raccontare qualcosa a una terza "
            "persona resta CHAT con recipient=third_party e goal=explanation. Una normale "
            "confidenza o un racconto rivolto all'assistente resta CHAT se non emerge "
            "l'intenzione di istruirlo. Una richiesta di ricordare direttamente un fatto "
            "resta SAVE_MEMORY, non TEACH. Nel dubbio resta CHAT e usa valori unclear.\n"
            "Valuta infine se il turno richiede una DELIBERAZIONE COMPETITIVA, cioe' "
            "il confronto lento di piu' ipotesi indipendenti. Usa mode=explicit e lo "
            "speech act REQUEST_DELIBERATION solo quando l'utente chiede davvero di "
            "esplorare, confrontare o mettere alla prova piu' alternative sullo stesso "
            "problema. Una normale domanda, 'cosa ne pensi?', una richiesta di spiegazione "
            "o un singolo consiglio restano mode=none. Usa mode=suggest soltanto quando "
            "nel problema sono visibili almeno due strade materialmente diverse, la scelta "
            "ha conseguenze non banali e il beneficio plausibile giustifica diversi minuti "
            "di calcolo; in questo caso il sistema chiedera' consenso prima di partire. "
            "Non proporla per ricordi fattuali, conversazione casuale, salvataggi, correzioni, "
            "azioni semplici o quando evidence_request segnala fatti indispensabili ancora "
            "mancanti. problem deve formulare il problema reale senza aggiungere premesse; "
            "constraints contiene solo vincoli espressi nel turno o nel dialogo recente. "
            "Cita in evidence un passaggio letterale del turno corrente che giustifica la "
            "classificazione. Nel dubbio usa mode=none.\n"
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
            "RESCHEDULE|ACTION_REASONING|TRANSLATE|DICTATION|TEACH\","
            "\"speech_acts\":[\"INFORM|ASK|REQUEST_WEB_SEARCH|REQUEST_MEMORY_SEARCH|"
            "REQUEST_SAVE|REQUEST_ACTION|CORRECT_ENTITY|CORRECT_FACT|CONFIRM|REJECT|"
            "DICTATE|TRANSLATE|INITIATE_TEACHING|REQUEST_DELIBERATION\"],\"entities\":[{\"observed_form\":\"\","
            "\"canonical_name\":\"\",\"entity_type\":\"person|organization|product|"
            "place|project|other\",\"status\":\"mentioned|resolved|explicit_correction\","
            "\"evidence\":\"\",\"confidence\":0.0}],\"facts\":[{\"claim\":\"\","
            "\"modality\":\"asserted|probable|planned|pending|counterfactual\","
            "\"durability\":\"reusable|session_only\"}],\"actions\":[{"
            "\"effect\":\"\",\"target\":\"\",\"capability_class\":\"\","
            "\"effect_scope\":\"response|read|write|state_change|external\","
            "\"polarity\":\"requested|negated|hypothetical\","
            "\"source_kind\":\"active_document|recent_conversation|instruction_only|unspecified\","
            "\"source_scope\":\"last_exchange|current_thread|recent_turns|unspecified\"}],"
            "\"web_query\":\"\",\"web_search_request\":{"
            "\"explicit\":false,\"source\":\"web|unspecified\",\"evidence\":\"\","
            "\"confidence\":0.0},\"preservation_mode\":\"semantic|verbatim\","
            "\"requires_clarification\":false,\"meaning_preserved\":true,"
            "\"confidence\":0.0,\"memory_disposition\":\"candidate|ephemeral|no_store\","
            "\"memory_reason\":\"\",\"memory_policy\":{"
            "\"action\":\"none|suppress|resume\",\"scope\":\"episode|turn\","
            "\"evidence\":\"\",\"scope_evidence\":\"\",\"confidence\":0.0},"
            "\"memory_retrieval\":{\"needed\":false,"
            "\"focus\":[{\"entity\":\"\",\"role\":\"focus|comparison|context|modifier\","
            "\"relevance\":0.0}],\"relation\":\"\","
            "\"evidence_goal\":\"overview|fact|comparison|provenance|timeline|continuity|other\","
            "\"confidence\":0.0},\"evidence_request\":{"
            "\"dependency\":\"none|optional|required\",\"entities\":[\"\"],"
            "\"premises\":[\"\"],\"missing_facts\":[\"\"],"
            "\"acceptable_sources\":[\"current_user|company_documents|web\"],"
            "\"memory_only\":false,\"confidence\":0.0},"
            "\"teaching_session\":{\"recipient\":\"assistant|third_party|unclear\","
            "\"goal\":\"knowledge_transfer|explanation|ordinary_conversation|direct_save|unclear\","
            "\"interaction\":\"guided_session|single_turn|unclear\","
            "\"evidence\":\"\",\"confidence\":0.0},"
            "\"deliberation_request\":{\"mode\":\"none|explicit|suggest\","
            "\"problem\":\"\",\"reason\":\"tradeoff|multiple_hypotheses|high_impact|uncertainty|other\","
            "\"alternatives_visible\":false,\"constraints\":[\"\"],"
            "\"evidence\":\"\",\"confidence\":0.0},"
            "\"addressed_to_assistant\":false,"
            "\"address_relation\":\"direct_address|direct_followup|ambient|unclear\","
            "\"address_confidence\":0.0}\n"
            "INPUT_JSON:\n" + json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    def _normalized_facts(data: dict) -> list[dict]:
        facts: list[dict] = []
        for item in data.get("facts") or []:
            if not isinstance(item, dict):
                continue
            claim = str(
                item.get("claim")
                or item.get("fact")
                or item.get("description")
                or item.get("content")
                or ""
            ).strip()
            if not claim:
                continue
            modality = str(item.get("modality") or "unspecified").strip().lower()
            if modality not in _ALLOWED_FACT_MODALITIES:
                modality = "unspecified"
            durability = str(item.get("durability") or "unspecified").strip().lower()
            if durability not in _ALLOWED_FACT_DURABILITY:
                durability = "unspecified"
            normalized = dict(item)
            normalized.update({
                "claim": claim[:1000],
                "modality": modality,
                "durability": durability,
            })
            facts.append(normalized)
        return facts[:24]

    @staticmethod
    def _normalized_actions(data: dict) -> list[dict]:
        actions: list[dict] = []
        for item in data.get("actions") or []:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("effect_scope") or "unspecified").strip().lower()
            if scope not in _ALLOWED_ACTION_EFFECT_SCOPES:
                scope = "unspecified"
            polarity = str(item.get("polarity") or "unspecified").strip().lower()
            if polarity not in _ALLOWED_ACTION_POLARITIES:
                polarity = "unspecified"
            source_kind = str(
                item.get("source_kind") or "unspecified"
            ).strip().lower()
            if source_kind not in _ALLOWED_DOCUMENT_SOURCE_KINDS:
                source_kind = "unspecified"
            source_scope = str(
                item.get("source_scope") or "unspecified"
            ).strip().lower()
            if source_scope not in _ALLOWED_DOCUMENT_SOURCE_SCOPES:
                source_scope = "unspecified"
            normalized = dict(item)
            normalized.update({
                "effect": str(item.get("effect") or "").strip()[:500],
                "target": str(item.get("target") or "").strip()[:300],
                "capability_class": str(
                    item.get("capability_class") or ""
                ).strip()[:120],
                "effect_scope": scope,
                "polarity": polarity,
                "source_kind": source_kind,
                "source_scope": source_scope,
            })
            actions.append(normalized)
        return actions[:12]

    @staticmethod
    def _replace_entity_form(text: str, observed: str, canonical: str) -> str:
        """Proietta un'identita' accettata usando soli confini generici."""
        if not text or not observed or not canonical:
            return str(text or "")
        return re.sub(
            rf"(?<!\w){re.escape(observed)}(?!\w)",
            lambda _match: canonical,
            str(text),
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _contains_entity_form(text: str, value: str) -> bool:
        if not text or not value:
            return False
        return bool(re.search(
            rf"(?<!\w){re.escape(value)}(?!\w)",
            str(text),
            flags=re.IGNORECASE,
        ))

    @classmethod
    def _has_non_surface_identity_rewrite(
        cls,
        *,
        raw_text: str,
        interpreted_text: str,
        entities: list[dict],
    ) -> bool:
        """Rileva quando un pronome/descrizione e' stato riscritto come nome."""
        for entity in entities:
            if str(entity.get("status") or "").strip().lower() not in {
                "mentioned", "resolved",
            }:
                continue
            observed = str(entity.get("observed_form") or "").strip()
            canonical = _compact_spelled_name(
                str(entity.get("canonical_name") or "").strip()
            )
            if (
                observed
                and canonical
                and _alias_token(observed) != _alias_token(canonical)
                and _surface_name_similarity(observed, canonical)
                < _MIN_CURRENT_ENTITY_SURFACE_SIMILARITY
                and cls._contains_entity_form(raw_text, observed)
                and not cls._contains_entity_form(interpreted_text, observed)
                and cls._contains_entity_form(interpreted_text, canonical)
            ):
                return True
        return False

    @classmethod
    def _project_current_entities(
        cls,
        *,
        raw_text: str,
        interpreted_text: str,
        web_query: str,
        entities: list[dict],
    ) -> tuple[str, str, list[dict]]:
        """Usa la comprensione del frame nel turno corrente senza apprendere alias."""
        projected_text = interpreted_text
        projected_query = web_query
        projections: list[dict] = []
        for entity in entities:
            if str(entity.get("status") or "").strip().lower() not in {
                "mentioned", "resolved",
            }:
                continue
            observed = str(entity.get("observed_form") or "").strip()
            canonical = _compact_spelled_name(
                str(entity.get("canonical_name") or "").strip()
            )
            evidence = str(entity.get("evidence") or "").strip()
            if (
                not observed
                or not canonical
                or not evidence
                or _confidence(entity.get("confidence")) < 0.80
                or _alias_token(observed) == _alias_token(canonical)
                or _surface_name_similarity(observed, canonical)
                < _MIN_CURRENT_ENTITY_SURFACE_SIMILARITY
                or not cls._contains_entity_form(raw_text, observed)
            ):
                continue
            next_text = cls._replace_entity_form(projected_text, observed, canonical)
            next_query = cls._replace_entity_form(projected_query, observed, canonical)
            if next_text == projected_text and next_query == projected_query:
                continue
            projected_text = next_text
            projected_query = next_query
            projections.append({
                "observed_form": observed,
                "canonical_name": canonical,
                "entity_type": str(entity.get("entity_type") or "other")[:64],
                "confidence": _confidence(entity.get("confidence")),
                "scope": "current_turn",
            })
        return projected_text, projected_query, projections

    @staticmethod
    def _normalized_memory_retrieval(data: dict, entities: list[dict]) -> dict:
        """Valida il piano mnemonico senza reinterpretare il linguaggio.

        Gemma decide il significato; questo bordo impedisce soltanto che un nome
        inventato dal piano apra uno schema non osservato nel frame del turno.
        """
        raw = data.get("memory_retrieval")
        if not isinstance(raw, dict):
            return {}
        allowed_roles = {"focus", "comparison", "context", "modifier"}
        allowed_goals = {
            "overview", "fact", "comparison", "provenance", "timeline",
            "continuity", "other",
        }
        known: dict[str, str] = {}
        for entity in entities:
            observed = str(entity.get("observed_form") or "").strip()
            canonical = str(entity.get("canonical_name") or observed).strip()
            if observed:
                known[_identity_token(observed)] = canonical or observed
            if canonical:
                known[_identity_token(canonical)] = canonical

        focus: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in raw.get("focus") or []:
            if not isinstance(item, dict):
                continue
            requested = str(
                item.get("entity") or item.get("canonical_name") or ""
            ).strip()
            canonical = known.get(_identity_token(requested))
            if not canonical:
                continue
            role = str(item.get("role") or "focus").strip().lower()
            if role not in allowed_roles:
                role = "focus"
            key = (_identity_token(canonical), role)
            if key in seen:
                continue
            seen.add(key)
            focus.append({
                "entity": canonical[:160],
                "role": role,
                "relevance": _confidence(item.get("relevance")),
            })
            if len(focus) >= 8:
                break

        goal = str(raw.get("evidence_goal") or "other").strip().lower()
        if goal not in allowed_goals:
            goal = "other"
        return {
            "needed": raw.get("needed") is True,
            "focus": focus,
            "relation": str(raw.get("relation") or "").strip()[:240],
            "evidence_goal": goal,
            "confidence": _confidence(raw.get("confidence")),
        }

    @staticmethod
    def _normalized_evidence_request(data: dict, entities: list[dict]) -> dict:
        """Valida il bisogno informativo deciso semanticamente da Gemma.

        Il bordo non interpreta il testo e non sceglie fonti: limita il piano a
        entita' gia' ancorate nel frame e a un vocabolario operativo compatto.
        """
        raw = data.get("evidence_request")
        if not isinstance(raw, dict):
            return {}

        dependency = str(raw.get("dependency") or "none").strip().lower()
        if dependency not in _ALLOWED_EVIDENCE_DEPENDENCIES:
            dependency = "none"

        known: dict[str, str] = {}
        for entity in entities:
            observed = str(entity.get("observed_form") or "").strip()
            canonical = str(entity.get("canonical_name") or observed).strip()
            if observed:
                known[_identity_token(observed)] = canonical or observed
            if canonical:
                known[_identity_token(canonical)] = canonical

        requested_entities: list[str] = []
        seen_entities: set[str] = set()
        for item in raw.get("entities") or []:
            requested = str(item or "").strip()
            canonical = known.get(_identity_token(requested))
            key = _identity_token(canonical or "")
            if not canonical or key in seen_entities:
                continue
            seen_entities.add(key)
            requested_entities.append(canonical[:160])
            if len(requested_entities) >= 8:
                break

        def _short_strings(name: str, limit: int) -> list[str]:
            values: list[str] = []
            for item in raw.get(name) or []:
                value = str(item or "").strip()
                if value and value not in values:
                    values.append(value[:240])
                if len(values) >= limit:
                    break
            return values

        sources: list[str] = []
        for item in raw.get("acceptable_sources") or []:
            source = str(item or "").strip().lower()
            if source in _ALLOWED_EVIDENCE_SOURCES and source not in sources:
                sources.append(source)

        memory_only = raw.get("memory_only") is True
        if memory_only:
            sources = []
        if dependency == "none":
            requested_entities = []
            sources = []

        return {
            "dependency": dependency,
            "entities": requested_entities,
            "premises": _short_strings("premises", 6),
            "missing_facts": _short_strings("missing_facts", 6),
            "acceptable_sources": sources,
            "memory_only": memory_only,
            "confidence": _confidence(raw.get("confidence")),
        }

    @staticmethod
    def _normalized_teaching_session(data: dict, raw_text: str) -> dict:
        """Conserva un contratto TEACH solo se la sua evidenza viene dal turno.

        Il modello comprende l'intenzione; questo bordo non tenta di rifarla con
        parole chiave. Verifica pero' che il passaggio addotto come motivo sia
        realmente pronunciato dall'utente, cosi' una classificazione astratta non
        puo' aprire da sola una modalita' capace di salvare memoria durevole.
        """
        raw = data.get("teaching_session")
        if not isinstance(raw, dict):
            return {}

        recipient = str(raw.get("recipient") or "unclear").strip().lower()
        if recipient not in {"assistant", "third_party", "unclear"}:
            recipient = "unclear"
        goal = str(raw.get("goal") or "unclear").strip().lower()
        if goal not in {
            "knowledge_transfer", "explanation", "ordinary_conversation",
            "direct_save", "unclear",
        }:
            goal = "unclear"
        interaction = str(raw.get("interaction") or "unclear").strip().lower()
        if interaction not in {"guided_session", "single_turn", "unclear"}:
            interaction = "unclear"

        evidence = str(raw.get("evidence") or "").strip()[:320]
        evidence_key = _alias_token(evidence)
        raw_key = _alias_token(raw_text)
        evidence_grounded = bool(evidence_key and evidence_key in raw_key)
        return {
            "recipient": recipient,
            "goal": goal,
            "interaction": interaction,
            "evidence": evidence if evidence_grounded else "",
            "evidence_grounded": evidence_grounded,
            "confidence": _confidence(raw.get("confidence")),
        }

    @staticmethod
    def _normalized_web_search_request(data: dict, raw_text: str) -> dict:
        """Valida l'autorizzazione esterna citata dal frame nel turno corrente."""
        raw = data.get("web_search_request")
        if not isinstance(raw, dict):
            return {
                "explicit": False,
                "source": "unspecified",
                "evidence": "",
                "evidence_grounded": False,
                "confidence": 0.0,
            }
        source = str(raw.get("source") or "unspecified").strip().lower()
        if source not in {"web", "unspecified"}:
            source = "unspecified"
        evidence = str(raw.get("evidence") or "").strip()[:320]
        evidence_key = _alias_token(evidence)
        raw_key = _alias_token(raw_text)
        grounded = bool(evidence_key and evidence_key in raw_key)
        explicit = raw.get("explicit") is True and source == "web" and grounded
        return {
            "explicit": explicit,
            "source": "web" if explicit else "unspecified",
            "evidence": evidence if grounded else "",
            "evidence_grounded": grounded,
            "confidence": _confidence(raw.get("confidence")),
        }

    @staticmethod
    def _normalized_memory_policy(data: dict, raw_text: str) -> dict:
        """Valida una direttiva mnemonica senza riconoscerla con regole lessicali.

        Gemma decide se l'utente stia imponendo o revocando un confine. Il bordo
        deterministico richiede soltanto che la motivazione citata compaia davvero
        nel turno corrente; una spiegazione astratta del modello non puo' aprire
        una policy multi-turn.
        """
        raw = data.get("memory_policy")
        if not isinstance(raw, dict):
            return {
                "action": "none", "scope": "turn", "evidence": "",
                "evidence_grounded": False, "scope_evidence": "",
                "scope_evidence_grounded": False, "confidence": 0.0,
            }
        action = str(raw.get("action") or "none").strip().lower()
        if action not in _ALLOWED_MEMORY_POLICY_ACTIONS:
            action = "none"
        scope = str(raw.get("scope") or "turn").strip().lower()
        if scope not in _ALLOWED_MEMORY_POLICY_SCOPES:
            scope = "turn"
        evidence = str(raw.get("evidence") or "").strip()[:320]
        evidence_key = _alias_token(evidence)
        raw_key = _alias_token(raw_text)
        grounded = bool(evidence_key and evidence_key in raw_key)
        scope_evidence = str(raw.get("scope_evidence") or "").strip()[:320]
        scope_evidence_key = _alias_token(scope_evidence)
        scope_grounded = bool(
            scope_evidence_key and scope_evidence_key in raw_key
        )
        if action != "none" and not grounded:
            action = "none"
            scope = "turn"
        # Privacy fail-safe: il singolo turno e' un'eccezione ammessa soltanto
        # quando il modello puo' indicare le parole con cui l'utente l'ha
        # delimitata. In assenza di quella prova, il divieto vale per l'episodio.
        if action == "suppress" and scope == "turn" and not scope_grounded:
            scope = "episode"
        return {
            "action": action,
            "scope": scope,
            "evidence": evidence if grounded else "",
            "evidence_grounded": grounded,
            "scope_evidence": scope_evidence if scope_grounded else "",
            "scope_evidence_grounded": scope_grounded,
            "confidence": _confidence(raw.get("confidence")),
        }

    @staticmethod
    def _normalized_deliberation_request(data: dict, raw_text: str) -> dict:
        """Valida il motivo semantico senza cercare parole di attivazione.

        L'evidenza letterale non decide il significato: impedisce pero' che un
        suggerimento costoso nasca da una spiegazione astratta del modello e non
        dal turno effettivamente pronunciato.
        """
        raw = data.get("deliberation_request")
        if not isinstance(raw, dict):
            return {}
        mode = str(raw.get("mode") or "none").strip().lower()
        if mode not in _ALLOWED_DELIBERATION_MODES:
            mode = "none"
        reason = str(raw.get("reason") or "other").strip().lower()
        if reason not in _ALLOWED_DELIBERATION_REASONS:
            reason = "other"
        evidence = str(raw.get("evidence") or "").strip()[:320]
        evidence_key = _alias_token(evidence)
        raw_key = _alias_token(raw_text)
        evidence_grounded = bool(evidence_key and evidence_key in raw_key)
        constraints: list[str] = []
        for item in raw.get("constraints") or []:
            value = str(item or "").strip()[:360]
            if value and value not in constraints:
                constraints.append(value)
            if len(constraints) >= 8:
                break
        if mode == "none":
            return {
                "mode": "none", "problem": "", "reason": reason,
                "alternatives_visible": False, "constraints": [],
                "evidence": "", "evidence_grounded": False,
                "confidence": _confidence(raw.get("confidence")),
            }
        return {
            "mode": mode,
            "problem": str(raw.get("problem") or "").strip()[:2400],
            "reason": reason,
            "alternatives_visible": raw.get("alternatives_visible") is True,
            "constraints": constraints,
            "evidence": evidence if evidence_grounded else "",
            "evidence_grounded": evidence_grounded,
            "confidence": _confidence(raw.get("confidence")),
        }

    @classmethod
    def _validated_frame(cls, data: dict, raw_text: str, baseline: str, scope: str) -> SemanticTurnFrame:
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
            "REQUEST_ACTION", "DICTATE", "TRANSLATE", "INITIATE_TEACHING",
            "REQUEST_DELIBERATION",
        }
        # Un turno informativo/interrogativo e' comunque conversazione. Senza
        # questo default un primary_intent vuoto puo' cadere nel controller fuzzy.
        if not intent and speech_acts and not (set(speech_acts) & operational_acts):
            intent = "CHAT"
        entities = [item for item in (data.get("entities") or []) if isinstance(item, dict)][:16]
        memory_retrieval = cls._normalized_memory_retrieval(data, entities)
        evidence_request = cls._normalized_evidence_request(data, entities)
        web_search_request = cls._normalized_web_search_request(data, raw_text)
        teaching_session = cls._normalized_teaching_session(data, raw_text)
        deliberation_request = cls._normalized_deliberation_request(data, raw_text)
        memory_policy = cls._normalized_memory_policy(data, raw_text)
        facts = cls._normalized_facts(data)
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
        acts = set(speech_acts)
        has_reusable_fact = any(
            item.get("durability") == "reusable" for item in facts
        )
        has_only_session_facts = bool(facts) and all(
            item.get("durability") == "session_only" for item in facts
        )
        # La granularita' del fatto prevale sull'etichetta globale: nel caso
        # reale una prova industriale era stata scambiata per un test del runtime.
        # La richiesta esplicita di salvataggio resta esclusa per non duplicare
        # il flusso attivo con il learner passivo.
        trusted_policy_suppression = (
            memory_policy.get("action") == "suppress"
            and memory_policy.get("evidence_grounded") is True
            and _confidence(memory_policy.get("confidence")) >= 0.72
            and confidence >= 0.72
        )
        if trusted_policy_suppression:
            memory_disposition = "no_store"
            data["memory_reason"] = "direttiva esplicita di non memorizzazione"
        elif "REQUEST_SAVE" in acts:
            memory_disposition = "no_store"
        elif "INFORM" in acts and has_reusable_fact:
            if memory_disposition != "candidate":
                data["memory_reason"] = "almeno un fatto riutilizzabile nel frame"
            memory_disposition = "candidate"
        elif has_only_session_facts:
            if memory_disposition != "ephemeral":
                data["memory_reason"] = "i fatti del turno valgono solo nella sessione corrente"
            memory_disposition = "ephemeral"
        # Compatibilita' conservativa con output prodotti senza il nuovo campo:
        # richieste e correzioni non diventano memorie passive per duplicazione.
        elif memory_disposition == "unspecified" and (
            acts & (operational_acts | {"CORRECT_ENTITY", "CORRECT_FACT"})
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
        if cls._has_non_surface_identity_rewrite(
            raw_text=raw_text,
            interpreted_text=interpreted,
            entities=entities,
        ):
            interpreted = baseline
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
        web_query = str(data.get("web_query") or "").strip()[:240]
        canonical_projections: list[dict] = []
        if safe_interpretation:
            interpreted, web_query, canonical_projections = cls._project_current_entities(
                raw_text=raw_text,
                interpreted_text=interpreted,
                web_query=web_query,
                entities=entities,
            )
        return SemanticTurnFrame(
            turn_id=str(uuid.uuid4()),
            raw_text=raw_text,
            interpreted_text=interpreted or baseline or raw_text,
            primary_intent=intent,
            speech_acts=speech_acts,
            entities=entities,
            facts=facts,
            actions=cls._normalized_actions(data),
            web_query=web_query,
            web_search_request=web_search_request,
            preservation_mode=preservation,
            requires_clarification=bool(data.get("requires_clarification")),
            confidence=confidence,
            meaning_preserved=meaning_preserved,
            status="interpreted",
            memory_scope=scope,
            memory_disposition=memory_disposition,
            memory_reason=str(data.get("memory_reason") or "").strip()[:240],
            memory_policy=memory_policy,
            memory_retrieval=memory_retrieval,
            evidence_request=evidence_request,
            teaching_session=teaching_session,
            deliberation_request=deliberation_request,
            addressed_to_assistant=data.get("addressed_to_assistant") is True,
            address_relation=address_relation,
            address_confidence=_confidence(data.get("address_confidence")),
            canonical_projections=canonical_projections,
        )

    @staticmethod
    def _trusted_memory_policy(frame: SemanticTurnFrame | dict) -> dict | None:
        if SemanticTurnService._frame_value(frame, "status") != "interpreted":
            return None
        if _confidence(SemanticTurnService._frame_value(frame, "confidence")) < 0.72:
            return None
        policy = SemanticTurnService._frame_value(frame, "memory_policy", {})
        if not isinstance(policy, dict):
            return None
        if policy.get("evidence_grounded") is not True:
            return None
        if _confidence(policy.get("confidence")) < 0.72:
            return None
        action = str(policy.get("action") or "none").lower()
        scope = str(policy.get("scope") or "turn").lower()
        if action not in {"suppress", "resume"}:
            return None
        if scope not in _ALLOWED_MEMORY_POLICY_SCOPES:
            return None
        return dict(policy)

    @classmethod
    def _apply_contextual_memory_policy(
        cls,
        frame: SemanticTurnFrame,
        recent_history: list[dict],
        *,
        now_ts: float | None = None,
    ) -> None:
        """Propaga un divieto esplicito fino al confine dell'episodio corrente.

        Ogni frame successivo viene marcato autonomamente: il learner puo' quindi
        lavorare a finestre o dopo un ack senza dover ritrovare il turno iniziale.
        Una pausa oltre il normale confine episodico interrompe l'eredita'.
        """
        current_policy = cls._trusted_memory_policy(frame)
        if current_policy and current_policy["action"] == "resume":
            return
        if current_policy and current_policy["action"] == "suppress":
            frame.passive_memory_blocked = True
            frame.passive_memory_block_scope = current_policy["scope"]
            frame.passive_memory_block_reason = "explicit_memory_policy"
            frame.passive_memory_block_source_turn_id = frame.turn_id
            frame.memory_disposition = "no_store"
            frame.memory_reason = "direttiva esplicita di non memorizzazione"
            if frame.primary_intent == "SAVE_MEMORY":
                frame.primary_intent = "CHAT"
            frame.speech_acts = [
                item for item in frame.speech_acts if item != "REQUEST_SAVE"
            ]
            return

        gap_limit = float(
            getattr(config, "TEMPORAL_EPISODE_GAP_SECONDS", 30 * 60)
        )
        now_ts = time.time() if now_ts is None else float(now_ts)
        for message in reversed(recent_history):
            if str(message.get("role") or "") != "user":
                continue
            if normalize_scope(message.get("memory_scope")) != frame.memory_scope:
                continue
            observed_at = message.get("observed_at")
            try:
                if observed_at is not None and now_ts - float(observed_at) > gap_limit:
                    return
            except (TypeError, ValueError):
                pass
            previous = message.get("semantic_frame")
            if not isinstance(previous, dict):
                continue
            previous_policy = cls._trusted_memory_policy(previous)
            if previous_policy and previous_policy["action"] == "resume":
                return
            inherited = (
                previous.get("passive_memory_blocked") is True
                and str(previous.get("passive_memory_block_scope") or "") == "episode"
            )
            explicit_episode = bool(
                previous_policy
                and previous_policy["action"] == "suppress"
                and previous_policy["scope"] == "episode"
            )
            if inherited or explicit_episode:
                frame.passive_memory_blocked = True
                frame.passive_memory_block_scope = "episode"
                frame.passive_memory_block_reason = "inherited_memory_policy"
                frame.passive_memory_block_source_turn_id = str(
                    previous.get("passive_memory_block_source_turn_id")
                    or previous.get("turn_id")
                    or ""
                )
                frame.memory_disposition = "no_store"
                frame.memory_reason = "direttiva no-store attiva nell'episodio"
                if frame.primary_intent == "SAVE_MEMORY":
                    frame.primary_intent = "CHAT"
                frame.speech_acts = [
                    item for item in frame.speech_acts if item != "REQUEST_SAVE"
                ]
            return

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
            # La somiglianza si valuta sulla grafia finale, la stessa che verra'
            # persistita: una correzione scandita (G-I-O) non deve sembrare
            # distante dalla forma osservata solo per i separatori.
            canonical = _compact_spelled_name(
                str(entity.get("canonical_name") or "").strip()
            )
            evidence = str(entity.get("evidence") or "").strip()
            if (
                observed
                and canonical
                and evidence
                and _alias_token(observed) in raw_token
                and _alias_token(observed) != _alias_token(canonical)
                and _confidence(entity.get("confidence")) >= 0.80
                # Un alias appreso e' permanente e riscrive ogni turno futuro:
                # ammettilo solo per una grafia plausibile dello stesso nome.
                # Senza questo vincolo il modello puo' etichettare un'anafora
                # come correzione esplicita e "loro" resta per sempre un'azienda.
                and _surface_name_similarity(observed, canonical)
                >= _MIN_CURRENT_ENTITY_SURFACE_SIMILARITY
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
        runtime_context: str = "",
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
            runtime_context=runtime_context,
        )
        started = time.perf_counter()
        try:
            with self._model_lock:
                raw_frame = self._call_model(prompt)
                try:
                    data = _json_object(raw_frame)
                except (ValueError, json.JSONDecodeError) as parse_exc:
                    logger.warning(
                        "Turno semantico: frame JSON non valido chars={} "
                        "done={} eval={} ({}) — avvio recupero routing compatto",
                        len(raw_frame),
                        self._last_model_meta.get("done_reason", ""),
                        self._last_model_meta.get("eval_count", 0),
                        parse_exc,
                    )
                    recovery_raw = self._call_routing_recovery_model(raw_text)
                    data = _json_object(recovery_raw)
                    data.setdefault("entities", [])
                    data.setdefault("facts", [])
                    data.setdefault("web_query", "")
                    data.setdefault("preservation_mode", "semantic")
            frame = self._validated_frame(data, raw_text, baseline, scope)
            self._apply_contextual_memory_policy(
                frame,
                list(recent_history or []),
            )
        except Exception as exc:
            logger.warning("Turno semantico: fallback al testo canonico ({})", exc)
            return frame.as_dict()

        if persist_corrections:
            self._commit_corrections(frame)

        logger.info(
            "[TIMING] Turno semantico: {:.0f}ms -> {} acts={} proiezioni={} canonicalizzazioni={}",
            (time.perf_counter() - started) * 1000,
            frame.primary_intent or "UNKNOWN",
            ",".join(frame.speech_acts) or "-",
            len(frame.canonical_projections),
            len(frame.canonicalizations),
        )
        return frame.as_dict()


def trusted_memory_retrieval_plan(
    frame: dict | None,
    *,
    minimum_confidence: float | None = None,
) -> dict | None:
    """Restituisce il piano Gemma soltanto quando il frame e' affidabile.

    ``None`` significa compatibilita' legacy: nessun piano utilizzabile. Un piano
    valido con ``needed=False`` e' invece una decisione semantica esplicita e deve
    poter impedire l'espansione schematica per una menzione incidentale.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    plan = frame.get("memory_retrieval")
    if not isinstance(plan, dict) or "needed" not in plan:
        return None
    floor = float(
        minimum_confidence
        if minimum_confidence is not None
        else getattr(config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72)
    )
    if _confidence(frame.get("confidence")) < floor:
        return None
    if _confidence(plan.get("confidence")) < floor:
        return None
    return {
        "needed": plan.get("needed") is True,
        "focus": [
            dict(item) for item in (plan.get("focus") or [])
            if isinstance(item, dict)
        ][:8],
        "relation": str(plan.get("relation") or "")[:240],
        "evidence_goal": str(plan.get("evidence_goal") or "other").lower(),
        "confidence": _confidence(plan.get("confidence")),
    }


def trusted_evidence_request(
    frame: dict | None,
    *,
    minimum_confidence: float | None = None,
) -> dict | None:
    """Espone il bisogno informativo solo da un frame semantico affidabile.

    La funzione non decide se le memorie coprono il bisogno: quella verifica puo'
    avvenire soltanto dopo il RAG, sui nodi realmente entrati nel prompt.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    request = frame.get("evidence_request")
    if not isinstance(request, dict) or "dependency" not in request:
        return None
    floor = float(
        minimum_confidence
        if minimum_confidence is not None
        else getattr(config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72)
    )
    if _confidence(frame.get("confidence")) < floor:
        return None
    if _confidence(request.get("confidence")) < floor:
        return None
    dependency = str(request.get("dependency") or "none").strip().lower()
    if dependency not in _ALLOWED_EVIDENCE_DEPENDENCIES:
        return None
    return {
        "dependency": dependency,
        "entities": [
            str(item) for item in (request.get("entities") or []) if str(item).strip()
        ][:8],
        "premises": [
            str(item) for item in (request.get("premises") or []) if str(item).strip()
        ][:6],
        "missing_facts": [
            str(item) for item in (request.get("missing_facts") or [])
            if str(item).strip()
        ][:6],
        "acceptable_sources": [
            str(item) for item in (request.get("acceptable_sources") or [])
            if item in _ALLOWED_EVIDENCE_SOURCES
        ],
        "memory_only": request.get("memory_only") is True,
        "confidence": _confidence(request.get("confidence")),
    }


def trusted_web_search_request(
    frame: dict | None,
    *,
    minimum_confidence: float | None = None,
) -> dict | None:
    """Espone l'accesso Web solo da autorizzazione semantica grounded."""
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    floor = float(
        minimum_confidence
        if minimum_confidence is not None
        else getattr(config, "SEMANTIC_WEB_MIN_CONFIDENCE", 0.82)
    )
    if frame.get("requires_clarification") or _confidence(frame.get("confidence")) < floor:
        return None
    if str(frame.get("primary_intent") or "").upper() != "WEB_SEARCH":
        return None
    acts = {str(item or "").upper() for item in frame.get("speech_acts") or []}
    if "REQUEST_WEB_SEARCH" not in acts:
        return None
    request = frame.get("web_search_request")
    if not isinstance(request, dict):
        return None
    if (
        request.get("explicit") is not True
        or request.get("source") != "web"
        or request.get("evidence_grounded") is not True
        or _confidence(request.get("confidence")) < floor
    ):
        return None
    return dict(request)


def gate_web_route(
    frame: dict | None,
    current_intent,
    *,
    minimum_confidence: float | None = None,
) -> str:
    """Chiude sempre un WEB_SEARCH privo di autorizzazione grounded."""
    current = str(getattr(current_intent, "value", current_intent) or "").upper()
    if current != "WEB_SEARCH":
        return current
    floor = float(
        minimum_confidence
        if minimum_confidence is not None
        else getattr(config, "SEMANTIC_WEB_MIN_CONFIDENCE", 0.82)
    )
    if trusted_web_search_request(frame, minimum_confidence=floor) is not None:
        return current
    acts = {
        str(item or "").upper()
        for item in (frame or {}).get("speech_acts") or []
    } if isinstance(frame, dict) else set()
    plan = trusted_memory_retrieval_plan(frame)
    if "REQUEST_MEMORY_SEARCH" in acts or (plan and plan.get("needed")):
        return "SEARCH"
    return "CHAT"


def _frame_has_concrete_action(frame: dict | None) -> bool:
    if not isinstance(frame, dict):
        return False
    for item in frame.get("actions") or []:
        if not isinstance(item, dict):
            continue
        effect_scope = str(item.get("effect_scope") or "").strip().lower()
        polarity = str(item.get("polarity") or "").strip().lower()
        if (
            effect_scope in _OPERATIONAL_ACTION_EFFECT_SCOPES
            and polarity == "requested"
            and str(item.get("effect") or "").strip()
            and str(item.get("target") or "").strip()
            and str(item.get("capability_class") or "").strip()
        ):
            return True
    return False


def _frame_has_grounded_contextual_action(frame: dict | None) -> bool:
    """Azione concreta con il grounding aggiuntivo richiesto dalle correzioni.

    Un ``CORRECT_FACT`` generico non identifica implicitamente un documento. Se il
    modello gli assegna comunque ``effect_scope=write``, la revisione e' operativa
    soltanto quando ha anche scelto una sorgente documentale esplicita. Gli altri
    effetti (read/state/external) conservano il contratto esistente.
    """
    if not _frame_has_concrete_action(frame):
        return False
    acts = {
        str(item or "").upper()
        for item in ((frame or {}).get("speech_acts") or [])
    }
    if not (acts & {"CORRECT_ENTITY", "CORRECT_FACT"}):
        return True
    for item in (frame or {}).get("actions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("polarity") or "").strip().lower() != "requested":
            continue
        scope = str(item.get("effect_scope") or "").strip().lower()
        if scope not in _OPERATIONAL_ACTION_EFFECT_SCOPES:
            continue
        if scope != "write":
            return True
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if source_kind in _ALLOWED_DOCUMENT_SOURCE_KINDS - {"unspecified"}:
            return True
    return False


def frame_document_source(frame: dict | None) -> dict:
    """Sorgente documentale compresa dal frame, senza risolvere dati o path.

    Il modello sceglie il tipo semantico; l'Executor legherà poi quel tipo a un
    documento o a turn_ref reali. Un valore incompleto non autorizza fallback.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return {}
    for item in frame.get("actions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("polarity") or "").lower() != "requested":
            continue
        if str(item.get("effect_scope") or "").lower() != "write":
            continue
        kind = str(item.get("source_kind") or "").strip().lower()
        scope = str(item.get("source_scope") or "").strip().lower()
        if kind not in _ALLOWED_DOCUMENT_SOURCE_KINDS - {"unspecified"}:
            continue
        result = {"source_mode": kind}
        if (
            kind == "recent_conversation"
            and scope in _ALLOWED_DOCUMENT_SOURCE_SCOPES - {"unspecified"}
        ):
            result["source_scope"] = scope
        return result
    return {}


def trusted_teaching_session(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.82,
) -> dict | None:
    """Restituisce il contratto didattico solo su intenzione forte e grounded.

    TEACH apre uno stato multi-turn e puo' culminare in memoria durevole: una
    classificazione generica, un destinatario terzo o un parser in fallback non
    hanno quindi autorita' sufficiente.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    if frame.get("requires_clarification"):
        return None
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return None
    if str(frame.get("primary_intent") or "").upper() != "TEACH":
        return None
    acts = {str(item or "").upper() for item in (frame.get("speech_acts") or [])}
    if "INITIATE_TEACHING" not in acts:
        return None
    contract = frame.get("teaching_session")
    if not isinstance(contract, dict):
        return None
    if contract.get("evidence_grounded") is not True:
        return None
    if _confidence(contract.get("confidence")) < minimum_confidence:
        return None
    if str(contract.get("recipient") or "").lower() != "assistant":
        return None
    if str(contract.get("goal") or "").lower() != "knowledge_transfer":
        return None
    if str(contract.get("interaction") or "").lower() != "guided_session":
        return None
    return dict(contract)


def trusted_deliberation_request(
    frame: dict | None,
    *,
    explicit_minimum_confidence: float | None = None,
    suggest_minimum_confidence: float | None = None,
) -> dict | None:
    """Autorizza Loop 2k solo da una decisione semantica forte e grounded.

    La richiesta esplicita puo' partire direttamente. Un suggerimento resta una
    proposta e richiede poi un CONFIRM separato. I fatti indispensabili mancanti
    chiudono entrambi i percorsi: prima va colmato il vuoto informativo.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    if frame.get("requires_clarification"):
        return None
    if frame.get("addressed_to_assistant") is not True:
        return None
    contract = frame.get("deliberation_request")
    if not isinstance(contract, dict):
        return None
    mode = str(contract.get("mode") or "none").strip().lower()
    if mode not in {"explicit", "suggest"}:
        return None
    explicit_floor = float(
        explicit_minimum_confidence
        if explicit_minimum_confidence is not None
        else getattr(config, "SEMANTIC_IDEATION_EXPLICIT_MIN_CONFIDENCE", 0.82)
    )
    suggest_floor = float(
        suggest_minimum_confidence
        if suggest_minimum_confidence is not None
        else getattr(config, "SEMANTIC_IDEATION_SUGGEST_MIN_CONFIDENCE", 0.88)
    )
    floor = explicit_floor if mode == "explicit" else suggest_floor
    if _confidence(frame.get("confidence")) < floor:
        return None
    if _confidence(contract.get("confidence")) < floor:
        return None
    if contract.get("evidence_grounded") is not True:
        return None
    if not str(contract.get("problem") or "").strip():
        return None
    if contract.get("alternatives_visible") is not True:
        return None
    acts = {str(item or "").upper() for item in frame.get("speech_acts") or []}
    if mode == "explicit" and "REQUEST_DELIBERATION" not in acts:
        return None
    evidence_request = frame.get("evidence_request")
    if isinstance(evidence_request, dict):
        if (
            str(evidence_request.get("dependency") or "none").lower() == "required"
            and any(str(item or "").strip() for item in evidence_request.get("missing_facts") or [])
        ):
            return None
    return dict(contract)


def gate_teaching_route(
    frame: dict | None,
    current_intent,
    *,
    minimum_confidence: float = 0.82,
) -> str:
    """TEACH e' fail-closed: senza contratto semantico torna sempre CHAT."""
    current = str(getattr(current_intent, "value", current_intent) or "").upper()
    if current != "TEACH":
        return current
    if trusted_teaching_session(
        frame, minimum_confidence=minimum_confidence
    ) is not None:
        return "TEACH"
    return "CHAT"


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
        "TEACH": {"INITIATE_TEACHING"},
    }.get(value)
    if required is not None and not (acts & required):
        return ""
    if value == "WEB_SEARCH" and trusted_web_search_request(
        frame,
        minimum_confidence=max(
            float(minimum_confidence),
            float(getattr(config, "SEMANTIC_WEB_MIN_CONFIDENCE", 0.82)),
        ),
    ) is None:
        return ""
    if value == "TEACH" and trusted_teaching_session(
        frame,
        minimum_confidence=max(0.82, float(minimum_confidence)),
    ) is None:
        return ""
    if (
        value in {"EXECUTE", "ACTION_REASONING"}
        and acts & {"CORRECT_ENTITY", "CORRECT_FACT"}
        and not _frame_has_grounded_contextual_action(frame)
    ):
        # La canonicalizzazione e' gia' l'effetto autorizzato del frame. Una
        # richiesta come "correggi il nome" non deve avviare un secondo
        # controller generico o fingere una mutazione di memoria distinta.
        return "CHAT"
    if (
        value in {"EXECUTE", "ACTION_REASONING"}
        and not _frame_has_grounded_contextual_action(frame)
    ):
        # Un modello non puo' autorizzare il controller con la sola etichetta:
        # deve anche rappresentare l'effetto operativo richiesto.
        return ""
    return value


def arbitrate_routable_intent(
    frame: dict | None,
    current_intent,
    *,
    allowed: set[str] | frozenset[str],
    minimum_confidence: float = 0.72,
) -> str:
    """Condivide l'intent semantico solo fra route gia' autorizzate.

    Il frame puo' correggere un router lessicale che ha restituito CHAT, ma non
    puo' trasformare una route mutante/non ammessa in una capability diversa.
    Simmetricamente, un comando SAVE riconosciuto in modo deterministico non viene
    annullato da un frame incoerente: il modello puo' scoprire un save naturale,
    non revocare l'autorita' esplicita gia' presente nelle parole dell'utente.
    Accetta sia enum con ``.value`` sia stringhe per restare channel-agnostic.
    """
    current = str(getattr(current_intent, "value", current_intent) or "").upper()
    safe_allowed = {str(value or "").upper() for value in allowed}
    shared = semantic_intent(frame, minimum_confidence=minimum_confidence)
    if current in {"SAVE_MEMORY", "SAVE_TODO", "SAVE_NOTE", "SAVE_LAST"}:
        return current
    if shared in safe_allowed and current in safe_allowed:
        return shared
    return current


def frame_is_correction(frame: dict | None) -> bool:
    acts = set(frame.get("speech_acts") or []) if isinstance(frame, dict) else set()
    return bool(acts & {"CORRECT_ENTITY", "CORRECT_FACT"})


def frame_requests_contextual_action(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.72,
) -> bool:
    """True se speech act ed effetto descrivono un'azione grounded.

    ``primary_intent`` e' una sintesi fallibile: nel caso reale "Crealo in
    formato Word" era vuoto, mentre REQUEST_ACTION e ``actions`` descrivevano
    correttamente l'effetto operativo.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return False
    if frame.get("requires_clarification"):
        return False
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return False
    acts = {str(item or "").upper() for item in (frame.get("speech_acts") or [])}
    return "REQUEST_ACTION" in acts and _frame_has_grounded_contextual_action(frame)


def frame_requests_linguistic_response(
    frame: dict | None,
    *,
    minimum_confidence: float = 0.72,
) -> bool:
    """True quando l'azione richiesta consiste soltanto nella risposta stessa.

    Presentare, recitare, formulare un discorso o spiegare possono comparire come
    ``REQUEST_ACTION`` nel frame, ma ``effect_scope=response`` dichiara che non
    esiste alcun effetto operativo esterno. Questa informazione serve anche a valle:
    il guard atto-parola non deve scambiare le frasi della presentazione per promesse
    di lavoro in background compiute da Euri.
    """
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return False
    if frame.get("requires_clarification"):
        return False
    if _confidence(frame.get("confidence")) < minimum_confidence:
        return False
    acts = {str(item or "").upper() for item in (frame.get("speech_acts") or [])}
    if "REQUEST_ACTION" not in acts or _frame_has_grounded_contextual_action(frame):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("polarity") or "").strip().lower() == "requested"
        and str(item.get("effect_scope") or "").strip().lower() == "response"
        for item in (frame.get("actions") or [])
    )


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
        # Un primary intent vuoto/UNKNOWN non annulla un effetto operativo
        # strutturato. Senza effetto, invece, il gate resta fail-closed.
        return not _frame_has_grounded_contextual_action(frame)
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
    if frame.get("passive_memory_blocked") is True:
        return True
    disposition = str(frame.get("memory_disposition") or "").lower()
    if disposition == "no_store":
        return True
    if disposition != "ephemeral":
        return False
    # Difesa anche per frame deserializzati o prodotti da versioni intermedie:
    # un fatto riutilizzabile esplicitamente INFORMato non deve sparire per una
    # classificazione globale incoerente.
    acts = {str(item or "").upper() for item in (frame.get("speech_acts") or [])}
    facts = [item for item in (frame.get("facts") or []) if isinstance(item, dict)]
    if "INFORM" in acts and any(
        str(item.get("durability") or "").lower() == "reusable"
        for item in facts
    ):
        return False
    return True


def filter_passive_memory_history(
    history: list[dict],
    *,
    minimum_confidence: float = 0.72,
) -> list[dict]:
    """Esclude turni protetti e la relativa risposta da qualsiasi learner.

    Il filtro e' condiviso da voce e Silent Chat: un contratto mnemonico non
    deve cambiare efficacia in base al canale che ha prodotto il turno.
    """
    eligible: list[dict] = []
    suppress_assistant = False
    for message in history:
        role = str(message.get("role") or "")
        if role == "user":
            suppress_assistant = frame_blocks_passive_memory(
                message.get("semantic_frame"),
                minimum_confidence=minimum_confidence,
            )
            if not suppress_assistant:
                eligible.append(message)
            continue
        if role == "assistant" and suppress_assistant:
            suppress_assistant = False
            continue
        eligible.append(message)
    return eligible


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
