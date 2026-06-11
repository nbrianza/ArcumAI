# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""
Tier 3 integration tests for the ingest pipeline.

ingest.py calls init_settings() at module level (loads the Ollama LLM +
HuggingFace embedding model).  We patch that call on the FIRST import so
the test suite does not block waiting for Ollama or download model weights.
Settings.text_splitter is set manually so read_and_chunk_file works.
"""
import sys
import importlib
from pathlib import Path
from unittest.mock import patch


def _get_ingest():
    """
    Return the ingest module.  On first call, imports it with init_settings()
    mocked so no ML models are loaded.  Subsequent calls return the cached module.
    """
    if 'ingest' not in sys.modules:
        with patch('src.config.init_settings'):
            importlib.import_module('ingest')
    return sys.modules['ingest']


def _ensure_text_splitter():
    """Set Settings.text_splitter if not already configured (needed by read_and_chunk_file)."""
    from llama_index.core import Settings
    from llama_index.core.node_parser import SentenceSplitter
    if Settings.text_splitter is None:
        Settings.text_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)


# --- Lock file ---

def test_acquire_lock_succeeds_when_no_lock_exists(tmp_path, monkeypatch):
    ingest = _get_ingest()
    monkeypatch.setattr(ingest, 'LOCK_FILE', tmp_path / 'test.lock')
    assert ingest.acquire_lock() is True
    ingest.release_lock()


def test_acquire_lock_fails_when_already_locked(tmp_path, monkeypatch):
    ingest = _get_ingest()
    monkeypatch.setattr(ingest, 'LOCK_FILE', tmp_path / 'test.lock')
    ingest.acquire_lock()
    assert ingest.acquire_lock() is False
    ingest.release_lock()


def test_release_lock_removes_lock_file(tmp_path, monkeypatch):
    ingest = _get_ingest()
    lock = tmp_path / 'test.lock'
    monkeypatch.setattr(ingest, 'LOCK_FILE', lock)
    ingest.acquire_lock()
    ingest.release_lock()
    assert not lock.exists()


def test_release_lock_is_safe_when_no_lock_exists(tmp_path, monkeypatch):
    ingest = _get_ingest()
    monkeypatch.setattr(ingest, 'LOCK_FILE', tmp_path / 'never_created.lock')
    ingest.release_lock()  # must not raise


# --- read_and_chunk_file ---

def test_read_and_chunk_txt_file_returns_nodes(tmp_path):
    _ensure_text_splitter()
    ingest = _get_ingest()
    txt = tmp_path / "contract.txt"
    txt.write_text(
        "Il presente contratto è stipulato tra le parti indicate di seguito. "
        "Le condizioni generali si applicano a tutti i servizi erogati. "
        "Il pagamento deve essere effettuato entro 30 giorni dalla fattura.",
        encoding="utf-8"
    )
    nodes, status = ingest.read_and_chunk_file(txt)
    assert nodes is not None
    assert len(nodes) >= 1
    assert status not in ("ERROR", "EMPTY", "SKIP_EXT")


def test_read_and_chunk_txt_nodes_carry_file_metadata(tmp_path):
    _ensure_text_splitter()
    ingest = _get_ingest()
    txt = tmp_path / "memo.txt"
    txt.write_text("Nota interna: riunione del consiglio il 15 marzo.", encoding="utf-8")
    nodes, _ = ingest.read_and_chunk_file(txt)
    assert nodes is not None
    for node in nodes:
        assert "file_hash" in node.metadata
        assert "filename" in node.metadata
        assert node.metadata["filename"] == "memo.txt"


def test_read_and_chunk_empty_txt_returns_empty(tmp_path):
    ingest = _get_ingest()
    txt = tmp_path / "empty.txt"
    txt.write_text("   \n  ", encoding="utf-8")  # whitespace only
    nodes, status = ingest.read_and_chunk_file(txt)
    assert nodes is None
    assert status == "EMPTY"


def test_read_and_chunk_unsupported_extension_returns_skip(tmp_path):
    ingest = _get_ingest()
    f = tmp_path / "binary.exe"
    f.write_bytes(b"\x4d\x5a\x00\x00")
    nodes, status = ingest.read_and_chunk_file(f)
    assert nodes is None
    assert status == "SKIP_EXT"


def test_read_and_chunk_nonexistent_file_returns_error(tmp_path):
    ingest = _get_ingest()
    nodes, status = ingest.read_and_chunk_file(tmp_path / "ghost.txt")
    assert nodes is None
    assert status == "ERROR"


# --- get_db_components (real ChromaDB, temp dir) ---

def test_get_db_components_creates_chroma_collection(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    ingest = _get_ingest()
    monkeypatch.setattr(ingest, 'DB_PATH', tmp_path / "chroma_test")
    monkeypatch.setattr(ingest, 'COLLECTION_NAME', 'test_col')
    # Mock VectorStoreIndex so the test only exercises the ChromaDB wiring
    mock_vsi = MagicMock()
    mock_vsi.from_vector_store.return_value = MagicMock()
    monkeypatch.setattr(ingest, 'VectorStoreIndex', mock_vsi)
    index, collection = ingest.get_db_components()
    assert collection is not None
    mock_vsi.from_vector_store.assert_called_once()


def test_get_db_components_collection_starts_empty(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    ingest = _get_ingest()
    monkeypatch.setattr(ingest, 'DB_PATH', tmp_path / "chroma2")
    monkeypatch.setattr(ingest, 'COLLECTION_NAME', 'empty_col')
    mock_vsi = MagicMock()
    mock_vsi.from_vector_store.return_value = MagicMock()
    monkeypatch.setattr(ingest, 'VectorStoreIndex', mock_vsi)
    _, collection = ingest.get_db_components()
    assert collection.count() == 0
