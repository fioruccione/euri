"""Ciclo generate -> execute -> feedback -> correct del coding agent."""
from __future__ import annotations

import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import config
from agent.code_runner import CodeResult
from agent.coding_job import CodingJob
from agent.opencode_adapter import OpenCodeReply


# Le prove sintetiche del job non devono contaminare l'archivio di ricerca
# usato per diagnosticare i tentativi reali. La persistenza ha un test dedicato.
config.CODE_AGENT_RESEARCH_LOG_ENABLED = False


class _FakeAdapter:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.prompts = []
        self.aborted = False
        self.closed = False

    def start(self):
        pass

    def create_session(self, _title):
        return "session-test"

    def prompt(self, message, **_kwargs):
        self.prompts.append(message)
        if len(self.prompts) == 1:
            (self.workspace / "main.py").write_text(
                "print(undefined_name)\n", encoding="utf-8"
            )
        else:
            assert "NameError" in message
            (self.workspace / "main.py").write_text("print(42)\n", encoding="utf-8")
        return OpenCodeReply("file aggiornato", {})

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


class _FakeRunner:
    def __init__(self):
        self.codes = []

    @staticmethod
    def list_input_files():
        return []

    def execute_generated_code(self, code, _stop_event, **kwargs):
        self.codes.append((code, kwargs))
        if "undefined_name" in code:
            return CodeResult(
                False,
                "Lo script ha dato errore: NameError",
                "Traceback\nNameError: name 'undefined_name' is not defined",
                exit_code=1,
            )
        return CodeResult(True, "42", exit_code=0)


def test_job_feeds_runtime_error_back_and_cleans_workspace():
    created = []

    def factory(workspace):
        adapter = _FakeAdapter(workspace)
        created.append(adapter)
        return adapter

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        runner = _FakeRunner()
        result = CodingJob(
            code_runner=runner,
            adapter_factory=factory,
            workspace_root=root,
            max_attempts=3,
        ).run("calcola la risposta", threading.Event())

        assert result.success and result.output == "42"
        assert len(result.attempts) == 2
        assert result.attempts[0].success is False
        assert result.attempts[1].success is True
        assert runner.codes[0][1]["require_bwrap"] is True
        assert created[0].closed is True
        assert list(root.iterdir()) == []


def test_job_does_not_require_input_files():
    created = []

    class ImmediateAdapter(_FakeAdapter):
        def prompt(self, message, **_kwargs):
            self.prompts.append(message)
            assert "nessun file" in message
            (self.workspace / "main.py").write_text("print(42)\n", encoding="utf-8")
            return OpenCodeReply("ok", {})

    def factory(workspace):
        adapter = ImmediateAdapter(workspace)
        created.append(adapter)
        return adapter

    with TemporaryDirectory() as tmp:
        result = CodingJob(
            code_runner=_FakeRunner(),
            adapter_factory=factory,
            workspace_root=tmp,
            max_attempts=1,
        ).run("verifica 6 per 7", threading.Event())
        assert result.success and result.output == "42"


def test_job_reports_real_progress_phases_without_affecting_result():
    events = []

    def factory(workspace):
        return _FakeAdapter(workspace)

    with TemporaryDirectory() as tmp:
        result = CodingJob(
            code_runner=_FakeRunner(),
            adapter_factory=factory,
            workspace_root=tmp,
            max_attempts=3,
            progress_callback=events.append,
        ).run("calcola la risposta", threading.Event())

    assert result.success
    phases = [item["phase"] for item in events]
    assert phases[:3] == ["preparing", "backend_starting", "backend_ready"]
    assert "generating" in phases
    assert "artifact_ready" in phases
    assert "executing" in phases
    assert "repairing" in phases
    assert phases[-1] == "completed"
    assert all(float(item["elapsed_s"]) >= 0.0 for item in events)


def test_no_artifact_watchdog_retries_with_full_task_in_fresh_session():
    created = []

    class NoArtifactThenSuccess(_FakeAdapter):
        def __init__(self, workspace):
            super().__init__(workspace)
            self.resets = 0

        def reset_session(self, _title):
            self.resets += 1
            return f"session-retry-{self.resets}"

        def prompt(self, message, **kwargs):
            self.prompts.append(message)
            assert kwargs["no_artifact_timeout"] == 7
            if len(self.prompts) == 1:
                return OpenCodeReply(
                    "Nessun main.py materializzato entro 7s.",
                    {"stopped_no_artifact": True},
                )
            assert "problema irripetibile 731" in message
            assert "Inizia usando subito il tool di scrittura" in message
            (self.workspace / "main.py").write_text("print(42)\n", encoding="utf-8")
            return OpenCodeReply("ok", {})

    def factory(workspace):
        adapter = NoArtifactThenSuccess(workspace)
        created.append(adapter)
        return adapter

    events = []
    with TemporaryDirectory() as tmp:
        result = CodingJob(
            code_runner=_FakeRunner(),
            adapter_factory=factory,
            workspace_root=tmp,
            max_attempts=2,
            no_artifact_timeout=7,
            progress_callback=events.append,
        ).run("problema irripetibile 731", threading.Event())

    assert result.success and result.output == "42"
    assert len(result.attempts) == 2
    assert created[0].resets == 1
    missing = next(item for item in events if item["phase"] == "missing_artifact")
    assert missing["watchdog"] is True


def test_watchdog_does_not_reexecute_unchanged_failed_artifact():
    created = []

    class FailedThenStaleThenFixed(_FakeAdapter):
        def __init__(self, workspace):
            super().__init__(workspace)
            self.resets = 0

        def reset_session(self, _title):
            self.resets += 1
            return f"session-retry-{self.resets}"

        def prompt(self, message, **_kwargs):
            self.prompts.append(message)
            if len(self.prompts) == 1:
                (self.workspace / "main.py").write_text(
                    "print(undefined_name)\n", encoding="utf-8"
                )
                return OpenCodeReply("creato", {})
            if len(self.prompts) == 2:
                # Il file fallito esiste ancora, ma OpenCode non lo ha toccato.
                return OpenCodeReply(
                    "Nessuna nuova versione materializzata.",
                    {"stopped_no_artifact": True},
                )
            (self.workspace / "main.py").write_text("print(42)\n", encoding="utf-8")
            return OpenCodeReply("corretto", {})

    def factory(workspace):
        adapter = FailedThenStaleThenFixed(workspace)
        created.append(adapter)
        return adapter

    with TemporaryDirectory() as tmp:
        runner = _FakeRunner()
        result = CodingJob(
            code_runner=runner,
            adapter_factory=factory,
            workspace_root=tmp,
            max_attempts=3,
            no_artifact_timeout=7,
        ).run("correggi senza rieseguire il vecchio file", threading.Event())

    assert result.success and result.output == "42"
    assert len(result.attempts) == 3
    assert len(runner.codes) == 2
    assert created[0].resets == 1


def test_exit_zero_without_stdout_or_artifacts_is_repaired():
    class EmptyThenUsefulAdapter(_FakeAdapter):
        def prompt(self, message, **_kwargs):
            self.prompts.append(message)
            if len(self.prompts) == 1:
                (self.workspace / "main.py").write_text(
                    "def main():\n    pass\n\nmain()\n", encoding="utf-8"
                )
            else:
                assert "missing_observable_result" in message
                assert "nessun stdout reale" in message
                (self.workspace / "main.py").write_text(
                    "print(42)\n", encoding="utf-8"
                )
            return OpenCodeReply("file aggiornato", {})

    class ObservableRunner(_FakeRunner):
        def execute_generated_code(self, code, _stop_event, **kwargs):
            self.codes.append((code, kwargs))
            if "pass" in code:
                return CodeResult(
                    True,
                    "Operazione completata senza errori.",
                    exit_code=0,
                    stdout_chars=0,
                )
            return CodeResult(True, "42", exit_code=0, stdout_chars=2)

    events = []
    with TemporaryDirectory() as tmp:
        result = CodingJob(
            code_runner=ObservableRunner(),
            adapter_factory=EmptyThenUsefulAdapter,
            workspace_root=tmp,
            max_attempts=2,
            progress_callback=events.append,
        ).run("produci un risultato osservabile", threading.Event())

    assert result.success and result.output == "42"
    assert len(result.attempts) == 2
    assert result.attempts[0].success is False
    assert result.attempts[0].exit_code == 0
    assert "missing_observable_result" in result.attempts[0].error
    assert result.attempts[1].success is True
    assert any(
        event["phase"] == "repairing"
        and "Nessun risultato osservabile" in event["label"]
        for event in events
    )


def test_exit_zero_with_artifact_is_an_observable_success():
    class ArtifactRunner(_FakeRunner):
        def execute_generated_code(self, code, _stop_event, **kwargs):
            self.codes.append((code, kwargs))
            return CodeResult(
                True,
                "Operazione completata senza errori.",
                exit_code=0,
                artifacts="[risultato.csv]\nvalore\n42",
                stdout_chars=0,
            )

    class ArtifactAdapter(_FakeAdapter):
        def prompt(self, message, **_kwargs):
            self.prompts.append(message)
            (self.workspace / "main.py").write_text(
                "from pathlib import Path\nresult = Path('result')\n",
                encoding="utf-8",
            )
            return OpenCodeReply("file aggiornato", {})

    with TemporaryDirectory() as tmp:
        result = CodingJob(
            code_runner=ArtifactRunner(),
            adapter_factory=ArtifactAdapter,
            workspace_root=tmp,
            max_attempts=1,
        ).run("salva il risultato come artefatto", threading.Event())

    assert result.success
    assert result.artifacts == "[risultato.csv]\nvalore\n42"
    assert len(result.attempts) == 1


if __name__ == "__main__":
    test_job_feeds_runtime_error_back_and_cleans_workspace()
    test_job_does_not_require_input_files()
    test_job_reports_real_progress_phases_without_affecting_result()
    test_no_artifact_watchdog_retries_with_full_task_in_fresh_session()
    test_watchdog_does_not_reexecute_unchanged_failed_artifact()
    test_exit_zero_without_stdout_or_artifacts_is_repaired()
    test_exit_zero_with_artifact_is_an_observable_success()
    print("TUTTI I TEST CODING JOB OK")
