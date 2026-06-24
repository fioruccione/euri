#!/usr/bin/env python3
"""Regression sul pre-gate del briefing sogni/intuizioni."""

from core.reaction import BRIEFING_FEEDBACK_RE, BRIEFING_HINT_RE


def test_dream_feedback_is_not_new_briefing():
    text = (
        "Mi torna come ragionamento analogico, non come collegamento tecnico diretto. "
        "Antenne e pallet non sono collegati davvero nello stesso processo, però il punto "
        "comune che hai trovato è interessante. Tienilo come analogia o sogno, non come "
        "fatto operativo."
    )

    assert BRIEFING_HINT_RE.search(text)
    assert BRIEFING_FEEDBACK_RE.search(text)


def test_plain_dream_question_still_reaches_briefing_classifier():
    text = "Euri, cosa hai sognato?"

    assert BRIEFING_HINT_RE.search(text)
    assert not BRIEFING_FEEDBACK_RE.search(text)


if __name__ == "__main__":
    test_dream_feedback_is_not_new_briefing()
    test_plain_dream_question_still_reaches_briefing_classifier()
    print("test_reaction_briefing: OK")
