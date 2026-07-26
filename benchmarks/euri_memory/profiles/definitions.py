"""Profili A/B dichiarativi: nessuna differenza resta nascosta nel runner."""

from benchmarks.euri_memory.contracts import MemoryProfile


PROFILES = {
    "rag_only": MemoryProfile(name="rag_only"),
    "passive_memory": MemoryProfile(name="passive_memory", passive_memory=True),
    "consolidation": MemoryProfile(
        name="consolidation",
        passive_memory=True,
        consolidation=True,
    ),
    "full_cognitive": MemoryProfile(
        name="full_cognitive",
        passive_memory=True,
        consolidation=True,
        reflection=True,
        pulse=True,
        initiative=True,
    ),
    "full_without_reflection": MemoryProfile(
        name="full_without_reflection",
        passive_memory=True,
        consolidation=True,
        pulse=True,
        initiative=True,
    ),
    "full_without_pulse": MemoryProfile(
        name="full_without_pulse",
        passive_memory=True,
        consolidation=True,
        reflection=True,
    ),
}


def get_profile(name: str) -> MemoryProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(f"profilo sconosciuto {name!r}; disponibili: {choices}") from exc
