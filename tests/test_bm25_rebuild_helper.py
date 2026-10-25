# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Developer unit tests for the #71 BM25 rebuild helper and ingest.py option.

- rebuild_bm25_index lets exceptions propagate (requirement of the #73 design);
- ``ingest.py --rebuild-bm25`` returns 1 when the rebuild fails or the lock is
  held, so the documented PowerShell step can check $LASTEXITCODE.
Synthetic data only; every path points to tmp_path.
"""
import importlib
import logging
import sys
from unittest.mock import patch

import pytest


class _SyntheticError(RuntimeError):
    pass


def _raise(_collection):
    raise _SyntheticError("synthetic failure")


def test_rebuild_helper_propagates_loader_exceptions(tmp_path):
    from src.utils import rebuild_bm25_index
    with pytest.raises(_SyntheticError):
        rebuild_bm25_index(object(), tmp_path / "bm25", nodes_loader=_raise)
    assert not (tmp_path / "bm25" / "retriever.json").exists()


def test_rebuild_helper_returns_zero_without_nodes(tmp_path):
    from src.utils import rebuild_bm25_index
    assert rebuild_bm25_index(object(), tmp_path / "bm25", nodes_loader=lambda _c: []) == 0
    assert not (tmp_path / "bm25").exists()


def _ingest(monkeypatch, tmp_path):
    if "ingest" not in sys.modules:
        with patch("src.config.init_settings"):
            importlib.import_module("ingest")
    ingest = sys.modules["ingest"]
    monkeypatch.setattr(ingest, "BM25_PATH", tmp_path / "bm25")
    monkeypatch.setattr(ingest, "LOCK_FILE", tmp_path / "ingestion.lock")
    monkeypatch.setattr(ingest, "get_db_components", lambda: (None, object()))
    monkeypatch.setattr(ingest, "get_all_nodes_from_chroma", _raise)
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--rebuild-bm25"])
    monkeypatch.setattr(logging.getLogger("ArcumIngestion"), "handlers", [logging.NullHandler()])
    return ingest


def test_rebuild_option_returns_one_on_failure_and_releases_lock(monkeypatch, tmp_path):
    ingest = _ingest(monkeypatch, tmp_path)
    assert ingest.main() == 1
    assert not (tmp_path / "ingestion.lock").exists()


def test_rebuild_option_returns_one_when_lock_is_held(monkeypatch, tmp_path):
    ingest = _ingest(monkeypatch, tmp_path)
    (tmp_path / "ingestion.lock").write_text("LOCKED by a synthetic run", encoding="utf-8")
    assert ingest.main() == 1
    assert (tmp_path / "ingestion.lock").exists()
