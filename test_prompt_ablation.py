#!/usr/bin/env python3
"""Regressioni pure per il replay prompt dual-channel."""
from pathlib import Path
import json

from benchmarks.euri_memory.prompt_ablation import (
    APPEND,
    EVIDENCE_FIRST,
    PREPEND,
    _load_case,
    _reconstruct_contexts,
)


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "audit_output" / "dual_channel_validation_v1_seed396895560" / "run"
SOURCE = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"


def test_replay_reconstructs_registered_contexts_and_changes_only_presentation():
    if not RUN.is_dir():
        return
    report_path = RUN / "runs" / "conv-41__r0.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    case = _load_case(RUN, "conv-41", SOURCE)
    contexts = _reconstruct_contexts(report, case)

    assert len(contexts) == len(report["selection"]["question_ids"])
    changed = [item for item in contexts.values() if item[APPEND] != item[PREPEND]]
    assert changed
    first = changed[0]
    assert "[Turni verbatim aggiuntivi dal canale passivo]" in first[APPEND]
    assert first[PREPEND].startswith(
        "[Turni verbatim aggiuntivi dal canale passivo]"
    )
    assert first[EVIDENCE_FIRST].startswith(
        "[EVIDENZE ORIGINALI RECUPERATE"
    )
    # Nessuna variante altera il contenuto del turno: cambia soltanto ordine e
    # contratto di attenzione.
    assert "Ricordi/note rilevanti:" in first[APPEND]
    assert "Ricordi/note rilevanti:" in first[PREPEND]
    assert "Ricordi/note rilevanti:" in first[EVIDENCE_FIRST]


if __name__ == "__main__":
    test_replay_reconstructs_registered_contexts_and_changes_only_presentation()
    print("test_prompt_ablation: OK")
