# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for #78: a stale ingestion lock is recovered, a live one is not.

Approved design (architect comment on #78, owner decisions D1 to D9 as recommended):
``src/ingest_lock.py`` (standard library only) offers ``try_acquire(path)``,
``is_held(path)`` and ``release(held)``. The holder keeps the file open with an
exclusive ``msvcrt.locking`` byte-range lock for the whole run. A file with no
kernel holder is stale, whatever it contains, and is taken over without being
deleted or parsed.

Group S (``test_s*``) checks the Windows behaviour the design only inferred, with
real child processes, real kills and real concurrent starts. If group S fails on
Windows, the design goes back to the owner (design, Risks).

Test contract beyond the design text, named here so the developer can see it:
``src.ui.admin._ingestion_notification(output) -> tuple[str, str]`` is the small
pure function the design's test plan asks for (criterion 9): it returns the
notification text and the NiceGUI notification type.

How the evidence is gathered:
- Every lock path is under tmp_path. The real ingestion.lock, chroma_db/ and
  storage_bm25/ are never touched, and only synthetic content is used.
- The child processes import only ``src.ingest_lock``, so they start quickly.
- ``.venv\\Scripts\\python.exe`` on Windows is a launcher that starts the real
  interpreter as a separate process: ``Popen.pid`` can differ from the process
  that holds the lock, and ``Popen.kill`` then hits the launcher. CI uses a plain
  interpreter with no launcher. Kill tests therefore run both ways, and a holder
  reports its own process id.
- Real ``ingest.py`` as a child needs ``init_settings()`` and models, so it is not
  run as a child (design, Test impact). ``ingest.main()`` runs in this process and
  ``_run_ingestion`` starts a small fake ``ingest.py`` that uses the helper.

Not tested here: criterion 10 and the Delete half of criterion 15 (moved to the
#73 test list by the owner's decision D4), real power loss, and non-Windows (D6).
"""
from __future__ import annotations

import ast
import ctypes
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the kernel lock of #78 is Windows only (design D6)")

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER = "ZZ-SYNTHETIC-LOCK-MARKER"
REFUSAL_TEXT = "An ingestion is already running. Try again when it has finished."
PID_TEXT = re.compile(r"LOCKED by PID (\d+)")
# Kept because tests replace subprocess.run through admin.subprocess, which is the
# same module object; the helpers below must keep working during those tests.
_real_run = subprocess.run
KILL_WAYS = ("popen_kill", "kill_real_pid")
CHILD_START_TIMEOUT_SEC = 60


# ---------------------------------------------------------------------------
# Child processes
# ---------------------------------------------------------------------------

_CHILD_PRELUDE = r"""
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from src.ingest_lock import release, try_acquire

def signal(path, payload):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    for _ in range(200):  # a virus scanner can hold the new file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.01)
    os.replace(tmp, path)

def wait_for(path, seconds=120):
    deadline = time.monotonic() + seconds
    while not Path(path).exists() and time.monotonic() < deadline:
        time.sleep(0.002)
"""

# argv: repo lock ready stop grandchild_mode. Exits by itself at the latest after
# the wait_for deadline, so no process outlives a crashed test run for long.
_HOLDER = _CHILD_PRELUDE + r"""
lock, ready, stop, mode = sys.argv[2:6]
held = try_acquire(Path(lock))
if held is None:
    signal(ready, {"state": "REFUSED", "pid": os.getpid()})
    sys.exit(3)
grandchild = None
if mode != "none":
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(90)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=(mode == "close_fds"),
    ).pid
signal(ready, {"state": "HELD", "pid": os.getpid(), "grandchild": grandchild})
wait_for(stop)
sys.exit(0 if release(held) is None else 4)
"""

# argv: repo lock armed go outcome stop
_RACER = _CHILD_PRELUDE + r"""
lock, armed, go, outcome, stop = sys.argv[2:7]
signal(armed, {"state": "ARMED"})
wait_for(go)
held = try_acquire(Path(lock))
if held is None:
    signal(outcome, {"state": "REFUSED"})
    sys.exit(0)
signal(outcome, {"state": "HELD"})
wait_for(stop)
release(held)
"""

# argv: repo lock marker seconds. The O_EXCL marker exists only while a process
# believes it holds the lock, so a create failure means two holders at once.
_CHURN = _CHILD_PRELUDE + r"""
lock, marker, seconds = sys.argv[2:5]
end = time.monotonic() + float(seconds)
acquired = overlaps = 0
while time.monotonic() < end:
    held = try_acquire(Path(lock))
    if held is None:
        continue
    acquired += 1
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        os.unlink(marker)
    except OSError:
        overlaps += 1
    release(held)
print(json.dumps({"acquired": acquired, "overlaps": overlaps}))
"""

# argv: holder_script repo lock ready stop. Stands for the web server: it starts a
# holder child, then waits to be killed.
_SERVER = r"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], *sys.argv[2:6], "none"],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(120)
"""


def _pid_alive(pid: int) -> bool:
    out = _real_run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out.split()


def _kill_pid(pid: int) -> None:
    _real_run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


class _ChildLab:
    """Starts child processes under tmp_path and cleans all of them up."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(exist_ok=True)
        self.count = 0
        self.popens: list[subprocess.Popen] = []
        self.pids: list[int] = []
        self.stops: list[Path] = []

    def _slot(self) -> Path:
        self.count += 1
        slot = self.root / f"child{self.count}"
        slot.mkdir()
        return slot

    def _start(self, script: str, args, slot: Path, **kwargs) -> subprocess.Popen:
        with open(slot / "stderr.txt", "wb") as err:
            popen = subprocess.Popen(
                [sys.executable, "-c", script, *[str(a) for a in args]],
                stdin=subprocess.DEVNULL, stderr=err, cwd=str(slot),
                stdout=kwargs.pop("stdout", subprocess.DEVNULL), **kwargs,
            )
        self.popens.append(popen)
        return popen

    @staticmethod
    def wait_signal(path: Path, popen: subprocess.Popen, slot: Path) -> dict:
        deadline = time.monotonic() + CHILD_START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except PermissionError:
                    # The signal file itself, not the lock: it can be busy for a
                    # moment just after the rename, seen under CPU load.
                    time.sleep(0.01)
                    continue
            if popen.poll() is not None and not path.exists():
                err = (slot / "stderr.txt").read_text(encoding="utf-8", errors="replace")
                raise AssertionError(f"child exited with {popen.returncode} before signalling: {err[-800:]}")
            time.sleep(0.005)
        raise AssertionError("child did not signal in time")

    def holder(self, lock: Path, grandchild: str = "none") -> SimpleNamespace:
        slot = self._slot()
        ready, stop = slot / "ready.json", slot / "stop"
        self.stops.append(stop)
        popen = self._start(_HOLDER, [REPO_ROOT, lock, ready, stop, grandchild], slot)
        state = self.wait_signal(ready, popen, slot)
        assert state["state"] == "HELD", f"the holder child was refused: {state}"
        self.pids.append(state["pid"])
        if state.get("grandchild"):
            self.pids.append(state["grandchild"])
        return SimpleNamespace(popen=popen, pid=state["pid"], stop=stop, grandchild=state.get("grandchild"))

    def kill(self, holder: SimpleNamespace, how: str = "popen_kill") -> None:
        if how == "popen_kill":
            holder.popen.kill()
        else:
            _kill_pid(holder.pid)
        holder.popen.wait(timeout=30)

    def finish(self, holder: SimpleNamespace) -> int:
        """Lets a holder release normally and returns its exit code."""
        holder.stop.write_bytes(b"")
        return holder.popen.wait(timeout=30)

    def unrelated_process(self) -> SimpleNamespace:
        slot = self._slot()
        popen = self._start("import time; time.sleep(90)", [], slot)
        self.pids.append(popen.pid)
        return SimpleNamespace(popen=popen, pid=popen.pid)

    def server_with_holder_child(self, lock: Path) -> SimpleNamespace:
        slot = self._slot()
        ready, stop = slot / "ready.json", slot / "stop"
        self.stops.append(stop)
        server = self._start(_SERVER, [_HOLDER, REPO_ROOT, lock, ready, stop], slot)
        state = self.wait_signal(ready, server, slot)
        assert state["state"] == "HELD", f"the holder child was refused: {state}"
        self.pids.append(state["pid"])
        return SimpleNamespace(popen=server, child_pid=state["pid"], stop=stop)

    def racers(self, lock: Path, count: int = 2) -> list[str]:
        """Starts racers together; returns outcomes while any winner still holds."""
        slots = [self._slot() for _ in range(count)]
        go, stop = self.root / f"go{self.count}", self.root / f"stop{self.count}"
        self.stops.append(stop)
        popens = [self._start(_RACER, [REPO_ROOT, lock, s / "armed.json", go, s / "outcome.json", stop], s)
                  for s in slots]
        for s, p in zip(slots, popens):
            self.wait_signal(s / "armed.json", p, s)
        go.write_bytes(b"")
        outcomes = [self.wait_signal(s / "outcome.json", p, s)["state"] for s, p in zip(slots, popens)]
        stop.write_bytes(b"")
        for p in popens:
            assert p.wait(timeout=30) == 0
        return outcomes

    def churn(self, lock: Path, marker: Path, processes: int, seconds: float) -> list[dict]:
        pairs = []
        for _ in range(processes):
            slot = self._slot()
            pairs.append((self._start(_CHURN, [REPO_ROOT, lock, marker, seconds], slot,
                                      stdout=subprocess.PIPE, text=True), slot))
        results = []
        for popen, slot in pairs:
            out, _ = popen.communicate(timeout=seconds + 60)
            assert popen.returncode == 0, (slot / "stderr.txt").read_text(encoding="utf-8", errors="replace")[-800:]
            results.append(json.loads(out))
        return results

    def close(self) -> None:
        for stop in self.stops:
            stop.parent.mkdir(exist_ok=True)
            stop.write_bytes(b"")
        for popen in self.popens:
            if popen.poll() is None:
                try:
                    popen.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_pid(popen.pid)
        for pid in self.pids:
            if _pid_alive(pid):
                _kill_pid(pid)
        for popen in self.popens:
            if popen.poll() is None:
                popen.kill()
            popen.wait(timeout=30)
            if popen.stdout:
                popen.stdout.close()


@pytest.fixture
def lab(tmp_path):
    children = _ChildLab(tmp_path / "children")
    yield children
    children.close()


# ---------------------------------------------------------------------------
# Lock helper and scanner helpers
# ---------------------------------------------------------------------------

def _lock_api():
    """Imported inside each test, so a missing module fails the test, not setup."""
    return importlib.import_module("src.ingest_lock")


def _release_quietly(held) -> None:
    if held is not None:
        _lock_api().release(held)


def _stale(lock: Path, data: bytes = b"LOCKED by PID 1") -> Path:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(data)
    return lock


def _left_by_killed_run(lab: _ChildLab, lock: Path, how: str = "popen_kill") -> Path:
    lab.kill(lab.holder(lock), how)
    assert lock.exists(), "the hard kill was expected to leave the lock file behind"
    return lock


def _overwrite(lock: Path, data: bytes) -> None:
    """Changes the content without closing or replacing the holder's file."""
    with open(lock, "r+b") as f:
        f.write(data)
        f.truncate()


class _ExclusiveOpen:
    """A scanner that opens the file with no sharing at all (CreateFileW, share 0)."""

    def __init__(self, path: Path):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32 = kernel32
        generic_read, open_existing = 0x80000000, 3
        self._handle = kernel32.CreateFileW(str(path), generic_read, 0, None, open_existing, 0, None)
        invalid = wintypes.HANDLE(-1).value
        assert self._handle != invalid, f"exclusive open failed: {ctypes.get_last_error()}"

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _patch_unlink(monkeypatch, lock: Path, behaviour) -> list:
    """Replaces removal of the lock file only; every other path works as usual."""
    calls: list[str] = []
    real_unlink, real_remove = os.unlink, os.remove

    def wrap(real):
        def replacement(path, *args, **kwargs):
            if Path(path) == lock:
                calls.append(str(path))
                return behaviour(lambda: real(path, *args, **kwargs))
            return real(path, *args, **kwargs)
        return replacement

    monkeypatch.setattr(os, "unlink", wrap(real_unlink))
    monkeypatch.setattr(os, "remove", wrap(real_remove))
    return calls


# ---------------------------------------------------------------------------
# ingest.py in this process, on synthetic folders
# ---------------------------------------------------------------------------

class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def lines(self) -> list[str]:
        return [r.getMessage() for r in self.records]

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == logging.WARNING]


class _EmptyCollection:
    def get(self, where=None, limit=None, include=None):
        return {"ids": [], "documents": [], "metadatas": []}

    def count(self):
        return 0


def _get_ingest():
    if "ingest" not in sys.modules:
        with patch("src.config.init_settings"):
            importlib.import_module("ingest")
    return sys.modules["ingest"]


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    """ingest.py wired to synthetic folders, fake stores and an in-memory log."""
    from llama_index.core import Settings
    from llama_index.core.node_parser import SentenceSplitter

    ingest = _get_ingest()
    root = tmp_path / f"{MARKER} stores"
    env = SimpleNamespace(
        module=ingest,
        root=root,
        lock=root / "ingestion.lock",
        inbox=root / "inbox",
        archive=root / "archive",
        bm25=root / "bm25",
        index=MagicMock(),
        db_opened=[],
        nodes_loaded=[],
        during_run=None,
    )
    root.mkdir()
    for name, value in {
        "INBOX_DIR": env.inbox, "ARCHIVE_DIR": env.archive, "ERROR_DIR": root / "errors",
        "DUPLICATES_DIR": root / "duplicates", "BM25_PATH": env.bm25, "LOCK_FILE": env.lock,
    }.items():
        monkeypatch.setattr(ingest, name, value)

    def get_db_components():
        env.db_opened.append(True)
        if env.during_run is not None:
            env.during_run()
        return env.index, _EmptyCollection()

    def load_nodes(_collection):
        env.nodes_loaded.append(True)
        return []

    monkeypatch.setattr(ingest, "get_db_components", get_db_components)
    monkeypatch.setattr(ingest, "get_all_nodes_from_chroma", load_nodes)
    monkeypatch.setattr(sys, "argv", ["ingest.py"])
    if Settings.text_splitter is None:
        Settings.text_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

    logger = logging.getLogger("ArcumIngestion")
    records = _Records()
    level = logger.level
    logger.setLevel(logging.DEBUG)
    monkeypatch.setattr(logger, "handlers", [records])
    env.records = records
    yield env
    logger.setLevel(level)
    if getattr(ingest, "_held_lock", None) is not None:
        ingest.release_lock()


def _add_inbox_file(env) -> Path:
    env.inbox.mkdir(parents=True, exist_ok=True)
    path = env.inbox / f"{MARKER} note.txt"
    path.write_text("Synthetic note for the stale lock tests.", encoding="utf-8")
    return path


def _assert_ingest_ran_and_released(env, inbox_file: Path) -> None:
    assert env.index.insert_nodes.called, "the ingestion did not write to the store"
    assert not inbox_file.exists(), "the inbox was not processed"
    assert (env.archive / inbox_file.name).is_file(), "the file was not archived"
    assert not env.lock.exists(), "the lock must be removed at the end of the run"


def _assert_no_path_in(lines, root: Path) -> None:
    for line in lines:
        for token in (MARKER, str(root), root.name, "ingestion.lock", ".lock", ":\\"):
            assert token not in line, f"log line contains {token!r}: {line!r}"


def _switch(monkeypatch, value: bool) -> None:
    import src.logger as logger_module
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", value)


# ---------------------------------------------------------------------------
# Admin page helper with a fake ingest.py
# ---------------------------------------------------------------------------

_FAKE_INGEST = r"""
import shutil, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
sys.path.insert(0, {repo!r})
from src.ingest_lock import release, try_acquire
held = try_acquire(here / "ingestion.lock")
if held is None:
    print("synthetic child refused")
    sys.exit(0)
with open(here / "runs.txt", "a", encoding="utf-8") as f:
    f.write("run\n")
if (here / "sleep_forever").exists():
    (here / "started").write_text("started", encoding="utf-8")
    time.sleep(120)
for item in sorted(p for p in (here / "inbox").rglob("*") if p.is_file()):
    target = here / "archive" / item.relative_to(here / "inbox")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(item), str(target))
release(held)
print("synthetic run done")
"""


class _RecordingLog:
    def __init__(self):
        self.lines: list[str] = []

    def _record(self, msg, *args, **kwargs):
        self.lines.append(str(msg))

    info = warning = error = debug = exception = critical = _record


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A temporary BASE_DIR with a fake ingest.py that uses the lock helper."""
    from src.ui import admin

    base = tmp_path / f"{MARKER} base"
    inbox, archive = base / "inbox", base / "archive"
    for p in (base, inbox, archive, base / "errors", base / "duplicates"):
        p.mkdir()
    (base / "ingest.py").write_text(_FAKE_INGEST.format(repo=str(REPO_ROOT)), encoding="utf-8")
    log = _RecordingLog()
    monkeypatch.setattr(admin, "BASE_DIR", base)
    monkeypatch.setattr(admin, "_INGEST_LOCK_FILE", base / "ingestion.lock", raising=False)
    monkeypatch.setattr(admin, "INBOX_DIR", inbox)
    monkeypatch.setattr(admin, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(admin, "ERROR_DIR", base / "errors")
    monkeypatch.setattr(admin, "DUPLICATES_DIR", base / "duplicates")
    monkeypatch.setattr(admin, "slog", log)

    def runs() -> int:
        path = base / "runs.txt"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    return SimpleNamespace(admin=admin, base=base, lock=base / "ingestion.lock", inbox=inbox,
                           archive=archive, log=log, runs=runs)


def _no_child_allowed(monkeypatch, admin) -> list:
    started: list = []

    def recording_run(cmd, **kwargs):
        started.append(cmd)
        return SimpleNamespace(stdout="a child ran", stderr="")

    monkeypatch.setattr(admin.subprocess, "run", recording_run)
    return started


# ---------------------------------------------------------------------------
# Group S: the Windows behaviour the design inferred
# ---------------------------------------------------------------------------

def test_s1_live_child_holder_refuses_try_acquire_and_is_held_is_true(tmp_path, lab):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    lab.holder(lock)
    assert api.try_acquire(lock) is None
    assert api.is_held(lock) is True
    assert lock.exists()


def test_s1_file_of_a_live_holder_cannot_be_deleted(tmp_path, lab):
    lock = tmp_path / "ingestion.lock"
    holder = lab.holder(lock)
    with pytest.raises(PermissionError):
        lock.unlink()
    assert lock.exists()
    assert holder.popen.poll() is None, "the holder must not be affected"


def test_s1_live_holder_is_refused_every_time_and_within_the_retry_budget(tmp_path, lab):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    lab.holder(lock)
    for _ in range(5):
        started = time.monotonic()
        assert api.try_acquire(lock) is None
        assert time.monotonic() - started < 3, "a refusal must take well under the design's bound"


@pytest.mark.parametrize("how", KILL_WAYS)
def test_s2_killed_holder_lock_is_taken_over_within_retries(tmp_path, lab, how):
    api = _lock_api()
    for round_no in range(5):
        lock = _left_by_killed_run(lab, tmp_path / f"ingestion{round_no}.lock", how)
        held = api.try_acquire(lock)
        assert held is not None, f"the lock of a killed holder was not recovered in round {round_no}"
        assert isinstance(held.stale_age_sec, int), "a recovered lock must report its age"
        assert held.stale_age_sec >= 0
        api.release(held)
        assert not lock.exists()


def test_s2_is_held_is_false_after_the_holder_is_killed(tmp_path, lab):
    api = _lock_api()
    lock = _left_by_killed_run(lab, tmp_path / "ingestion.lock")
    deadline = time.monotonic() + 1
    while api.is_held(lock) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert api.is_held(lock) is False
    assert lock.exists(), "is_held never removes anything"


def test_s3_acquire_in_one_thread_and_release_in_another(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    box: dict = {}

    def acquire():
        box["held"] = api.try_acquire(lock)

    def do_release():
        box["result"] = api.release(box["held"])

    for target in (acquire, do_release):
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
    assert box["held"] is not None
    assert box["result"] is None
    assert not lock.exists()


@pytest.mark.parametrize("how", KILL_WAYS)
@pytest.mark.parametrize("grandchild", ["close_fds", "inherit_handles"])
def test_s4_lock_is_free_while_a_grandchild_outlives_the_killed_holder(tmp_path, lab, how, grandchild):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    holder = lab.holder(lock, grandchild=grandchild)
    if how == "kill_real_pid":
        # /T would take the grandchild too, which is the case under test.
        _real_run(["taskkill", "/F", "/PID", str(holder.pid)], capture_output=True)
        holder.popen.wait(timeout=30)
    else:
        lab.kill(holder, how)
    assert _pid_alive(holder.grandchild), "the scenario needs the grandchild alive"
    held = api.try_acquire(lock)
    assert held is not None, "a surviving grandchild keeps the lock of a killed holder"
    api.release(held)


def test_s5_pid_text_is_readable_by_another_handle_while_held(tmp_path, lab):
    lock = tmp_path / "ingestion.lock"
    holder = lab.holder(lock)
    assert lock.read_text(encoding="ascii") == f"LOCKED by PID {holder.pid}"


def test_s5_refused_contender_does_not_change_the_holder_text(tmp_path, lab):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    holder = lab.holder(lock)
    assert api.try_acquire(lock) is None
    api.is_held(lock)
    assert lock.read_text(encoding="ascii") == f"LOCKED by PID {holder.pid}"


def test_s6_stale_file_is_taken_over_without_being_deleted(tmp_path):
    # Design principle: nothing is deleted to recover, so there is no read, check,
    # delete, create race.
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock")
    file_id = lock.stat().st_ino
    held = api.try_acquire(lock)
    assert held is not None
    assert lock.stat().st_ino == file_id, "recovery replaced the file instead of locking it"
    api.release(held)


def test_s7_release_gap_takeover_keeps_the_lock_of_the_new_run(tmp_path, monkeypatch, lab):
    # Design, release: a run that takes the lock between close and unlink keeps it.
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    first = api.try_acquire(lock)
    box: dict = {}

    def takeover_then_unlink(real):
        box.setdefault("second", api.try_acquire(lock))
        return real()

    _patch_unlink(monkeypatch, lock, takeover_then_unlink)
    assert api.release(first) == "PermissionError"
    monkeypatch.undo()
    assert box["second"] is not None, "the second run could not take the lock in the gap"
    assert lock.exists()
    assert api.is_held(lock) is True, "the first run removed the lock of the second run"
    api.release(box["second"])
    assert not lock.exists()


# ---------------------------------------------------------------------------
# Helper contract (design, point 1; D1, D2, D8)
# ---------------------------------------------------------------------------

def test_d2_helper_uses_the_standard_library_only_and_does_not_log():
    tree = ast.parse((REPO_ROOT / "src" / "ingest_lock.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: child processes must start fast"
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names), f"not standard library: {imported - set(sys.stdlib_module_names)}"
    assert "logging" not in imported, "the helper does not log; ingest.py writes the log lines"


def test_helper_fresh_lock_reports_no_stale_age_and_writes_only_a_pid(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    held = api.try_acquire(lock)
    assert held is not None
    assert held.stale_age_sec is None
    assert held.path == lock
    assert isinstance(held.fd, int)
    assert lock.read_text(encoding="ascii") == f"LOCKED by PID {os.getpid()}"
    assert api.release(held) is None
    assert not lock.exists()


def test_helper_recovered_lock_reports_the_age_of_the_file(tmp_path):
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock")
    past = time.time() - 100
    os.utime(lock, (past, past))
    held = api.try_acquire(lock)
    assert held is not None
    assert 95 <= held.stale_age_sec <= 130
    api.release(held)


def test_helper_second_handle_in_the_same_process_is_refused(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    held = api.try_acquire(lock)
    try:
        assert api.try_acquire(lock) is None
        assert api.is_held(lock) is True
    finally:
        api.release(held)


def test_helper_release_closes_the_descriptor(tmp_path):
    api = _lock_api()
    held = api.try_acquire(tmp_path / "ingestion.lock")
    api.release(held)
    with pytest.raises(OSError):
        os.fstat(held.fd)


def test_helper_is_held_on_a_missing_file_is_false_and_creates_nothing(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    assert api.is_held(lock) is False
    assert not lock.exists()


def test_helper_is_held_leaves_a_stale_file_untouched_and_free(tmp_path):
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock", b"LOCKED by PID 1")
    assert api.is_held(lock) is False
    assert lock.read_bytes() == b"LOCKED by PID 1"
    held = api.try_acquire(lock)
    assert held is not None, "the probe must not leave the lock taken"
    api.release(held)


def test_helper_is_held_is_false_when_it_cannot_decide(tmp_path):
    # Design: advisory only; ingest.py makes the authoritative decision.
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock")
    scanner = _ExclusiveOpen(lock)
    try:
        assert api.is_held(lock) is False
    finally:
        scanner.close()


def test_helper_is_held_probes_do_not_cause_a_false_refusal(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    stop = threading.Event()

    def probe():
        while not stop.is_set():
            api.is_held(lock)
            time.sleep(0.01)

    thread = threading.Thread(target=probe)
    thread.start()
    try:
        for _ in range(15):
            # A stale file, so the probes really take and drop the byte: on a free
            # or held lock they would not contend with try_acquire at all.
            _stale(lock)
            time.sleep(0.02)
            held = api.try_acquire(lock)
            assert held is not None, "a momentary is_held probe refused an ingestion"
            api.release(held)
    finally:
        stop.set()
        thread.join()


@pytest.mark.parametrize("case", ["missing_folder", "path_is_a_directory", "read_only_file", "exclusive_open"])
def test_helper_fails_closed_without_an_exception(tmp_path, case):
    # Design point 1.4: after the attempts or on any other OSError, a refusal.
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    scanner = None
    if case == "missing_folder":
        lock = tmp_path / "absent" / "ingestion.lock"
    elif case == "path_is_a_directory":
        lock.mkdir()
    elif case == "read_only_file":
        _stale(lock)
        os.chmod(lock, 0o444)
    else:
        _stale(lock)
        scanner = _ExclusiveOpen(lock)
    try:
        assert api.try_acquire(lock) is None
    finally:
        if scanner:
            scanner.close()
        if case == "read_only_file":
            os.chmod(lock, 0o666)


def test_d8_release_retries_are_bounded_and_return_the_class_name_only(tmp_path, monkeypatch):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    held = api.try_acquire(lock)

    def denied(_real):
        raise PermissionError(13, "synthetic denial", f"C:/synthetic/{MARKER}/ingestion.lock")

    calls = _patch_unlink(monkeypatch, lock, denied)
    started = time.monotonic()
    assert api.release(held) == "PermissionError"
    assert 2 <= len(calls) <= 4, f"expected a bounded retry, saw {len(calls)} attempts"
    assert time.monotonic() - started < 2
    with pytest.raises(OSError):
        os.fstat(held.fd)


def test_helper_release_treats_an_already_removed_file_as_success(tmp_path, monkeypatch):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    held = api.try_acquire(lock)

    def gone(_real):
        raise FileNotFoundError(2, "synthetic")

    _patch_unlink(monkeypatch, lock, gone)
    assert api.release(held) is None


# ---------------------------------------------------------------------------
# Criteria 1 and 2: a stale lock does not block the next run
# ---------------------------------------------------------------------------

def test_c1_admin_ingestion_recovers_a_lock_left_by_a_killed_run(site, lab):
    _left_by_killed_run(lab, site.lock)
    (site.inbox / "note.txt").write_text("Synthetic note.", encoding="utf-8")
    output = site.admin._run_ingestion()
    assert "synthetic run done" in output
    assert site.runs() == 1
    assert (site.archive / "note.txt").is_file(), "the inbox was not processed"
    assert not site.lock.exists(), "the lock must be removed at the end"


@pytest.mark.parametrize("how", KILL_WAYS)
def test_c2_ingest_main_recovers_a_lock_left_by_a_killed_run(ingest_env, lab, how):
    _left_by_killed_run(lab, ingest_env.lock, how)
    inbox_file = _add_inbox_file(ingest_env)
    assert ingest_env.module.main() is None
    _assert_ingest_ran_and_released(ingest_env, inbox_file)


def test_c2_main_ends_with_a_lock_released_even_when_the_run_crashes(ingest_env):
    # A normal error passes through finally, as the issue states.
    _add_inbox_file(ingest_env)

    def crash():
        raise RuntimeError("synthetic crash")

    ingest_env.during_run = crash
    ingest_env.module.main()
    assert not ingest_env.lock.exists()


# ---------------------------------------------------------------------------
# Criterion 3: timeout kill
# ---------------------------------------------------------------------------

def test_c3_next_admin_ingestion_after_a_real_timeout_kill_is_not_refused(site, monkeypatch):
    (site.base / "sleep_forever").write_text("synthetic", encoding="utf-8")
    # The scenario needs a child that took the lock before the timeout. Below-normal
    # priority is not under test here and starved the child for over 40 s under CPU
    # load, so it is off, and a busy machine gets longer attempts. Each attempt is a
    # real timeout kill by the real subprocess.run.
    monkeypatch.setattr(site.admin, "_low_priority_kwargs", lambda: {})
    for timeout_sec in (3, 10, 30):
        monkeypatch.setattr(site.admin, "INGEST_TIMEOUT_SEC", timeout_sec)
        output = site.admin._run_ingestion()
        assert "timeout" in output
        if (site.base / "started").exists():
            break
    assert (site.base / "started").exists(), "the child did not reach the lock before the timeout"
    assert site.lock.exists(), "the kill was expected to leave the lock file behind"

    (site.base / "sleep_forever").unlink()
    (site.inbox / "note.txt").write_text("Synthetic note.", encoding="utf-8")
    monkeypatch.setattr(site.admin, "INGEST_TIMEOUT_SEC", 60)
    output = site.admin._run_ingestion()
    assert "already running" not in output
    assert "synthetic child refused" not in output
    assert "synthetic run done" in output
    assert (site.archive / "note.txt").is_file()
    assert not site.lock.exists()


# ---------------------------------------------------------------------------
# Criterion 4: a live run is never run twice
# ---------------------------------------------------------------------------

def test_c4_second_admin_ingestion_is_refused_and_holder_continues(site, lab, monkeypatch):
    holder = lab.holder(site.lock)
    started = _no_child_allowed(monkeypatch, site.admin)
    assert site.admin._run_ingestion() == REFUSAL_TEXT
    assert started == [], "no child may start while another ingestion runs"
    assert holder.popen.poll() is None, "the first ingestion must continue"
    assert site.lock.exists()
    assert site.admin._ingestion_lock.locked() is False


def test_c4_second_ingest_main_by_hand_or_watcher_is_refused(ingest_env, lab):
    holder = lab.holder(ingest_env.lock)
    text_before = ingest_env.lock.read_bytes()
    inbox_file = _add_inbox_file(ingest_env)
    # None gives exit code 0: the watcher's check=True keeps working as today.
    assert ingest_env.module.main() is None
    assert ingest_env.db_opened == [], "a refused run must not open the stores"
    assert inbox_file.exists(), "a refused run must not process the inbox"
    assert holder.popen.poll() is None, "the first ingestion must continue"
    assert ingest_env.lock.read_bytes() == text_before, "the refused run changed the lock of the holder"
    assert lab.finish(holder) == 0, "the holder could not release its own lock at the end"
    assert not ingest_env.lock.exists()


def test_c4_running_ingestion_older_than_the_timeout_is_still_refused(site, lab, monkeypatch):
    holder = lab.holder(site.lock)
    past = time.time() - site.admin.INGEST_TIMEOUT_SEC - 3600
    os.utime(site.lock, (past, past))
    started = _no_child_allowed(monkeypatch, site.admin)
    assert site.admin._run_ingestion() == REFUSAL_TEXT
    assert started == []
    assert holder.popen.poll() is None


def test_c4_ingest_main_refuses_a_holder_older_than_the_timeout(ingest_env, lab):
    from src.config import INGEST_TIMEOUT_SEC
    holder = lab.holder(ingest_env.lock)
    past = time.time() - INGEST_TIMEOUT_SEC - 3600
    os.utime(ingest_env.lock, (past, past))
    _add_inbox_file(ingest_env)
    assert ingest_env.module.main() is None
    assert ingest_env.db_opened == []
    assert holder.popen.poll() is None
    assert ingest_env.lock.exists()


def test_c4_lock_is_held_for_the_whole_ingest_main_run(ingest_env, lab, tmp_path):
    # The kernel lock must not end when acquire_lock returns: a run that held it
    # only briefly would look stale for the rest of the run.
    api = _lock_api()
    seen: dict = {}

    def check_contender():
        seen["is_held"] = api.is_held(ingest_env.lock)
        slot = tmp_path / "contender"
        slot.mkdir()
        probe = _RACER_ONE_SHOT.format(repo=str(REPO_ROOT), lock=str(ingest_env.lock))
        seen["child"] = _real_run([sys.executable, "-c", probe], capture_output=True, text=True,
                                  cwd=str(slot), timeout=60)

    ingest_env.during_run = check_contender
    inbox_file = _add_inbox_file(ingest_env)
    ingest_env.module.main()
    assert seen["is_held"] is True
    assert seen["child"].stdout.strip() == "REFUSED", seen["child"].stderr[-800:]
    _assert_ingest_ran_and_released(ingest_env, inbox_file)


_RACER_ONE_SHOT = r"""
import sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from src.ingest_lock import release, try_acquire
held = try_acquire(Path({lock!r}))
print("REFUSED" if held is None else "ACQUIRED")
if held is not None:
    release(held)
"""


def test_c4_release_lock_of_a_refused_run_leaves_the_holder_lock_alone(ingest_env, lab):
    # Design point 2: release_lock removes only a lock this process holds.
    lab.holder(ingest_env.lock)
    ingest = ingest_env.module
    assert ingest.acquire_lock() is False
    ingest.release_lock()
    assert ingest_env.lock.exists()
    assert ingest_env.records.warnings() == [], "nothing was held, so nothing failed"


def test_release_lock_never_removes_a_file_this_process_does_not_hold(ingest_env):
    _stale(ingest_env.lock)
    ingest_env.module.release_lock()
    assert ingest_env.lock.exists()


def test_acquire_lock_second_call_keeps_the_first_lock(ingest_env):
    api = _lock_api()
    ingest = ingest_env.module
    assert ingest.acquire_lock() is True
    assert ingest.acquire_lock() is False
    assert api.is_held(ingest_env.lock) is True, "the refused call dropped the lock of the first"
    ingest.release_lock()
    assert not ingest_env.lock.exists()


def test_c4_admin_refusal_happens_before_a_target_is_staged(site, lab, monkeypatch):
    # Design point 3: the probe runs before staging, so a refused re-ingest moves nothing.
    lab.holder(site.lock)
    archived = site.archive / "alice" / "note.txt"
    archived.parent.mkdir()
    archived.write_text("Synthetic note.", encoding="utf-8")
    started = _no_child_allowed(monkeypatch, site.admin)
    assert site.admin._run_ingestion(str(archived)) == REFUSAL_TEXT
    assert started == []
    assert archived.is_file()
    assert not (site.inbox / "alice" / "note.txt").exists()


def test_admin_probe_and_ingest_use_the_same_fixed_lock_path():
    # A different path would make the probe useless. No file is opened here.
    from src.config import BASE_DIR
    from src.ui import admin
    assert admin._INGEST_LOCK_FILE == BASE_DIR / "ingestion.lock"
    assert Path(_get_ingest().LOCK_FILE).resolve() == Path(BASE_DIR / "ingestion.lock").resolve()


# ---------------------------------------------------------------------------
# Criterion 5: PID reuse
# ---------------------------------------------------------------------------

def test_c5_recorded_pid_of_an_unrelated_live_process_is_stale_for_the_helper(tmp_path, lab):
    api = _lock_api()
    other = lab.unrelated_process()
    lock = _stale(tmp_path / "ingestion.lock", f"LOCKED by PID {other.pid}".encode())
    held = api.try_acquire(lock)
    assert held is not None
    assert other.popen.poll() is None
    api.release(held)


def test_c5_recorded_pid_of_an_unrelated_live_process_does_not_block_ingest_main(ingest_env, lab):
    other = lab.unrelated_process()
    _stale(ingest_env.lock, f"LOCKED by PID {other.pid}".encode())
    inbox_file = _add_inbox_file(ingest_env)
    assert ingest_env.module.main() is None
    _assert_ingest_ran_and_released(ingest_env, inbox_file)
    assert other.popen.poll() is None, "the unrelated process must not be touched"


def test_c5_own_pid_recorded_in_a_stale_file_is_not_trusted_either(tmp_path):
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock", f"LOCKED by PID {os.getpid()}".encode())
    held = api.try_acquire(lock)
    assert held is not None
    api.release(held)


# ---------------------------------------------------------------------------
# Criterion 6: concurrent starts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start", ["stale_file", "no_file"])
def test_c6_two_ingestions_started_together_exactly_one_runs(tmp_path, lab, start):
    for round_no in range(3):
        lock = tmp_path / f"ingestion{round_no}.lock"
        if start == "stale_file":
            _stale(lock)
        outcomes = lab.racers(lock)
        assert sorted(outcomes) == ["HELD", "REFUSED"], f"round {round_no}: {outcomes}"
        assert not lock.exists(), f"round {round_no}: the winner must remove the lock at the end"


def test_c6_many_ingestions_started_together_exactly_one_runs(tmp_path, lab):
    lock = _stale(tmp_path / "ingestion.lock")
    outcomes = lab.racers(lock, count=6)
    assert outcomes.count("HELD") == 1, outcomes


def test_c6_stores_are_never_written_by_two_runs_at_once_under_churn(tmp_path, lab):
    lock = tmp_path / "ingestion.lock"
    results = lab.churn(lock, tmp_path / "writing.marker", processes=4, seconds=3)
    assert sum(r["acquired"] for r in results) > 0, "the scenario never acquired the lock"
    assert sum(r["overlaps"] for r in results) == 0, f"two holders at once: {results}"


# ---------------------------------------------------------------------------
# Criterion 7: lock file content is never parsed
# ---------------------------------------------------------------------------

CONTENTS = {
    "current_format": b"LOCKED by PID 1",
    "empty": b"",
    "binary_garbage": bytes(range(256)),
}


@pytest.mark.parametrize("data", [*CONTENTS.values(), b"\xff" * (2 * 1024 * 1024)],
                         ids=[*CONTENTS, "garbage_past_the_lock_offset"])
def test_c7_any_content_with_no_holder_is_recovered_with_no_exception(ingest_env, data):
    _stale(ingest_env.lock, data)
    ingest = ingest_env.module
    assert ingest.acquire_lock() is True
    assert ingest_env.lock.read_text(encoding="ascii") == f"LOCKED by PID {os.getpid()}"
    ingest.release_lock()
    assert not ingest_env.lock.exists()


@pytest.mark.parametrize("data", CONTENTS.values(), ids=CONTENTS.keys())
def test_c7_any_content_with_a_holder_is_refused_with_no_exception(ingest_env, lab, data):
    holder = lab.holder(ingest_env.lock)
    _overwrite(ingest_env.lock, data)
    ingest = ingest_env.module
    assert ingest.acquire_lock() is False
    assert ingest_env.lock.read_bytes() == data, "the refused run changed the file"
    assert holder.popen.poll() is None


@pytest.mark.parametrize("data", CONTENTS.values(), ids=CONTENTS.keys())
def test_c7_is_held_ignores_the_content(tmp_path, lab, data):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    lab.holder(lock)
    _overwrite(lock, data)
    assert api.is_held(lock) is True


def test_c7_unusable_lock_path_is_a_refusal_with_no_exception(ingest_env):
    ingest_env.lock.mkdir()
    assert ingest_env.module.acquire_lock() is False
    assert ingest_env.lock.is_dir()


# ---------------------------------------------------------------------------
# Criteria 8 and 14: log lines
# ---------------------------------------------------------------------------

def _stale_lines(records: _Records) -> list[str]:
    return [line for line in records.warnings() if "stale" in line.lower()]


@pytest.mark.parametrize("switch", [False, True], ids=["switch_off", "switch_on"])
def test_c8_recovery_logs_exactly_one_warning_with_no_path(ingest_env, monkeypatch, capsys, switch):
    _switch(monkeypatch, switch)
    _stale(ingest_env.lock)
    past = time.time() - 100
    os.utime(ingest_env.lock, (past, past))
    assert ingest_env.module.main() is None  # empty inbox
    warnings = ingest_env.records.warnings()
    assert len(warnings) == 1, f"expected exactly one WARNING, saw {warnings}"
    assert warnings == _stale_lines(ingest_env.records)
    assert warnings[0].isascii(), "the recovery line has no emoji (cp1252 console, design point 2)"
    assert re.search(r"\b\d{2,3}s\b", warnings[0]), "the design's line states the age in seconds"
    _assert_no_path_in(ingest_env.records.lines(), ingest_env.root)
    out = capsys.readouterr()
    _assert_no_path_in([out.out, out.err], ingest_env.root)


def test_c8_fresh_lock_logs_no_recovery_warning(ingest_env):
    assert ingest_env.module.main() is None
    assert ingest_env.records.warnings() == []


@pytest.mark.parametrize("how", KILL_WAYS)
def test_c8_recovery_after_a_real_kill_logs_exactly_one_warning(ingest_env, lab, how):
    _left_by_killed_run(lab, ingest_env.lock, how)
    assert ingest_env.module.main() is None
    assert len(_stale_lines(ingest_env.records)) == 1
    assert len(ingest_env.records.warnings()) == 1


def test_c8_contenders_for_one_stale_lock_log_at_most_one_recovery(ingest_env, lab):
    # Only the winner logs (design, Risks). The contender here is refused and logs
    # the existing refusal line only.
    holder = lab.holder(ingest_env.lock)
    assert ingest_env.module.acquire_lock() is False
    assert _stale_lines(ingest_env.records) == []
    assert holder.popen.poll() is None


@pytest.mark.parametrize("switch", [False, True], ids=["switch_off", "switch_on"])
def test_c14_failed_removal_logs_the_class_name_only(ingest_env, monkeypatch, capsys, switch):
    _switch(monkeypatch, switch)
    secret = f"C:/synthetic/{MARKER}/ingestion.lock"

    def denied(_real):
        raise PermissionError(13, "synthetic denial", secret)

    ingest = ingest_env.module
    assert ingest.acquire_lock() is True
    _patch_unlink(monkeypatch, ingest_env.lock, denied)
    ingest.release_lock()

    warnings = ingest_env.records.warnings()
    assert len(warnings) == 1, warnings
    assert "PermissionError" in warnings[0]
    assert warnings[0].isascii()
    _assert_no_path_in(ingest_env.records.lines(), ingest_env.root)
    assert "denial" not in warnings[0]
    out = capsys.readouterr()
    _assert_no_path_in([out.out, out.err], ingest_env.root)


@pytest.mark.parametrize("exc", [OSError(5, "synthetic"), RuntimeError("synthetic")], ids=["OSError", "RuntimeError"])
def test_c14_release_lock_never_raises_into_the_finally_block(ingest_env, monkeypatch, exc):
    # Today's release_lock catches every Exception; main() calls it in finally.
    def broken(_real):
        raise exc

    ingest = ingest_env.module
    assert ingest.acquire_lock() is True
    _patch_unlink(monkeypatch, ingest_env.lock, broken)
    ingest.release_lock()
    warnings = ingest_env.records.warnings()
    assert len(warnings) == 1, warnings
    assert type(exc).__name__ in warnings[0]
    assert "synthetic" not in warnings[0]


@pytest.mark.parametrize("switch", [False, True], ids=["switch_off", "switch_on"])
def test_c14_no_lock_line_has_a_path_over_acquire_refusal_and_release(ingest_env, lab, monkeypatch, capsys, switch):
    _switch(monkeypatch, switch)
    ingest = ingest_env.module
    assert ingest.main() is None  # fresh acquire and release, empty inbox
    holder = lab.holder(ingest_env.lock)
    assert ingest.main() is None  # refusal
    assert lab.finish(holder) == 0
    _stale(ingest_env.lock)
    assert ingest.main() is None  # recovery and release
    assert ingest_env.records.lines(), "the scenario logged nothing"
    _assert_no_path_in(ingest_env.records.lines(), ingest_env.root)
    out = capsys.readouterr()
    _assert_no_path_in([out.out, out.err], ingest_env.root)


# ---------------------------------------------------------------------------
# Criterion 9: refusal on the Admin page
# ---------------------------------------------------------------------------

def test_c9_refusal_message_and_log_have_no_path(site, lab, monkeypatch):
    lab.holder(site.lock)
    started = _no_child_allowed(monkeypatch, site.admin)
    output = site.admin._run_ingestion()
    assert output == REFUSAL_TEXT
    assert started == []
    _assert_no_path_in([output, *site.log.lines], site.base)


def test_c9_in_process_refusal_uses_the_same_message(site, monkeypatch):
    started = _no_child_allowed(monkeypatch, site.admin)
    site.admin._ingestion_lock.acquire()
    try:
        assert site.admin._run_ingestion() == REFUSAL_TEXT
    finally:
        site.admin._ingestion_lock.release()
    assert started == []


def test_c9_refusal_notification_is_a_warning_that_an_ingestion_is_running():
    from src.ui import admin
    text, kind = admin._ingestion_notification(REFUSAL_TEXT)
    assert "already running" in text.lower()
    assert kind == "warning"
    assert "complete" not in text.lower()


@pytest.mark.parametrize("output", ["synthetic run done", ""])
def test_c9_completed_run_notification_is_ingestion_complete(output):
    from src.ui import admin
    assert admin._ingestion_notification(output) == ("Ingestion complete.", "positive")


def test_c9_ingest_all_takes_its_notification_from_the_pure_function():
    # The closure needs a browser; this ties it to the function tested above.
    tree = ast.parse((REPO_ROOT / "src" / "ui" / "admin.py").read_text(encoding="utf-8"))
    closures = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ingest_all"]
    assert len(closures) == 1
    source = ast.unparse(closures[0])
    assert "_ingestion_notification" in source
    assert "Ingestion complete." not in source, "an unconditional success message would come back"


# ---------------------------------------------------------------------------
# Criterion 11: changelog, upgrade note and ADR (D9)
# ---------------------------------------------------------------------------

def _unreleased() -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]


def test_c11_changelog_has_a_line_under_unreleased():
    assert any("#78" in line and "lock" in line.lower() for line in _unreleased().splitlines())


def test_design_changelog_carries_the_upgrade_note():
    assert "Get-CimInstance" in _unreleased(), "design point 5: the upgrade check for a run of the old version"


def test_d9_adr_records_the_lock_protocol():
    path = REPO_ROOT / "doc" / "decisions" / "0005-ingestion-lock-held-by-the-kernel.md"
    text = path.read_text(encoding="utf-8")
    assert "src/ingest_lock.py" in text
    assert "byte-range" in text


# ---------------------------------------------------------------------------
# Criterion 12: no lock left by a normal run, 20 consecutive runs
# ---------------------------------------------------------------------------

def test_c12_admin_ingest_all_with_empty_inbox_leaves_no_lock_20_runs(site):
    for run_no in range(20):
        assert "synthetic run done" in site.admin._run_ingestion(), f"run {run_no}"
        assert not site.lock.exists(), f"lock left after run {run_no}"
    assert site.runs() == 20


def test_c12_admin_ingest_all_with_synthetic_files_leaves_no_lock_20_runs(site):
    for run_no in range(20):
        (site.inbox / f"note{run_no}.txt").write_text("Synthetic note.", encoding="utf-8")
        assert "synthetic run done" in site.admin._run_ingestion(), f"run {run_no}"
        assert not site.lock.exists(), f"lock left after run {run_no}"
        assert (site.archive / f"note{run_no}.txt").is_file()
    assert site.runs() == 20


def test_c12_admin_reingest_selected_leaves_no_lock_20_runs(site):
    archived = site.archive / "alice" / "note.txt"
    archived.parent.mkdir()
    archived.write_text("Synthetic note.", encoding="utf-8")
    for run_no in range(20):
        assert "synthetic run done" in site.admin._run_ingestion(str(archived)), f"run {run_no}"
        assert not site.lock.exists(), f"lock left after run {run_no}"
        assert archived.is_file()
    assert site.runs() == 20


def test_c12_real_ingest_main_with_empty_inbox_leaves_no_lock_20_runs(ingest_env):
    for run_no in range(20):
        assert ingest_env.module.main() is None
        assert not ingest_env.lock.exists(), f"lock left after run {run_no}"
    assert ingest_env.records.warnings() == []


# ---------------------------------------------------------------------------
# Criterion 13: the removal fails because another process holds the file open
# ---------------------------------------------------------------------------

def test_c13_scanner_handle_makes_release_report_the_class_name(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    held = api.try_acquire(lock)
    with open(lock, "rb"):
        assert api.release(held) == "PermissionError"
    assert lock.exists()


def test_c13_next_run_is_not_refused_while_the_scanner_still_holds_the_file(tmp_path):
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    first = api.try_acquire(lock)
    with open(lock, "rb"):
        api.release(first)
        second = api.try_acquire(lock)
        assert second is not None, "a leftover file held open by a scanner refused the next run"
        api.release(second)  # the scanner still holds it, so this removal fails too
    assert api.is_held(lock) is False


def test_c13_next_ingest_main_runs_after_a_failed_removal(ingest_env):
    ingest = ingest_env.module
    assert ingest.acquire_lock() is True
    with open(ingest_env.lock, "rb"):
        ingest.release_lock()
    assert ingest_env.lock.exists(), "the scenario needs the file left behind"
    assert any("PermissionError" in line for line in ingest_env.records.warnings())
    inbox_file = _add_inbox_file(ingest_env)
    assert ingest.main() is None
    _assert_ingest_ran_and_released(ingest_env, inbox_file)


def test_c13_scanner_with_a_brief_exclusive_open_does_not_refuse_the_run(tmp_path):
    # Design point 1.1: PermissionError on open means another attempt, not a refusal.
    api = _lock_api()
    lock = _stale(tmp_path / "ingestion.lock")
    scanner = _ExclusiveOpen(lock)
    timer = threading.Timer(0.05, scanner.close)
    timer.start()
    try:
        held = api.try_acquire(lock)
    finally:
        timer.join()
    assert held is not None
    api.release(held)


# ---------------------------------------------------------------------------
# Criterion 15: the server is stopped during a run (Delete half: #73)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("how", KILL_WAYS)
def test_c15_next_admin_ingestion_after_server_stop_is_not_refused(site, lab, how):
    _left_by_killed_run(lab, site.lock, how)
    (site.inbox / "note.txt").write_text("Synthetic note.", encoding="utf-8")
    output = site.admin._run_ingestion()
    assert "already running" not in output
    assert "synthetic run done" in output
    assert (site.archive / "note.txt").is_file()
    assert not site.lock.exists()


def test_c15_server_killed_while_its_child_survives_keeps_the_lock(tmp_path, lab):
    # Design table: the surviving child still holds the lock, with no special code.
    api = _lock_api()
    lock = tmp_path / "ingestion.lock"
    server = lab.server_with_holder_child(lock)
    server.popen.kill()
    server.popen.wait(timeout=30)
    assert _pid_alive(server.child_pid), "the scenario needs the child alive"
    assert api.is_held(lock) is True
    assert api.try_acquire(lock) is None


# ---------------------------------------------------------------------------
# --rebuild-bm25 follows the same lock (coordinator request, PR #77 merged)
# ---------------------------------------------------------------------------

@pytest.fixture
def rebuild_env(ingest_env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--rebuild-bm25"])
    return ingest_env


def test_rebuild_option_recovers_a_stale_plain_file(rebuild_env):
    _stale(rebuild_env.lock)
    assert rebuild_env.module.main() is None
    assert rebuild_env.nodes_loaded, "the rebuild did not run"
    assert not rebuild_env.lock.exists()
    assert len(_stale_lines(rebuild_env.records)) == 1


@pytest.mark.parametrize("how", KILL_WAYS)
def test_rebuild_option_recovers_a_lock_left_by_a_killed_run(rebuild_env, lab, how):
    _left_by_killed_run(lab, rebuild_env.lock, how)
    assert rebuild_env.module.main() is None
    assert rebuild_env.nodes_loaded, "the rebuild did not run"
    assert not rebuild_env.lock.exists()


def test_rebuild_option_with_a_live_holder_returns_one_and_writes_nothing(rebuild_env, lab):
    holder = lab.holder(rebuild_env.lock)
    text_before = rebuild_env.lock.read_bytes()
    assert rebuild_env.module.main() == 1
    assert rebuild_env.db_opened == [], "the rebuild opened the stores while another run held the lock"
    assert rebuild_env.nodes_loaded == []
    assert not rebuild_env.bm25.exists()
    assert holder.popen.poll() is None
    assert rebuild_env.lock.read_bytes() == text_before


def test_rebuild_option_holds_the_lock_for_the_whole_rebuild(rebuild_env):
    api = _lock_api()
    seen: dict = {}

    def check():
        seen["is_held"] = api.is_held(rebuild_env.lock)

    rebuild_env.during_run = check
    assert rebuild_env.module.main() is None
    assert seen["is_held"] is True
    assert not rebuild_env.lock.exists()


def test_rebuild_option_failure_releases_the_lock(rebuild_env):
    def crash():
        raise RuntimeError("synthetic failure")

    rebuild_env.during_run = crash
    assert rebuild_env.module.main() == 1
    assert not rebuild_env.lock.exists()
