# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Developer tests for #46 suspicion S2: an unexpected error (not a timeout)
while running ingestion is reported, not raised, so the re-ingest summary still
settles every staged file. Nothing logged or returned contains a path or a
file name.

Synthetic files under tmp_path only.
"""
from pathlib import Path

import pytest

DOCS = ["alice/sub/contract-sample.txt", "contract-root.txt"]
SECRET_PATH = "C:/synthetic/secret-folder/contract-sample.txt"


class _RecordingLog:
    def __init__(self):
        self.lines: list[str] = []

    def _record(self, msg, *args, **kwargs):
        self.lines.append(str(msg))

    info = warning = error = debug = exception = _record


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    from src.ui import admin
    archive = tmp_path / "archive"
    inbox = tmp_path / "inbox"
    error = tmp_path / "error"
    duplicates = tmp_path / "duplicates"
    for p in (archive, inbox, error, duplicates):
        p.mkdir()
    monkeypatch.setattr(admin, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(admin, "INBOX_DIR", inbox)
    monkeypatch.setattr(admin, "ERROR_DIR", error)
    monkeypatch.setattr(admin, "DUPLICATES_DIR", duplicates)
    for rel in DOCS:
        p = archive / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"synthetic content")
    return archive, inbox


@pytest.mark.parametrize("exc", [
    OSError(2, "synthetic start failure", SECRET_PATH),
    RuntimeError(f"synthetic failure on {SECRET_PATH}"),
])
def test_unexpected_run_error_is_reported_and_files_are_settled(dirs, monkeypatch, exc):
    from src.ui import admin
    archive, inbox = dirs
    log = _RecordingLog()
    monkeypatch.setattr(admin, "slog", log)

    def broken_run(cmd, **kwargs):
        raise exc

    monkeypatch.setattr(admin.subprocess, "run", broken_run)

    staged = [admin._stage_for_reingest(r) for r in DOCS]
    output = admin._run_ingestion()
    statuses = [admin._settle_after_reingest(Path(s)) for s in staged]

    assert "data_nuovi" in output
    assert statuses == ["queued", "queued"]
    for rel in DOCS:
        assert (inbox / rel).is_file()
    assert not admin._ingestion_lock.locked()
    assert log.lines, "the failure must be logged"
    for text in [output, *log.lines]:
        assert "secret-folder" not in text
        assert "contract" not in text
        assert "alice" not in text


def test_timeout_is_still_reported_as_timeout(dirs, monkeypatch):
    import subprocess
    from src.ui import admin

    def slow_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(admin.subprocess, "run", slow_run)

    assert "timeout" in admin._run_ingestion()
    assert not admin._ingestion_lock.locked()
