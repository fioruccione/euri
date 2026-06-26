#!/usr/bin/env python3
"""Test di core.utterance_pragmatics.is_clarification_request."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.utterance_pragmatics import is_clarification_request as clar


def test_clarification_questions():
    # caso reale dal vivo (26/06)
    assert clar("Di quale insight parli Euri?")
    assert clar("quale insight?")
    assert clar("cosa intendi?")
    assert clar("in che senso?")
    assert clar("non ho capito")          # marcatore forte, anche senza '?'
    assert clar("non ho capito a cosa ti riferisci")
    assert clar("di cosa stai parlando?")
    print("ok clarification rilevate")


def test_real_answers_pass_through():
    # risposte vere → NON sono chiarimenti (devono essere catturate come verdetto)
    assert not clar("sì, è utile per i nostri processi")
    assert not clar("secondo me è una forzatura")
    assert not clar("regge, l'ho visto in produzione")
    assert not clar("no, non c'entra niente")
    assert not clar("esatto")
    assert not clar("")
    # una domanda generica SENZA marcatore di non-comprensione non scatta
    assert not clar("davvero funziona così?")
    print("ok risposte non scambiate per chiarimenti")


if __name__ == "__main__":
    test_clarification_questions()
    test_real_answers_pass_through()
    print("\nTUTTI I TEST OK")
