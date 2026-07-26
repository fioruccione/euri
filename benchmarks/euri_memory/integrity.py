"""Guardie di integrità end-to-end che legano gli artefatti al manifest.

Il manifest è immutabile e hashato. Tutto ciò che viene prodotto dopo (selezioni,
report per coppia, checkpoint, analisi) deve dimostrare di appartenere ESATTAMENTE
a quel manifest: stesso corpus, stesso commit, stesse domande, stesso ordine dei
bracci, stesso answer_seed. Così due esperimenti non possono mescolarsi per
sbaglio prima della cecità.

Questi validatori sono in gran parte puri (nessun Redis, nessun LLM) e condivisi
da runner e analisi.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class IntegrityError(RuntimeError):
    pass


_SPEAKER_MAPPING = {"speaker_a": "owner_user", "speaker_b": "assistant"}


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_corpus_matches(manifest: dict, corpus_path: Path) -> str:
    """Il corpus effettivamente usato deve avere l'hash preregistrato.

    Un ``--source`` diverso è ammesso solo se il contenuto ha lo stesso SHA-256.
    """

    expected = manifest.get("corpus", {}).get("sha256")
    if not expected:
        raise IntegrityError("manifest privo di corpus.sha256")
    actual = sha256_file(corpus_path)
    if actual != expected:
        raise IntegrityError(
            f"corpus non corrisponde al manifest: atteso {expected}, trovato {actual}"
        )
    return actual


# --------------------------------------------------------------------------- #
# Git: commit registrato + worktree tracciata pulita
# --------------------------------------------------------------------------- #
def tracked_worktree_dirty(porcelain_no_untracked: str) -> bool:
    """Vero se ci sono modifiche tracciate (staged o meno). Puro."""

    return bool(porcelain_no_untracked.strip())


def git_head(repo_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_tracked_status(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        raise IntegrityError(f"git status non disponibile: {exc}") from exc


def assert_worktree_clean(repo_root: Path) -> None:
    """I file tracciati devono essere puliti; i report non tracciati sono ammessi."""

    if tracked_worktree_dirty(git_tracked_status(repo_root)):
        raise IntegrityError(
            "worktree con modifiche tracciate: il run richiede un albero pulito "
            "(i file report non tracciati/ignorati sono ammessi)"
        )


def assert_head_matches_manifest(manifest: dict, repo_root: Path) -> None:
    expected = manifest.get("git_commit")
    head = git_head(repo_root)
    if expected and head and expected != head:
        raise IntegrityError(
            f"HEAD {head} diverso dal commit registrato nel manifest {expected}"
        )


# --------------------------------------------------------------------------- #
# Selezione canonica (identica byte-a-byte fra scrittura e verifica)
# --------------------------------------------------------------------------- #
def canonical_selection_payload(
    sample_id: str,
    session_ids: list[str],
    question_ids: list[str],
) -> dict:
    return {
        "schema_version": 1,
        "selection_id": f"heldout-{sample_id}",
        "dataset": "locomo",
        "sample_id": sample_id,
        "session_ids": list(session_ids),
        "question_ids": list(question_ids),
        "speaker_mapping": dict(_SPEAKER_MAPPING),
    }


def canonical_selection_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, indent=2, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")


def selection_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_selection_bytes(payload)).hexdigest()


# --------------------------------------------------------------------------- #
# Coppie attese e validazione dei report per coppia
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExpectedPair:
    sample_id: str
    replica_index: int
    key: str
    question_ids: frozenset[str]
    answer_seed: int
    branch_order: tuple[str, ...]
    selection_sha256: str


def expected_pairs(manifest: dict) -> dict[str, ExpectedPair]:
    """Insieme delle coppie (conversazione, replica) attese dal manifest."""

    pairs: dict[str, ExpectedPair] = {}
    conv_by_id = {c["sample_id"]: c for c in manifest["conversations"]}
    for conversation in manifest["conversations"]:
        payload = canonical_selection_payload(
            conversation["sample_id"],
            conversation["session_ids"],
            conversation["question_ids"],
        )
        sel_sha = selection_sha256(payload)
        for replica in manifest["replicas"]:
            index = int(replica["replica_index"])
            key = f"{conversation['sample_id']}__r{index}"
            pairs[key] = ExpectedPair(
                sample_id=conversation["sample_id"],
                replica_index=index,
                key=key,
                question_ids=frozenset(conversation["question_ids"]),
                answer_seed=int(replica["answer_seed"]),
                branch_order=tuple(replica["branch_order"]),
                selection_sha256=sel_sha,
            )
    # ``conv_by_id`` resta utile per messaggi diagnostici dei chiamanti.
    _ = conv_by_id
    return pairs


def validate_pair_report(report: dict, manifest: dict, expected: ExpectedPair) -> list[str]:
    """Restituisce la lista dei problemi; vuota se il report appartiene alla coppia.

    Controlla sample_id, run_label, answer_seed, branch_order, source_sha256,
    git commit + worktree tracciata pulita, question_ids esatti, hash della
    selezione, presenza dei due bracci con scoring completo.
    """

    problems: list[str] = []
    corpus_sha = manifest.get("corpus", {}).get("sha256")
    git_commit = manifest.get("git_commit")

    dataset = report.get("dataset", {})
    run = report.get("run", {})
    selection = report.get("selection", {})
    git = report.get("git", {})

    if dataset.get("sample_id") != expected.sample_id:
        problems.append(f"sample_id {dataset.get('sample_id')} ≠ {expected.sample_id}")
    if run.get("run_label") != expected.key:
        problems.append(f"run_label {run.get('run_label')} ≠ {expected.key}")
    if int(run.get("answer_seed", -1)) != expected.answer_seed:
        problems.append(f"answer_seed {run.get('answer_seed')} ≠ {expected.answer_seed}")
    if tuple(run.get("branch_order") or ()) != expected.branch_order:
        problems.append(f"branch_order {run.get('branch_order')} ≠ {list(expected.branch_order)}")
    if corpus_sha and dataset.get("source_sha256") != corpus_sha:
        problems.append("source_sha256 diverso dal corpus del manifest")
    if git_commit and git.get("commit") != git_commit:
        problems.append(f"git commit {git.get('commit')} ≠ {git_commit}")
    if git.get("worktree_tracked_dirty") is not False:
        problems.append("report prodotto con worktree tracciata non pulita")
    if frozenset(selection.get("question_ids") or ()) != expected.question_ids:
        problems.append("question_ids diversi dall'insieme preregistrato")
    if selection.get("selection_sha256") != expected.selection_sha256:
        problems.append("selection_sha256 diverso dalla selezione attesa")

    profiles = {item.get("profile", {}).get("name"): item for item in report.get("profiles", [])}
    if {"rag_only", "passive_memory"} - set(profiles):
        problems.append("manca uno dei due bracci")
    else:
        for name in ("rag_only", "passive_memory"):
            if "scoring" not in profiles[name]:
                problems.append(f"scoring mancante per {name}")
    return problems


# --------------------------------------------------------------------------- #
# Identità del checkpoint / output directory
# --------------------------------------------------------------------------- #
def run_identity(manifest: dict) -> dict[str, Any]:
    return {
        "manifest_sha256": manifest.get("manifest_sha256"),
        "corpus_sha256": manifest.get("corpus", {}).get("sha256"),
        "git_commit": manifest.get("git_commit"),
    }


def assert_same_identity(recorded: dict, current: dict, *, context: str) -> None:
    for field in ("manifest_sha256", "corpus_sha256", "git_commit"):
        if recorded.get(field) != current.get(field):
            raise IntegrityError(
                f"{context}: {field} non corrisponde "
                f"(registrato {recorded.get(field)}, atteso {current.get(field)})"
            )
