# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Developer tests for #46 suspicion S1: moving a staged file back to the
archive after a failed chunk deletion (_unstage_after_failed_delete).

Synthetic files under tmp_path only.
"""
from pathlib import Path

import pytest

DOC = "alice/sub/contract-sample.txt"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    from src.ui import admin
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    archive.mkdir()
    inbox.mkdir()
    monkeypatch.setattr(admin, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(admin, "INBOX_DIR", inbox)
    return archive, inbox


def _put(root: Path, rel: str, content: bytes = b"synthetic content") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_unstage_moves_file_back_to_its_archive_folder(dirs):
    from src.ui import admin
    archive, inbox = dirs
    _put(archive, DOC, b"synthetic staged")
    rel = admin._stage_for_reingest(DOC)

    assert admin._unstage_after_failed_delete(rel) is True

    assert (archive / DOC).read_bytes() == b"synthetic staged"
    assert not (inbox / DOC).exists()


def test_unstage_reports_failure_when_move_raises(dirs, monkeypatch):
    from src.ui import admin
    archive, inbox = dirs
    _put(inbox, DOC)

    def locked_move(src, dst):
        raise PermissionError("synthetic lock")

    monkeypatch.setattr(admin.shutil, "move", locked_move)

    assert admin._unstage_after_failed_delete(Path(DOC)) is False
    assert (inbox / DOC).is_file()
    assert not (archive / DOC).exists()


def test_unstage_never_overwrites_an_archived_file(dirs):
    from src.ui import admin
    archive, inbox = dirs
    _put(inbox, DOC, b"synthetic staged")
    _put(archive, DOC, b"synthetic archived")

    assert admin._unstage_after_failed_delete(Path(DOC)) is False
    assert (archive / DOC).read_bytes() == b"synthetic archived"
    assert (inbox / DOC).read_bytes() == b"synthetic staged"


def test_unstage_reports_failure_when_staged_file_is_gone(dirs):
    from src.ui import admin
    assert admin._unstage_after_failed_delete(Path(DOC)) is False
