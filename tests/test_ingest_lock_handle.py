# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Developer tests for #78: the lock handle must not reach a child of the holder.

test_s4 in test_ingest_stale_lock.py passes even with an inheritable descriptor,
because the byte-range lock does not follow the handle into a child. An inherited
handle still keeps the file open, so the removal at the end of the next run fails.
That removal is what these tests look at. Only tmp_path locks and synthetic content.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.ingest_lock import release, try_acquire

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the kernel lock of #78 is Windows only (design D6)")

REPO_ROOT = Path(__file__).resolve().parent.parent

# argv: repo lock ready inheritable
_HOLDER_WITH_GRANDCHILD = r"""
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from src.ingest_lock import try_acquire
lock, ready, inheritable = sys.argv[2:5]
held = try_acquire(Path(lock))
if inheritable == "yes":
    os.set_inheritable(held.fd, True)
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    close_fds=False,
)
tmp = Path(ready + ".tmp")
tmp.write_text(json.dumps({"pid": os.getpid(), "grandchild": grandchild.pid}), encoding="utf-8")
os.replace(tmp, ready)
time.sleep(60)
"""


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def _removal_after_killed_holder(tmp_path: Path, inheritable: str) -> str | None:
    """A holder starts a grandchild and is killed; the next run takes and releases the lock."""
    lock, ready = tmp_path / "ingestion.lock", tmp_path / "ready.json"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_WITH_GRANDCHILD, str(REPO_ROOT), str(lock), str(ready), inheritable],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pids: dict = {}
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert time.monotonic() < deadline, "the holder did not start in time"
            assert holder.poll() is None, f"the holder exited with {holder.returncode}"
            time.sleep(0.01)
        pids = json.loads(ready.read_text(encoding="utf-8"))
        # Without /T: the grandchild must outlive the holder. On a venv Popen.pid is
        # only the launcher, so the process that holds the lock is killed by its pid.
        subprocess.run(["taskkill", "/F", "/PID", str(pids["pid"])], capture_output=True)
        holder.wait(timeout=30)
        held = try_acquire(lock)
        assert held is not None, "the lock of a killed holder was not recovered"
        return release(held)
    finally:
        for pid in (pids.get("grandchild"), holder.pid):
            if pid:
                _kill_tree(pid)
        holder.wait(timeout=30)


def test_lock_descriptor_is_not_inheritable(tmp_path):
    held = try_acquire(tmp_path / "ingestion.lock")
    try:
        assert os.get_inheritable(held.fd) is False
    finally:
        release(held)


def test_surviving_grandchild_does_not_keep_the_file_open(tmp_path):
    assert _removal_after_killed_holder(tmp_path, "no") is None
    assert not (tmp_path / "ingestion.lock").exists()


def test_control_an_inherited_handle_would_block_the_removal(tmp_path):
    # Shows that the test above can fail: same scenario with the descriptor made
    # inheritable on purpose.
    assert _removal_after_killed_holder(tmp_path, "yes") == "PermissionError"
    assert (tmp_path / "ingestion.lock").exists()
