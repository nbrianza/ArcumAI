# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #50: the session cookie is marked ``Secure``
when TLS is enabled (direct TLS or ``TLS_TERMINATED_UPSTREAM=true``).

The tests follow the approved design (decisions D1 to D5 on the issue):

- D1: ``Secure`` when direct TLS is active OR ``TLS_TERMINATED_UPSTREAM`` is
  true, never otherwise.
- D2: pure helper ``session_cookie_kwargs(tls_enabled, upstream)`` in
  ``src/tls.py``, returning ``{"https_only": True}`` or ``{}``; ``main_nice.py``
  passes it to ``ui.run`` as ``session_middleware_kwargs``.
- D3: criteria 1 and 2 are evidenced by (a) unit tests of the helper, (b) a
  Starlette ``TestClient`` app using ``SessionMiddleware`` with the helper
  output, inspecting the raw ``Set-Cookie`` header, and (c) a static (AST)
  check of the ``ui.run`` call. ``main_nice.py`` is never imported because it
  loads models at import time. The real-browser HTTPS check is manual.
- D5: one bullet in the Consequences of ADR 0001.

Privacy: the session secret is synthetic, and no cookie value is ever placed
in an assertion or printed. Assertions only look at the cookie name and its
attribute tokens, after the ``name=value`` pair has been split off.
"""
import ast
import re
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent

SYNTHETIC_SESSION_SECRET = "synthetic-session-secret-not-real-7Hq2"
SYNTHETIC_BROWSER_ID = "synthetic-browser-id-0001"

# (tls_enabled, upstream) -> expected Secure
TLS_CONFIGS = {
    "direct_tls": (True, False),
    "upstream_tls": (False, True),
    "direct_and_upstream": (True, True),
    "no_tls": (False, False),
}

# Base URL the browser (or proxy) uses to reach ArcumAI in each configuration.
# Direct TLS: HTTPS. Upstream: the proxy forwards over plain HTTP.
# No TLS: plain HTTP on localhost.
BASE_URLS = {
    "direct_tls": "https://testserver",
    "upstream_tls": "http://testserver",
    "direct_and_upstream": "https://testserver",
    "no_tls": "http://testserver",
}


def _tls():
    """Import the module under test lazily, so a missing helper fails each
    test with a clear error instead of a collection error."""
    import src.tls as tls_module
    return tls_module


def _session_cookie_kwargs(tls_enabled, upstream):
    return _tls().session_cookie_kwargs(tls_enabled, upstream)


# --- Test app ----------------------------------------------------------------

async def _login(request: Request):
    # Stands in for NiceGUI's RequestTrackingMiddleware writing the browser id
    # and the login page marking the browser as authenticated.
    request.session["id"] = SYNTHETIC_BROWSER_ID
    return JSONResponse({"ok": True})


async def _whoami(request: Request):
    return JSONResponse({"has_session": "id" in request.session})


async def _logout(request: Request):
    request.session.clear()
    return JSONResponse({"ok": True})


def _app(middleware_kwargs):
    return Starlette(
        routes=[
            Route("/login", _login),
            Route("/whoami", _whoami),
            Route("/logout", _logout),
        ],
        middleware=[
            Middleware(SessionMiddleware, secret_key=SYNTHETIC_SESSION_SECRET,
                       **middleware_kwargs),
        ],
    )


def _cookie_parts(response, name="session"):
    """Return (cookie_name, attribute_tokens) for every Set-Cookie header that
    sets ``name``. The cookie value is dropped and never returned."""
    result = []
    for header in response.headers.get_list("set-cookie"):
        first, _, rest = header.partition(";")
        cookie_name = first.split("=", 1)[0].strip()
        if cookie_name != name:
            continue
        tokens = [t.strip().lower() for t in rest.split(";") if t.strip()]
        result.append((cookie_name, tokens))
    return result


def _login_cookie_attrs(middleware_kwargs, base_url):
    client = TestClient(_app(middleware_kwargs), base_url=base_url)
    response = client.get("/login")
    assert response.status_code == 200
    parts = _cookie_parts(response)
    assert parts, "login response sets no session cookie"
    return parts


def _logout_cookie_attrs(middleware_kwargs, base_url):
    client = TestClient(_app(middleware_kwargs), base_url=base_url)
    login = client.get("/login")
    assert login.status_code == 200
    # Send the cookie back explicitly: httpx would not resend a Secure cookie
    # over http://, which is browser-like but not what this test is about.
    cookie_header = _raw_cookie_pair(login)
    response = client.get("/logout", headers={"Cookie": cookie_header})
    assert response.status_code == 200
    parts = _cookie_parts(response)
    assert parts, "logout response does not clear the session cookie"
    return parts


def _raw_cookie_pair(response):
    for header in response.headers.get_list("set-cookie"):
        first = header.split(";", 1)[0]
        if first.split("=", 1)[0].strip() == "session":
            return first.strip()
    raise AssertionError("no session cookie in response")


def _attrs_without_secure(tokens):
    return sorted(t for t in tokens if t != "secure")


# --- (a) Helper unit tests (criteria 1, 2, 3) --------------------------------

def test_criterion1_helper_direct_tls_returns_https_only():
    assert _session_cookie_kwargs(True, False) == {"https_only": True}


def test_criterion2_helper_upstream_tls_returns_https_only():
    assert _session_cookie_kwargs(False, True) == {"https_only": True}


def test_criterion1_2_helper_direct_and_upstream_returns_https_only():
    assert _session_cookie_kwargs(True, True) == {"https_only": True}


def test_criterion3_helper_no_tls_returns_empty_dict():
    # {} (not {"https_only": False}) keeps the no-TLS path identical to main.
    result = _session_cookie_kwargs(False, False)
    assert result == {}
    assert "https_only" not in result


def test_criterion4_helper_sets_no_other_cookie_attribute():
    for tls_enabled, upstream in TLS_CONFIGS.values():
        keys = set(_session_cookie_kwargs(tls_enabled, upstream))
        assert keys <= {"https_only"}, keys


def test_criterion4_helper_returns_a_fresh_dict_each_call():
    first = _session_cookie_kwargs(False, False)
    first["same_site"] = "strict"
    assert _session_cookie_kwargs(False, False) == {}
    second = _session_cookie_kwargs(True, False)
    second["https_only"] = False
    assert _session_cookie_kwargs(True, False) == {"https_only": True}


# --- (b) Raw Set-Cookie header through SessionMiddleware ---------------------

def test_criterion1_direct_tls_login_set_cookie_has_secure():
    kwargs = _session_cookie_kwargs(True, False)
    for name, tokens in _login_cookie_attrs(kwargs, BASE_URLS["direct_tls"]):
        assert "secure" in tokens, tokens


def test_criterion2_upstream_tls_login_over_http_set_cookie_has_secure():
    kwargs = _session_cookie_kwargs(False, True)
    for name, tokens in _login_cookie_attrs(kwargs, BASE_URLS["upstream_tls"]):
        assert "secure" in tokens, tokens


def test_criterion1_2_direct_and_upstream_login_set_cookie_has_secure():
    kwargs = _session_cookie_kwargs(True, True)
    for name, tokens in _login_cookie_attrs(kwargs, BASE_URLS["direct_and_upstream"]):
        assert "secure" in tokens, tokens


def test_criterion3_no_tls_login_set_cookie_has_no_secure():
    kwargs = _session_cookie_kwargs(False, False)
    for name, tokens in _login_cookie_attrs(kwargs, BASE_URLS["no_tls"]):
        assert "secure" not in tokens, tokens


@pytest.mark.parametrize("config", ["direct_tls", "upstream_tls", "direct_and_upstream"])
def test_criterion1_2_secure_also_on_session_cookie_after_login(config):
    """Starlette re-sends Set-Cookie on every response with a non-empty
    session; every one of them must carry Secure, not only the first."""
    kwargs = _session_cookie_kwargs(*TLS_CONFIGS[config])
    client = TestClient(_app(kwargs), base_url=BASE_URLS[config])
    login = client.get("/login")
    cookie_header = _raw_cookie_pair(login)
    response = client.get("/whoami", headers={"Cookie": cookie_header})
    assert response.json() == {"has_session": True}
    parts = _cookie_parts(response)
    assert parts, "follow-up response does not refresh the session cookie"
    for name, tokens in parts:
        assert "secure" in tokens, tokens


@pytest.mark.parametrize("config", ["direct_tls", "upstream_tls", "direct_and_upstream"])
def test_criterion1_2_clearing_cookie_on_logout_has_secure(config):
    kwargs = _session_cookie_kwargs(*TLS_CONFIGS[config])
    for name, tokens in _logout_cookie_attrs(kwargs, BASE_URLS[config]):
        assert "secure" in tokens, tokens


def test_criterion3_no_tls_login_navigation_logout_work_over_http():
    kwargs = _session_cookie_kwargs(False, False)
    client = TestClient(_app(kwargs), base_url="http://localhost:8080")
    assert client.get("/whoami").json() == {"has_session": False}
    assert client.get("/login").status_code == 200
    # The client cookie jar resends the cookie over http:// only when it is
    # not Secure, as a browser would.
    assert client.get("/whoami").json() == {"has_session": True}
    assert client.get("/logout").status_code == 200
    assert client.get("/whoami").json() == {"has_session": False}


def test_criterion1_direct_tls_login_navigation_logout_work_over_https():
    kwargs = _session_cookie_kwargs(True, False)
    client = TestClient(_app(kwargs), base_url="https://testserver")
    assert client.get("/login").status_code == 200
    assert client.get("/whoami").json() == {"has_session": True}
    assert client.get("/logout").status_code == 200
    assert client.get("/whoami").json() == {"has_session": False}


@pytest.mark.parametrize("config", list(TLS_CONFIGS))
def test_criterion4_cookie_attributes_identical_to_main_apart_from_secure(config):
    """Baseline = SessionMiddleware without extra kwargs, which is what
    NiceGUI installs on main (ui.run without session_middleware_kwargs)."""
    baseline = _login_cookie_attrs({}, BASE_URLS[config])
    current = _login_cookie_attrs(_session_cookie_kwargs(*TLS_CONFIGS[config]),
                                  BASE_URLS[config])
    assert len(current) == len(baseline)
    for (base_name, base_tokens), (cur_name, cur_tokens) in zip(baseline, current):
        assert cur_name == base_name == "session"
        assert _attrs_without_secure(cur_tokens) == _attrs_without_secure(base_tokens)


@pytest.mark.parametrize("config", list(TLS_CONFIGS))
def test_criterion4_cookie_name_httponly_samesite_path_lifetime(config):
    kwargs = _session_cookie_kwargs(*TLS_CONFIGS[config])
    for name, tokens in _login_cookie_attrs(kwargs, BASE_URLS[config]):
        assert name == "session"
        assert "httponly" in tokens
        assert "samesite=lax" in tokens
        assert "path=/" in tokens
        assert "max-age=1209600" in tokens
        assert not any(t.startswith("domain=") for t in tokens), tokens


@pytest.mark.parametrize("config", list(TLS_CONFIGS))
def test_criterion4_clearing_cookie_attributes_identical_to_main_apart_from_secure(config):
    baseline = _logout_cookie_attrs({}, BASE_URLS[config])
    current = _logout_cookie_attrs(_session_cookie_kwargs(*TLS_CONFIGS[config]),
                                   BASE_URLS[config])
    assert len(current) == len(baseline)
    for (base_name, base_tokens), (cur_name, cur_tokens) in zip(baseline, current):
        assert cur_name == base_name == "session"
        assert _attrs_without_secure(cur_tokens) == _attrs_without_secure(base_tokens)


def test_privacy_cookie_parser_never_returns_cookie_value():
    """Guard for the helper used by every assertion above: the synthetic
    browser id and secret must not leak into what the tests compare."""
    kwargs = _session_cookie_kwargs(True, False)
    parts = _login_cookie_attrs(kwargs, BASE_URLS["direct_tls"])
    flattened = repr(parts)
    assert SYNTHETIC_SESSION_SECRET not in flattened
    assert SYNTHETIC_BROWSER_ID not in flattened
    # Every token is a known attribute, not a value fragment.
    for name, tokens in parts:
        for token in tokens:
            assert re.fullmatch(r"(httponly|secure|samesite=\w+|path=\S+|max-age=\d+)", token), \
                "unexpected attribute token"


# --- (c) Wiring in main_nice.py (static; importing main_nice loads models) ---

def _main_tree():
    return ast.parse((REPO_ROOT / "main_nice.py").read_text(encoding="utf-8"))


def _main_block_calls():
    tree = _main_tree()
    main_blocks = [
        node for node in tree.body
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
    ]
    assert main_blocks, "main_nice.py has no `if __name__ ...` block"
    return [node for block in main_blocks for node in ast.walk(block)
            if isinstance(node, ast.Call)]


def _ui_run_call():
    ui_runs = [c for c in _main_block_calls()
               if isinstance(c.func, ast.Attribute) and c.func.attr == "run"
               and ast.unparse(c.func.value) == "ui"]
    assert len(ui_runs) == 1
    return ui_runs[0]


def _keyword(call, name):
    matches = [kw for kw in call.keywords if kw.arg == name]
    return matches[0] if matches else None


def test_criterion1_2_ui_run_receives_session_middleware_kwargs():
    assert _keyword(_ui_run_call(), "session_middleware_kwargs") is not None


def test_criterion1_2_session_middleware_kwargs_built_by_helper_from_tls_settings():
    kw = _keyword(_ui_run_call(), "session_middleware_kwargs")
    assert kw is not None
    value = kw.value
    if isinstance(value, ast.Name):
        # Allow a local variable assigned from the helper in the startup block.
        assignments = [
            node for node in ast.walk(_main_tree())
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == value.id for t in node.targets)
        ]
        assert len(assignments) == 1, ast.unparse(value)
        value = assignments[0].value
    assert isinstance(value, ast.Call), ast.unparse(value)
    func = value.func
    func_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    assert func_name == "session_cookie_kwargs"
    args_src = " ".join(ast.unparse(a) for a in value.args) + " " + \
        " ".join(ast.unparse(k) for k in value.keywords)
    assert "ssl_kwargs" in args_src
    assert "TLS_TERMINATED_UPSTREAM" in args_src


def test_criterion1_2_main_nice_imports_helper_from_src_tls():
    imported = set()
    for node in ast.walk(_main_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == "src.tls":
            imported.update(alias.name for alias in node.names)
    assert "session_cookie_kwargs" in imported


def test_criterion4_ui_run_still_passes_storage_secret_and_ssl_kwargs():
    call = _ui_run_call()
    assert _keyword(call, "storage_secret") is not None
    assert any(kw.arg is None for kw in call.keywords), ast.unparse(call)


# --- Documentation (criterion 6 and D5) --------------------------------------

def _env_row(name):
    text = (REPO_ROOT / "doc" / "ENV_VARIABLES.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` |")]
    assert len(rows) == 1, f"expected one table row for {name}"
    return rows[0]


@pytest.mark.parametrize("name", ["SSL_CERTFILE", "TLS_TERMINATED_UPSTREAM"])
def test_criterion6_env_variables_row_says_cookie_secure_and_https(name):
    row = _env_row(name)
    assert "Secure" in row
    assert "cookie" in row.lower()
    assert "https://" in row


def _unreleased_section():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"## \[Unreleased\](.*?)(\n## \[|\Z)", text, re.S)
    assert match, "CHANGELOG.md has no [Unreleased] section"
    return match.group(1)


def test_criterion6_changelog_unreleased_mentions_issue_50():
    section = _unreleased_section()
    lines = [line for line in section.splitlines() if "#50" in line]
    assert lines, "no [Unreleased] line references #50"
    assert any("Secure" in line or "secure" in line for line in lines)


def test_d5_adr_0001_consequences_mention_secure_session_cookie():
    text = (REPO_ROOT / "doc" / "decisions" / "0001-transport-security.md").read_text(
        encoding="utf-8")
    match = re.search(r"## Consequences(.*?)(\n## |\Z)", text, re.S)
    assert match, "ADR 0001 has no Consequences section"
    bullets = [line for line in match.group(1).splitlines()
               if line.lstrip().startswith("-") and "#50" in line]
    assert bullets, "no Consequences bullet references #50"
    assert any("Secure" in b for b in bullets)
