#!/usr/bin/env python3
"""Test isolati per il routing regex dell'Executor."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.executor import Executor, ToolCall, ToolResult, ToolSpec


def _selected_tool(text: str) -> str | None:
    executor = object.__new__(Executor)
    call = executor.select_tool_by_regex(text)
    return call.tool_name if call else None


def test_spreadsheets_route_to_code_runner():
    cases = [
        "Leggi il file Excel e dimmi cosa contiene",
        "Analizza il file xlsx appena caricato",
        "Che dati ci sono nel foglio di calcolo?",
        "Controlla il documento ODS nella cartella dati",
    ]
    for text in cases:
        assert _selected_tool(text) == "run_code", text


def test_linear_documents_still_route_to_document_reader():
    cases = [
        "Analizza il documento PDF",
        "Leggi la scheda tecnica",
        "Riassumi il file CSV",
    ]
    for text in cases:
        assert _selected_tool(text) == "read_document", text


def test_coding_agent_is_semantic_capability_not_regex_rule():
    executor = Executor()
    specs = {item["name"]: item for item in executor.get_contextual_capabilities()}
    assert "build_computational_tool" in specs
    assert specs["build_computational_tool"]["effect"] == "local_write"
    # Nessuna scorciatoia lessicale nuova: il controller semantico decide se il
    # problema richiede davvero uno strumento, invece di una parola magica.
    assert _selected_tool(
        "Scrivi il codice necessario per verificare questa ipotesi numerica"
    ) is None


def test_executor_relays_worker_progress_and_heartbeat_on_calling_thread():
    executor = Executor()

    def handler(_params, **kwargs):
        kwargs["progress_callback"]({
            "phase": "working", "label": "Sto lavorando"
        })
        time.sleep(0.6)
        return ToolResult(True, "ok")

    executor._registry["progress_probe"] = ToolSpec(
        name="progress_probe",
        description="test",
        parameters_schema={},
        handler=handler,
        timeout_seconds=3,
    )
    observed = []
    result = executor.execute(
        ToolCall("progress_probe", {}), progress_callback=observed.append
    )

    assert result.success
    assert any(item.get("phase") == "working" for item in observed)
    assert any(item.get("phase") == "heartbeat" for item in observed)
    assert all(item.get("tool_name") == "progress_probe" for item in observed)


if __name__ == "__main__":
    test_spreadsheets_route_to_code_runner()
    test_linear_documents_still_route_to_document_reader()
    test_coding_agent_is_semantic_capability_not_regex_rule()
    test_executor_relays_worker_progress_and_heartbeat_on_calling_thread()
    print("TUTTI I TEST ROUTING OK")
