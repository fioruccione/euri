"""
RAG context condiviso tra voce, mobile e Silent Chat.

Costruisce il contesto base da Redis senza conoscere domini specifici:
- chat/mobile: mantiene un piccolo contesto ambientale recente;
- search: privilegia evidenza cercata dalla query, preservando la recency solo per
  richieste sul contesto immediato ("di cosa parlavamo prima", "poco fa"...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

import config
from core.memory_risk import memory_verification_suffix
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


def infer_context_mode(text: str, default: str = "chat") -> str:
    """Inferenza cheap per canali senza intent router, come Silent Chat."""
    if _RECENT_CONTEXT_RE.search(text or "") or _SEARCH_CUE_RE.search(text or ""):
        return "search"
    return default


def _format_recent_history(
    recent_history: list[dict] | None,
    *,
    limit: int = 8,
    reference_at: float | None = None,
) -> list[str]:
    rows = []
    previous_at = None
    for msg in (recent_history or [])[-limit:]:
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
        who = "Stefano" if role == "user" else "Euri" if role == "assistant" else str(role or "?")
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
) -> RagContext:
    """Costruisce il contesto RAG base e ritorna anche gli ID iniettati."""
    from utils.temporal import extract_temporal_range
    from core.temporal_recall import prioritize_window

    source_filter = config.DEMO_CONTEXT_SOURCES if config.DEMO_MODE else None
    search_mode = mode == "search"
    recent_context_query = bool(_RECENT_CONTEXT_RE.search(text or ""))
    reference_at = now().timestamp()
    history_lines = (
        _format_recent_history(recent_history, reference_at=reference_at)
        if recent_context_query else []
    )
    history_resolves_query = recent_context_query and bool(history_lines)

    reflection_lines: list[str] = []
    if not history_resolves_query and not config.DEMO_MODE and (not search_mode or recent_context_query):
        for r in memory.get_recent_reflections(limit=2, touch=False):
            reflection_lines.append(f"- [INTERPRETAZIONE DI EURI] {r['content']}")

    if history_resolves_query:
        results = []
    elif not search_mode or recent_context_query:
        results = memory.get_recent_memories(
            limit=config.RAG_RECENCY_LIMIT,
            source_filter=source_filter,
            touch=False,
        )
    else:
        results = []
    seen_ids = {r.get("id") for r in results}

    time_range = extract_temporal_range(text, now())
    if time_range:
        ts_start, ts_end = time_range
        window = memory.search_memories_by_timerange(ts_start, ts_end, limit=200)
        merged = []
        merged_seen = set()
        for r in prioritize_window(window) + results:
            rid = r.get("id")
            if rid not in merged_seen:
                merged.append(r)
                merged_seen.add(rid)
        results = merged
        seen_ids = merged_seen
        reflection_lines = []

    words = re.findall(
        r'\b[a-zA-ZàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]{4,}\b',
        text or "",
    )
    keywords = list(dict.fromkeys(w for w in words if w.lower() not in _STOP_WORDS))
    if keywords and not history_resolves_query:
        extra_memories = memory.search_memories(
            text, limit=config.RAG_SEMANTIC_LIMIT, source_filter=source_filter
        )
        kw_query = " | ".join(keywords[:8])
        extra_notes = memory.search_notes(kw_query, limit=2)
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
    commitment_ids: set = set()
    try:
        for t in memory.get_pending_todos()[:5]:
            tid = t.get("id")
            if tid in commitment_ids:
                continue
            commitment_ids.add(tid)
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
    if keywords and not history_resolves_query:
        for ins in memory.search_insights(text, limit=2):
            dom_a = ins.get("domain_a", "?")
            dom_b = ins.get("domain_b", "?")
            ext = ins.get("external_reaction") or {}
            tentative = (
                bool(ins.get("requires_verification"))
                or ins.get("verification_status") == "hypothesis_to_test"
                or ext.get("verdict") == "DA_VALUTARE"
            )
            marker = "[IPOTESI DA VERIFICARE] " if tentative else ""
            insight_lines.append(f"- {marker}[{dom_a} ↔ {dom_b}] {ins['content']}")

    sections: list[str] = []
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
            "Interpretazioni recenti di Euri (sintesi o ipotesi interne, non fatti "
            "attribuiti a Stefano):\n" + "\n".join(reflection_lines)
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
            kind_label = "FILO CONVERSAZIONALE | " if kind == "conversation_anchor" else ""
            label = (
                f"[{kind_label}{r.get('domain', 'generale')} | {age}]"
                if age else f"[{kind_label}{r.get('domain', 'generale')}]"
            )
            suffix = memory_verification_suffix(r)
            anchor_note = (
                " [ancora episodica: indica un discorso aperto, non prova il fatto raccontato]"
                if kind == "conversation_anchor" else ""
            )
            mem_lines.append(f"- {label} {r['content']}{anchor_note}{suffix}")
        if mem_lines:
            sections.append("Ricordi/note rilevanti:\n" + "\n".join(mem_lines))
    if insight_lines:
        sections.append("Principi trasversali:\n" + "\n".join(insight_lines))

    if results and not recent_history:
        sections.insert(0, "Regola cronologica interna:\n" + temporal_prompt_contract())

    ids = [r.get("id") for r in results[:mem_cap] if r.get("id")]
    if results:
        node_tags = [
            f"{r.get('source','?')}:{r.get('domain','?')}({r.get('id','')[:8]})"
            for r in results[:mem_cap]
        ]
        logger.info(f"RAG ctx [{len(results)} nodi]: {' | '.join(node_tags)}")

    return RagContext(text="\n\n".join(sections), ids=ids, mode=mode)
