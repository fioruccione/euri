"""Aggregazione durevole e uso prudente della lineage RAG.

`response_lineage_shadow_v1` osserva già quali nodi entrano nel prompt e quali
presentano evidenza lessicale distintiva nella risposta. Questo modulo
compatta giornalmente quegli eventi in un rapporto leggibile e fa maturare un
promemoria persistente. Il solo effetto runtime è un rinforzo limitato
dell'attenzione Loop 2e per memorie con uso sostenuto; verità, TTL e gate di
promozione restano invariati.
"""
from __future__ import annotations

import hashlib
import json
import time

import config
from core.cognitive_projector import COGNITIVE_STREAM
from core.response_lineage import LINEAGE_EXPERIMENT
from loguru import logger


# Stato di sistema, non documenti di memoria: il namespace non deve combaciare
# con il prefisso RedisJSON ``euri:memory:*`` usato da indice e backfill.
UTILITY_STATE_KEY = "euri:utility:memory_shadow:state"
UTILITY_REPORT_KEY = "euri:utility:memory_shadow:latest"
UTILITY_REVIEW_PENDING_KEY = "euri:utility:memory_shadow:review_pending"
_LEGACY_UTILITY_KEYS = {
    "euri:memory:utility_shadow:state": UTILITY_STATE_KEY,
    "euri:memory:utility_shadow:latest": UTILITY_REPORT_KEY,
    "euri:memory:utility_shadow:review_pending": UTILITY_REVIEW_PENDING_KEY,
}
UTILITY_SCHEMA_VERSION = 1
_QUERY_HASH_CAP_PER_ENTITY = 64


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _json_dict(value) -> dict:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value) -> list:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _load_json_key(redis_client, key: str) -> dict:
    try:
        return _json_dict(redis_client.get(key))
    except Exception:
        return {}


def migrate_legacy_utility_shadow_keys(redis_client) -> dict:
    """Copia una sola volta lo stato legacy fuori da ``euri:memory:*``.

    La copia è conservativa: una chiave nuova già esistente vince e le vecchie
    stringhe non vengono cancellate. Il backfill type-safe le ignora; restano
    disponibili come rollback/audit del passaggio.
    """
    migrated = []
    preserved = []
    for legacy_key, current_key in _LEGACY_UTILITY_KEYS.items():
        try:
            current = redis_client.get(current_key)
            legacy = redis_client.get(legacy_key)
        except Exception:
            continue
        if current:
            if legacy:
                preserved.append(legacy_key)
            continue
        if not legacy:
            continue
        redis_client.set(current_key, legacy)
        migrated.append({"from": legacy_key, "to": current_key})
    if migrated:
        logger.info(
            "Utilità memoria: namespace migrato conservativamente ({} chiavi)",
            len(migrated),
        )
    return {
        "migrated": migrated,
        "legacy_preserved": preserved,
    }


def get_memory_utility_review_pending(redis_client) -> dict:
    """Ritorna l'avviso durevole, senza mutare la finestra osservativa."""
    migrate_legacy_utility_shadow_keys(redis_client)
    review = _load_json_key(redis_client, UTILITY_REVIEW_PENDING_KEY)
    policy = str(getattr(config, "MEMORY_ATTENTION_POLICY", ""))
    if policy == "selective_reuse_rate_v1":
        return {}
    return review if review.get("status") == "review_pending" else {}


def _new_state(reference_at: float) -> dict:
    return {
        "schema_version": UTILITY_SCHEMA_VERSION,
        "experiment_version": LINEAGE_EXPERIMENT,
        "observation_started_at": float(reference_at),
        "last_processed_stream_id": "0-0",
        "processed_lineage_events": 0,
        "totals": {
            "turns_started": 0,
            "turns_responded": 0,
            "recalled_nodes": 0,
            "used_nodes_supported_not_proven": 0,
        },
        "channels": {},
        "entities": {},
    }


def _entity_from_event(event: dict) -> tuple[str, str] | None:
    refs = _json_list(event.get("entity_refs"))
    if not refs or not isinstance(refs[0], dict):
        return None
    kind = str(refs[0].get("type") or "")
    entity_id = str(refs[0].get("id") or "")
    if kind not in {"memory", "insight"} or not entity_id:
        return None
    return kind, entity_id


def _counter_increment(mapping: dict, key: str) -> None:
    if key:
        mapping[key] = int(mapping.get(key) or 0) + 1


def aggregate_lineage_events(state: dict, rows: list) -> dict:
    """Aggrega eventi proiettati in ordine cronologico.

    Funzione pura rispetto a Redis: utile per replay, regressioni e audit.
    Gli hash delle query servono solo a contare formulazioni differenti e sono
    limitati per entità; nessun testo di domanda o risposta viene conservato.
    """
    totals = state.setdefault("totals", {})
    channels = state.setdefault("channels", {})
    entities = state.setdefault("entities", {})

    for raw_event_id, raw_fields in rows:
        event_id = _text(raw_event_id)
        event = {
            _text(key): _text(value)
            for key, value in dict(raw_fields or {}).items()
        }
        state["last_processed_stream_id"] = event_id
        if (
            event.get("producer") != "response_lineage"
            or event.get("experiment_version") != LINEAGE_EXPERIMENT
        ):
            continue

        state["processed_lineage_events"] = (
            int(state.get("processed_lineage_events") or 0) + 1
        )
        kind = event.get("kind")
        payload = _json_dict(event.get("payload"))
        channel = str(payload.get("channel") or "")

        if event.get("sense") == "turn" and kind == "started":
            totals["turns_started"] = int(totals.get("turns_started") or 0) + 1
            _counter_increment(channels, channel)
            continue
        if event.get("sense") == "turn" and kind == "responded":
            totals["turns_responded"] = int(totals.get("turns_responded") or 0) + 1
            continue
        if kind not in {"recalled", "used_in_response"}:
            continue

        entity = _entity_from_event(event)
        if entity is None:
            continue
        entity_kind, entity_id = entity
        entity_key = f"{entity_kind}:{entity_id}"
        stats = entities.setdefault(
            entity_key,
            {
                "kind": entity_kind,
                "id": entity_id,
                "recalled": 0,
                "used_supported_not_proven": 0,
                "query_hashes": [],
                "query_hashes_capped": False,
                "channels": {},
                "retrieval_paths": {},
                "first_seen_at": None,
                "last_seen_at": None,
                "last_used_at": None,
            },
        )
        try:
            event_ts = float(event.get("ts") or 0) or None
        except (TypeError, ValueError):
            event_ts = None
        if stats.get("first_seen_at") is None and event_ts is not None:
            stats["first_seen_at"] = event_ts
        if event_ts is not None:
            stats["last_seen_at"] = event_ts
        _counter_increment(stats["channels"], channel)

        if kind == "recalled":
            totals["recalled_nodes"] = int(totals.get("recalled_nodes") or 0) + 1
            stats["recalled"] = int(stats.get("recalled") or 0) + 1
            _counter_increment(
                stats["retrieval_paths"],
                str(payload.get("retrieval_path") or ""),
            )
            query_hash = str(payload.get("query_sha256") or "")
            query_hashes = stats.setdefault("query_hashes", [])
            if query_hash and query_hash not in query_hashes:
                if len(query_hashes) < _QUERY_HASH_CAP_PER_ENTITY:
                    query_hashes.append(query_hash)
                else:
                    stats["query_hashes_capped"] = True
        else:
            totals["used_nodes_supported_not_proven"] = (
                int(totals.get("used_nodes_supported_not_proven") or 0) + 1
            )
            stats["used_supported_not_proven"] = (
                int(stats.get("used_supported_not_proven") or 0) + 1
            )
            if event_ts is not None:
                stats["last_used_at"] = event_ts
    return state


def sync_supported_use_metadata(redis_client, state: dict) -> dict:
    """Materializza l'uso osservato nei JSON, in modo idempotente e limitato.

    Il contatore è un lower bound cumulativo della finestra lineage. Non tocca
    `recalled_count`; il solo consumer è lo score derivato di attenzione Loop
    2e. Se la sincronizzazione viene ripetuta, usa il massimo e non raddoppia.
    """
    updated_memories = updated_insights = failures = 0
    from core.memory_attention import update_loop2e_candidate_index

    for stats in (state.get("entities") or {}).values():
        observed = int(stats.get("used_supported_not_proven") or 0)
        observed_recalled = max(int(stats.get("recalled") or 0), observed)
        if observed <= 0:
            continue
        kind = str(stats.get("kind") or "")
        entity_id = str(stats.get("id") or "")
        if kind not in {"memory", "insight"} or not entity_id:
            continue
        key = f"euri:{kind}:{entity_id}"
        try:
            raw = redis_client.json().get(key, "$")
            if not raw or not isinstance(raw[0], dict):
                continue
            doc = raw[0]
            current = int(doc.get("supported_use_count") or 0)
            cumulative = max(current, observed)
            current_recalled = int(
                doc.get("supported_use_observed_recalled_count") or 0
            )
            cumulative_recalled = max(current_recalled, observed_recalled)
            if cumulative == current and cumulative_recalled == current_recalled:
                continue
            redis_client.json().set(
                key, "$.supported_use_count", cumulative
            )
            redis_client.json().set(
                key,
                "$.supported_use_observed_recalled_count",
                cumulative_recalled,
            )
            redis_client.json().set(
                key,
                "$.last_supported_use_at",
                stats.get("last_used_at") or time.time(),
            )
            redis_client.json().set(
                key,
                "$.supported_use_signal",
                {
                    "method": "distinctive_lexical_overlap_v1",
                    "status": "supported_not_proven",
                    "attention_only": True,
                    "attention_policy": "selective_reuse_rate_v1",
                    "observed_recalled_count": cumulative_recalled,
                    "observed_supported_use_count": cumulative,
                    "observed_selective_reuse_ratio": round(
                        cumulative / cumulative_recalled, 6
                    ) if cumulative_recalled else 0.0,
                    "updated_at": time.time(),
                },
            )
            if kind == "memory":
                indexed = dict(doc)
                indexed["supported_use_count"] = cumulative
                indexed["supported_use_observed_recalled_count"] = (
                    cumulative_recalled
                )
                indexed["last_supported_use_at"] = (
                    stats.get("last_used_at") or time.time()
                )
                update_loop2e_candidate_index(redis_client, indexed)
                updated_memories += 1
            else:
                updated_insights += 1
        except Exception:
            failures += 1
    return {
        "updated_memories": updated_memories,
        "updated_insights": updated_insights,
        "failures": failures,
    }


def build_memory_utility_report(
    state: dict,
    *,
    reference_at: float,
    min_days: int,
    min_responded_turns: int,
    max_days: int,
) -> dict:
    """Costruisce il rapporto e il gate temporale preregistrato di revisione."""
    totals = state.get("totals") or {}
    observation_started_at = state.get("observation_started_at")
    if observation_started_at is None:
        observation_started_at = reference_at
    age_days = max(
        0.0,
        (
            float(reference_at)
            - float(observation_started_at)
        )
        / 86400,
    )
    responded = int(totals.get("turns_responded") or 0)
    enough_window_and_data = age_days >= min_days and responded >= min_responded_turns
    forced_by_max_age = age_days >= max_days
    review_due = bool(enough_window_and_data or forced_by_max_age)
    reason = (
        "minimum_window_and_data_reached"
        if enough_window_and_data
        else "maximum_wait_reached"
        if forced_by_max_age
        else "collecting"
    )

    rows = []
    for stats in (state.get("entities") or {}).values():
        recalled = int(stats.get("recalled") or 0)
        used = int(stats.get("used_supported_not_proven") or 0)
        rows.append(
            {
                "kind": stats.get("kind"),
                "id": stats.get("id"),
                "recalled": recalled,
                "used_supported_not_proven": used,
                "recall_to_supported_use_ratio": (
                    round(used / recalled, 4) if recalled else None
                ),
                "unique_query_hashes_lower_bound": len(
                    stats.get("query_hashes") or []
                ),
                "query_hashes_capped": bool(stats.get("query_hashes_capped")),
                "channels": dict(stats.get("channels") or {}),
                "retrieval_paths": dict(stats.get("retrieval_paths") or {}),
                "first_seen_at": stats.get("first_seen_at"),
                "last_seen_at": stats.get("last_seen_at"),
                "last_used_at": stats.get("last_used_at"),
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item["used_supported_not_proven"]),
            -int(item["recalled"]),
            str(item["kind"]),
            str(item["id"]),
        )
    )
    recalled_total = int(totals.get("recalled_nodes") or 0)
    used_total = int(totals.get("used_nodes_supported_not_proven") or 0)
    return {
        "schema_version": UTILITY_SCHEMA_VERSION,
        "mode": "shadow_observation_plus_bounded_selective_attention",
        "experiment_version": LINEAGE_EXPERIMENT,
        "reference_at": float(reference_at),
        "observation_started_at": observation_started_at,
        "observation_age_days": round(age_days, 3),
        "thresholds": {
            "min_days": int(min_days),
            "min_responded_turns": int(min_responded_turns),
            "max_days": int(max_days),
        },
        "review_due": review_due,
        "review_reason": reason,
        "automatic_policy_change": False,
        "runtime_application": {
            "target": "loop2e_attention_order_only",
            "supported_use_weight": float(
                getattr(config, "MEMORY_ATTENTION_SUPPORTED_USE_WEIGHT", 2.0)
            ),
            "supported_use_cap": int(
                getattr(config, "MEMORY_ATTENTION_SUPPORTED_USE_CAP", 5)
            ),
            "attention_policy": str(
                getattr(config, "MEMORY_ATTENTION_POLICY", "")
            ),
            "exposure_prior": float(
                getattr(
                    config,
                    "MEMORY_ATTENTION_SUPPORTED_USE_EXPOSURE_PRIOR",
                    5.0,
                )
            ),
            "truth_gate_changed": False,
            "promotion_gate_changed": False,
            "ttl_changed": False,
        },
        "interpretation": (
            "used_supported_not_proven is conservative lexical evidence, "
            "not proven causal model use or truth"
        ),
        "totals": {
            **totals,
            "entities_observed": len(rows),
            "recall_to_supported_use_ratio": (
                round(used_total / recalled_total, 4)
                if recalled_total else None
            ),
        },
        "channels": dict(state.get("channels") or {}),
        "top_entities": rows[:25],
    }


def run_memory_utility_shadow_maintenance(
    redis_client,
    *,
    reference_at: float | None = None,
    emit_pulse: bool = True,
    min_days: int | None = None,
    min_responded_turns: int | None = None,
    max_days: int | None = None,
) -> dict:
    """Aggiorna rapporto e promemoria durante la manutenzione giornaliera."""
    now_ts = time.time() if reference_at is None else float(reference_at)
    namespace_migration = migrate_legacy_utility_shadow_keys(redis_client)
    state = _load_json_key(redis_client, UTILITY_STATE_KEY) or _new_state(now_ts)
    if state.get("schema_version") != UTILITY_SCHEMA_VERSION:
        raise ValueError("memory utility shadow state schema incompatibile")

    cursor = str(state.get("last_processed_stream_id") or "0-0")
    rows = redis_client.xrange(
        COGNITIVE_STREAM,
        min=f"({cursor}",
        max="+",
    )
    aggregate_lineage_events(state, rows)
    sync_result = sync_supported_use_metadata(redis_client, state)
    state["updated_at"] = now_ts
    redis_client.set(
        UTILITY_STATE_KEY,
        json.dumps(state, ensure_ascii=False, sort_keys=True),
    )

    report = build_memory_utility_report(
        state,
        reference_at=now_ts,
        min_days=int(
            getattr(config, "MEMORY_UTILITY_REVIEW_MIN_DAYS", 14)
            if min_days is None else min_days
        ),
        min_responded_turns=int(
            getattr(config, "MEMORY_UTILITY_REVIEW_MIN_RESPONDED_TURNS", 100)
            if min_responded_turns is None else min_responded_turns
        ),
        max_days=int(
            getattr(config, "MEMORY_UTILITY_REVIEW_MAX_DAYS", 30)
            if max_days is None else max_days
        ),
    )
    report["metadata_sync"] = sync_result
    report["namespace_migration"] = namespace_migration
    attention_policy = str(getattr(config, "MEMORY_ATTENTION_POLICY", ""))
    if attention_policy == "selective_reuse_rate_v1":
        report["review_resolution"] = {
            "status": "completed",
            "decided_at": "2026-08-17",
            "decision": attention_policy,
            "automatic_policy_change": False,
        }
    redis_client.set(
        UTILITY_REPORT_KEY,
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )

    if not report["review_due"]:
        logger.info(
            "Utilità memoria shadow: raccolta {:.1f}/{} giorni, {} / {} "
            "risposte osservate",
            report["observation_age_days"],
            report["thresholds"]["min_days"],
            report["totals"].get("turns_responded", 0),
            report["thresholds"]["min_responded_turns"],
        )
        return report

    if attention_policy == "selective_reuse_rate_v1":
        previous = _load_json_key(redis_client, UTILITY_REVIEW_PENDING_KEY)
        newly_completed = not (
            previous.get("status") == "review_completed"
            and previous.get("decision") == attention_policy
        )
        completed = {
            "schema_version": UTILITY_SCHEMA_VERSION,
            "status": "review_completed",
            "decision": attention_policy,
            "decided_at": "2026-08-17",
            "first_due_at": previous.get("first_due_at"),
            "last_checked_at": now_ts,
            "observation_age_days": report["observation_age_days"],
            "turns_responded": report["totals"].get("turns_responded", 0),
            "entities_observed": report["totals"].get("entities_observed", 0),
            "report_key": UTILITY_REPORT_KEY,
            "automatic_review_tuning": False,
        }
        redis_client.set(
            UTILITY_REVIEW_PENDING_KEY,
            json.dumps(completed, ensure_ascii=False, sort_keys=True),
        )
        if newly_completed:
            logger.info(
                "Utilità memoria shadow: revisione chiusa manualmente — "
                "policy={}, {} risposte, {} entità",
                attention_policy,
                report["totals"].get("turns_responded", 0),
                report["totals"].get("entities_observed", 0),
            )
        return report

    fingerprint = hashlib.sha256(
        (
            f"{report['observation_started_at']}:"
            f"{report['experiment_version']}:"
            f"{report['review_reason']}"
        ).encode("utf-8")
    ).hexdigest()
    previous = get_memory_utility_review_pending(redis_client)
    pending = {
        "schema_version": UTILITY_SCHEMA_VERSION,
        "status": "review_pending",
        "fingerprint": fingerprint,
        "first_due_at": previous.get("first_due_at") or now_ts,
        "last_checked_at": now_ts,
        "reason": report["review_reason"],
        "observation_age_days": report["observation_age_days"],
        "turns_responded": report["totals"].get("turns_responded", 0),
        "entities_observed": report["totals"].get("entities_observed", 0),
        "report_key": UTILITY_REPORT_KEY,
        "automatic_review_tuning": False,
    }
    redis_client.set(
        UTILITY_REVIEW_PENDING_KEY,
        json.dumps(pending, ensure_ascii=False, sort_keys=True),
    )
    logger.warning(
        "Utilità memoria shadow: REVISIONE MATURA — {:.1f} giorni, "
        "{} risposte, {} entità; nessun auto-tuning eseguito",
        report["observation_age_days"],
        report["totals"].get("turns_responded", 0),
        report["totals"].get("entities_observed", 0),
    )
    if emit_pulse and previous.get("fingerprint") != fingerprint:
        from core.pulse import pulse_emit

        pulse_emit(
            redis_client,
            "memory",
            "intero",
            "utility_shadow_review_due",
            payload=pending,
            salience=0.7,
            producer="memory_utility_shadow",
            logical_event_id=f"memory-utility-review:{fingerprint}",
            experiment_version=LINEAGE_EXPERIMENT,
        )
    return report


def explain_insight_promotion(
    insight: dict,
    *,
    latest_trace: dict | None = None,
) -> dict:
    """Spiega lo stato di promozione senza rivalutare o mutare l'insight."""
    trace = latest_trace or {}
    status = str(insight.get("status") or "unknown")
    external = insight.get("external_reaction") or {}
    bridge = str(insight.get("bridge_validity") or "")
    fidelity = insight.get("premise_fidelity")
    convergences = int(
        trace.get("convergences")
        or insight.get("convergence_count")
        or 1
    )
    minimum = int(getattr(config, "DREAM_INSIGHT_MIN_CONVERGENCES", 3))

    if status == "promoted":
        decision, reason = "PROMOTED", (
            "externally_confirmed"
            if external.get("verdict") == "CONFERMA"
            else "quality_and_convergence_gates_passed"
        )
    elif status == "hypothesis":
        decision, reason = "HYPOTHESIS", "bridge_requires_new_premise"
    elif insight.get("promotion_blocked_reason"):
        decision, reason = "BLOCKED", str(insight["promotion_blocked_reason"])
    elif trace.get("outcome"):
        decision, reason = "NOT_PROMOTED", str(trace["outcome"])
    elif convergences < minimum:
        decision, reason = "NOT_PROMOTED", "convergence_below_threshold"
    else:
        decision, reason = "DEFERRED", "current_gate_state_incomplete"

    return {
        "schema_version": 1,
        "mode": "read_only_explanation",
        "id": str(insight.get("id") or ""),
        "status": status,
        "decision": decision,
        "decisive_reason": reason,
        "signals": {
            "convergences": convergences,
            "minimum_convergences": minimum,
            "premise_fidelity": fidelity,
            "bridge_validity": bridge or None,
            "bridge_validity_score": insight.get("bridge_validity_score"),
            "external_verdict": external.get("verdict"),
            "requires_verification": bool(insight.get("requires_verification")),
            "epistemic_status": insight.get("epistemic_status"),
            "recalled_count": int(insight.get("recalled_count") or 0),
            "last_trace_outcome": trace.get("outcome"),
            "judge_confirmed": int(trace.get("n_judge_confirmed") or 0),
            "judge_deferred": int(trace.get("n_judge_deferred") or 0),
        },
        "sources": {
            "direct": list(insight.get("source_memory_ids") or []),
            "convergent": list(
                insight.get("convergence_source_memory_ids") or []
            ),
        },
        "automatic_policy_change": False,
    }
