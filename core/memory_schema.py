"""Proiezione schematica reversibile delle memorie (Loop 2j).

Il modulo non sintetizza fatti e non modifica i nodi ``euri:memory:*``.  Costruisce
una vista derivata, versionata e ricostruibile, che collega memorie pulite tramite
entita' esplicite gia' annotate in ``memory_axes``.  Il retrieval puo' seguire un
solo arco della vista e porta nel prompt esclusivamente le memorie sorgente.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import re
import time
import unicodedata
import uuid

from loguru import logger
import numpy as np

import config
from core.memory_axes import analyze_memory_axes
from core.memory_risk import memory_epistemic_rank
from core.memory_scope import PERSONAL_SCOPE, current_scope, scope_of


SCHEMA_PROJECTION_VERSION = "loop2j_entity_schema_v1"
SCHEMA_CURRENT_KEY = "euri:loop2j:current_generation"
SCHEMA_PROJECTION_PREFIX = "euri:loop2j:projection:"

_DIRECT_SOURCES = frozenset({
    "user", "teach", "passive", "conversation", "obsidian_vault", "mobile_in",
})
_BLOCKED_KINDS = frozenset({
    "conversation_anchor", "conversation_episode", "reflection", "reaction_lesson",
    "derived_consolidation",
})
_BLOCKED_TAGS = frozenset({
    "confronto", "lesson", "from_correction", "self_observation", "consolidated",
})
_CORPORATE_SUFFIX_RE = re.compile(
    r"\s+(?:s\.?\s*p\.?\s*a\.?|s\.?\s*r\.?\s*l\.?|srls|ltd|limited|inc)\.?$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9à-öø-ÿ]+", re.IGNORECASE)
_QUERY_STOP = frozenset({
    "anche", "cosa", "come", "della", "delle", "degli", "dello", "dove",
    "memoria", "memorie", "quale", "quali", "quello", "questa", "questo",
    "ricordi", "sapere", "tutto", "tutta", "tutti", "tutte",
})
_SCHEMA_LABEL_STOP = frozenset({
    # Falsi propri tipici dell'annotatore leggero quando una parola comune apre
    # frase, elenco o intestazione. Sono categorie linguistiche, non domini Euri.
    "analisi", "capacità", "ecco", "implementazione", "in", "inoltre",
    "non", "offre", "per", "questa", "questo", "sede", "sistema", "test",
    "testo", "ti", "tipo", "utilizzare", "utilizzo",
})


def _normalise_label(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _CORPORATE_SUFFIX_RE.sub("", text)
    text = re.sub(r"[^\wà-öø-ÿ]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.casefold().split())


def _ambient_entities() -> set[str]:
    return {
        value
        for value in (
            _normalise_label(getattr(config, "OWNER_DISPLAY_NAME", "")),
            _normalise_label(getattr(config, "OWNER_ACTOR_ID", "")),
            _normalise_label(getattr(config, "ASSISTANT_DISPLAY_NAME", "")),
        )
        if value
    }


def _supported_entity_label(raw_label: str, content: str) -> bool:
    """Riduce i falsi propri senza inventare una tassonomia di dominio.

    Nomi composti, codici e acronimi sono strutturalmente forti. Un nome singolo
    TitleCase viene accettato solo se compare anche dentro una frase, non soltanto
    come prima parola dopo punteggiatura/intestazione.
    """
    label = str(raw_label or "").strip()
    normalised = _normalise_label(label)
    if not normalised or normalised in _SCHEMA_LABEL_STOP:
        return False
    # Un numero nudo e' un valore/codice ambiguo, non uno schema. Il percorso
    # identifier-first del RAG continua a recuperarlo direttamente senza creare
    # associazioni fra misure omonime.
    if normalised.isdigit():
        return False
    if " " in normalised or any(char.isdigit() for char in label):
        return True
    compact = re.sub(r"[^A-Za-zÀ-ÖØ-Þ0-9]", "", label)
    if len(compact) >= 2 and compact.isupper():
        return True
    if len(compact) < 3:
        return False
    for match in re.finditer(re.escape(label), str(content or ""), re.IGNORECASE):
        prefix = str(content or "")[:match.start()].rstrip()
        if prefix and prefix[-1] not in ".!?;:\n":
            return True
    return False


def _retrieval_policy(raw_label: str) -> str:
    """Gli acronimi brevi sono concetti contestuali, non ancore globali.

    PP, MFI, GPU o UBQ possono ricorrere in prodotti, aziende e casi distinti.
    Restano nella mappa concettuale, ma da soli non autorizzano l'espansione.
    """
    compact = re.sub(r"[^A-Za-zÀ-ÖØ-Þ0-9]", "", str(raw_label or ""))
    if compact.isupper() and len(compact) <= 4:
        return "contextual_only"
    return "anchor"


def _label_is_explicit_in_query(normalised_label: str, normalised_query: str) -> bool:
    if not normalised_label or not normalised_query:
        return False
    return f" {normalised_label} " in f" {normalised_query} "


def schema_memory_rejection_reason(doc: dict) -> str | None:
    """Gate strutturale: l'appartenenza non decide la verita' del contenuto."""
    if not doc or not doc.get("id") or not doc.get("content"):
        return "incomplete"
    if scope_of(doc) != PERSONAL_SCOPE:
        return "non_personal_scope"
    if str(doc.get("source") or "").lower() not in _DIRECT_SOURCES:
        return "derived_source"
    if str(doc.get("memory_kind") or "").lower() in _BLOCKED_KINDS:
        return "non_factual_kind"
    tags = {str(tag).lower() for tag in (doc.get("tags") or [])}
    if tags & _BLOCKED_TAGS:
        return "derived_tag"
    if doc.get("superseded_by") or doc.get("correction_pending"):
        return "inactive"
    if doc.get("provenance_stale") or doc.get("safety_flag"):
        return "unsafe_provenance"
    try:
        if int(doc.get("audit_flag") or 0) > 0:
            return "audit_flag"
    except (TypeError, ValueError):
        return "audit_flag_invalid"
    if doc.get("passive_support") == "tacit_acceptance":
        return "tacit_acceptance"
    axes = doc.get("memory_axes") or analyze_memory_axes(
        str(doc.get("content") or ""),
        source=str(doc.get("source") or ""),
        created_at=doc.get("created_at"),
    )
    if axes.get("subject_status") == "acephalous":
        return "acephalous"
    if not axes.get("entity_mentions"):
        return "no_entities"
    return None


def _member_priority(doc: dict) -> tuple:
    """Ordine stabile: qualita'/uso, poi recenza; non e' un verdetto di verita'."""
    return (
        memory_epistemic_rank(doc),
        -min(int(doc.get("supported_use_count") or 0), 5),
        -min(int(doc.get("recalled_count") or 0), 20),
        -float(doc.get("last_recalled_at") or doc.get("created_at") or 0.0),
        str(doc.get("id") or ""),
    )


def _diverse_member_ids(docs: list[dict], cap: int) -> list[str]:
    """Round-robin per dominio: uno schema ampio non collassa sul tema dominante."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for doc in sorted(docs, key=_member_priority):
        buckets[str(doc.get("domain") or "generale")].append(doc)
    ordered_domains = sorted(buckets, key=lambda d: (-len(buckets[d]), d))
    selected: list[str] = []
    while ordered_domains and len(selected) < cap:
        remaining = []
        for domain in ordered_domains:
            bucket = buckets[domain]
            if bucket and len(selected) < cap:
                selected.append(str(bucket.pop(0).get("id") or ""))
            if bucket:
                remaining.append(domain)
        ordered_domains = remaining
    return [mid for mid in selected if mid]


def build_schema_projection_from_documents(
    documents: list[dict],
    *,
    generation: str,
    created_at: float | None = None,
    min_members: int | None = None,
    max_members: int | None = None,
) -> dict:
    """Costruisce una proiezione pura e serializzabile da documenti canonici."""
    min_members = int(
        min_members
        if min_members is not None
        else getattr(config, "MEMORY_SCHEMA_MIN_MEMBERS", 3)
    )
    max_members = int(
        max_members
        if max_members is not None
        else getattr(config, "MEMORY_SCHEMA_MAX_MEMBERS", 200)
    )
    ambient = _ambient_entities()
    grouped: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, Counter] = defaultdict(Counter)
    eligible = 0
    rejected = Counter()

    for doc in documents:
        reason = schema_memory_rejection_reason(doc)
        if reason:
            rejected[reason] += 1
            continue
        axes = doc.get("memory_axes") or analyze_memory_axes(
            str(doc.get("content") or ""),
            source=str(doc.get("source") or ""),
            created_at=doc.get("created_at"),
        )
        normalised_in_doc: set[str] = set()
        for raw_label in axes.get("entity_mentions") or []:
            normalised = _normalise_label(raw_label)
            if (
                not normalised
                or normalised in ambient
                or normalised in normalised_in_doc
                or not _supported_entity_label(raw_label, str(doc.get("content") or ""))
            ):
                continue
            normalised_in_doc.add(normalised)
            grouped[normalised].append(doc)
            labels[normalised][str(raw_label).strip()] += 1
        if normalised_in_doc:
            eligible += 1

    schemas: dict[str, dict] = {}
    membership: dict[str, list[str]] = defaultdict(list)
    for normalised, docs in sorted(grouped.items()):
        unique = {str(doc.get("id")): doc for doc in docs if doc.get("id")}
        if len(unique) < min_members:
            continue
        unique_docs = list(unique.values())
        member_ids = _diverse_member_ids(unique_docs, max_members)
        if len(member_ids) < min_members:
            continue
        schema_id = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:20]
        label = sorted(
            labels[normalised].items(), key=lambda item: (-item[1], len(item[0]), item[0])
        )[0][0]
        domains = Counter(
            str(unique[mid].get("domain") or "generale")
            for mid in member_ids
        )
        schemas[schema_id] = {
            "id": schema_id,
            "kind": "entity_schema",
            "label": label,
            "normalised_label": normalised,
            "retrieval_policy": _retrieval_policy(label),
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "total_member_count": len(unique_docs),
            "domains": dict(sorted(domains.items())),
            "source_memory_ids": member_ids,
            "epistemic_status": "derived_index_not_evidence",
        }
        for mid in member_ids:
            membership[mid].append(schema_id)

    return {
        "version": SCHEMA_PROJECTION_VERSION,
        "generation": generation,
        "created_at": float(created_at if created_at is not None else time.time()),
        "scope": PERSONAL_SCOPE,
        "schemas": schemas,
        "membership": dict(membership),
        "stats": {
            "documents_seen": len(documents),
            "documents_eligible": eligible,
            "schemas": len(schemas),
            "memberships": sum(len(v) for v in membership.values()),
            "rejected": dict(sorted(rejected.items())),
        },
    }


def _load_memory_documents(r, *, batch_size: int = 250) -> list[dict]:
    keys = list(r.scan_iter("euri:memory:*"))
    documents: list[dict] = []
    for offset in range(0, len(keys), batch_size):
        batch = keys[offset:offset + batch_size]
        # Il namespace contiene anche checkpoint/stringhe tecniche (per esempio
        # utility_shadow). TYPE evita che un singolo JSON.GET su una chiave non-JSON
        # faccia fallire l'intera pipeline e, soprattutto, la pubblicazione atomica.
        json_batch = batch
        if hasattr(r, "type"):
            try:
                type_pipe = r.pipeline(transaction=False)
                for key in batch:
                    type_pipe.type(key)
                key_types = type_pipe.execute()
                json_batch = [
                    key for key, key_type in zip(batch, key_types)
                    if str(
                        key_type.decode()
                        if isinstance(key_type, (bytes, bytearray))
                        else key_type
                    ).lower() in {"rejson-rl", "json"}
                ]
            except Exception as exc:
                logger.debug(f"Loop 2j: filtro TYPE non disponibile ({exc})")
        if not json_batch:
            continue
        try:
            pipe = r.pipeline(transaction=False)
            for key in json_batch:
                pipe.json().get(key, "$")
            rows = pipe.execute()
        except Exception:
            rows = []
            for key in json_batch:
                try:
                    rows.append(r.json().get(key, "$"))
                except Exception:
                    rows.append(None)
        for raw in rows:
            if not raw:
                continue
            doc = raw[0] if isinstance(raw, list) else raw
            if isinstance(doc, dict):
                documents.append(doc)
    return documents


def build_schema_projection(r) -> dict:
    """Pubblica una nuova generazione e sposta il puntatore solo a fine build."""
    if r is None:
        return {"status": "unavailable", "stats": {}}
    generation = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    documents = _load_memory_documents(r)
    projection = build_schema_projection_from_documents(
        documents,
        generation=generation,
    )
    key = f"{SCHEMA_PROJECTION_PREFIX}{generation}"
    r.json().set(key, "$", projection)
    r.expire(key, int(getattr(config, "MEMORY_SCHEMA_GENERATION_TTL_DAYS", 3)) * 86400)
    # Il puntatore e' l'unica mutazione autoritativa: se il build fallisce prima,
    # il retrieval continua a usare la generazione precedente.
    r.set(SCHEMA_CURRENT_KEY, generation)
    return {"status": "updated", "generation": generation, "stats": projection["stats"]}


def load_current_schema_projection(r) -> dict:
    if r is None:
        return {}
    try:
        generation = r.get(SCHEMA_CURRENT_KEY)
        if isinstance(generation, (bytes, bytearray)):
            generation = generation.decode()
        if not generation:
            return {}
        raw = r.json().get(f"{SCHEMA_PROJECTION_PREFIX}{generation}", "$")
        projection = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(projection, dict):
            return {}
        if projection.get("version") != SCHEMA_PROJECTION_VERSION:
            return {}
        return projection
    except Exception as exc:
        logger.debug(f"Loop 2j: proiezione non disponibile ({exc})")
        return {}


def _query_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(text or "")
        if len(token) >= 3 and token.casefold() not in _QUERY_STOP
    }


def expand_memories_via_schema(
    memory,
    seed_documents: list[dict],
    query: str,
    *,
    limit: int | None = None,
    source_exclude: set[str] | None = None,
    query_feature_cache: dict | None = None,
    semantic_plan: dict | None = None,
) -> tuple[list[dict], dict]:
    """Segue un solo arco schema e restituisce soltanto documenti canonici."""
    limit = int(
        limit
        if limit is not None
        else getattr(config, "MEMORY_SCHEMA_RETRIEVAL_MAX", 2)
    )
    diagnostics = {
        "enabled": bool(getattr(config, "MEMORY_SCHEMA_ENABLED", True)),
        "generation": None,
        "activated_schema_ids": [],
        "added_memory_ids": [],
        "candidate_count": 0,
        "activation_mode": "semantic" if semantic_plan is not None else "legacy",
        "focused_schema_ids": [],
        "evidence_goal": str((semantic_plan or {}).get("evidence_goal") or ""),
    }
    if (
        limit <= 0
        or not diagnostics["enabled"]
        or (semantic_plan is not None and not semantic_plan.get("needed"))
        or (not seed_documents and not (semantic_plan or {}).get("focus"))
    ):
        return [], diagnostics
    projection = load_current_schema_projection(getattr(memory, "r", None))
    if not projection or scope_of(projection) != current_scope():
        return [], diagnostics
    diagnostics["generation"] = projection.get("generation")
    schemas = projection.get("schemas") or {}
    membership = projection.get("membership") or {}
    seed_ids = {
        str(doc.get("id") or "").removeprefix("euri:memory:")
        for doc in seed_documents
        if doc.get("id")
    }
    activated: set[str] = set()
    for mid in seed_ids:
        activated.update(str(sid) for sid in membership.get(mid, []) if sid in schemas)

    focused_schema_relevance: dict[str, float] = {}
    focused_schema_roles: dict[str, set[str]] = defaultdict(set)
    if semantic_plan is not None:
        for item in semantic_plan.get("focus") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "focus").lower()
            try:
                relevance = float(item.get("relevance") or 0.0)
            except (TypeError, ValueError):
                relevance = 0.0
            # `context` e' una menzione compresa ma non un soggetto da aprire.
            if role == "context" or relevance < 0.55:
                continue
            entity_label = _normalise_label(str(item.get("entity") or ""))
            for sid, schema in schemas.items():
                if str(schema.get("normalised_label") or "") != entity_label:
                    continue
                activated.add(str(sid))
                focused_schema_relevance[str(sid)] = max(
                    relevance, focused_schema_relevance.get(str(sid), 0.0)
                )
                focused_schema_roles[str(sid)].add(role)
    if not activated:
        return [], diagnostics
    diagnostics["activated_schema_ids"] = sorted(activated)
    diagnostics["focused_schema_ids"] = sorted(focused_schema_relevance)

    query_normalised = _normalise_label(query)
    query_tokens = _query_tokens(query)
    candidate_support: Counter = Counter()
    candidate_schema_match: Counter = Counter()
    candidate_schema_ids: dict[str, set[str]] = defaultdict(set)
    explicit_activated: set[str] = set()
    for sid in activated:
        schema = schemas[sid]
        explicit_match = (
            sid in focused_schema_relevance
            if semantic_plan is not None
            else _label_is_explicit_in_query(
                str(schema.get("normalised_label") or ""), query_normalised
            )
        )
        if explicit_match:
            explicit_activated.add(sid)
        for mid in schema.get("member_ids") or []:
            mid = str(mid).removeprefix("euri:memory:")
            if mid in seed_ids:
                continue
            candidate_support[mid] += 1
            candidate_schema_ids[mid].add(sid)
            if explicit_match:
                candidate_schema_match[mid] += 1
    diagnostics["candidate_count"] = len(candidate_support)
    evidence_goal = str((semantic_plan or {}).get("evidence_goal") or "").lower()

    candidates: list[dict] = []
    excluded = {str(source).lower() for source in (source_exclude or set())}
    query_vector = None
    cached = (
        ((query_feature_cache or {}).get("entries") or {}).get(str(query))
        if query_feature_cache is not None else None
    )
    if isinstance(cached, dict):
        query_vector = cached.get("vector")
    if query_vector is None:
        embedder = getattr(memory, "_embedder", None)
        if embedder is not None and getattr(embedder, "available", False):
            try:
                query_vector = embedder.encode(query, mode="query")
            except Exception:
                query_vector = None
    for mid in candidate_support:
        supporting_ids = candidate_schema_ids[mid]
        has_anchor_support = any(
            (schemas.get(sid) or {}).get("retrieval_policy") == "anchor"
            for sid in supporting_ids
        )
        shared_explicit = supporting_ids & explicit_activated
        if explicit_activated:
            if evidence_goal == "comparison":
                # Un confronto richiede fonti separate per ciascun soggetto; non
                # pretende che una singola memoria nomini entrambi.
                if not shared_explicit:
                    continue
            elif len(explicit_activated) >= 2:
                # La query ha specificato un contesto composto (es. azienda +
                # proprietà). Una fonte deve rispettarlo interamente: «birra
                # bionda Peroni» non autorizza membri «birra bionda Raffo».
                if shared_explicit != explicit_activated:
                    continue
            else:
                only_sid = next(iter(explicit_activated))
                if only_sid not in supporting_ids:
                    continue
                # Una proprietà contestuale nuda (es. solo «MFI») e' troppo
                # ambigua per espandere. Il RAG base/identifier-first resta attivo.
                if (schemas.get(only_sid) or {}).get("retrieval_policy") != "anchor":
                    continue
        elif not (len(supporting_ids) >= 2 and has_anchor_support):
            # Se la query non nomina gli schemi, servono almeno due legami
            # concordanti e uno deve essere un'ancora non ambigua.
            continue
        try:
            raw = memory.r.json().get(f"euri:memory:{mid}", "$")
        except Exception:
            continue
        doc = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(doc, dict) or scope_of(doc) != current_scope():
            continue
        if str(doc.get("source") or "").lower() in excluded:
            continue
        if schema_memory_rejection_reason(doc):
            continue
        tokens = _query_tokens(str(doc.get("content") or ""))
        overlap = len(tokens & query_tokens)
        semantic_similarity = None
        if query_vector is not None and doc.get("embedding"):
            try:
                qv = np.asarray(query_vector, dtype=np.float32)
                dv = np.asarray(doc.get("embedding"), dtype=np.float32)
                if qv.shape == dv.shape and qv.size:
                    semantic_similarity = float(np.dot(qv, dv))
            except (TypeError, ValueError):
                semantic_similarity = None
        doc = dict(doc)
        doc["_schema_retrieval"] = True
        doc["_schema_support"] = candidate_support[mid]
        doc["_schema_explicit_match"] = candidate_schema_match[mid]
        doc["_schema_query_overlap"] = overlap
        doc["_schema_semantic_similarity"] = semantic_similarity
        doc["_schema_focus_ids"] = sorted(shared_explicit)
        doc["_schema_focus_relevance"] = max(
            (focused_schema_relevance.get(sid, 0.0) for sid in shared_explicit),
            default=0.0,
        )
        doc["_schema_evidence_goal"] = evidence_goal
        candidates.append(doc)

    seed_domains = {str(doc.get("domain") or "") for doc in seed_documents}
    candidates.sort(key=lambda doc: (
        -int(doc.get("_schema_explicit_match") or 0),
        -float(doc.get("_schema_focus_relevance") or 0.0),
        (
            {"user": 0, "teach": 1, "conversation": 2, "passive": 3}.get(
                str(doc.get("source") or "").lower(), 4
            )
            if evidence_goal == "provenance" else 0
        ),
        -float(doc.get("_schema_semantic_similarity") or -1.0),
        -int(doc.get("_schema_query_overlap") or 0),
        str(doc.get("domain") or "") in seed_domains,
        -int(doc.get("_schema_support") or 0),
        memory_epistemic_rank(doc),
        -min(int(doc.get("recalled_count") or 0), 20),
        -float(doc.get("last_recalled_at") or doc.get("created_at") or 0.0),
        str(doc.get("id") or ""),
    ))
    selected: list[dict] = []
    if evidence_goal == "comparison" and len(explicit_activated) >= 2:
        # Un giro per entita' evita che il soggetto con piu' memorie occupi
        # l'intero budget. L'ordine segue la rilevanza decisa da Gemma.
        ordered_focus = sorted(
            explicit_activated,
            key=lambda sid: (-focused_schema_relevance.get(sid, 0.0), sid),
        )
        selected_ids: set[str] = set()
        for sid in ordered_focus:
            match = next(
                (
                    doc for doc in candidates
                    if sid in set(doc.get("_schema_focus_ids") or [])
                    and str(doc.get("id") or "") not in selected_ids
                ),
                None,
            )
            if match is not None and len(selected) < limit:
                selected.append(match)
                selected_ids.add(str(match.get("id") or ""))
        for doc in candidates:
            if len(selected) >= limit:
                break
            if str(doc.get("id") or "") not in selected_ids:
                selected.append(doc)
                selected_ids.add(str(doc.get("id") or ""))
    else:
        selected = candidates[:limit]
    diagnostics["added_memory_ids"] = [str(doc.get("id")) for doc in selected]
    return selected, diagnostics
