#!/usr/bin/env python3
"""Test isolati per il routing regex dell'Executor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.executor import Executor


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


if __name__ == "__main__":
    test_spreadsheets_route_to_code_runner()
    test_linear_documents_still_route_to_document_reader()
    print("TUTTI I TEST ROUTING OK")
