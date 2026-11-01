# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Developer tests for the owner decision on #78: 'Ingestion complete.' only after
a run that finished. Outputs come from the real _run_ingestion where that is possible,
so a reworded message there cannot silently bring the green notification back.
Only tmp_path locks and synthetic text.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.ui import admin

COMPLETE = ("Ingestion complete.", "positive")


@pytest.fixture
def run_with(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "_INGEST_LOCK_FILE", tmp_path / "ingestion.lock")
    monkeypatch.setattr(admin, "INGEST_TIMEOUT_SEC", 1)

    def _run_with(child):
        monkeypatch.setattr(admin.subprocess, "run", child)
        return admin._run_ingestion()

    return _run_with


def _assert_not_complete(notification, kind):
    text, notify_type = notification
    assert notify_type == kind
    assert "complete" not in text.lower()


def test_timeout_does_not_notify_complete(run_with):
    def child(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1, output="synthetic partial output")

    _assert_not_complete(admin._ingestion_notification(run_with(child)), "negative")


def test_failed_start_does_not_notify_complete(run_with):
    def child(cmd, **kwargs):
        raise OSError("synthetic start failure")

    _assert_not_complete(admin._ingestion_notification(run_with(child)), "negative")


def test_child_refused_in_the_gap_after_the_probe_does_not_notify_complete(run_with):
    def child(cmd, **kwargs):
        return SimpleNamespace(stdout="Ingestion already running (Lock file present). Exiting.", stderr="")

    _assert_not_complete(admin._ingestion_notification(run_with(child)), "warning")


def test_refusal_by_the_probe_does_not_notify_complete():
    _assert_not_complete(admin._ingestion_notification(admin._ALREADY_RUNNING), "warning")


def test_crash_logged_by_the_child_does_not_notify_complete(run_with):
    def child(cmd, **kwargs):
        return SimpleNamespace(stdout="CRASH MAIN: RuntimeError", stderr="")

    _assert_not_complete(admin._ingestion_notification(run_with(child)), "negative")


def test_finished_run_notifies_complete(run_with):
    def child(cmd, **kwargs):
        return SimpleNamespace(stdout="synthetic run done", stderr="")

    assert admin._ingestion_notification(run_with(child)) == COMPLETE


def test_child_lines_match_what_ingest_py_logs():
    # The two markers are copied from log lines of ingest.py; a reworded line there
    # would bring the green notification back.
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent / "ingest.py").read_text(encoding="utf-8")
    assert admin._CHILD_REFUSED_LINE in source
    assert admin._CHILD_CRASHED_LINE in source
