# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #38: transport encryption (TLS) for the web UI
and the Outlook bridge.

The tests follow the approved design and the owner decisions on the issue:

- Without TLS on a non-loopback bind the server starts and logs exactly one
  WARNING that names the setting to change; it never refuses to start
  (owner decision, criterion 1 is read as "starts with a warning").
- D1: if only one of ``SSL_CERTFILE`` / ``SSL_KEYFILE`` is set, or a path does
  not exist, the server stops at startup with a clear message. The pure helper
  ``build_ssl_kwargs`` raises ``ValueError`` naming the variable.
- D4: setting names ``SSL_CERTFILE``, ``SSL_KEYFILE`` and
  ``TLS_TERMINATED_UPSTREAM``.

The helpers live in ``src/tls.py`` (design point 2) so they can be tested
without importing ``main_nice.py``, which loads the embedding model at import
time. ``main_nice.py`` is only inspected statically (AST) to check the wiring.

All host names, secrets and file contents are synthetic. No real certificate
or private key is used or committed: the dummy PEM files are created in
``tmp_path`` at runtime and uvicorn is never started.
"""
import ast
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Synthetic test data -----------------------------------------------------

SYNTHETIC_STORAGE_SECRET = "synthetic-storage-secret-QX7v9"
SYNTHETIC_WS_API_KEY = "synthetic-ws-api-key-ZK42p"
SYNTHETIC_CERT_MARKER = "SYNTHETIC-CERT-CONTENT-NOT-A-REAL-CERT"
SYNTHETIC_KEY_MARKER = "SYNTHETIC-KEY-CONTENT-NOT-A-REAL-KEY"

LOOPBACK_HOSTS = ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost", "LOCALHOST"]
NON_LOOPBACK_HOSTS = [
    "0.0.0.0",
    "::",
    "192.0.2.10",
    "arcumai.example.test",
    "localhost.example.test",
    "127.0.0.1.example.test",
]


def _tls():
    """Import the module under test lazily, so a missing module fails each
    test with a clear ImportError instead of a collection error."""
    import src.tls as tls_module
    return tls_module


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def recording_logger():
    """A real ``logging.Logger`` that keeps every record it receives."""
    logger = logging.getLogger(f"test_tls_{uuid.uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.records = handler.records
    yield logger
    logger.removeHandler(handler)


def _warnings(logger):
    return [r for r in logger.records if r.levelno >= logging.WARNING]


@pytest.fixture
def synthetic_secrets(monkeypatch):
    """Put synthetic secrets in the environment and in src.config, so a
    message that leaked them would be caught."""
    monkeypatch.setenv("STORAGE_SECRET", SYNTHETIC_STORAGE_SECRET)
    monkeypatch.setenv("WS_API_KEY", SYNTHETIC_WS_API_KEY)
    import src.config as config_module
    monkeypatch.setattr(config_module, "WS_API_KEY", SYNTHETIC_WS_API_KEY, raising=False)


@pytest.fixture
def dummy_pem_files(tmp_path):
    """Two throwaway files that stand in for a certificate and a key.
    Their content is a marker, not key material."""
    cert = tmp_path / "server-cert.pem"
    key = tmp_path / "server-key.pem"
    cert.write_text(SYNTHETIC_CERT_MARKER, encoding="utf-8")
    key.write_text(SYNTHETIC_KEY_MARKER, encoding="utf-8")
    return str(cert), str(key)


# --- Loopback detection (criteria 1, 2, 3 depend on it) ----------------------

@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_criterion2_is_loopback_host_true_for_loopback(host):
    assert _tls().is_loopback_host(host) is True


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_criterion1_is_loopback_host_false_for_non_loopback(host):
    assert _tls().is_loopback_host(host) is False


# --- Criterion 1 (owner decision: warn, not refuse) and criterion 3 ----------

@pytest.mark.parametrize("arcumai_env", [None, "dev", "production"])
def test_criterion1_and_3_non_loopback_without_tls_logs_one_warning_and_does_not_raise(
        monkeypatch, recording_logger, arcumai_env):
    if arcumai_env is None:
        monkeypatch.delenv("ARCUMAI_ENV", raising=False)
    else:
        monkeypatch.setenv("ARCUMAI_ENV", arcumai_env)

    warned = _tls().warn_if_unencrypted("0.0.0.0", 8080, False, False, recording_logger)

    assert warned is True
    warnings = _warnings(recording_logger)
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert warnings[0].levelno == logging.WARNING


def test_criterion1_warning_names_the_settings_to_change(recording_logger):
    _tls().warn_if_unencrypted("0.0.0.0", 8080, False, False, recording_logger)
    message = _warnings(recording_logger)[0].getMessage()
    assert "SSL_CERTFILE" in message
    assert "TLS_TERMINATED_UPSTREAM" in message


def test_criterion3_warning_says_traffic_is_unencrypted(recording_logger):
    _tls().warn_if_unencrypted("0.0.0.0", 8080, False, False, recording_logger)
    message = _warnings(recording_logger)[0].getMessage()
    assert "encrypt" in message.lower()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "arcumai.example.test",
                                  "localhost.example.test", "::"])
def test_criterion1_every_non_loopback_host_without_tls_warns(recording_logger, host):
    assert _tls().warn_if_unencrypted(host, 8443, False, False, recording_logger) is True
    assert len(_warnings(recording_logger)) == 1


def test_criterion1_warning_does_not_leak_secrets(recording_logger, synthetic_secrets):
    _tls().warn_if_unencrypted("0.0.0.0", 8080, False, False, recording_logger)
    for record in recording_logger.records:
        message = record.getMessage()
        assert SYNTHETIC_STORAGE_SECRET not in message
        assert SYNTHETIC_WS_API_KEY not in message


# --- Criterion 2: loopback starts normally, no warning -----------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_criterion2_loopback_without_tls_logs_no_warning(recording_logger, host):
    warned = _tls().warn_if_unencrypted(host, 8080, False, False, recording_logger)
    assert warned is False
    assert _warnings(recording_logger) == []


# --- TLS enabled or terminated upstream: no warning --------------------------

def test_tls_enabled_on_non_loopback_logs_no_warning(recording_logger):
    warned = _tls().warn_if_unencrypted("0.0.0.0", 8443, True, False, recording_logger)
    assert warned is False
    assert _warnings(recording_logger) == []


def test_tls_terminated_upstream_on_non_loopback_logs_no_warning(recording_logger):
    warned = _tls().warn_if_unencrypted("0.0.0.0", 8080, False, True, recording_logger)
    assert warned is False
    assert _warnings(recording_logger) == []


# --- D1: build_ssl_kwargs, fail fast on half or broken TLS configuration -----

def test_d1_no_tls_files_returns_empty_kwargs():
    assert _tls().build_ssl_kwargs("", "") == {}


def test_d1_both_tls_files_existing_returns_uvicorn_kwargs(dummy_pem_files):
    cert, key = dummy_pem_files
    assert _tls().build_ssl_kwargs(cert, key) == {"ssl_certfile": cert, "ssl_keyfile": key}


def test_d1_only_certfile_set_raises_naming_ssl_keyfile(dummy_pem_files):
    cert, _ = dummy_pem_files
    with pytest.raises(ValueError) as excinfo:
        _tls().build_ssl_kwargs(cert, "")
    assert "SSL_KEYFILE" in str(excinfo.value)


def test_d1_only_keyfile_set_raises_naming_ssl_certfile(dummy_pem_files):
    _, key = dummy_pem_files
    with pytest.raises(ValueError) as excinfo:
        _tls().build_ssl_kwargs("", key)
    assert "SSL_CERTFILE" in str(excinfo.value)


def test_d1_missing_certfile_path_raises_naming_ssl_certfile(tmp_path, dummy_pem_files):
    _, key = dummy_pem_files
    missing = str(tmp_path / "does-not-exist-cert.pem")
    with pytest.raises(ValueError) as excinfo:
        _tls().build_ssl_kwargs(missing, key)
    assert "SSL_CERTFILE" in str(excinfo.value)


def test_d1_missing_keyfile_path_raises_naming_ssl_keyfile(tmp_path, dummy_pem_files):
    cert, _ = dummy_pem_files
    missing = str(tmp_path / "does-not-exist-key.pem")
    with pytest.raises(ValueError) as excinfo:
        _tls().build_ssl_kwargs(cert, missing)
    assert "SSL_KEYFILE" in str(excinfo.value)


def test_d1_error_messages_never_contain_file_content(tmp_path, dummy_pem_files):
    cert, key = dummy_pem_files
    missing = str(tmp_path / "does-not-exist.pem")
    for args in ((cert, ""), ("", key), (cert, missing), (missing, key)):
        with pytest.raises(ValueError) as excinfo:
            _tls().build_ssl_kwargs(*args)
        text = str(excinfo.value)
        assert SYNTHETIC_CERT_MARKER not in text
        assert SYNTHETIC_KEY_MARKER not in text


# --- D4: settings read from the environment under the approved names ---------

def _read_tls_settings_in_subprocess(extra_env):
    """Import src.config in a fresh interpreter with the given environment,
    so the already imported module in this process is never reloaded."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("SSL_CERTFILE", "SSL_KEYFILE", "TLS_TERMINATED_UPSTREAM")}
    env["ARCUMAI_ENV"] = "dev"
    env.update(extra_env)
    code = (
        "from src.config import SSL_CERTFILE, SSL_KEYFILE, TLS_TERMINATED_UPSTREAM\n"
        "print(repr((SSL_CERTFILE, SSL_KEYFILE, TLS_TERMINATED_UPSTREAM)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return ast.literal_eval(result.stdout.strip().splitlines()[-1])


def test_d4_tls_settings_default_to_disabled():
    assert _read_tls_settings_in_subprocess({}) == ("", "", False)


def test_d4_tls_settings_are_read_from_approved_env_names(tmp_path):
    cert = str(tmp_path / "synthetic-cert.pem")
    key = str(tmp_path / "synthetic-key.pem")
    values = _read_tls_settings_in_subprocess({
        "SSL_CERTFILE": cert,
        "SSL_KEYFILE": key,
        "TLS_TERMINATED_UPSTREAM": "true",
    })
    assert values == (cert, key, True)


def test_d4_tls_terminated_upstream_accepts_uppercase_true():
    assert _read_tls_settings_in_subprocess({"TLS_TERMINATED_UPSTREAM": "TRUE"})[2] is True


# --- Wiring in main_nice.py (static check; importing main_nice loads models) --

def _main_block_calls():
    tree = ast.parse((REPO_ROOT / "main_nice.py").read_text(encoding="utf-8"))
    main_blocks = [
        node for node in tree.body
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
    ]
    assert main_blocks, "main_nice.py has no `if __name__ ...` block"
    calls = []
    for block in main_blocks:
        for node in ast.walk(block):
            if isinstance(node, ast.Call):
                calls.append(node)
    return calls


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def test_startup_calls_build_ssl_kwargs_and_warn_if_unencrypted():
    names = [_call_name(c) for c in _main_block_calls()]
    assert "build_ssl_kwargs" in names
    assert "warn_if_unencrypted" in names


def test_startup_passes_ssl_kwargs_to_ui_run():
    ui_runs = [c for c in _main_block_calls()
               if isinstance(c.func, ast.Attribute) and c.func.attr == "run"
               and ast.unparse(c.func.value) == "ui"]
    assert len(ui_runs) == 1
    # A `**ssl_kwargs` expansion (keyword with arg None) forwards the TLS files.
    assert any(kw.arg is None for kw in ui_runs[0].keywords), ast.unparse(ui_runs[0])


def test_startup_builds_ssl_kwargs_before_ui_run():
    calls = _main_block_calls()
    assert any(_call_name(c) == "build_ssl_kwargs" for c in calls),         "build_ssl_kwargs is not called in the startup block"
    first_build = min(c.lineno for c in calls if _call_name(c) == "build_ssl_kwargs")
    ui_run = min(c.lineno for c in calls
                 if isinstance(c.func, ast.Attribute) and c.func.attr == "run"
                 and ast.unparse(c.func.value) == "ui")
    assert first_build < ui_run


# --- Criterion 6: TLS guide linked from README, PowerShell-friendly ----------

TLS_GUIDE = REPO_ROOT / "doc" / "TLS_SETUP.md"


def _tls_guide_text():
    assert TLS_GUIDE.is_file(), "doc/TLS_SETUP.md does not exist"
    return TLS_GUIDE.read_text(encoding="utf-8")


def test_criterion6_tls_guide_exists_and_is_linked_from_readme():
    assert TLS_GUIDE.is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "TLS_SETUP.md" in readme


def test_criterion6_tls_guide_covers_certificate_sources_and_addin():
    text = _tls_guide_text()
    lower = text.lower()
    for setting in ("SSL_CERTFILE", "SSL_KEYFILE", "TLS_TERMINATED_UPSTREAM"):
        assert setting in text
    assert "wss://" in text
    assert "UseSecureConnection" in text
    assert "reverse proxy" in lower
    assert "self-signed" in lower
    assert re.search(r"\bCA\b", text)
    assert "private key" in lower and "commit" in lower


def test_criterion6_tls_guide_has_no_unix_only_shell_syntax():
    text = _tls_guide_text()
    code = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S))
    for forbidden in ("&&", "||", "sudo ", "export ", "chmod "):
        assert forbidden not in code, f"Unix-only syntax {forbidden!r} in TLS_SETUP.md code"


def test_criterion6_tls_guide_contains_no_private_key_material():
    text = _tls_guide_text()
    assert not re.search(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{40,}", text)


# --- Criterion 8: changelog and env variable reference ------------------------

def test_criterion8_env_variables_doc_lists_new_settings():
    text = (REPO_ROOT / "doc" / "ENV_VARIABLES.md").read_text(encoding="utf-8")
    for setting in ("SSL_CERTFILE", "SSL_KEYFILE", "TLS_TERMINATED_UPSTREAM"):
        assert setting in text


def test_criterion8_changelog_unreleased_mentions_issue_38():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1]
    unreleased = re.split(r"\n## \[", unreleased, maxsplit=1)[0]
    assert "#38" in unreleased
