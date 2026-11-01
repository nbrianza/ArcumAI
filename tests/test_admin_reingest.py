# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for #46: Admin page re-ingestion keeps the document in its
owner folder.

Design (architect comment on #46, approved D1 to D4): the Admin page stages the
archived file into the inbox at the same archive-relative path by moving it
(`_stage_for_reingest`), runs ingestion, then reports per file
(`_settle_after_reingest`). `_list_documents` groups rows by `file_path`.

Real ingestion (`ingest.py`) loads the embedding model, so it is replaced by a
fake that applies the two rules confirmed by the architect in `ingest.py`:
the chunk `file_path` metadata is the path relative to INBOX_DIR, and a
successful file is archived with the real `sposta_file_con_struttura`.

Synthetic files under tmp_path only; the real archive and inbox are never used.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

DOC = "alice/sub/contract-sample.txt"
DOC_BOB = "bob/contract-sample.txt"
ROOT_DOC = "contract-root.txt"
TIMESTAMP_COPY = re.compile(r"_\d{9,}$")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Synthetic archive, inbox, error and duplicates folders wired into admin."""
    from src.ui import admin
    import src.documents as docs
    import src.utils as utils

    d = SimpleNamespace(
        archive=tmp_path / "archive",
        inbox=tmp_path / "inbox",
        error=tmp_path / "error",
        duplicates=tmp_path / "duplicates",
        outside=tmp_path / "outside",
    )
    for p in vars(d).values():
        p.mkdir()
    monkeypatch.setattr(admin, "ARCHIVE_DIR", d.archive)
    monkeypatch.setattr(admin, "INBOX_DIR", d.inbox)
    # ERROR_DIR and DUPLICATES_DIR are not imported by admin.py yet (design 2a).
    monkeypatch.setattr(admin, "ERROR_DIR", d.error, raising=False)
    monkeypatch.setattr(admin, "DUPLICATES_DIR", d.duplicates, raising=False)
    monkeypatch.setattr(docs, "ARCHIVE_DIR", d.archive)
    monkeypatch.setattr(utils, "ARCHIVE_DIR", d.archive)
    return d


def _put(root: Path, rel: str, content: bytes = b"synthetic content") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _fake_ingestion(dirs, indexed: list[str]):
    """Replacement for subprocess.run that mimics ingest.py on the inbox."""
    from src.utils import sposta_file_con_struttura

    def fake_run(cmd, **kwargs):
        for f in sorted(p for p in dirs.inbox.rglob("*") if p.is_file()):
            indexed.append(Path(str(f.relative_to(dirs.inbox))).as_posix())
            sposta_file_con_struttura(f, dirs.inbox, dirs.archive)
        return SimpleNamespace(stdout="synthetic ingestion done", stderr="")

    return fake_run


def _reingest(admin, rels: list[str]) -> list[str]:
    """Admin-page sequence from design 2b without the NiceGUI closure."""
    staged = [admin._stage_for_reingest(r) for r in rels]
    admin._run_ingestion()
    return [admin._settle_after_reingest(Path(s)) for s in staged]


# ---------------------------------------------------------------------------
# Criterion 1: document stays in alice/sub, no copy at the archive root
# ---------------------------------------------------------------------------

def test_c1_stage_moves_file_to_same_relative_inbox_folder(dirs):
    from src.ui import admin
    _put(dirs.archive, DOC)

    rel = admin._stage_for_reingest(DOC)

    assert Path(rel).as_posix() == DOC
    assert (dirs.inbox / DOC).is_file()
    assert not (dirs.inbox / "contract-sample.txt").exists()
    # D1: moved, not copied
    assert not (dirs.archive / DOC).exists()


def test_c1_reingest_keeps_document_in_owner_folder(dirs, monkeypatch):
    from src.ui import admin
    _put(dirs.archive, DOC)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    statuses = _reingest(admin, [DOC])

    assert statuses == ["ok"]
    assert _files(dirs.archive) == {DOC}
    assert not (dirs.archive / "contract-sample.txt").exists()


def test_c1_run_ingestion_with_target_keeps_owner_folder(dirs, monkeypatch):
    """Design 2c: the _run_ingestion(target_path) code path keeps the folder too."""
    from src.ui import admin
    archived = _put(dirs.archive, DOC)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    admin._run_ingestion(str(archived))

    assert _files(dirs.archive) == {DOC}
    assert indexed == [DOC]
    assert not admin._ingestion_lock.locked()


# ---------------------------------------------------------------------------
# Criterion 2: chunks carry alice/sub/contract-sample.txt, alice gets the link
# ---------------------------------------------------------------------------

def test_c2_indexed_file_path_is_archive_relative_and_alice_can_open(dirs, monkeypatch):
    from src.ui import admin
    from src.documents import source_link
    _put(dirs.archive, DOC)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    _reingest(admin, [DOC])

    assert indexed == [DOC]
    assert source_link(indexed[0], "contract-sample.txt", "alice", "LEGAL") == \
        "/documents/alice/sub/contract-sample.txt"
    # Another non-admin user still has no access.
    assert source_link(indexed[0], "contract-sample.txt", "bob", "LEGAL") is None


# ---------------------------------------------------------------------------
# Criterion 3: a root document stays at the root
# ---------------------------------------------------------------------------

def test_c3_root_document_is_staged_at_inbox_root(dirs):
    from src.ui import admin
    _put(dirs.archive, ROOT_DOC)

    rel = admin._stage_for_reingest(ROOT_DOC)

    assert Path(rel).as_posix() == ROOT_DOC
    assert _files(dirs.inbox) == {ROOT_DOC}
    assert not (dirs.archive / ROOT_DOC).exists()


def test_c3_root_document_stays_at_archive_root(dirs, monkeypatch):
    from src.ui import admin
    _put(dirs.archive, ROOT_DOC)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    statuses = _reingest(admin, [ROOT_DOC])

    assert statuses == ["ok"]
    assert _files(dirs.archive) == {ROOT_DOC}
    assert indexed == [ROOT_DOC]


# ---------------------------------------------------------------------------
# Criterion 4: several documents from different folders keep their folders
# ---------------------------------------------------------------------------

def test_c4_multiple_documents_keep_their_own_folders(dirs, monkeypatch):
    from src.ui import admin
    selection = [DOC, DOC_BOB, ROOT_DOC, "carol/notes-sample.txt"]
    for rel in selection:
        _put(dirs.archive, rel, f"synthetic {rel}".encode())
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    statuses = _reingest(admin, selection)

    assert statuses == ["ok"] * len(selection)
    assert _files(dirs.archive) == set(selection)
    assert sorted(indexed) == sorted(selection)
    for rel in selection:
        assert (dirs.archive / rel).read_bytes() == f"synthetic {rel}".encode()


def test_c4_list_documents_separates_same_name_in_different_folders(monkeypatch):
    """D3: one row per file_path, so same-named files are selected separately."""
    from src.ui import admin

    class FakeCollection:
        def get(self, include=None, **kwargs):
            return {
                "ids": ["a1", "a2", "b1", "r1"],
                "metadatas": [
                    {"filename": "contract-sample.txt", "file_path": "alice/sub/contract-sample.txt",
                     "file_hash": "hash-a", "tipo": "txt"},
                    {"filename": "contract-sample.txt", "file_path": "alice/sub/contract-sample.txt",
                     "file_hash": "hash-a", "tipo": "txt"},
                    {"filename": "contract-sample.txt", "file_path": "bob/contract-sample.txt",
                     "file_hash": "hash-b", "tipo": "txt"},
                    {"filename": "contract-root.txt", "file_path": "contract-root.txt",
                     "file_hash": "hash-r", "tipo": "txt"},
                ],
            }

    monkeypatch.setattr(admin, "_get_chroma_collection", lambda: FakeCollection())

    rows = admin._list_documents()
    by_path = {r["file_path"]: r for r in rows}

    assert len(rows) == 3
    assert sorted(by_path["alice/sub/contract-sample.txt"]["chunk_ids"]) == ["a1", "a2"]
    assert by_path["alice/sub/contract-sample.txt"]["chunk_count"] == 2
    assert by_path["bob/contract-sample.txt"]["chunk_ids"] == ["b1"]
    assert by_path["contract-root.txt"]["chunk_ids"] == ["r1"]
    assert all(r["filename"] for r in rows)


# ---------------------------------------------------------------------------
# Criterion 5: exactly one file per document, no _timestamp copy, no stale path
# ---------------------------------------------------------------------------

def test_c5_no_timestamp_duplicate_after_reingest(dirs, monkeypatch):
    from src.ui import admin
    selection = [DOC, ROOT_DOC]
    for rel in selection:
        _put(dirs.archive, rel)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    _reingest(admin, selection)

    files = _files(dirs.archive)
    assert files == set(selection)
    assert not [f for f in files if TIMESTAMP_COPY.search(Path(f).stem)]
    # Every indexed path points to the single archived file.
    for rel in indexed:
        assert (dirs.archive / rel).is_file()
    assert _files(dirs.inbox) == set()


def test_c5_run_ingestion_with_root_target_creates_no_timestamp_copy(dirs, monkeypatch):
    """Today a root re-ingestion collides with the original and adds a _<ts> copy."""
    from src.ui import admin
    archived = _put(dirs.archive, ROOT_DOC)
    indexed: list[str] = []
    monkeypatch.setattr(admin.subprocess, "run", _fake_ingestion(dirs, indexed))

    admin._run_ingestion(str(archived))

    assert _files(dirs.archive) == {ROOT_DOC}
    assert indexed == [ROOT_DOC]


def test_c5_existing_inbox_file_is_never_overwritten(dirs):
    from src.ui import admin
    _put(dirs.archive, DOC, b"synthetic archived")
    _put(dirs.inbox, DOC, b"synthetic waiting")

    with pytest.raises(FileExistsError):
        admin._stage_for_reingest(DOC)

    assert (dirs.archive / DOC).read_bytes() == b"synthetic archived"
    assert (dirs.inbox / DOC).read_bytes() == b"synthetic waiting"


def test_c5_missing_archive_file_is_reported(dirs):
    from src.ui import admin

    with pytest.raises(FileNotFoundError) as exc:
        admin._stage_for_reingest(DOC)

    assert "alice" not in str(exc.value)
    assert _files(dirs.inbox) == set()


def test_c5_settle_ok_when_archived(dirs):
    from src.ui import admin
    _put(dirs.archive, DOC)
    assert admin._settle_after_reingest(Path(DOC)) == "ok"


def test_c5_settle_queued_when_still_in_inbox(dirs):
    from src.ui import admin
    _put(dirs.inbox, DOC)
    assert admin._settle_after_reingest(Path(DOC)) == "queued"
    assert (dirs.inbox / DOC).is_file()


@pytest.mark.parametrize("folder", ["error", "duplicates"])
def test_c5_settle_restores_failed_file_to_owner_folder(dirs, folder):
    """D2: a failed re-ingestion moves the file back to its archive folder."""
    from src.ui import admin
    _put(getattr(dirs, folder), DOC, b"synthetic failed")

    assert admin._settle_after_reingest(Path(DOC)) == "restored"

    assert (dirs.archive / DOC).read_bytes() == b"synthetic failed"
    assert not (getattr(dirs, folder) / DOC).exists()
    assert _files(dirs.archive) == {DOC}


def test_c5_settle_missing_when_file_is_nowhere(dirs):
    from src.ui import admin
    assert admin._settle_after_reingest(Path(DOC)) == "missing"


def test_c5_failed_ingestion_returns_file_to_owner_folder(dirs, monkeypatch):
    """End to end D2: ingest.py sends the staged file to data_error."""
    from src.ui import admin
    from src.utils import sposta_file_con_struttura
    _put(dirs.archive, DOC)

    def failing_run(cmd, **kwargs):
        for f in [p for p in dirs.inbox.rglob("*") if p.is_file()]:
            sposta_file_con_struttura(f, dirs.inbox, dirs.error)
        return SimpleNamespace(stdout="", stderr="synthetic read error")

    monkeypatch.setattr(admin.subprocess, "run", failing_run)

    assert _reingest(admin, [DOC]) == ["restored"]
    assert _files(dirs.archive) == {DOC}
    assert _files(dirs.error) == set()


# ---------------------------------------------------------------------------
# Criterion 6: unsafe paths are rejected, nothing is written outside the folders
# ---------------------------------------------------------------------------

UNSAFE_PATHS = [
    "",
    "../outside/secret-sample.txt",
    "alice/../../outside/secret-sample.txt",
    "..\\outside\\secret-sample.txt",
    "C:/outside/secret-sample.txt",
    "C:secret-sample.txt",
    "/outside/secret-sample.txt",
    "\\outside\\secret-sample.txt",
    ".",
]


@pytest.mark.parametrize("unsafe", UNSAFE_PATHS)
def test_c6_stage_rejects_paths_outside_archive(dirs, unsafe):
    from src.ui import admin
    victim = _put(dirs.outside, "secret-sample.txt", b"synthetic outside")
    _put(dirs.archive, DOC)

    with pytest.raises(ValueError) as exc:
        admin._stage_for_reingest(unsafe)

    # The rejected path must not be echoed back (design 2a).
    if unsafe:
        assert unsafe not in str(exc.value)
    assert victim.read_bytes() == b"synthetic outside"
    assert _files(dirs.inbox) == set()
    assert _files(dirs.archive) == {DOC}
    assert _files(dirs.outside) == {"secret-sample.txt"}


def test_c6_stage_rejects_absolute_path_to_existing_outside_file(dirs):
    from src.ui import admin
    victim = _put(dirs.outside, "secret-sample.txt", b"synthetic outside")

    with pytest.raises(ValueError):
        admin._stage_for_reingest(str(victim))

    assert victim.is_file()
    assert _files(dirs.inbox) == set()


def test_c6_run_ingestion_target_outside_archive_still_rejected(dirs, monkeypatch):
    from src.ui import admin
    victim = _put(dirs.outside, "secret-sample.txt", b"synthetic outside")

    def fail_run(cmd, **kwargs):
        raise AssertionError("ingestion must not start for a rejected path")

    monkeypatch.setattr(admin.subprocess, "run", fail_run)

    with pytest.raises(ValueError, match="Path traversal"):
        admin._run_ingestion(str(victim))

    assert victim.is_file()
    assert _files(dirs.inbox) == set()
    assert not admin._ingestion_lock.locked()


# ---------------------------------------------------------------------------
# Criterion 7: changelog entry
# ---------------------------------------------------------------------------

def test_c7_changelog_unreleased_mentions_reingest_fix():
    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    lines = [l for l in unreleased.splitlines() if l.lstrip().startswith("-")]
    assert any("#46" in l or re.search(r"re-?ingest", l, re.I) for l in lines)
