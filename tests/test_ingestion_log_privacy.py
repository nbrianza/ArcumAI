# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #52: no archive file or folder names in ingestion
and watcher logs.

Approved design (issue #52 design comment, owner decisions D1 to D6 as recommended):
- A file is referenced in a log line as ``id=<first 12 hex of the content MD5>``
  plus ``ext=<safe_ext>``, built by ``src.utils.file_ref(path, file_hash=None,
  shown=None)``. When the hash cannot be computed the id is ``id=unavailable``.
  With ``LOG_CLIENT_CONTENT`` on, ``file_ref`` appends ``file=<content(...)>``.
- ``ingest.py`` computes the hash at the start of ``read_and_chunk_file``.
- ``watcher.py`` logs only the extension on a successful transfer, and the id plus
  the exception type on a failure.
- Exception text goes only through ``content(str(e), type(e).__name__)``;
  tracebacks (``exc_info``) only when the switch is on (D4).

How the evidence is gathered:
- ``ArcumIngestion`` has ``propagate = False``, so the ``ingestion_records`` fixture
  swaps its handlers (file, console and, once ``watcher`` is imported, the
  ``watcher.log`` handler) for an in-memory handler at DEBUG. Nothing is written to
  or read from ``logs/``. Records are the single source of every log line, so a
  record without the marker means neither ``ingestion.log``, ``watcher.log`` nor the
  console handler can contain it. ``capsys`` covers ``print`` and ``tqdm``.
- ``watcher.py`` builds a ``TimedRotatingFileHandler`` on ``logs/watcher.log`` at
  import time. ``_get_watcher`` imports it with that class replaced by a null
  handler, so the test run never opens the watcher log.
- All files live under ``tmp_path`` and every file and subfolder name carries
  ``ZZ-SYNTHETIC-MARKER-123``. Exceptions raised by the fakes quote the full path,
  as operating system errors do, to prove that exception text is not logged.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import logging
import logging.handlers
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.logger as logger_module
import src.utils as utils_module

# --- Synthetic test data -----------------------------------------------------

MARKER = "ZZ-SYNTHETIC-MARKER-123"
# Any log text containing this fragment (case-insensitive) counts as a leak.
LEAK_FRAGMENT = "synthetic-marker"

CLIENT_DIR = f"Client {MARKER} matter"
READABLE_NAME = f"{MARKER} contract.txt"
DUPLICATE_NAME = f"{MARKER} copy.txt"
EMPTY_NAME = f"{MARKER} blank.txt"
BROKEN_NAME = f"{MARKER} broken.docx"

READABLE_TEXT = "Synthetic readable document. The parties agree on the synthetic terms below."
DUPLICATE_TEXT = "Synthetic document already present in the archive."
EMPTY_TEXT = "   \n   "
BROKEN_BYTES = b"synthetic bytes that are not a docx archive"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _id12(data: bytes) -> str:
    return _md5(data)[:12]


def _ref(data: bytes, ext: str) -> str:
    """Expected default-mode file reference (design point 2)."""
    return f"id={_id12(data)} ext={ext}"


# --- Log capture -------------------------------------------------------------

class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def ingestion_records(monkeypatch):
    """Capture every ArcumIngestion (and ArcumServer) record at DEBUG.

    Asserts first that the content switch is off, so that every marker-absent
    assertion proves "off by default means safe".
    """
    assert getattr(logger_module, "LOG_CLIENT_CONTENT", False) is False, (
        "LOG_CLIENT_CONTENT must be off in the test process"
    )
    handler = _ListHandler()
    loggers = [logging.getLogger("ArcumIngestion"), logging.getLogger("ArcumServer")]
    old_levels = [lg.level for lg in loggers]
    for lg in loggers:
        monkeypatch.setattr(lg, "handlers", [handler])
        lg.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        for lg, level in zip(loggers, old_levels):
            lg.setLevel(level)


_EXC_FORMATTER = logging.Formatter()


def _record_text(record: logging.LogRecord) -> str:
    parts = [record.getMessage(), repr(record.msg), repr(record.args)]
    if record.exc_info:
        parts.append(_EXC_FORMATTER.formatException(record.exc_info))
    if record.exc_text:
        parts.append(record.exc_text)
    return " ".join(parts)


def _leaks(records) -> list[str]:
    return [
        f"[{r.levelname}] {r.getMessage()[:300]}"
        for r in records
        if LEAK_FRAGMENT in _record_text(r).lower()
    ]


def _assert_no_leak(records, capsys=None):
    leaks = _leaks(records)
    assert not leaks, "archive file or folder name found in log records:\n" + "\n".join(leaks)
    if capsys is not None:
        out = capsys.readouterr()
        assert LEAK_FRAGMENT not in (out.out + out.err).lower(), (
            "archive file or folder name printed to the console"
        )


def _messages(records) -> list[str]:
    return [r.getMessage() for r in records]


def _lines_with(records, *fragments) -> list[str]:
    return [m for m in _messages(records) if all(f in m for f in fragments)]


def _assert_line(records, *fragments):
    found = _lines_with(records, *fragments)
    assert found, (
        f"no log line contains all of {fragments!r}; lines were:\n" + "\n".join(_messages(records))
    )
    return found


# --- Module access -----------------------------------------------------------

def _get_ingest():
    """Import ingest.py with init_settings() mocked (as tests/test_ingest_pipeline.py)."""
    if "ingest" not in sys.modules:
        with patch("src.config.init_settings"):
            importlib.import_module("ingest")
    return sys.modules["ingest"]


class _NullFileHandler(logging.NullHandler):
    """Stand-in for TimedRotatingFileHandler: accepts its arguments, opens nothing."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.suffix = ""


def _get_watcher():
    """Import watcher.py without opening logs/watcher.log."""
    if "watcher" not in sys.modules:
        with patch.object(logging.handlers, "TimedRotatingFileHandler", _NullFileHandler):
            importlib.import_module("watcher")
    return sys.modules["watcher"]


def _ensure_text_splitter():
    from llama_index.core import Settings
    from llama_index.core.node_parser import SentenceSplitter
    if Settings.text_splitter is None:
        Settings.text_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)


def _file_ref():
    value = getattr(utils_module, "file_ref", None)
    if value is None:
        pytest.fail("src.utils.file_ref is missing (issue #52 design, point 2)")
    return value


def _switch_on(monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True)


def _no_sleep(monkeypatch):
    monkeypatch.setattr(utils_module.time, "sleep", lambda *_a, **_k: None)


# --- ingest.main() scenario (criteria 1, 5, 6, 7) ----------------------------

class _FakeCollection:
    """ChromaDB collection stand-in: reports one known hash as already archived."""

    def __init__(self, known_hashes):
        self.known_hashes = set(known_hashes)

    def get(self, where=None, limit=None, include=None):
        if where and where.get("file_hash") in self.known_hashes:
            return {"ids": ["zz-existing-node"]}
        return {"ids": [], "documents": [], "metadatas": []}


class _RaisingDocxReader:
    """DocxReader stand-in that fails like an OS error, quoting the full path."""

    def load_data(self, file_path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(file_path))


def _build_ingest_scenario(tmp_path, monkeypatch, *, insert_side_effect=None):
    _ensure_text_splitter()
    ingest = _get_ingest()

    inbox = tmp_path / "inbox"
    dirs = {
        "INBOX_DIR": inbox,
        "ARCHIVE_DIR": tmp_path / "archive",
        "ERROR_DIR": tmp_path / "errors",
        "DUPLICATES_DIR": tmp_path / "duplicates",
        "BM25_PATH": tmp_path / "bm25",
    }
    for name, value in dirs.items():
        monkeypatch.setattr(ingest, name, value)
    monkeypatch.setattr(ingest, "LOCK_FILE", tmp_path / "ingestion.lock")

    client = inbox / CLIENT_DIR
    client.mkdir(parents=True)
    files = {
        "readable": (client / READABLE_NAME, READABLE_TEXT.encode("utf-8"), ".txt"),
        "duplicate": (client / DUPLICATE_NAME, DUPLICATE_TEXT.encode("utf-8"), ".txt"),
        "empty": (client / EMPTY_NAME, EMPTY_TEXT.encode("utf-8"), ".txt"),
        "broken": (client / BROKEN_NAME, BROKEN_BYTES, ".docx"),
    }
    for path, data, _ext in files.values():
        path.write_bytes(data)

    index = MagicMock()
    if insert_side_effect is not None:
        index.insert_nodes.side_effect = insert_side_effect
    collection = _FakeCollection({_md5(files["duplicate"][1])})
    monkeypatch.setattr(ingest, "get_db_components", lambda: (index, collection))
    monkeypatch.setattr(ingest, "get_all_nodes_from_chroma", lambda _c: [])
    monkeypatch.setattr(ingest, "DocxReader", _RaisingDocxReader)
    return ingest, dirs, files, index


def test_criterion1_ingest_main_default_logs_and_stdout_have_no_file_or_folder_names(
        tmp_path, monkeypatch, ingestion_records, capsys):
    ingest, _dirs, _files, _index = _build_ingest_scenario(tmp_path, monkeypatch)
    ingest.main()
    assert ingestion_records, "ingest.main() logged nothing; the scenario did not run"
    _assert_no_leak(ingestion_records, capsys)


def test_criterion5_ingest_main_lines_state_action_id_and_extension(
        tmp_path, monkeypatch, ingestion_records):
    ingest, _dirs, files, _index = _build_ingest_scenario(tmp_path, monkeypatch)
    ingest.main()

    def ref(key):
        _path, data, ext = files[key]
        return _ref(data, ext)

    _assert_line(ingestion_records, "READ", ref("readable"))
    _assert_line(ingestion_records, "Duplicate skipped", ref("duplicate"))
    _assert_line(ingestion_records, "No content", ref("empty"))
    _assert_line(ingestion_records, "Error reading", ref("broken"), "PermissionError")
    # Failed-file list: one entry per failed file with its reason.
    _assert_line(ingestion_records, ref("empty"), "(EMPTY)")
    _assert_line(ingestion_records, ref("broken"), "(ERROR)")


def test_criterion5_ingest_main_summary_totals_unchanged(tmp_path, monkeypatch, ingestion_records):
    ingest, dirs, _files, _index = _build_ingest_scenario(tmp_path, monkeypatch)
    ingest.main()
    _assert_line(ingestion_records, "Completed: 1/4 files archived")
    _assert_line(ingestion_records, "2 files failed")
    # The configured error root is configuration, not client data, and stays.
    _assert_line(ingestion_records, "Failed files have been moved to", str(dirs["ERROR_DIR"]))


def test_criterion6_switch_on_ingest_main_logs_names_with_ids(tmp_path, monkeypatch, ingestion_records):
    ingest, _dirs, files, _index = _build_ingest_scenario(tmp_path, monkeypatch)
    _switch_on(monkeypatch)
    ingest.main()

    def id12(key):
        return f"id={_id12(files[key][1])}"

    for action, key in (("READ", "readable"), ("Duplicate skipped", "duplicate"),
                        ("No content", "empty"), ("Error reading", "broken")):
        lines = _assert_line(ingestion_records, action, id12(key))
        assert any(MARKER in line for line in lines), (
            f"with LOG_CLIENT_CONTENT on, the {action!r} line must also show the file name: {lines!r}"
        )


def test_criterion7_failed_files_still_moved_to_error_folder_with_names(
        tmp_path, monkeypatch, ingestion_records):
    ingest, dirs, _files, _index = _build_ingest_scenario(tmp_path, monkeypatch)
    ingest.main()
    error_client = dirs["ERROR_DIR"] / CLIENT_DIR
    assert (error_client / EMPTY_NAME).is_file()
    assert (error_client / BROKEN_NAME).is_file()
    assert (dirs["DUPLICATES_DIR"] / CLIENT_DIR / DUPLICATE_NAME).is_file()
    assert (dirs["ARCHIVE_DIR"] / CLIENT_DIR / READABLE_NAME).is_file()
    assert not (dirs["INBOX_DIR"] / CLIENT_DIR).exists(), "empty inbox subfolder should be cleaned up"


def test_criterion1_batch_db_error_logs_exception_type_not_text(tmp_path, monkeypatch, ingestion_records):
    """ChromaDB metadata errors can quote the metadata dict (filename, file_path)."""
    error = ValueError(f"Expected metadata value, got {{'filename': '{READABLE_NAME}', "
                       f"'file_path': '{CLIENT_DIR}/{READABLE_NAME}'}}")
    ingest, _dirs, _files, _index = _build_ingest_scenario(
        tmp_path, monkeypatch, insert_side_effect=error)
    ingest.main()
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "Error writing Final Batch", "ValueError")


def test_criterion1_bm25_error_logs_exception_type_not_text(tmp_path, monkeypatch, ingestion_records):
    ingest, _dirs, _files, _index = _build_ingest_scenario(tmp_path, monkeypatch)

    def _raise(_collection):
        raise RuntimeError(f"cannot rebuild node for {CLIENT_DIR}/{READABLE_NAME}")

    monkeypatch.setattr(ingest, "get_all_nodes_from_chroma", _raise)
    ingest.main()
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "BM25", "RuntimeError")


def test_criterion1_crash_main_logs_exception_type_without_text_or_traceback(
        tmp_path, monkeypatch, ingestion_records):
    """A PermissionError from the move helper reaches CRASH MAIN with the full path."""
    ingest, _dirs, _files, _index = _build_ingest_scenario(tmp_path, monkeypatch)

    def _raise(file_path, *_args, **_kwargs):
        raise PermissionError(13, "Permission denied", str(file_path))

    monkeypatch.setattr(ingest, "sposta_file_con_struttura", _raise)
    ingest.main()
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "CRASH MAIN", "PermissionError")


# --- watcher.py (criteria 2 and 5) -------------------------------------------

def _build_drop_zone(tmp_path, monkeypatch):
    watcher = _get_watcher()
    drop = tmp_path / "drop"
    inbox = tmp_path / "inbox"
    ok_dir = drop / f"Moved {MARKER}"
    fail_dir = drop / f"Locked {MARKER}"
    ok_dir.mkdir(parents=True)
    fail_dir.mkdir(parents=True)
    ok_file = ok_dir / f"{MARKER} moved.txt"
    fail_file = fail_dir / f"{MARKER} locked.txt"
    ok_data = b"Synthetic file that moves."
    fail_data = b"Synthetic file that stays locked."
    ok_file.write_bytes(ok_data)
    fail_file.write_bytes(fail_data)

    monkeypatch.setattr(watcher, "DROP_DIR", drop)
    monkeypatch.setattr(watcher, "INBOX_DIR", inbox)

    real_move = shutil.move

    def _move(src, dst, *args, **kwargs):
        if Path(src).name == fail_file.name:
            raise PermissionError(13, "The process cannot access the file", str(src))
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(utils_module.shutil, "move", _move)
    _no_sleep(monkeypatch)
    return watcher, drop, inbox, (ok_file, ok_data), (fail_file, fail_data)


def test_criterion2_watcher_move_default_logs_and_stdout_have_no_file_or_folder_names(
        tmp_path, monkeypatch, ingestion_records, capsys):
    watcher, _drop, inbox, (ok_file, _), _fail = _build_drop_zone(tmp_path, monkeypatch)
    moved = watcher.process_drop_zone()
    assert moved == 1
    assert (inbox / ok_file.parent.name / ok_file.name).is_file()
    _assert_no_leak(ingestion_records, capsys)


def test_criterion5_watcher_lines_state_action_extension_and_failure_id(
        tmp_path, monkeypatch, ingestion_records):
    watcher, _drop, _inbox, _ok, (_fail_file, fail_data) = _build_drop_zone(tmp_path, monkeypatch)
    watcher.process_drop_zone()
    _assert_line(ingestion_records, "Transferred", "ext=.txt")
    _assert_line(ingestion_records, "File in use", _ref(fail_data, ".txt"))
    _assert_line(ingestion_records, "Failed to move", _ref(fail_data, ".txt"), "PermissionError")
    _assert_line(ingestion_records, "Cleanup: Removed 1 empty folders")


def test_criterion2_watcher_detected_print_has_extension_not_name(monkeypatch, tmp_path, capsys):
    watcher = _get_watcher()
    handler = watcher.StagingHandler()
    event = SimpleNamespace(is_directory=False, src_path=str(tmp_path / f"{MARKER} new.txt"))
    handler._trigger(event)
    out = capsys.readouterr()
    assert handler.needs_processing is True
    assert LEAK_FRAGMENT not in (out.out + out.err).lower(), "file name printed to stdout"
    assert "ext=.txt" in out.out


def test_criterion2_watcher_scan_error_logs_exception_type_not_text(
        tmp_path, monkeypatch, ingestion_records):
    watcher = _get_watcher()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(watcher, "DROP_DIR", drop)
    monkeypatch.setattr(watcher, "INBOX_DIR", tmp_path / "inbox")

    original_rglob = Path.rglob

    def _rglob(self, pattern):
        if self == drop:
            raise PermissionError(13, "Access is denied", str(drop / f"Sub {MARKER}"))
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", _rglob)
    assert watcher.process_drop_zone() == 0
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "Error scanning Drop Zone", "PermissionError")


# --- src/readers.py (criteria 3 and 5) ---------------------------------------

def test_criterion3_pdf_reader_error_logs_id_and_type_not_name_or_traceback(
        tmp_path, monkeypatch, ingestion_records):
    import llama_index.readers.file as llama_file_readers
    from src.readers import SmartPDFReader

    data = b"%PDF-1.4 synthetic not really a pdf"
    path = tmp_path / f"{MARKER} scan.pdf"
    path.write_bytes(data)

    class _RaisingPDFReader:
        def load_data(self, file_path, *args, **kwargs):
            raise OSError(5, "Input/output error", str(file_path))

    monkeypatch.setattr(llama_file_readers, "PDFReader", _RaisingPDFReader)
    assert SmartPDFReader().load_data(path) == []
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, _ref(data, ".pdf"), "OSError")


def test_criterion3_msg_reader_error_logs_id_and_type_not_name(tmp_path, monkeypatch, ingestion_records):
    import src.readers as readers

    data = b"synthetic msg bytes"
    path = tmp_path / f"{MARKER} mail.msg"
    path.write_bytes(data)

    def _raise(file_name, *args, **kwargs):
        raise OSError(22, "Invalid OLE file", file_name)

    monkeypatch.setattr(readers.extract_msg, "Message", _raise)
    assert readers.MyOutlookReader().load_data(path) == []
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, _ref(data, ".msg"), "OSError")


def test_criterion3_eml_reader_error_logs_id_and_type_not_name(tmp_path, monkeypatch, ingestion_records):
    import src.readers as readers

    data = b"synthetic eml bytes"
    path = tmp_path / f"{MARKER} mail.eml"
    path.write_bytes(data)

    class _RaisingParser:
        def __init__(self, *args, **kwargs):
            pass

        def parse(self, fp, *args, **kwargs):
            raise ValueError(f"cannot parse {fp.name}")

    monkeypatch.setattr(readers, "BytesParser", _RaisingParser)
    assert readers.MyEmlReader().load_data(path) == []
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, _ref(data, ".eml"), "ValueError")


# --- src/utils.py (criteria 4 and 5) -----------------------------------------

def _locked_file(tmp_path, monkeypatch):
    src_root = tmp_path / "src_root"
    dst_root = tmp_path / "dst_root"
    folder = src_root / f"Folder {MARKER}"
    folder.mkdir(parents=True)
    data = b"Synthetic file kept open by another process."
    path = folder / f"{MARKER} in use.txt"
    path.write_bytes(data)

    def _move(src, dst, *args, **kwargs):
        raise PermissionError(32, "The process cannot access the file", str(src))

    monkeypatch.setattr(utils_module.shutil, "move", _move)
    _no_sleep(monkeypatch)
    return path, data, src_root, dst_root


def test_criterion4_file_in_use_and_unable_to_move_have_no_name(tmp_path, monkeypatch, ingestion_records):
    path, _data, src_root, dst_root = _locked_file(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        utils_module.sposta_file_con_struttura(path, src_root, dst_root)
    assert path.is_file(), "the file must stay where it was"
    _assert_no_leak(ingestion_records)


def test_criterion5_file_in_use_and_unable_to_move_carry_id_and_extension(
        tmp_path, monkeypatch, ingestion_records):
    path, data, src_root, dst_root = _locked_file(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        utils_module.sposta_file_con_struttura(path, src_root, dst_root, max_retries=3)
    in_use = _lines_with(ingestion_records, "File in use", _ref(data, ".txt"))
    assert len(in_use) == 3, f"expected one 'File in use' line per attempt, got {in_use!r}"
    _assert_line(ingestion_records, "Unable to move", _ref(data, ".txt"))


def test_criterion5_file_in_use_without_hash_logs_id_unavailable(tmp_path, monkeypatch, ingestion_records):
    path, _data, src_root, dst_root = _locked_file(tmp_path, monkeypatch)
    monkeypatch.setattr(utils_module, "calcola_hash_file", lambda _p: "hash_error")
    with pytest.raises(PermissionError):
        utils_module.sposta_file_con_struttura(path, src_root, dst_root)
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "File in use", "id=unavailable ext=.txt")
    _assert_line(ingestion_records, "Unable to move", "id=unavailable ext=.txt")


def test_criterion4_empty_folder_cleanup_has_no_folder_names(tmp_path, ingestion_records):
    root = tmp_path / "root"
    (root / f"Client {MARKER}" / f"Matter {MARKER}").mkdir(parents=True)
    utils_module.pulisci_cartelle_vuote(root)
    assert not (root / f"Client {MARKER}").exists()
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "Cleanup: Removed 2 empty folders")


def test_criterion4_folder_removal_error_logs_type_not_folder_name(tmp_path, monkeypatch, ingestion_records):
    root = tmp_path / "root"
    (root / f"Client {MARKER}").mkdir(parents=True)

    def _rmtree(path, *args, **kwargs):
        raise OSError(13, "Access is denied", str(path))

    monkeypatch.setattr(utils_module.shutil, "rmtree", _rmtree)
    utils_module.pulisci_cartelle_vuote(root)
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "Could not remove", "OSError")


def test_criterion4_chroma_node_retrieval_error_logs_type_not_text(ingestion_records):
    collection = MagicMock()
    collection.get.side_effect = RuntimeError(f"bad metadata {{'filename': '{READABLE_NAME}'}}")
    assert utils_module.get_all_nodes_from_chroma(collection) == []
    _assert_no_leak(ingestion_records)
    _assert_line(ingestion_records, "Error retrieving nodes", "RuntimeError")


# --- file_ref helper (design point 2, D1, D3) ---------------------------------

def test_file_ref_default_is_id12_and_safe_extension(tmp_path):
    file_ref = _file_ref()
    data = b"Synthetic content for the file reference."
    path = tmp_path / f"{MARKER} memo.PDF"
    path.write_bytes(data)
    assert file_ref(path) == f"id={_id12(data)} ext=.pdf"


def test_file_ref_uses_given_hash_without_reading_the_file(tmp_path):
    file_ref = _file_ref()
    missing = tmp_path / f"{MARKER} gone.docx"
    assert file_ref(missing, "0123456789abcdef0123456789abcdef") == "id=0123456789ab ext=.docx"


def test_file_ref_hash_error_gives_id_unavailable(tmp_path):
    file_ref = _file_ref()
    path = tmp_path / f"{MARKER} memo.txt"
    assert file_ref(path, "hash_error") == "id=unavailable ext=.txt"
    assert file_ref(path) == "id=unavailable ext=.txt", "an unreadable file must give id=unavailable"


def test_file_ref_unknown_extension_is_other(tmp_path):
    file_ref = _file_ref()
    path = tmp_path / f"memo.{MARKER}"
    result = file_ref(path, "0123456789abcdef0123456789abcdef")
    assert result == "id=0123456789ab ext=other"


def test_file_ref_switch_on_appends_shown_value(tmp_path, monkeypatch):
    file_ref = _file_ref()
    path = tmp_path / f"{MARKER} memo.txt"
    _switch_on(monkeypatch)
    plain = file_ref(path, "0123456789abcdef0123456789abcdef")
    assert plain.startswith("id=0123456789ab ext=.txt")
    assert f"file={path.name!r}" in plain
    shown = f"{CLIENT_DIR}/{path.name}"
    with_shown = file_ref(path, "0123456789abcdef0123456789abcdef", shown=shown)
    assert f"file={shown!r}" in with_shown


# --- Static guard (design, Test impact) --------------------------------------

GUARDED_FILES = ("ingest.py", "watcher.py", "src/readers.py", "src/utils.py")
# Names that hold a client file or folder name, a path below a configured root, or
# an exception (whose text can quote a path).
CLIENT_NAMES = {"file_path", "rel_path", "fname", "filename", "folder", "file", "e", "exc", "f"}
CLIENT_ATTRS = {"name", "stem", "parent", "src_path", "suffix"}
SAFE_CALLS = {"content", "file_ref", "safe_ext", "len", "type"}
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
# Compliant lines listed in the design: lock file and configuration roots.
ALLOWED_FUNCTIONS = {
    ("ingest.py", "release_lock"),
    ("watcher.py", "wait_for_drop_zone"),
    ("watcher.py", "run_watcher"),
    ("src/utils.py", "load_global_triggers"),
    ("src/utils.py", "load_chat_triggers"),
}
ALLOWED_EXPRESSIONS = {"root_dir.name"}


def _call_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _unsafe_parts(node) -> list[str]:
    """Sub-expressions of `node` that interpolate a client value outside a safe call."""
    if isinstance(node, ast.Call) and _call_name(node.func) in SAFE_CALLS:
        return []
    if ast.unparse(node) in ALLOWED_EXPRESSIONS:
        return []
    if isinstance(node, ast.Name) and node.id in CLIENT_NAMES:
        return [node.id]
    if isinstance(node, ast.Attribute) and node.attr in CLIENT_ATTRS:
        return [ast.unparse(node)]
    found = []
    for child in ast.iter_child_nodes(node):
        found.extend(_unsafe_parts(child))
    return found


def _is_log_call(call: ast.Call, rel: str) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        return isinstance(func.value, ast.Name) and func.value.id == "log"
    return rel == "watcher.py" and isinstance(func, ast.Name) and func.id == "print"


def _violations(rel: str) -> list[str]:
    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
    found = []

    def visit(node, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call) and _is_log_call(node, rel) and (rel, function) not in ALLOWED_FUNCTIONS:
            for arg in node.args:
                values = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
                for value in values:
                    expr = value.value if isinstance(value, ast.FormattedValue) else value
                    if isinstance(expr, ast.Constant):
                        continue
                    for part in _unsafe_parts(expr):
                        found.append(f"{rel}:{node.lineno} ({function}) interpolates {part}")
            for kw in node.keywords:
                if kw.arg == "exc_info" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    found.append(f"{rel}:{node.lineno} ({function}) always logs a traceback (exc_info=True)")
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, None)
    return found


@pytest.mark.parametrize("rel", GUARDED_FILES)
def test_static_guard_no_file_name_or_exception_text_in_ingestion_log_calls(rel):
    violations = _violations(rel)
    assert not violations, (
        "log calls interpolate a file or folder name or exception text outside "
        "content()/file_ref()/safe_ext():\n" + "\n".join(violations)
    )
