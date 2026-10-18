# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #36: authenticated, owner-scoped `/documents`.

The tests follow the approved design on the issue (owner decisions D1 to D8):

- a new module ``src.documents`` exposes a FastAPI ``APIRouter`` (named
  ``router`` or ``documents_router``) serving ``GET /documents/{rel_path:path}``
  plus the helpers ``resolve_archive_path(rel_path, root)``,
  ``document_owner(resolved, root)`` and ``can_read(username, role, owner)``;
- the archive root is the module-level ``ARCHIVE_DIR`` of ``src.documents``;
- the current user is read from NiceGUI ``app.storage.user`` and the role is
  re-read from the users file on every request (D6);
- a document is owned by the user named like its first folder under the
  archive, compared case-insensitively (D1, D2); documents without an owner
  are ADMIN-only (D3); unauthenticated requests get 401 (D4).

Instead of ``dependency_overrides`` (which would tie the tests to the return
type of ``current_user``), the tests replace the ``Storage.user`` property of
NiceGUI with a synthetic session dict and point ``USERS_FILE`` to a synthetic
users file in ``tmp_path``. This exercises the real dependency end to end.

All files, users and contents are synthetic and live under ``tmp_path``.
The real archive and the real ``users.json`` are never touched.
Log records are captured by swapping the handlers of the ``ArcumServer``
logger (it has ``propagate = False``, so ``caplog`` cannot see it) and by
adding a handler on the root logger for loggers that propagate.
"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import pytest

# --- Synthetic data ----------------------------------------------------------

ALICE_TEXT = b"SYNTHETIC-ALPHA content owned by alice 36"
BOB_TEXT = b"SYNTHETIC-BRAVO content owned by bob 36"
ROOT_TEXT = b"SYNTHETIC-CHARLIE unowned root content 36"
OUTSIDE_TEXT = b"SYNTHETIC-DELTA content outside the archive 36"
STRAY_TEXT = b"SYNTHETIC-FOXTROT content under an unknown folder 36"
CAROL_TEXT = b"SYNTHETIC-GOLF content in a capitalised folder 36"

# Tokens taken from file and folder names. None of them may appear in a log
# entry for a denied request (AC6).
NAME_TOKENS = ("brief", "alpha", "bravo", "charlie", "escape", "delta",
               "ghost", "echo", "foxtrot", "stray", "golf", "alice/", "bob/")

USERS = {
    "alice": {"pw_hash": "synthetic-not-a-hash", "role": "LEGAL",
              "name": "Alice Example", "outlook_id": ""},
    "bob": {"pw_hash": "synthetic-not-a-hash", "role": "DEFAULT",
            "name": "Bob Example", "outlook_id": ""},
    "carol": {"pw_hash": "synthetic-not-a-hash", "role": "EXECUTIVE",
              "name": "Carol Example", "outlook_id": ""},
    "boss": {"pw_hash": "synthetic-not-a-hash", "role": "ADMIN",
             "name": "Boss Example", "outlook_id": ""},
}


def _session(username: str, role: str | None = None) -> dict:
    """A logged-in NiceGUI session as written by the login page."""
    if role is None:
        role = USERS[username]["role"]
    return {"authenticated": True, "username": username, "role": role,
            "full_name": f"{username} synthetic"}


# --- Log capture -------------------------------------------------------------

class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


# --- Environment fixture -----------------------------------------------------

class _Env:
    """Synthetic archive, users file, session and log capture for one test."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.archive = tmp_path / "archive"
        (self.archive / "alice").mkdir(parents=True)
        (self.archive / "bob").mkdir()
        (self.archive / "Carol").mkdir()
        (self.archive / "stray").mkdir()
        (self.archive / "alice" / "brief_alpha.txt").write_bytes(ALICE_TEXT)
        (self.archive / "bob" / "brief_bravo.txt").write_bytes(BOB_TEXT)
        (self.archive / "Carol" / "brief_golf.txt").write_bytes(CAROL_TEXT)
        (self.archive / "stray" / "brief_foxtrot.txt").write_bytes(STRAY_TEXT)
        (self.archive / "root_charlie.txt").write_bytes(ROOT_TEXT)
        self.outside = tmp_path / "escape_delta.txt"
        self.outside.write_bytes(OUTSIDE_TEXT)

        self.users_file = tmp_path / "users_synthetic.json"
        self.users_file.write_text(json.dumps(USERS), encoding="utf-8")

        self.session: dict = {}
        self.session_error: Exception | None = None

        self.handler = _ListHandler()
        server_logger = logging.getLogger("ArcumServer")
        monkeypatch.setattr(server_logger, "handlers", [self.handler])
        root_logger = logging.getLogger()
        root_logger.addHandler(self.handler)
        self._root_logger = root_logger

        self._client = None
        self.docs = None

    # Called inside each test, so a missing module is a test failure, not a
    # fixture error.
    def client(self):
        if self._client is not None:
            return self._client
        import src.auth as auth_module
        import src.config as config_module
        import src.documents as docs
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from nicegui.storage import Storage

        mp = self.monkeypatch
        # Archive root: the design names ARCHIVE_DIR in src.documents.
        mp.setattr(docs, "ARCHIVE_DIR", self.archive)
        mp.setattr(config_module, "ARCHIVE_DIR", self.archive)
        # Users file: never read the real users.json.
        mp.setattr(auth_module, "USERS_FILE", self.users_file)
        mp.setattr(config_module, "USERS_FILE", self.users_file)
        mp.setattr(docs, "USERS_FILE", self.users_file, raising=False)

        env = self

        def _fake_user(_self):
            if env.session_error is not None:
                raise env.session_error
            return env.session

        mp.setattr(Storage, "user", property(_fake_user))

        router = getattr(docs, "router", None) or getattr(docs, "documents_router")
        app = FastAPI()
        app.include_router(router)
        self.docs = docs
        self._client = TestClient(app, raise_server_exceptions=False)
        return self._client

    def login(self, username: str, role: str | None = None):
        self.session = _session(username, role)

    def warnings(self) -> list[logging.LogRecord]:
        seen, out = set(), []
        for r in self.handler.records:
            if r.levelno >= logging.WARNING and id(r) not in seen:
                seen.add(id(r))
                out.append(r)
        return out

    def clear_logs(self):
        self.handler.records.clear()

    def close(self):
        self._root_logger.removeHandler(self.handler)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _Env(tmp_path, monkeypatch)
    yield e
    e.close()


_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="backslash separator is Windows-only")


def _url(rel: str) -> str:
    return "/documents/" + rel


def _assert_no_content(resp, *contents: bytes):
    for c in contents:
        assert c not in resp.content


# =============================================================================
# AC1: no session, no document
# =============================================================================

def test_ac1_unauthenticated_request_gets_401_without_content(env):
    client = env.client()
    env.session = {}
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 401
    _assert_no_content(resp, ALICE_TEXT)


@pytest.mark.parametrize("session", [
    {},
    {"authenticated": False, "username": "alice", "role": "LEGAL"},
    {"authenticated": True, "role": "ADMIN"},
    {"username": "boss", "role": "ADMIN"},
], ids=["empty", "flag-false", "no-username", "no-flag"])
def test_ac1_incomplete_session_is_unauthenticated(env, session):
    client = env.client()
    env.session = session
    resp = client.get(_url("root_charlie.txt"))
    assert resp.status_code == 401
    _assert_no_content(resp, ROOT_TEXT)


def test_ac1_admin_cannot_bypass_without_session_on_unowned_file(env):
    client = env.client()
    env.session = {}
    for rel in ("root_charlie.txt", "bob/brief_bravo.txt", "stray/brief_foxtrot.txt"):
        resp = client.get(_url(rel))
        assert resp.status_code == 401, rel
        _assert_no_content(resp, ROOT_TEXT, BOB_TEXT, STRAY_TEXT)


def test_ac1_session_unavailable_is_401_not_500(env):
    client = env.client()
    env.session_error = RuntimeError("synthetic: no request context")
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 401
    _assert_no_content(resp, ALICE_TEXT)


def test_ac1_static_mount_removed_from_main_nice():
    # main_nice.py cannot be imported in tests (it loads models), so check the
    # source: the unauthenticated static mount of the archive must be gone.
    source = (Path(__file__).resolve().parent.parent / "main_nice.py").read_text(encoding="utf-8")
    assert "add_static_files('/documents'" not in source
    assert 'add_static_files("/documents"' not in source


# =============================================================================
# AC2: authorized users download as today (route level)
# =============================================================================

def test_ac2_owner_downloads_own_document(env):
    client = env.client()
    env.login("alice")
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 200
    assert resp.content == ALICE_TEXT


def test_ac2_admin_downloads_any_document_including_unowned(env):
    client = env.client()
    env.login("boss")
    expected = {
        "alice/brief_alpha.txt": ALICE_TEXT,
        "bob/brief_bravo.txt": BOB_TEXT,
        "root_charlie.txt": ROOT_TEXT,
        "stray/brief_foxtrot.txt": STRAY_TEXT,
    }
    for rel, content in expected.items():
        resp = client.get(_url(rel))
        assert resp.status_code == 200, rel
        assert resp.content == content, rel


def test_ac2_owner_match_is_case_insensitive_d2(env):
    client = env.client()
    env.login("carol")
    resp = client.get(_url("Carol/brief_golf.txt"))
    assert resp.status_code == 200
    assert resp.content == CAROL_TEXT


def test_ac2_url_encoded_name_as_built_by_footer(env):
    # The footer builds links with quote(path, safe="/").
    name = "brief alpha (v2) d\u00e9j\u00e0.txt"
    (env.archive / "alice" / name).write_bytes(ALICE_TEXT)
    client = env.client()
    env.login("alice")
    resp = client.get(_url(quote(f"alice/{name}", safe="/")))
    assert resp.status_code == 200
    assert resp.content == ALICE_TEXT


def test_ac2_head_request_is_supported_like_static_files(env):
    # Design section 5: StaticFiles answered HEAD; declare GET and HEAD.
    client = env.client()
    env.login("alice")
    resp = client.head(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 200


def test_ac2_response_headers_nosniff_and_no_store(env):
    # Design section 2b, step 5.
    client = env.client()
    env.login("alice")
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options", "").lower() == "nosniff"
    cache = resp.headers.get("cache-control", "").lower()
    assert "no-store" in cache
    assert "private" in cache


# =============================================================================
# AC3: paths resolving outside the archive
# =============================================================================

def _outside_requests(env) -> list[str]:
    absolute = env.outside.resolve().as_posix()
    # Backslash is a path separator only on Windows (the target platform and CI).
    backslash = []
    if os.name == "nt":
        backslash = ["..%5Cescape_delta.txt", "alice%5C..%5C..%5Cescape_delta.txt"]
    return backslash + [
        "%2e%2e/escape_delta.txt",
        "alice/%2e%2e/%2e%2e/escape_delta.txt",
        "%2e%2e%2fescape_delta.txt",
        quote(absolute, safe="/"),
        quote(absolute, safe=""),
        "%2F" + quote(absolute.lstrip("/"), safe="/"),
    ]


def test_ac3_traversal_outside_archive_is_refused(env):
    client = env.client()
    env.login("alice")
    for rel in _outside_requests(env):
        resp = client.get(_url(rel))
        assert resp.status_code in (403, 404), rel
        _assert_no_content(resp, OUTSIDE_TEXT)


def test_ac3_traversal_outside_archive_is_refused_for_admin(env):
    client = env.client()
    env.login("boss")
    for rel in _outside_requests(env):
        resp = client.get(_url(rel))
        assert resp.status_code in (403, 404), rel
        _assert_no_content(resp, OUTSIDE_TEXT)


def test_ac3_traversal_inside_archive_cannot_bypass_ownership(env):
    client = env.client()
    env.login("alice")
    rels = ["alice/%2e%2e/bob/brief_bravo.txt", "alice/%2e%2e/root_charlie.txt"]
    if os.name == "nt":
        rels.append("alice%5C..%5Cbob%5Cbrief_bravo.txt")
    for rel in rels:
        resp = client.get(_url(rel))
        assert resp.status_code == 403, rel
        _assert_no_content(resp, BOB_TEXT, ROOT_TEXT)


def test_ac3_symlink_pointing_outside_is_refused(env):
    link = env.archive / "alice" / "link_escape.txt"
    try:
        os.symlink(env.outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    client = env.client()
    env.login("alice")
    resp = client.get(_url("alice/link_escape.txt"))
    assert resp.status_code in (403, 404)
    _assert_no_content(resp, OUTSIDE_TEXT)


def test_ac3_resolve_archive_path_accepts_inside_paths(env):
    env.client()
    docs = env.docs
    root = env.archive
    assert docs.resolve_archive_path("alice/brief_alpha.txt", root) == \
        (root / "alice" / "brief_alpha.txt").resolve()
    # Non-strict: a missing file inside the archive still resolves; the route
    # decides 404 later.
    assert docs.resolve_archive_path("alice/ghost_echo.txt", root) == \
        (root / "alice" / "ghost_echo.txt").resolve()
    # ".." that stays inside the archive resolves to the inner target.
    assert docs.resolve_archive_path("alice/../bob/brief_bravo.txt", root) == \
        (root / "bob" / "brief_bravo.txt").resolve()


@pytest.mark.parametrize("rel", [
    "",
    ".",
    "alice/..",
    "../escape_delta.txt",
    "alice/../../escape_delta.txt",
    pytest.param("..\\escape_delta.txt", marks=_WINDOWS_ONLY),
    pytest.param("alice\\..\\..\\escape_delta.txt", marks=_WINDOWS_ONLY),
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "C:escape_delta.txt",
    "alice/brief_alpha.txt:hidden_stream",
    "alice/brief\x00alpha.txt",
    "//synthetic-host/share/escape_delta.txt",
], ids=["empty", "dot", "back-to-root", "dotdot", "nested-dotdot", "backslash-dotdot",
        "nested-backslash", "drive-fwd", "drive-back", "drive-relative", "ads", "nul", "unc"])
def test_ac3_resolve_archive_path_rejects_unsafe(env, rel):
    env.client()
    assert env.docs.resolve_archive_path(rel, env.archive) is None


def test_ac3_resolve_archive_path_rejects_absolute_outside(env):
    env.client()
    docs = env.docs
    assert docs.resolve_archive_path(str(env.outside.resolve()), env.archive) is None
    assert docs.resolve_archive_path(env.outside.resolve().as_posix(), env.archive) is None


# =============================================================================
# AC4: missing file, no server path in the body
# =============================================================================

def _assert_no_server_path(env, resp):
    body = resp.text
    for p in (env.tmp_path, env.archive):
        for form in (str(p), str(p.resolve()), p.as_posix(), p.resolve().as_posix()):
            assert form not in body
    assert "ghost" not in body
    assert "echo" not in body.lower()


def test_ac4_missing_own_file_is_404_without_path(env):
    client = env.client()
    env.login("alice")
    resp = client.get(_url("alice/ghost_echo.txt"))
    assert resp.status_code == 404
    _assert_no_server_path(env, resp)


def test_ac4_missing_file_for_admin_is_404_without_path(env):
    client = env.client()
    env.login("boss")
    for rel in ("ghost_echo.txt", "bob/ghost_echo.txt", "nowhere/ghost_echo.txt"):
        resp = client.get(_url(rel))
        assert resp.status_code == 404, rel
        _assert_no_server_path(env, resp)


def test_ac4_directory_is_not_served_or_listed(env):
    client = env.client()
    env.login("boss")
    resp = client.get(_url("alice"))
    assert resp.status_code == 404
    assert "brief_alpha" not in resp.text
    _assert_no_server_path(env, resp)


# =============================================================================
# AC5: forbidden for non-owners (owner rule: ADMIN all, others own only)
# =============================================================================

def test_ac5_other_users_document_is_403(env):
    client = env.client()
    env.login("alice")
    resp = client.get(_url("bob/brief_bravo.txt"))
    assert resp.status_code == 403
    _assert_no_content(resp, BOB_TEXT)


def test_ac5_unowned_documents_are_admin_only_d3(env):
    client = env.client()
    env.login("alice")
    for rel, content in (("root_charlie.txt", ROOT_TEXT),
                         ("stray/brief_foxtrot.txt", STRAY_TEXT)):
        resp = client.get(_url(rel))
        assert resp.status_code == 403, rel
        _assert_no_content(resp, content)


def test_ac5_missing_file_in_other_users_folder_is_403_not_404(env):
    # Ownership is checked before existence: no file-name oracle.
    client = env.client()
    env.login("alice")
    resp = client.get(_url("bob/ghost_echo.txt"))
    assert resp.status_code == 403
    resp = client.get(_url("ghost_echo.txt"))
    assert resp.status_code == 403


def test_ac5_folder_prefix_is_not_ownership(env):
    # "alice" must not own "alicex/..." or "alice_old/...".
    (env.archive / "alicex").mkdir()
    (env.archive / "alicex" / "brief_alpha.txt").write_bytes(STRAY_TEXT)
    client = env.client()
    env.login("alice")
    resp = client.get(_url("alicex/brief_alpha.txt"))
    assert resp.status_code == 403
    _assert_no_content(resp, STRAY_TEXT)


def test_ac5_role_is_reread_from_users_file_d6(env):
    # The session still says ADMIN, the users file says LEGAL.
    client = env.client()
    env.login("alice", role="ADMIN")
    resp = client.get(_url("bob/brief_bravo.txt"))
    assert resp.status_code == 403
    _assert_no_content(resp, BOB_TEXT)
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 200
    assert resp.content == ALICE_TEXT


def test_ac5_promotion_in_users_file_applies_without_new_login_d6(env):
    users = dict(USERS)
    users["alice"] = dict(USERS["alice"], role="ADMIN")
    env.users_file.write_text(json.dumps(users), encoding="utf-8")
    client = env.client()
    env.login("alice", role="LEGAL")
    resp = client.get(_url("bob/brief_bravo.txt"))
    assert resp.status_code == 200
    assert resp.content == BOB_TEXT


def test_ac5_user_removed_from_users_file_gets_401_d6(env):
    client = env.client()
    env.session = {"authenticated": True, "username": "zoe", "role": "ADMIN"}
    resp = client.get(_url("root_charlie.txt"))
    assert resp.status_code == 401
    _assert_no_content(resp, ROOT_TEXT)


@pytest.mark.parametrize("username,role,owner,expected", [
    ("alice", "LEGAL", "alice", True),
    ("alice", "LEGAL", "bob", False),
    ("alice", "LEGAL", None, False),
    ("alice", "LEGAL", "Alice", True),
    ("ALICE", "DEFAULT", "alice", True),
    ("alice", "LEGAL", "alicex", False),
    ("boss", "ADMIN", None, True),
    ("boss", "ADMIN", "bob", True),
    ("alice", "EXECUTIVE", "", False),
], ids=["own", "other", "unowned", "case-owner", "case-user", "prefix",
        "admin-unowned", "admin-other", "empty-owner"])
def test_ac5_can_read(env, username, role, owner, expected):
    env.client()
    assert env.docs.can_read(username, role, owner) is expected


def test_ac5_document_owner_is_first_folder(env):
    env.client()
    docs = env.docs
    root = env.archive
    owner = docs.document_owner((root / "alice" / "brief_alpha.txt").resolve(), root)
    assert owner is not None and owner.casefold() == "alice"
    nested = root / "bob" / "sub" / "deeper" / "brief_bravo.txt"
    owner = docs.document_owner(nested.resolve(), root)
    assert owner is not None and owner.casefold() == "bob"


def test_ac5_document_owner_is_none_at_archive_root(env):
    env.client()
    docs = env.docs
    assert docs.document_owner((env.archive / "root_charlie.txt").resolve(), env.archive) is None


# =============================================================================
# AC6: one WARNING per denial, with IP and reason, without name or path
# =============================================================================

def _assert_one_clean_warning(env, reason: str):
    warnings = env.warnings()
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    msg = warnings[0].getMessage()
    assert "testclient" in msg  # TestClient client host
    assert reason in msg.lower()
    low = msg.lower()
    for token in NAME_TOKENS:
        assert token not in low, token
    for p in (env.tmp_path, env.archive):
        for form in (str(p), p.as_posix(), str(p.resolve()), p.resolve().as_posix()):
            assert form.lower() not in low


def test_ac6_unauthenticated_denial_logs_one_clean_warning(env):
    client = env.client()
    env.session = {}
    env.clear_logs()
    client.get(_url("alice/brief_alpha.txt"))
    _assert_one_clean_warning(env, "unauthenticated")


def test_ac6_outside_archive_denial_logs_one_clean_warning(env):
    client = env.client()
    env.login("alice")
    for rel in _outside_requests(env):
        env.clear_logs()
        resp = client.get(_url(rel))
        assert resp.status_code in (403, 404), rel
        _assert_one_clean_warning(env, "outside archive")


def test_ac6_forbidden_denial_logs_one_clean_warning(env):
    client = env.client()
    env.login("alice")
    for rel in ("bob/brief_bravo.txt", "root_charlie.txt", "bob/ghost_echo.txt"):
        env.clear_logs()
        client.get(_url(rel))
        _assert_one_clean_warning(env, "forbidden")


def test_ac6_not_found_denial_logs_one_clean_warning(env):
    # Design section 2b: "not found" is also a logged denial reason.
    client = env.client()
    env.login("alice")
    env.clear_logs()
    client.get(_url("alice/ghost_echo.txt"))
    _assert_one_clean_warning(env, "not found")


def test_ac6_successful_download_logs_no_warning(env):
    client = env.client()
    env.login("alice")
    env.clear_logs()
    resp = client.get(_url("alice/brief_alpha.txt"))
    assert resp.status_code == 200
    assert env.warnings() == []
