"""Regressioni della selezione sessionale e causale del Loop 2a."""

from core.reflection_policy import (
    latest_reflection_checkpoint,
    reflection_parent_ids,
    select_reflection_session,
)


def _memory(
    memory_id,
    created_at,
    *,
    source="passive",
    conversation_id="",
    segment_id=None,
    **extra,
):
    return {
        "id": memory_id,
        "created_at": created_at,
        "source": source,
        "content": memory_id,
        "temporal_context": {
            "conversation_id": conversation_id,
            "segment_id": segment_id,
        },
        **extra,
    }


def test_restart_checkpoint_excludes_old_reactions_from_new_dialogue():
    memories = [
        _memory("reaction-1", 100, source="reaction"),
        _memory("reaction-2", 110, source="reaction"),
        _memory("reaction-3", 120, source="reaction"),
        _memory("previous-reflection", 200, source="reflection"),
    ]
    checkpoint = latest_reflection_checkpoint(memories, boot_at=300)
    assert checkpoint == 200
    assert select_reflection_session(
        memories, checkpoint=checkpoint, snapshot_at=400
    ) == []


def test_only_latest_conversation_segment_becomes_session():
    memories = [
        _memory("old-a", 210, conversation_id="conv-a", segment_id=1),
        _memory("old-b", 220, conversation_id="conv-a", segment_id=1),
        _memory("new-a", 230, conversation_id="conv-b", segment_id=2),
        _memory("new-b", 240, conversation_id="conv-b", segment_id=2),
        _memory("new-c", 250, conversation_id="conv-b", segment_id=2),
    ]
    selected = select_reflection_session(
        memories, checkpoint=200, snapshot_at=300
    )
    assert [memory["id"] for memory in selected] == ["new-a", "new-b", "new-c"]


def test_activity_snapshot_does_not_include_later_memory():
    memories = [
        _memory("a", 210),
        _memory("b", 220),
        _memory("later", 310),
    ]
    selected = select_reflection_session(
        memories, checkpoint=200, snapshot_at=300
    )
    assert [memory["id"] for memory in selected] == ["a", "b"]


def test_invalid_and_superseded_sources_are_not_parents():
    memories = [
        _memory("active", 210),
        _memory("superseded", 220, superseded_by="replacement"),
        _memory("web", 230, source="web"),
        _memory("active-2", 240),
    ]
    selected = select_reflection_session(
        memories, checkpoint=200, snapshot_at=300
    )
    assert [memory["id"] for memory in selected] == ["active", "active-2"]
    assert reflection_parent_ids(
        selected,
        [_memory("active", 210), _memory("related", 100)],
    ) == ["active", "active-2", "related"]


def test_unscoped_temporal_tail_stops_at_explicit_domain_boundary():
    memories = [
        _memory("icma", 210, domain="estrusione plastica"),
        _memory("orione", 220, domain="produzione industriale"),
        _memory("orione-2", 230, domain="produzione industriale"),
    ]
    selected = select_reflection_session(
        memories, checkpoint=200, snapshot_at=300
    )
    assert [memory["id"] for memory in selected] == ["orione", "orione-2"]


if __name__ == "__main__":
    test_restart_checkpoint_excludes_old_reactions_from_new_dialogue()
    test_only_latest_conversation_segment_becomes_session()
    test_activity_snapshot_does_not_include_later_memory()
    test_invalid_and_superseded_sources_are_not_parents()
    test_unscoped_temporal_tail_stops_at_explicit_domain_boundary()
    print("test_loop2a_reflection: 4/4 OK")
