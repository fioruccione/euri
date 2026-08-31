"""Policy pura per scegliere le fonti del Loop 2a.

Una finestra temporale globale non equivale a una sessione: dopo un riavvio
mescolava memorie vecchie con il dialogo corrente. Qui la selezione parte da un
checkpoint durevole e conserva un solo episodio/segmento coerente.
"""

from __future__ import annotations


LOOP2A_CHECKPOINT_KEY = "euri:loop2a:memory_checkpoint"
LOOP2A_SESSION_GAP_S = 30 * 60
LOOP2A_EXCLUDE_SOURCES = frozenset({"campus", "web", "reflection"})


def _created_at(memory: dict) -> float:
    try:
        return float(memory.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def latest_reflection_checkpoint(memories: list[dict], boot_at: float) -> float:
    """Riconcilia il primo boot senza riprocessare l'intero archivio."""
    latest = max(
        (
            _created_at(memory)
            for memory in memories
            if memory.get("source") == "reflection"
        ),
        default=0.0,
    )
    return latest or float(boot_at)


def _conversation_scope(memory: dict) -> tuple[str, str] | None:
    temporal = memory.get("temporal_context") or {}
    conversation_id = str(temporal.get("conversation_id") or "").strip()
    if not conversation_id:
        return None
    segment_id = temporal.get("segment_id")
    return conversation_id, "" if segment_id is None else str(segment_id)


def _domain(memory: dict) -> str:
    return str(memory.get("domain") or "").strip().casefold()


def select_reflection_session(
    memories: list[dict],
    *,
    checkpoint: float,
    snapshot_at: float,
    max_gap_s: float = LOOP2A_SESSION_GAP_S,
) -> list[dict]:
    """Ritorna l'ultimo gruppo coerente, ordinato e successivo al checkpoint."""
    eligible = sorted(
        (
            memory
            for memory in memories
            if checkpoint < _created_at(memory) <= snapshot_at
            and memory.get("source") not in LOOP2A_EXCLUDE_SOURCES
            and not memory.get("superseded_by")
            and not memory.get("correction_pending")
        ),
        key=_created_at,
    )
    if not eligible:
        return []

    latest = eligible[-1]
    scope = _conversation_scope(latest)
    if scope is not None:
        return [
            memory for memory in eligible
            if _conversation_scope(memory) == scope
        ]

    # Fonti senza provenienza conversazionale: soltanto la coda temporale
    # contigua, mai tutti i record di una finestra arbitraria.
    selected = [latest]
    latest_domain = _domain(latest)
    next_created = _created_at(latest)
    for memory in reversed(eligible[:-1]):
        created = _created_at(memory)
        if next_created - created > max_gap_s:
            break
        # In assenza di conversation_id il tempo da solo non prova che due
        # fatti appartengano allo stesso filo. Un dominio esplicito diverso
        # chiude la coda: evita che una reflection trasformi una coincidenza
        # temporale in una relazione tra impianti/progetti distinti.
        memory_domain = _domain(memory)
        if latest_domain and memory_domain and memory_domain != latest_domain:
            break
        selected.append(memory)
        next_created = created
    return list(reversed(selected))


def reflection_parent_ids(
    session_memories: list[dict],
    related_memories: list[dict],
) -> list[str]:
    """Deduplica i genitori preservando prima le fonti della sessione."""
    result: list[str] = []
    seen: set[str] = set()
    for memory in [*session_memories, *related_memories]:
        memory_id = str(memory.get("id") or "").strip()
        if memory_id and memory_id not in seen:
            seen.add(memory_id)
            result.append(memory_id)
    return result
