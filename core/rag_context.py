"""
RAG context condiviso tra voce, mobile e Silent Chat.

Costruisce il contesto base da Redis senza conoscere domini specifici:
- chat/mobile: mantiene un piccolo contesto ambientale recente;
- search: privilegia evidenza cercata dalla query, preservando la recency solo per
  richieste sul contesto immediato ("di cosa parlavamo prima", "poco fa"...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

import config
from core.conversation_turns import TURN_RENDER_VERSION
from core.memory_risk import is_document_summary, memory_verification_suffix
from core.temporal_context import memory_time_label, temporal_prompt_contract, turn_time_label
from utils.date_utils import now


_RECENT_CONTEXT_RE = re.compile(
    r'\b(?:stavamo\s+parlando|parlavamo|dicevamo|detto\s+prima|poco\s+fa|'
    r'appena|prima\?|di\s+cosa\s+(?:stavamo\s+)?parlavamo|quella\s+cosa|'
    r'quello\s+che\s+(?:dicevo|ti\s+ho\s+detto)|riprendiamo|corrett\w*|'
    r'correzion\w*)\b',
    re.IGNORECASE,
)

_SEARCH_CUE_RE = re.compile(
    r'\b(?:ricordi|ricordati|memoria|memorie|hai\s+in\s+memoria|cosa\s+sai|'
    r'che\s+cosa\s+sai|cosa\s+conosci|quali?\s+nomi|quali?\s+ruoli?|'
    r'chi\s+(?:lavora|fa|si\s+occupa|sono|è)|di\s+cosa\s+(?:stavamo\s+)?parlavamo|'
    r'fai\s+(?:degli?\s+)?esempi\s+pratici)\b',
    re.IGNORECASE,
)

_STOP_WORDS = {
    "come", "cosa", "quando", "dove", "perché", "però", "anche", "solo",
    "tutto", "tutti", "tutta", "tutte", "questo", "questa", "questi", "queste",
    "quello", "quella", "quelli", "quelle", "volevo", "volendo", "posso", "devo",
    "sono", "essere", "avere", "fare", "dire", "stare", "della", "delle", "degli",
    "dello", "nella", "nelle", "negli", "nello", "oppure", "invece",
    "ancora", "adesso", "quindi", "allora", "certo", "magari", "tanto", "molto",
    "poco", "bene", "male", "così", "tipo", "parte", "fatto", "altra",
    "altro", "altri", "altre", "prima", "dopo", "sempre", "spesso", "quasi",
    "circa", "forse", "senza", "verso", "dentro", "fuori", "sopra", "sotto",
    "mentre", "comunque", "ricordi", "saper", "sapere",
}


@dataclass
class RagContext:
    text: str
    ids: list[str]
    mode: str
    # Provenance osservazionale dei soli nodi realmente inseriti nel prompt.
    # `ids` resta invariato per compatibilità con l'Audit di Coerenza legacy:
    # include soltanto il blocco results, mentre nodes distingue anche reflection,
    # impegni e insight senza cambiare retrieval o risposta.
    nodes: list[dict] = field(default_factory=list)
    turn_ids: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def selective_thinking_decision(rag: RagContext | None) -> dict:
    """Decide localmente se il turno merita il thinking della chat.

    Il segnale è strutturale e fail-closed: non interpreta il testo e non basta
    che il dual-channel abbia trovato una nota. Serve almeno un turno originale
    promosso dal gate selettivo. Il chiamante passa poi questa decisione alla
    singola invocazione LLM, senza side-channel condivisi.
    """
    decision = {
        "enabled": False,
        "reason": "disabled",
        "promoted_turn_ids": [],
    }
    if not getattr(config, "RAG_DUAL_SELECTIVE_THINKING", False):
        return decision
    if rag is None:
        decision["reason"] = "rag_unavailable"
        return decision

    diagnostics = rag.diagnostics or {}
    if diagnostics.get("mode") != "dual_channel":
        decision["reason"] = "not_dual_channel"
        return decision
    gate = diagnostics.get("selective_gate") or {}
    promoted = list(dict.fromkeys(
        str(turn_id)
        for turn_id in (gate.get("promoted_turn_ids") or [])
        if turn_id
    ))
    decision["promoted_turn_ids"] = promoted
    if (
        diagnostics.get("presentation_applied") != "selective_prepend"
        or not promoted
    ):
        decision["reason"] = "no_promoted_verbatim"
        return decision

    decision["enabled"] = True
    decision["reason"] = "promoted_verbatim"
    return decision


def insight_requires_external_validation(insight: dict) -> bool:
    """La convergenza interna fa emergere un insight, ma non lo valida nel mondo."""
    external = insight.get("external_reaction") or {}
    if external.get("verdict") != "CONFERMA":
        return True
    return (
        bool(insight.get("requires_verification"))
        or insight.get("verification_status") in {
            "hypothesis_to_test",
            "partially_refuted_by_user",
            "internally_emergent",
            "internally_convergent",
        }
    )


def infer_context_mode(text: str, default: str = "chat") -> str:
    """Inferenza cheap per canali senza intent router, come Silent Chat."""
    from utils.temporal import detect_recent_memory_intent

    recent_memory = detect_recent_memory_intent(
        text or "",
        now(),
        window_days=getattr(config, "RAG_RECENT_MEMORY_WINDOW_DAYS", 14),
    )
    if (
        _RECENT_CONTEXT_RE.search(text or "")
        or _SEARCH_CUE_RE.search(text or "")
        or recent_memory is not None
    ):
        return "search"
    return default


def _format_recent_history(
    recent_history: list[dict] | None,
    *,
    limit: int = 8,
    reference_at: float | None = None,
) -> list[str]:
    from core.memory_scope import current_scope, scope_of
    scoped_history = [
        message for message in (recent_history or [])
        if scope_of(message) == current_scope()
    ]
    rows = []
    previous_at = None
    for msg in scoped_history[-limit:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        observed_at = msg.get("observed_at")
        try:
            observed_at = float(observed_at) if observed_at is not None else None
        except (TypeError, ValueError):
            observed_at = None
        if (
            previous_at is not None
            and observed_at is not None
            and observed_at - previous_at > getattr(config, "TEMPORAL_EPISODE_GAP_SECONDS", 1800)
        ):
            rows.append("- [nuovo segmento dopo una pausa]")
        who = (
            config.OWNER_DISPLAY_NAME if role == "user"
            else config.ASSISTANT_DISPLAY_NAME if role == "assistant"
            else str(role or "?")
        )
        rows.append(
            f"- [{turn_time_label(observed_at, reference_at)}] {who}: {content[:500]}"
        )
        if observed_at is not None:
            previous_at = observed_at
    return rows


def build_rag_context(
    text: str,
    memory,
    *,
    mode: str = "chat",
    recent_history: list[dict] | None = None,
    excluded_sources: set[str] | None = None,
    touch: bool = True,
    enable_recent_memory_intent: bool = True,
) -> RagContext:
    """Costruisce il contesto RAG base e ritorna anche gli ID iniettati.

    ``enable_recent_memory_intent=False`` esiste soltanto per riprodurre
    byte-per-byte artefatti benchmark creati prima della policy del 29/07/2026.
    I dispatcher runtime conservano sempre il default attivo.
    """
    from utils.temporal import detect_recent_memory_intent, extract_temporal_range
    from core.temporal_recall import prioritize_recent_window, prioritize_window

    source_filter = config.DEMO_CONTEXT_SOURCES if config.DEMO_MODE else None
    source_exclude = sorted(excluded_sources or ())
    search_mode = mode == "search"
    recent_context_query = bool(_RECENT_CONTEXT_RE.search(text or ""))
    reference_dt = now()
    reference_at = reference_dt.timestamp()
    recent_memory_intent = (
        detect_recent_memory_intent(
            text or "",
            reference_dt,
            window_days=getattr(config, "RAG_RECENT_MEMORY_WINDOW_DAYS", 14),
        )
        if enable_recent_memory_intent
        else None
    )
    history_lines = (
        _format_recent_history(recent_history, reference_at=reference_at)
        if recent_context_query else []
    )
    history_resolves_query = recent_context_query and bool(history_lines)

    reflection_lines: list[str] = []
    reflection_docs: list[dict] = []
    if not history_resolves_query and not config.DEMO_MODE and (not search_mode or recent_context_query):
        for r in memory.get_recent_reflections(limit=2, touch=False):
            assistant_label = config.ASSISTANT_DISPLAY_NAME.upper()
            reflection_lines.append(f"- [INTERPRETAZIONE DI {assistant_label}] {r['content']}")
            reflection_docs.append(r)

    if history_resolves_query:
        results = []
    elif not search_mode or recent_context_query:
        recent_kwargs = {
            "limit": config.RAG_RECENCY_LIMIT,
            "source_filter": source_filter,
            "touch": False,
        }
        if source_exclude:
            recent_kwargs["source_exclude"] = source_exclude
        results = memory.get_recent_memories(**recent_kwargs)
    else:
        results = []
    seen_ids = {r.get("id") for r in results}

    explicit_time_range = extract_temporal_range(text, reference_dt)
    time_range = (
        (recent_memory_intent.start, recent_memory_intent.end)
        if recent_memory_intent is not None
        else explicit_time_range
    )
    recent_window_hits = 0
    if time_range:
        ts_start, ts_end = time_range
        # Per la recenza generica tocchiamo soltanto i nodi che superano il
        # gate sull'evento, non i candidati vecchi entrati per asserted/created.
        temporal_kwargs = {
            "limit": 200,
            "touch": False if recent_memory_intent is not None else touch,
        }
        if source_exclude:
            temporal_kwargs["source_exclude"] = source_exclude
        window = memory.search_memories_by_timerange(
            ts_start, ts_end, **temporal_kwargs
        )
        prioritized = (
            prioritize_recent_window(window, ts_start, ts_end)
            if recent_memory_intent is not None
            else prioritize_window(window)
        )
        recent_window_hits = len(prioritized)
        if recent_memory_intent is not None and touch and hasattr(
            memory, "_touch_memories"
        ):
            memory._touch_memories(prioritized[: config.RAG_MEM_CAP_TEMPORAL])
        merged = []
        merged_seen = set()
        for r in prioritized + ([] if recent_memory_intent is not None else results):
            rid = r.get("id")
            if rid not in merged_seen:
                merged.append(r)
                merged_seen.add(rid)
        results = merged
        seen_ids = merged_seen
        reflection_lines = []
        reflection_docs = []

    words = re.findall(
        r'\b[a-zA-ZàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]{4,}\b',
        text or "",
    )
    keywords = list(dict.fromkeys(w for w in words if w.lower() not in _STOP_WORDS))
    if (
        keywords
        and not history_resolves_query
        and recent_memory_intent is None
    ):
        search_kwargs = {
            "limit": config.RAG_SEMANTIC_LIMIT,
            "source_filter": source_filter,
            "touch": touch,
        }
        if source_exclude:
            search_kwargs["source_exclude"] = source_exclude
        extra_memories = memory.search_memories(text, **search_kwargs)
        kw_query = " | ".join(keywords[:8])
        from core.memory_scope import PERSONAL_SCOPE, current_scope
        extra_notes = (
            memory.search_notes(kw_query, limit=2)
            if current_scope() == PERSONAL_SCOPE
            else []
        )
        for r in extra_memories + extra_notes:
            rid = r.get("id")
            if rid not in seen_ids:
                results.append(r)
                seen_ids.add(rid)

    # Impegni aperti (TUTTI i pending, anche futuri): stato reale, iniettato in modo
    # deterministico — il retrieval semantico non copre le domande temporali
    # ("cosa è scaduto?", "che appuntamenti ho?") e perde il nodo-impegno nella
    # competizione coi vicini (13/07: "scadenza Poseidon" negata con l'impegno vivo
    # a domani). I pending sono pochi per natura → il blocco resta compatto e compare
    # solo quando l'agenda non è vuota; niente diluizione degli slot.
    commitment_lines: list[str] = []
    commitment_docs: list[dict] = []
    commitment_ids: set = set()
    try:
        for t in memory.get_pending_todos()[:5]:
            tid = t.get("id")
            if tid in commitment_ids:
                continue
            commitment_ids.add(tid)
            commitment_docs.append(t)
            due = t.get("_due_at")
            state = "senza scadenza"
            if due:
                delta_days = (due.date() - now().date()).days
                if delta_days < 0:
                    state = f"SCADUTO da {-delta_days} {'giorno' if delta_days == -1 else 'giorni'}"
                elif delta_days == 0:
                    state = f"scade oggi alle {due.strftime('%H:%M')}"
                elif delta_days == 1:
                    state = f"scade domani alle {due.strftime('%H:%M')}"
                else:
                    state = f"scade il {due.strftime('%d/%m alle %H:%M')}"
            commitment_lines.append(f"- [{state}] {t['content']}")
    except Exception as e:
        logger.debug(f"RAG impegni aperti non disponibili: {e}")

    insight_lines: list[str] = []
    insight_docs: list[dict] = []
    if (
        keywords
        and not history_resolves_query
        and recent_memory_intent is None
    ):
        for ins in memory.search_insights(text, limit=2):
            dom_a = ins.get("domain_a", "?")
            dom_b = ins.get("domain_b", "?")
            ext = ins.get("external_reaction") or {}
            tentative = insight_requires_external_validation(ins)
            marker = (
                "[CONNESSIONE EMERSA INTERNAMENTE — DA VERIFICARE] "
                if tentative else
                "[CONNESSIONE CONFERMATA ESTERNAMENTE] "
            )
            line = f"- {marker}[{dom_a} ↔ {dom_b}] {ins['content']}"
            if ext.get("verdict") == "PARZIALE":
                patch = ext.get("reaction_patch") or ins.get("reaction_patch") or {}
                patch_parts = []
                for field, label in (
                    ("confirmed_claims", "confermato"),
                    ("refuted_claims", "smentito"),
                    ("replacement_claims", "sostituzione affermata"),
                ):
                    claims = [
                        str(item.get("claim") or "").strip()
                        for item in patch.get(field, [])
                        if isinstance(item, dict) and item.get("claim")
                    ]
                    if claims:
                        patch_parts.append(f"{label}: {'; '.join(claims)}")
                correction = " | ".join(patch_parts)
                if not correction:
                    correction = str(ext.get("reaction") or "").strip()
                if correction:
                    line += (
                        f"\n  [CORREZIONE PARZIALE DI {config.OWNER_DISPLAY_NAME.upper()}] "
                        f"{correction[:1200]}"
                    )
            insight_lines.append(line)
            insight_docs.append(ins)

    sections: list[str] = []
    if recent_memory_intent is not None:
        from utils.date_utils import from_timestamp

        start_dt = from_timestamp(recent_memory_intent.start)
        end_dt = from_timestamp(recent_memory_intent.end)
        start_label = (
            start_dt.strftime("%d/%m/%Y %H:%M") if start_dt else "inizio ignoto"
        )
        end_label = (
            end_dt.strftime("%d/%m/%Y %H:%M") if end_dt else "fine ignota"
        )
        sections.append(
            "Vincolo temporale richiesto — MEMORIA RECENTE:\n"
            f"- finestra locale: ultimi {recent_memory_intent.window_days} giorni "
            f"({start_label} → {end_label})\n"
            "- chiama «recente» soltanto ciò che è avvenuto dentro questa finestra; "
            "la data di creazione di una sintesi non rende recente un evento vecchio.\n"
            "- se il blocco dei ricordi recenti è vuoto, dichiaralo e proponi di "
            "allargare la finestra: non sostituirlo con ricordi più vecchi."
        )
    if recent_context_query:
        if history_lines:
            sections.append(
                "Conversazione recente immediata:\n"
                + "\n".join(history_lines)
                + "\nGli orari sono metadati interni: usali per la cronologia senza recitarli. "
                "Usa questo blocco per risolvere pronomi, correzioni e riferimenti tipo "
                "'prima', 'quella cosa', 'appena'."
            )

    mem_cap = config.RAG_MEM_CAP_TEMPORAL if time_range else config.RAG_MEM_CAP
    if reflection_lines:
        sections.append(
            f"Interpretazioni recenti di {config.ASSISTANT_DISPLAY_NAME} "
            f"(sintesi o ipotesi interne, non fatti attribuiti a "
            f"{config.OWNER_DISPLAY_NAME}):\n" + "\n".join(reflection_lines)
        )
    if commitment_lines:
        sections.append(
            "Impegni aperti (stato reale in agenda, non ricordi):\n"
            + "\n".join(commitment_lines)
        )
    if results:
        mem_lines = []
        for r in results[:mem_cap]:
            if r.get("id") in commitment_ids:
                continue  # già nel blocco impegni, non duplicare
            age = memory_time_label(r, reference_at=reference_at)
            kind = r.get("memory_kind") or ""
            source = r.get("source") or ""
            if kind == "conversation_anchor":
                kind_label = "FILO CONVERSAZIONALE | "
            elif kind == "conversation_episode":
                kind_label = "EPISODIO CONVERSAZIONALE | "
            elif kind == "reaction_lesson" or source == "reaction":
                kind_label = "LEZIONE DI EURI DA FEEDBACK | "
            elif r.get("passive_support") == "tacit_acceptance":
                kind_label = "VECCHIA IPOTESI DI EURI NON CONFERMATA | "
            elif is_document_summary(r):
                kind_label = "SINTESI DOCUMENTO | "
            else:
                kind_label = ""
            label = (
                f"[{kind_label}{r.get('domain', 'generale')} | {age}]"
                if age else f"[{kind_label}{r.get('domain', 'generale')}]"
            )
            suffix = memory_verification_suffix(r)
            anchor_note = (
                " [ancora episodica: indica un discorso aperto, non prova il fatto raccontato]"
                if kind == "conversation_anchor" else ""
            )
            episode_note = (
                f" [sintesi del dialogo: preserva il filo ma non usare le parole di "
                f"{config.ASSISTANT_DISPLAY_NAME} come fatti di {config.OWNER_DISPLAY_NAME}]"
                if kind == "conversation_episode" else ""
            )
            reaction_note = (
                f" [interpretazione operativa di {config.ASSISTANT_DISPLAY_NAME} derivata "
                f"da un feedback: non attribuire questa formulazione a "
                f"{config.OWNER_DISPLAY_NAME}]"
                if kind == "reaction_lesson" or source == "reaction" else ""
            )
            tacit_note = (
                " [derivata storicamente dalla mancata contestazione: non vale come "
                f"affermazione di {config.OWNER_DISPLAY_NAME}]"
                if r.get("passive_support") == "tacit_acceptance" else ""
            )
            mem_lines.append(
                f"- {label} {r['content']}{anchor_note}{episode_note}{reaction_note}"
                f"{tacit_note}{suffix}"
            )
        if mem_lines:
            memory_heading = (
                "Ricordi avvenuti nella finestra recente:"
                if recent_memory_intent is not None
                else "Ricordi/note rilevanti:"
            )
            sections.append(memory_heading + "\n" + "\n".join(mem_lines))
    elif recent_memory_intent is not None:
        sections.append(
            "Ricordi avvenuti nella finestra recente:\n"
            "- Nessuna memoria disponibile nella finestra richiesta."
        )
    if insight_lines:
        sections.append(
            "Connessioni trasversali emerse (la convergenza interna non equivale a verità):\n"
            + "\n".join(insight_lines)
        )

    if results and not recent_history:
        sections.insert(0, "Regola cronologica interna:\n" + temporal_prompt_contract())

    ids = [r.get("id") for r in results[:mem_cap] if r.get("id")]
    nodes: list[dict] = []
    seen_nodes: set[tuple[str, str]] = set()

    def _append_node(doc: dict, *, kind: str, path: str, position: int) -> None:
        node_id = str(doc.get("id") or "").removeprefix("euri:memory:").removeprefix(
            "euri:insight:"
        )
        content = str(doc.get("content") or "").strip()
        key = (kind, node_id)
        if not node_id or not content or key in seen_nodes:
            return
        seen_nodes.add(key)
        raw_score = doc.get("score")
        if raw_score is None:
            raw_score = doc.get("_vec_score")
        try:
            retrieval_score = (
                round(float(raw_score), 6) if raw_score is not None else None
            )
        except (TypeError, ValueError):
            retrieval_score = None
        nodes.append({
            "kind": kind,
            "id": node_id,
            "content": content,
            "position": position,
            "retrieval_path": path,
            # Distanza cosine RediSearch: più bassa = semanticamente più vicina.
            # Può essere None per risultati keyword/recency.
            "retrieval_score": retrieval_score,
            "source": str(doc.get("source") or ""),
            "domain": str(
                doc.get("domain")
                or doc.get("domain_a")
                or ""
            ),
        })

    for position, doc in enumerate(reflection_docs, 1):
        _append_node(doc, kind="memory", path="recent_reflection", position=position)
    for position, doc in enumerate(commitment_docs, 1):
        _append_node(doc, kind="memory", path="open_commitment", position=position)
    visible_results = [
        doc for doc in results[:mem_cap] if doc.get("id") not in commitment_ids
    ]
    for position, doc in enumerate(visible_results, 1):
        _append_node(doc, kind="memory", path="base_rag", position=position)
    for position, doc in enumerate(insight_docs, 1):
        _append_node(doc, kind="insight", path="insight_rag", position=position)
    if results:
        node_tags = [
            f"{r.get('source','?')}:{r.get('domain','?')}({r.get('id','')[:8]})"
            for r in results[:mem_cap]
        ]
        logger.info(f"RAG ctx [{len(results)} nodi]: {' | '.join(node_tags)}")

    history_turn_ids = []
    if history_resolves_query:
        history_turn_ids = [
            str(message.get("turn_ref"))
            for message in (recent_history or [])[-8:]
            if message.get("turn_ref") and str(message.get("content") or "").strip()
        ]
    temporal_diagnostics = None
    if recent_memory_intent is not None:
        temporal_diagnostics = recent_memory_intent.to_record()
        temporal_diagnostics.update(
            {
                "candidate_hits": len(window),
                "eligible_hits": recent_window_hits,
                "visible_hits": len(visible_results),
                "fallback": "none",
            }
        )
        logger.info(
            "RAG memoria recente: finestra={}g candidati={} eleggibili={} "
            "visibili={} fallback=none",
            recent_memory_intent.window_days,
            len(window),
            recent_window_hits,
            len(visible_results),
        )
    return RagContext(
        text="\n\n".join(sections),
        ids=ids,
        mode=mode,
        nodes=nodes,
        turn_ids=history_turn_ids,
        diagnostics={
            "mode": "base",
            "temporal_query": temporal_diagnostics,
        },
    )


def _load_memory_document(memory, memory_id: str) -> dict:
    key = (
        memory_id
        if str(memory_id).startswith("euri:memory:")
        else f"euri:memory:{memory_id}"
    )
    try:
        raw = memory.r.json().get(key, "$")
    except Exception:
        return {}
    if not raw:
        return {}
    return raw[0] if isinstance(raw, list) else raw


def _passive_source_refs(doc: dict) -> list[str]:
    temporal = doc.get("temporal_context") or {}
    refs = [
        str(ref) for ref in temporal.get("source_turn_refs") or [] if ref
    ]
    if refs:
        return list(dict.fromkeys(refs))

    # Compatibilità con le note create prima dei riferimenti stabili. Possono
    # essere idratate soltanto se il relativo turno esiste già nell'archivio.
    conversation_id = str(temporal.get("conversation_id") or "").strip()
    if not conversation_id:
        return []
    refs = []
    for turn_id in temporal.get("source_turn_ids") or []:
        try:
            refs.append(f"{conversation_id}:{int(turn_id)}")
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(refs))


def build_dual_channel_context(
    text: str,
    memory,
    turn_store,
    *,
    mode: str = "chat",
    recent_history: list[dict] | None = None,
    touch: bool = True,
    presentation: str = "append",
    observe_selective: bool = False,
) -> RagContext:
    """Base senza passive + note passive come locator verso turni originali."""
    from core.dual_channel import FROZEN_POLICY, POLICY_ID, compose_dual_channel
    from core.dual_channel_gate import (
        SelectiveThresholds,
        compose_selective_presentation,
        evaluate_selective_gate,
    )
    from core.memory_scope import current_scope, scope_of
    expected_scope = current_scope()

    if presentation not in {"append", "selective"}:
        raise ValueError(f"presentazione dual-channel non valida: {presentation}")

    base = build_rag_context(
        text,
        memory,
        mode=mode,
        recent_history=recent_history,
        excluded_sources={"passive"},
        touch=touch,
    )
    locator_view = build_rag_context(
        text,
        memory,
        mode=mode,
        recent_history=recent_history,
        touch=False,
    )
    passive_nodes = [
        node
        for node in sorted(locator_view.nodes, key=lambda item: item.get("position", 0))
        if node.get("kind") == "memory" and node.get("source") == "passive"
    ][: FROZEN_POLICY["Q_notes"]]

    locator_notes = []
    for node in passive_nodes:
        locator_doc = _load_memory_document(memory, node["id"])
        locator_notes.append(
            _passive_source_refs(locator_doc)
            if scope_of(locator_doc) == expected_scope
            else []
        )

    def _render_scoped_turn(turn_ref: str) -> str:
        turn = turn_store.get(turn_ref)
        if not turn or turn.memory_scope != expected_scope:
            return ""
        return turn.render()

    composition = compose_dual_channel(
        base_context_text=base.text,
        base_slots=len(base.nodes),
        base_turn_ids=base.turn_ids,
        locator_notes=locator_notes,
        render_turn=_render_scoped_turn,
    )

    added_nodes = []
    addition_gate_inputs = []
    addition_by_turn = {
        item["turn_id"]: item for item in composition.additions
    }
    for position, turn_ref in enumerate(composition.added_turn_ids(), 1):
        turn = turn_store.get(turn_ref)
        if not turn or turn.memory_scope != expected_scope:
            continue
        addition = addition_by_turn[turn_ref]
        addition_gate_inputs.append(
            {
                "turn_id": turn_ref,
                "content": turn.content,
                "from_note_index": addition["from_note_index"],
            }
        )
        added_nodes.append(
            {
                "kind": "turn",
                "id": turn_ref,
                "content": turn.content,
                "position": position,
                "retrieval_path": "passive_locator_hydrated",
                "source": "conversation_verbatim",
                "domain": "",
            }
        )

    gate = {
        "policy_id": "dual-channel-selective-prepend-v0",
        "presentation": "append",
        "candidates": [],
        "promoted_turn_ids": [],
        "fallback_reason": "not_evaluated",
    }
    final_text = composition.final_context_text
    prompt_regions = {
        item["turn_id"]: "append" for item in composition.additions
    }
    if observe_selective or presentation == "selective":
        thresholds = SelectiveThresholds(
            min_query_source_similarity=getattr(
                config, "RAG_DUAL_SELECTIVE_MIN_QUERY_SOURCE", 0.92
            ),
            min_relevance_margin=getattr(
                config, "RAG_DUAL_SELECTIVE_MIN_MARGIN", -0.01
            ),
            max_source_base_similarity=getattr(
                config, "RAG_DUAL_SELECTIVE_MAX_REDUNDANCY", 0.985
            ),
        )
        gate = evaluate_selective_gate(
            query=text,
            base_nodes=base.nodes,
            additions=addition_gate_inputs,
            locator_nodes=passive_nodes,
            embedder=getattr(memory, "_embedder", None),
            thresholds=thresholds,
        )
        if presentation == "selective":
            final_text, prompt_regions = compose_selective_presentation(
                composition,
                gate["promoted_turn_ids"],
            )

    gate_by_turn = {
        candidate["turn_id"]: candidate for candidate in gate.get("candidates", [])
    }
    for node in added_nodes:
        candidate = gate_by_turn.get(node["id"], {})
        node.update(
            {
                "prompt_region": prompt_regions.get(node["id"], "append"),
                "selective_gate_decision": candidate.get("decision", "append"),
                "query_source_similarity": candidate.get(
                    "query_source_similarity"
                ),
                "relevance_margin": candidate.get("relevance_margin"),
                "source_base_max_similarity": candidate.get(
                    "source_base_max_similarity"
                ),
                "locator_memory_id": candidate.get("locator_memory_id", ""),
            }
        )

    diagnostics = composition.to_record()
    diagnostics.update(
        {
            "mode": "dual_channel",
            "temporal_query": base.diagnostics.get("temporal_query"),
            "verbatim_render_version": TURN_RENDER_VERSION,
            "presentation_requested": presentation,
            "presentation_applied": (
                gate.get("presentation", "append")
                if presentation == "selective" else "append"
            ),
            "locator_memory_ids": [node["id"] for node in passive_nodes],
            "locator_notes_considered": len(passive_nodes),
            "selective_gate": gate,
        }
    )
    logger.info(
        "RAG dual-channel [{}]: base={} locator={} aggiunti={} presentazione={} "
        "promossi={} non_disponibili={} budget_scartati={}",
        POLICY_ID,
        len(base.nodes),
        len(passive_nodes),
        len(composition.additions),
        diagnostics["presentation_applied"],
        len(gate.get("promoted_turn_ids") or []),
        sum(
            item.get("decision") == "source_unavailable"
            for item in composition.candidates_considered
        ),
        composition.discarded_budget,
    )
    for candidate in gate.get("candidates", []):
        logger.info(
            "RAG dual gate: turn={} decision={} q_src={} base_best={} "
            "margin={} redundancy={} locator_dist={} reasons={}",
            candidate.get("turn_id"),
            candidate.get("decision"),
            candidate.get("query_source_similarity"),
            candidate.get("query_base_best_similarity"),
            candidate.get("relevance_margin"),
            candidate.get("source_base_max_similarity"),
            candidate.get("locator_distance"),
            ",".join(candidate.get("reasons") or []) or "high_confidence",
        )
    return RagContext(
        text=final_text,
        ids=list(base.ids),
        mode=mode,
        nodes=list(base.nodes) + added_nodes,
        turn_ids=list(composition.final_turn_ids),
        diagnostics=diagnostics,
    )


def build_runtime_rag_context(
    text: str,
    memory,
    turn_store,
    *,
    mode: str = "chat",
    recent_history: list[dict] | None = None,
    dual_mode: str | None = None,
) -> RagContext:
    """Unico dispatcher RAG per voce, mobile e Silent Chat."""
    selected = (
        dual_mode
        if dual_mode is not None
        else getattr(config, "RAG_DUAL_CHANNEL_MODE", "off")
    )
    if selected in {"on", "selective"}:
        try:
            return build_dual_channel_context(
                text,
                memory,
                turn_store,
                mode=mode,
                recent_history=recent_history,
                presentation=(
                    "selective" if selected == "selective" else "append"
                ),
                observe_selective=selected == "selective",
            )
        except Exception as exc:
            logger.error(
                "RAG dual-channel fallito: fallback alla sola base protetta ({})",
                exc,
            )
            return build_rag_context(
                text,
                memory,
                mode=mode,
                recent_history=recent_history,
                excluded_sources={"passive"},
            )

    rag = build_rag_context(
        text,
        memory,
        mode=mode,
        recent_history=recent_history,
    )
    if selected == "shadow":
        try:
            shadow = build_dual_channel_context(
                text,
                memory,
                turn_store,
                mode=mode,
                recent_history=recent_history,
                touch=False,
                presentation="selective",
                observe_selective=True,
            )
            gate = shadow.diagnostics.get("selective_gate") or {}
            logger.info(
                "RAG dual shadow: legacy_chars={} dual_chars={} "
                "aggiunti={} promossi={} presentazione={} base_sha={}",
                len(rag.text),
                len(shadow.text),
                len(shadow.diagnostics.get("added_turn_ids") or []),
                len(gate.get("promoted_turn_ids") or []),
                shadow.diagnostics.get("presentation_applied"),
                str(shadow.diagnostics.get("base_sha256") or "")[:12],
            )
        except Exception as exc:
            logger.warning(f"RAG dual shadow non disponibile ({exc})")
    return rag
