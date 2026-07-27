"""Policy dual-channel condivisa fra benchmark e runtime.

Il compositore validato vive ora nel core: il benchmark lo re-esporta per
impedire divergenze fra la policy misurata e quella usata da Euri.
"""
from __future__ import annotations

from typing import Sequence

from core.dual_channel import (  # noqa: F401
    ADDITIONS_HEADER,
    ADDITION_SEP,
    FROZEN_POLICY,
    POLICY_ID,
    DualChannelComposition,
    compose_dual_channel,
    gold_covered,
    render_additions_block,
)


# Universo della validazione dual-channel: le conversazioni LoCoMo mai usate.
DUAL_CHANNEL_EXCLUDED_SAMPLE_IDS = frozenset(
    {"conv-26", "conv-42", "conv-30", "conv-43", "conv-47"}
)


def untouched_universe(all_sample_ids: Sequence[str]) -> list[str]:
    return sorted(
        sample_id
        for sample_id in all_sample_ids
        if sample_id not in DUAL_CHANNEL_EXCLUDED_SAMPLE_IDS
    )
