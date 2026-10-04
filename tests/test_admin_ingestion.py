# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import subprocess
import sys
from types import SimpleNamespace


def test_run_ingestion_returns_output(monkeypatch):
    from src.ui import admin
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout="done", stderr="")

    monkeypatch.setattr(admin.subprocess, "run", fake_run)
    assert admin._run_ingestion() == "done"
    assert calls[0]["timeout"] == admin.INGEST_TIMEOUT_SEC
    if sys.platform == "win32":
        assert calls[0]["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS


def test_run_ingestion_handles_timeout(monkeypatch):
    from src.ui import admin

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output=b"partial log")

    monkeypatch.setattr(admin.subprocess, "run", fake_run)
    out = admin._run_ingestion()
    assert "timeout" in out
    assert "partial log" in out
    assert not admin._ingestion_lock.locked()


def test_run_ingestion_rejects_concurrent_run(monkeypatch):
    from src.ui import admin

    def fail_run(cmd, **kwargs):
        raise AssertionError("subprocess must not start while another ingestion runs")

    monkeypatch.setattr(admin.subprocess, "run", fail_run)
    admin._ingestion_lock.acquire()
    try:
        assert "already running" in admin._run_ingestion()
    finally:
        admin._ingestion_lock.release()


def test_run_ingestion_releases_lock_on_path_traversal(tmp_path):
    import pytest
    from src.ui import admin

    with pytest.raises(ValueError, match="Path traversal"):
        admin._run_ingestion(str(tmp_path / "outside.pdf"))
    assert not admin._ingestion_lock.locked()
