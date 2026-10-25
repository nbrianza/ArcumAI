# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Issue #39, tester finding S2 from PR #58: a document that disappears between
the existence check and the send must not put its path (and so the client file
name) into the error that the server logs.

Starlette raises ``RuntimeError("File at path <absolute path> does not exist.")``
or an ``OSError`` quoting the path; uvicorn logs it as an ERROR traceback. The
route must replace it with an error that carries no path, and log the original
text only through the LOG_CLIENT_CONTENT switch.

All files are synthetic and live under ``tmp_path``. The users file is never read
(the ``current_user`` dependency is overridden).
"""
import logging
import traceback

import pytest

import src.logger as logger_module

MARKER = "ZZ-SYNTHETIC-MARKER-123"
LEAK_FRAGMENT = "synthetic-marker"


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _record_text(record: logging.LogRecord) -> str:
    parts = [record.getMessage()]
    if record.exc_info:
        parts.append(logging.Formatter().formatException(record.exc_info))
    return " ".join(parts)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import src.documents as docs
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    archive = tmp_path / "archive"
    (archive / "zzowner").mkdir(parents=True)
    monkeypatch.setattr(docs, "ARCHIVE_DIR", archive)

    handler = _ListHandler()
    server_logger = logging.getLogger("ArcumServer")
    monkeypatch.setattr(server_logger, "handlers", [handler])
    old_level = server_logger.level
    server_logger.setLevel(logging.DEBUG)

    app = FastAPI()
    app.include_router(docs.router)
    app.dependency_overrides[docs.current_user] = lambda: {"username": "zzowner", "role": "ADMIN"}
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield archive, client, handler.records
    finally:
        server_logger.setLevel(old_level)


def _formatted(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def test_s2_file_removed_before_send_error_has_no_path(setup, monkeypatch):
    """Race: the file passes is_file() but is gone when Starlette stats it."""
    archive, client, records = setup
    from pathlib import Path
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: True if MARKER in str(self) else real_is_file(self))

    with pytest.raises(Exception) as excinfo:
        client.get(f"/documents/zzowner/{MARKER}.pdf")

    assert LEAK_FRAGMENT not in _formatted(excinfo.value).lower(), "server error quotes the document path"
    leaks = [r.getMessage() for r in records if LEAK_FRAGMENT in _record_text(r).lower()]
    assert not leaks, f"document path in log records: {leaks}"
    assert any(r.levelno == logging.ERROR and "Document send failed" in r.getMessage() for r in records)


def test_s2_file_removed_before_open_error_has_no_path(setup, monkeypatch):
    """Race: the stat succeeds, then opening the file fails with an OSError quoting the path."""
    archive, client, records = setup
    target = archive / "zzowner" / f"{MARKER}.pdf"
    target.write_bytes(b"%PDF-1.4 synthetic")

    import starlette.responses as responses

    async def _gone(path, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(responses.anyio, "open_file", _gone)

    with pytest.raises(Exception) as excinfo:
        client.get(f"/documents/zzowner/{MARKER}.pdf")

    assert LEAK_FRAGMENT not in _formatted(excinfo.value).lower(), "server error quotes the document path"
    leaks = [r.getMessage() for r in records if LEAK_FRAGMENT in _record_text(r).lower()]
    assert not leaks, f"document path in log records: {leaks}"


def test_s2_switch_on_logs_the_original_error_text(setup, monkeypatch):
    archive, client, records = setup
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True)
    from pathlib import Path
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: True if MARKER in str(self) else real_is_file(self))

    with pytest.raises(Exception):
        client.get(f"/documents/zzowner/{MARKER}.pdf")

    assert any(MARKER in r.getMessage() for r in records if r.levelno == logging.ERROR)


def test_s2_normal_download_still_works(setup):
    archive, client, records = setup
    (archive / "zzowner" / "note.txt").write_bytes(b"synthetic note")
    response = client.get("/documents/zzowner/note.txt")
    assert response.status_code == 200
    assert response.content == b"synthetic note"
