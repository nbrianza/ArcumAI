# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Unit tests for src.documents.source_link (Sources links in the chat, #36 D5).

Synthetic files under tmp_path only; the real archive is never touched.
"""
import pytest


@pytest.fixture
def archive(tmp_path, monkeypatch):
    import src.documents as docs
    import src.utils as utils

    root = tmp_path / "archive"
    (root / "alice").mkdir(parents=True)
    (root / "bob").mkdir()
    (root / "alice" / "same name.txt").write_bytes(b"synthetic alice")
    (root / "bob" / "same name.txt").write_bytes(b"synthetic bob")
    (root / "loose.txt").write_bytes(b"synthetic loose")
    monkeypatch.setattr(docs, "ARCHIVE_DIR", root)
    monkeypatch.setattr(utils, "ARCHIVE_DIR", root)
    return root


def test_link_uses_indexed_file_path_for_duplicate_names(archive):
    from src.documents import source_link
    assert source_link("bob/same name.txt", "same name.txt", "bob", "DEFAULT") == \
        "/documents/bob/same%20name.txt"


def test_no_link_for_document_the_user_cannot_open(archive):
    from src.documents import source_link
    assert source_link("bob/same name.txt", "same name.txt", "alice", "LEGAL") is None
    assert source_link("loose.txt", "loose.txt", "alice", "LEGAL") is None


def test_admin_gets_link_for_unowned_document(archive):
    from src.documents import source_link
    assert source_link("loose.txt", "loose.txt", "boss", "ADMIN") == "/documents/loose.txt"


def test_falls_back_to_name_search_when_file_path_is_stale(archive):
    from src.documents import source_link
    url = source_link("moved/elsewhere.txt", "loose.txt", "boss", "ADMIN")
    assert url == "/documents/loose.txt"


def test_unsafe_file_path_is_ignored(archive):
    from src.documents import source_link
    # The unsafe indexed path is never used; the name search fallback is.
    assert source_link("../loose.txt", "loose.txt", "boss", "ADMIN") == "/documents/loose.txt"
    assert source_link("../outside.txt", "missing.txt", "alice", "LEGAL") is None
