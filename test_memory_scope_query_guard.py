#!/usr/bin/env python3
"""Guardrail: nel runtime nessuna ricerca sugli indici di memoria senza scope.

NON è la primitiva per costruzione. Quella sarebbe un'interfaccia di ricerca che
pretende uno scope e non espone `idx:memories` ai chiamanti normali, e verrà
progettata dopo la tabella offline degli operatori. Questo test rende soltanto
*verificabile* una convenzione che oggi dipende dalla memoria di chi scrive: un
nuovo call site che dimentica il confine viene intercettato subito invece di
manifestarsi mesi dopo come richiamo mancante.

Limite dichiarato: l'analisi è statica e ragiona per funzione. Vede la
dimenticanza (nessuno scope in tutta la funzione che interroga), non un uso
sbagliato dello scope giusto. È il difetto che è realmente accaduto.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEXES = ("idx:memories", "idx:notes")
SCOPE_MARKERS = ("memory_scope", "scope_clause", "_source_prefix")

# Non è runtime di Euri: snapshot storici, sperimentazione offline, tooling.
SKIP_DIRS = {
    "archive", "backups", "backup_pre_migration_2026-05-26", "sandbox", "venv",
    "__pycache__", "models", "logs", "tests", "benchmarks", "scripts",
    "audit_output", "experiments_output", "thought_maps", "docs", "ssl",
    "codice aggiornato",
}
# Convenzioni di nome già in uso nel repo per sonde e utilità manuali.
SKIP_PREFIXES = ("test_", "diag_", "probe_", "force_")

# Esenzioni esplicite e motivate. Ogni voce deve corrispondere a un call site
# ancora esistente: un'esenzione che sopravvive al codice che la giustificava
# è peggio di nessuna esenzione. `attesi` fissa quante ricerche non scopate quella
# posizione può contenere: ui/app.py è uno script Streamlit e i suoi call site
# stanno a livello di modulo, quindi senza conteggio una seconda query
# dimenticata erediterebbe l'esenzione della prima senza che nessuno lo veda.
ALLOWLIST = {
    ("ui/app.py", "<module>"): {
        "attesi": 1,
        "motivo": (
            "Control Room: ispezione umana deliberatamente cross-scope. "
            "L'operatore deve poter vedere anche le memorie sperimentali, "
            "comprese quelle di una sessione chiusa."
        ),
    },
}


def _iter_runtime_files():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name.startswith(SKIP_PREFIXES):
            continue
        yield path, relative


def _searched_index(node: ast.Call) -> str | None:
    """Riconosce `<x>.ft("idx:...").search(...)` e ritorna il nome dell'indice."""
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "search":
        return None
    inner = node.func.value
    if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
        return None
    if inner.func.attr != "ft" or not inner.args:
        return None
    target = inner.args[0]
    if isinstance(target, ast.Constant) and target.value in INDEXES:
        return target.value
    return None


def _enclosing_function(tree: ast.AST, lineno: int):
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= lineno <= end:
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def collect_call_sites() -> list[dict]:
    sites = []
    for path, relative in _iter_runtime_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            index = _searched_index(node)
            if index is None:
                continue
            function = _enclosing_function(tree, node.lineno)
            if function is not None:
                context = ast.get_source_segment(source, function) or ""
                name = function.name
            else:
                lo = max(0, node.lineno - 16)
                context = "\n".join(lines[lo:node.lineno + 2])
                name = "<module>"
            sites.append({
                "file": str(relative),
                "function": name,
                "line": node.lineno,
                "index": index,
                "scoped": any(marker in context for marker in SCOPE_MARKERS),
            })
    return sites


def test_every_runtime_search_carries_a_scope():
    sites = collect_call_sites()
    assert sites, "nessun call site trovato: lo scanner non sta guardando il runtime"

    unscoped = [
        site for site in sites
        if not site["scoped"]
        and (site["file"], site["function"]) not in ALLOWLIST
    ]
    assert not unscoped, (
        "ricerche runtime senza clausola di scope:\n"
        + "\n".join(
            f"  {s['file']}:{s['line']} in {s['function']}() su {s['index']}"
            for s in unscoped
        )
        + "\nAggiungere lo scope alla query, oppure una voce motivata in ALLOWLIST."
    )


def test_allowlist_matches_reality():
    """Un'esenzione deve coprire esattamente i call site che dichiara."""
    sites = collect_call_sites()
    for key, entry in ALLOWLIST.items():
        esenti = [
            site for site in sites
            if (site["file"], site["function"]) == key and not site["scoped"]
        ]
        assert esenti, f"esenzione senza più un call site corrispondente: {key}"
        assert len(esenti) == entry["attesi"], (
            f"{key}: attese {entry['attesi']} ricerche non scopate, trovate "
            f"{len(esenti)} (righe {[s['line'] for s in esenti]}). "
            "Una nuova query non eredita l'esenzione della precedente: "
            "scoparla, oppure aggiornare la motivazione."
        )


def test_reaction_gate_is_covered_by_the_scanner():
    """Sentinella dello scanner: se smette di vedere il caso noto, è rotto."""
    sites = collect_call_sites()
    reaction = [
        site for site in sites
        if site["file"] == "core/reaction.py"
        and site["function"] == "gather_grounded_evidence"
    ]
    assert reaction, "lo scanner non vede più gather_grounded_evidence"
    assert reaction[0]["scoped"], "la clausola di scope è sparita dal gate del briefing"


if __name__ == "__main__":
    test_every_runtime_search_carries_a_scope()
    test_allowlist_matches_reality()
    test_reaction_gate_is_covered_by_the_scanner()
    for site in collect_call_sites():
        flag = "scoped" if site["scoped"] else "ESENTO"
        print(f"  [{flag}] {site['file']}:{site['line']} {site['function']}() → {site['index']}")
    print("test_memory_scope_query_guard: OK")
