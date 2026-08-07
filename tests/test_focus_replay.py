"""Regressioni pure per la provenienza del replay Active Focus."""
from scripts.experiments.replay_focus import FocusEngine, normalize_focus_event


def test_reaction_uses_raw_feedback_and_cannot_birth():
    raw, source = normalize_focus_event({
        "source": "reaction",
        "content": "Ho compreso una lezione causale molto piu' ampia.",
        "reaction_raw": "No, questa analogia e' una forzatura.",
    })
    assert raw == "No, questa analogia e' una forzatura."
    assert source == "reaction_raw"

    engine = FocusEngine()
    engine.step(
        100.0,
        "processi",
        "Il progetto Poseidon richiede una nuova prova documentata sul bancale di laboratorio.",
        "user",
        "u1",
    )
    assert engine.births == 1
    before = engine.focuses[0].activation
    engine.step(
        110.0,
        "processi",
        "La prova Poseidon va rifatta sul bancale.",
        "reaction_raw",
        "r1",
    )
    assert engine.births == 1
    assert engine.focuses[0].activation > before

    standalone = FocusEngine()
    standalone.step(
        100.0,
        "altro",
        "Stefano ha smentito una connessione abbastanza lunga da sembrare un tema di lavoro.",
        "reaction_raw",
        "r2",
    )
    assert standalone.births == 0 and not standalone.focuses


def test_reaction_without_raw_is_excluded():
    assert normalize_focus_event({
        "source": "reaction",
        "content": "Sintesi prodotta dal modello",
    }) == ("", "reaction_raw")


if __name__ == "__main__":
    test_reaction_uses_raw_feedback_and_cannot_birth()
    test_reaction_without_raw_is_excluded()
    print("PASS")
