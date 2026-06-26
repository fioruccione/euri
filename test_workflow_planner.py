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
    assert not wp.looks_like_workflow("come stai oggi")
    assert not wp.looks_like_workflow("controlla la cpu")  # un solo verbo
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
    assert res["path"] and Path(res["path"]).exists()
    saved = Path(res["path"]).read_text()
    assert saved.startswith("DRAFT::")           # la bozza è finita nel file di revisione
    assert "non l'ho inviata" in res["spoken"]    # bozza NON inviata
    print("ok engine chain + draft-not-sent")


def test_engine_stops_on_read_error(tmp_review):
    config.WORKFLOW_REVIEW_DIR = tmp_review
    eng = WorkflowEngine(FailExecutor(), FakeBrain())
    res = eng.run([{"cap": "READ", "input": None}, {"cap": "SUMMARIZE", "input": "$1"}])
    assert not res["ok"] and "read" in res["spoken"].lower()
    print("ok engine stop on error")


if __name__ == "__main__":
    test_looks_like_workflow()
    test_extract_and_validate()
    test_plan_with_fake_llm()
    with tempfile.TemporaryDirectory() as d:
        test_engine_chains_and_drafts(d)
        test_engine_stops_on_read_error(d)
    print("\nTUTTI I TEST OK")
