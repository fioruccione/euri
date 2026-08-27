#!/usr/bin/env python3
"""Test del Workflow Planner: gate, parsing, validazione, esecuzione incatenata."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from core import workflow_planner as wp
from core.workflow_planner import WorkflowEngine


# ── fakes ──
class _StopEvent:
    def clear(self):
        pass


class FakeResp:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})


class FakeChat:
    def __init__(self, content):
        self._c = content

    def chat(self, **kw):
        return FakeResp(self._c)


class FakeToolResult:
    def __init__(self, success, output="", raw_data=None):
        self.success, self.output, self.raw_data = success, output, raw_data or {}


class FakeExecutor:
    def __init__(self, text):
        self._text = text
        self.stop_event = _StopEvent()

    def execute(self, call):
        assert call.tool_name == "read_document"
        return FakeToolResult(True, output="comprensione", raw_data={"context_extra": self._text})


class FailExecutor:
    def __init__(self):
        self.stop_event = _StopEvent()

    def execute(self, call):
        return FakeToolResult(False, output="niente file")


class FakeBrain:
    @staticmethod
    def _clean(t):
        return (t or "").strip()


def test_looks_like_workflow():
    assert wp.looks_like_workflow("leggi il documento e preparami una mail")
    assert wp.looks_like_workflow("analizza l'allegato e scrivi una risposta")
    assert wp.looks_like_workflow("riassumi quello che ho detto e scrivimi una mail")
    assert not wp.looks_like_workflow("come stai oggi")
    assert not wp.looks_like_workflow("controlla la cpu")  # un solo verbo
    assert not wp.looks_like_workflow("leggi e rileggi")  # stessa capability, nessun artefatto
    # Regressione live 17/07: `legg\w*` scambiava "leggero" per "leggere" e,
    # insieme a "controllato", faceva creare una bozza non richiesta.
    assert not wp.looks_like_workflow(
        "No, non l'ho controllato ma non dovrebbe cambiare nulla. "
        "Se c'e' un leggero ritiro in piu' sul bancale non cambia l'utilizzo reale."
    )
    print("ok looks_like_workflow")


def test_extract_and_validate():
    arr = wp._extract_json_array('ecco: [{"cap":"READ","input":null}] fine')
    assert arr == [{"cap": "READ", "input": None}]
    steps = wp._validate([
        {"cap": "read", "input": None},
        {"cap": "DRAFT", "args": {"kind": "mail"}, "input": "$1"},
    ])
    assert steps[0]["cap"] == "READ" and steps[1]["args"]["kind"] == "mail"
    # cap invalida → tutto-o-niente
    assert wp._validate([{"cap": "READ"}, {"cap": "NOPE"}]) == []
    assert wp._validate("garbage") == []
    # Il piano non puo' costruire cicli o leggere risultati futuri.
    assert wp._validate([{"cap": "READ", "input": "$1"}]) == []
    assert wp._validate([
        {"cap": "READ", "input": None},
        {"cap": "DRAFT", "input": "$3"},
        {"cap": "SAVE_FOR_REVIEW", "input": "$2"},
    ]) == []
    assert wp._validate([
        {"cap": "READ", "input": None}
        for _ in range(wp.MAX_WORKFLOW_STEPS + 1)
    ]) == []
    print("ok extract+validate")


def test_plan_with_fake_llm():
    js = '[{"cap":"READ","args":{},"input":null},{"cap":"SUMMARIZE","args":{},"input":"$1"}]'
    steps = wp.plan("leggi e riassumi", chat=FakeChat(js))
    assert len(steps) == 2 and steps[1]["input"] == "$1"
    # LLM che risponde spazzatura → [] (fail-open)
    assert wp.plan("x", chat=FakeChat("non e' json")) == []
    print("ok plan")


def test_engine_chains_and_drafts(tmp_review):
    config.WORKFLOW_REVIEW_DIR = tmp_review

    class E(WorkflowEngine):
        def _llm(self, prompt, **k):
            return "DRAFT::" + prompt[-20:]

        def _summarize(self, text):
            return "SUMMARY::" + text

    eng = E(FakeExecutor("CONTENUTO_DOC"), FakeBrain())
    steps = [
        {"cap": "READ", "args": {}, "input": None},
        {"cap": "SUMMARIZE", "args": {}, "input": "$1"},
        {"cap": "DRAFT", "args": {"kind": "mail"}, "input": "$2"},
        {"cap": "SAVE_FOR_REVIEW", "args": {}, "input": "$3"},
    ]
    res = eng.run(steps)
    assert res["ok"]
    assert res["goal_status"] == "completed"
    assert res["completed_steps"] == 4
    assert [event["event"] for event in res["trace"]].count("completed") == 4
    assert res["path"] and Path(res["path"]).exists()
    saved = Path(res["path"]).read_text()
    assert saved.startswith("DRAFT::")           # la bozza è finita nel file di revisione
    assert "non l'ho inviata" in res["spoken"]    # bozza NON inviata
    print("ok engine chain + draft-not-sent")


def test_ensure_review_appends_save():
    # piano che finisce con DRAFT → SAVE_FOR_REVIEW appeso d'ufficio
    steps = [{"cap": "READ", "input": None}, {"cap": "DRAFT", "args": {"kind": "mail"}, "input": "$1"}]
    out = WorkflowEngine._ensure_review(steps)
    assert [s["cap"] for s in out] == ["READ", "DRAFT", "SAVE_FOR_REVIEW"]
    assert out[-1]["input"] == "$2"  # salva l'output della DRAFT
    # già presente → invariato
    has = steps + [{"cap": "SAVE_FOR_REVIEW", "input": "$2"}]
    assert WorkflowEngine._ensure_review(has) == has
    # Un vecchio SAVE non soddisfa una DRAFT successiva: va salvata l'ultima bozza.
    earlier_save = [
        {"cap": "READ", "input": None},
        {"cap": "SAVE_FOR_REVIEW", "input": "$1"},
        {"cap": "DRAFT", "input": "$2"},
    ]
    guarded = WorkflowEngine._ensure_review(earlier_save)
    assert [step["cap"] for step in guarded][-2:] == ["DRAFT", "SAVE_FOR_REVIEW"]
    assert guarded[-1]["input"] == "$3"
    # non finisce con DRAFT → invariato
    only_read = [{"cap": "READ", "input": None}]
    assert WorkflowEngine._ensure_review(only_read) == only_read
    print("ok ensure_review")


def test_engine_3step_draft_gets_saved(tmp_review):
    config.WORKFLOW_REVIEW_DIR = tmp_review

    class E(WorkflowEngine):
        def _llm(self, prompt, **k):
            return "BOZZA"

        def _summarize(self, text):
            return "SUM"

    eng = E(FakeExecutor("DOC"), FakeBrain())
    # piano "incostante" senza SAVE → l'engine lo salva comunque
    steps = [
        {"cap": "READ", "args": {}, "input": None},
        {"cap": "SUMMARIZE", "args": {}, "input": "$1"},
        {"cap": "DRAFT", "args": {"kind": "mail"}, "input": "$2"},
    ]
    res = eng.run(steps)
    assert res["ok"] and res["path"] and Path(res["path"]).exists()
    assert Path(res["path"]).read_text() == "BOZZA"
    assert "non l'ho inviata" in res["spoken"]
    print("ok 3step draft auto-saved")


def test_engine_stops_on_read_error(tmp_review):
    config.WORKFLOW_REVIEW_DIR = tmp_review
    eng = WorkflowEngine(FailExecutor(), FakeBrain())
    res = eng.run([{"cap": "READ", "input": None}, {"cap": "SUMMARIZE", "input": "$1"}])
    assert not res["ok"] and "read" in res["spoken"].lower()
    assert res["goal_status"] == "failed"
    print("ok engine stop on error")


def test_engine_checks_preconditions_and_observations(tmp_review):
    config.WORKFLOW_REVIEW_DIR = tmp_review

    class EmptySummary(WorkflowEngine):
        def _summarize(self, text):
            return ""

    # Riferimento non disponibile: nessuna azione successiva viene improvvisata.
    bad_ref = EmptySummary(FakeExecutor("DOC"), FakeBrain()).run([
        {"cap": "READ", "input": None},
        {"cap": "SUMMARIZE", "input": "$9"},
    ])
    assert not bad_ref["ok"] and bad_ref["completed_steps"] == 0

    # Un output vuoto non puo' essere passato alla capability seguente.
    empty = EmptySummary(FakeExecutor("DOC"), FakeBrain()).run([
        {"cap": "READ", "input": None},
        {"cap": "SUMMARIZE", "input": "$1"},
    ])
    assert not empty["ok"] and empty["completed_steps"] == 1
    assert empty["trace"][-1]["event"] == "failed"
    assert "testo vuoto" in empty["trace"][-1]["reason"]

    class MissingArtifact(WorkflowEngine):
        def _run_cap(self, cap, args, src):
            if cap == "SAVE_FOR_REVIEW":
                return {"text": src, "path": str(Path(tmp_review) / "inesistente.md")}
            return super()._run_cap(cap, args, src)

        def _llm(self, prompt, **kwargs):
            return "BOZZA"

    missing = MissingArtifact(FakeExecutor("DOC"), FakeBrain()).run([
        {"cap": "READ", "input": None},
        {"cap": "DRAFT", "input": "$1"},
        {"cap": "SAVE_FOR_REVIEW", "input": "$2"},
    ])
    assert not missing["ok"] and missing["completed_steps"] == 2
    assert "non esiste" in missing["trace"][-1]["reason"]
    print("ok engine preconditions + observations")


if __name__ == "__main__":
    test_looks_like_workflow()
    test_extract_and_validate()
    test_plan_with_fake_llm()
    test_ensure_review_appends_save()
    with tempfile.TemporaryDirectory() as d:
        test_engine_chains_and_drafts(d)
        test_engine_3step_draft_gets_saved(d)
        test_engine_stops_on_read_error(d)
        test_engine_checks_preconditions_and_observations(d)
    print("\nTUTTI I TEST OK")
