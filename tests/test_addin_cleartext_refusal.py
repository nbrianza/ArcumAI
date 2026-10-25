# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #47: the Outlook add-in refuses a cleartext
connection to a remote server.

The add-in is a C# VSTO project. There is no C# test project and it can only
be compiled on Windows CI, so its runtime behaviour (no connection attempt,
one ERROR line in plugin.log, Outlook keeps working) is verified manually and
by the CI compile, as criterion 8 of the issue requires.

What is automated here, following the approved design (D1 to D4) on #47:

- Static source checks on ``ThisAddIn.cs`` and ``PluginConfigLoader.cs``.
  They do not prove runtime behaviour; they pin the shape the design relies
  on: one guard in ``ConnectToArcum()`` (the only caller of ``ConnectAsync``)
  that uses the shared predicate ``IsCleartextRemoteConnection()`` from #48,
  logs one ERROR naming ``UseSecureConnection`` and ``wss://``, and returns
  without scheduling a reconnect. The checks deliberately say nothing about
  ``PluginConfig.Validate()`` or about how ``wss://`` is detected, which
  belong to #48.
- Documentation checks for criterion 7 and decision D4, and the ADR 0001
  update the owner approved.

All host names in this file are synthetic.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDIN_DIR = REPO_ROOT / "outlook-plugin" / "ArcumAI.Outlook" / "ArcumAI.OutlookAddIn"
THIS_ADDIN = ADDIN_DIR / "ThisAddIn.cs"
CONFIG_LOADER = ADDIN_DIR / "Core" / "PluginConfigLoader.cs"
CONFIG_README = ADDIN_DIR / "config.README.md"
TLS_GUIDE = REPO_ROOT / "doc" / "TLS_SETUP.md"
ENV_DOC = REPO_ROOT / "doc" / "ENV_VARIABLES.md"
ADR_0001 = REPO_ROOT / "doc" / "decisions" / "0001-transport-security.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

PREDICATE = "IsCleartextRemoteConnection("


# --- Minimal C# source helpers ------------------------------------------------

def _mask_literals_and_comments(src):
    """Return ``src`` with the content of string/char literals and comments
    replaced by spaces, keeping offsets, so braces inside them do not affect
    brace matching. Good enough for the hand-written code in this add-in."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if src[k] != "\n":
                    out[k] = " "
            i = j
        elif c == '"' or c == "'":
            verbatim = i > 0 and src[i - 1] == "@" and c == '"'
            j = i + 1
            while j < n:
                if not verbatim and src[j] == "\\":
                    j += 2
                    continue
                if src[j] == c:
                    if verbatim and j + 1 < n and src[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                if src[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _block_from(src, masked, open_index):
    """Return the text of the ``{ ... }`` block that starts at ``open_index``."""
    assert masked[open_index] == "{"
    depth = 0
    for k in range(open_index, len(masked)):
        if masked[k] == "{":
            depth += 1
        elif masked[k] == "}":
            depth -= 1
            if depth == 0:
                return src[open_index:k + 1]
    raise AssertionError("unbalanced braces")


def _method_body(path, signature_regex):
    src = path.read_text(encoding="utf-8")
    masked = _mask_literals_and_comments(src)
    match = re.search(signature_regex, masked)
    assert match, f"{signature_regex!r} not found in {path.name}"
    open_index = masked.index("{", match.end())
    return _block_from(src, masked, open_index)


def _connect_to_arcum_body():
    return _method_body(THIS_ADDIN, r"\bvoid\s+ConnectToArcum\s*\(\s*\)")


def _refusal_block():
    """The ``{ ... }`` block executed when the predicate is true."""
    body = _connect_to_arcum_body()
    masked = _mask_literals_and_comments(body)
    index = masked.find(PREDICATE)
    assert index != -1, (
        "ConnectToArcum() does not call IsCleartextRemoteConnection(): "
        "no cleartext refusal guard (#47 design step 1)")
    open_index = masked.index("{", index)
    return _block_from(body, masked, open_index)


def _code_only(text):
    return _mask_literals_and_comments(text)


# --- Criterion 1: refuse, no connection attempt, one ERROR naming the fix ----

def test_criterion1_connect_to_arcum_checks_cleartext_remote_before_connecting():
    body = _connect_to_arcum_body()
    code = _code_only(body)
    guard = code.find(PREDICATE)
    assert guard != -1, "ConnectToArcum() has no IsCleartextRemoteConnection() guard"
    connect = code.find("ConnectAsync(")
    assert connect != -1, "ConnectToArcum() no longer calls ConnectAsync()"
    assert guard < connect, "the cleartext guard must run before ConnectAsync()"
    connecting_log = body.find("Connecting to")
    if connecting_log != -1:
        assert guard < connecting_log, \
            "the cleartext guard must run before the 'Connecting to' log line"


def test_criterion1_refusal_logs_an_error_naming_the_setting_to_change():
    block = _refusal_block()
    assert re.search(r'_logger\.Log\(\s*"ERROR"', block), \
        "the refusal branch does not log at ERROR level"
    assert "UseSecureConnection" in block, "the ERROR must name UseSecureConnection"
    assert "wss://" in block, "the ERROR must name a wss:// address as the alternative fix"


def test_criterion1_refusal_logs_exactly_one_line():
    block = _refusal_block()
    assert len(re.findall(r"_logger\.Log\(", _code_only(block))) == 1


def test_criterion1_refusal_returns_without_connecting_or_retrying():
    code = _code_only(_refusal_block())
    assert re.search(r"\breturn\b", code), "the refusal branch must return"
    assert "ConnectAsync(" not in code
    # No retry loop: the configuration cannot change until Outlook restarts.
    assert "ScheduleReconnect(" not in code


def test_criterion1_refusal_error_does_not_log_secrets_or_identities():
    # Privacy note of the issue: the server address is acceptable, the API key,
    # user id and email addresses are not.
    code = _code_only(_refusal_block())
    for forbidden in ("ApiKey", "UserId", "ArcumAIEmailAddress", "Environment.UserName"):
        assert forbidden not in code, f"refusal ERROR must not include {forbidden}"


def test_criterion1_every_connection_attempt_goes_through_connect_to_arcum():
    # The design relies on ConnectToArcum() being the single place that opens a
    # connection, so one guard covers startup, reconnect and heartbeat paths.
    src = _code_only(THIS_ADDIN.read_text(encoding="utf-8"))
    total = len(re.findall(r"\bConnectAsync\s*\(", src))
    inside = len(re.findall(r"\bConnectAsync\s*\(", _code_only(_connect_to_arcum_body())))
    assert total == inside == 1, \
        f"ConnectAsync is called {total} times in ThisAddIn.cs, {inside} inside ConnectToArcum()"
    for path in ADDIN_DIR.rglob("*.cs"):
        if path.name in ("ThisAddIn.cs", "ThisAddIn.EXAMPLE.cs") or "Transport" in path.parts:
            continue
        if "obj" in path.parts or "bin" in path.parts:
            continue
        code = _code_only(path.read_text(encoding="utf-8", errors="replace"))
        assert not re.search(r"\.ConnectAsync\s*\(", code), \
            f"{path.name} opens a connection outside ConnectToArcum()"


# --- D1: refuse only on the TLS rule, not on any Validate() failure ----------

def test_d1_refusal_does_not_depend_on_validate():
    # A server-pushed value cached in server-config.json can fail unrelated
    # Validate() rules; refusing on those would break criterion 5.
    code = _code_only(_connect_to_arcum_body())
    assert "Validate(" not in code


# --- Criterion 4: the server push cannot change the transport settings ------

def test_criterion4_server_push_cannot_change_server_url_or_secure_flag():
    body = _code_only(_method_body(CONFIG_LOADER, r"\bApplyServerValues\s*\("))
    for setting in ("ServerUrl", "UseSecureConnection"):
        assert not re.search(rf"\.{setting}\s*=(?!=)", body), \
            f"ApplyServerValues must not assign {setting} (#47 criterion 4, design D2)"


# --- Criterion 6: Outlook keeps working, no blocking dialog ------------------

def test_criterion6_refusal_shows_no_blocking_dialog():
    code = _code_only(THIS_ADDIN.read_text(encoding="utf-8"))
    assert "MessageBox" not in code


def test_criterion6_refusal_does_not_throw():
    code = _code_only(_refusal_block())
    assert not re.search(r"\bthrow\b", code)


# --- Criterion 7: documentation for administrators, changelog, ADR ----------

def _section(text, heading_prefix):
    start = text.find(heading_prefix)
    assert start != -1, f"section {heading_prefix!r} not found"
    rest = text[start + len(heading_prefix):]
    end = re.search(r"\n## ", rest)
    return rest[:end.start()] if end else rest


def test_criterion7_tls_guide_explains_refusal_error_and_fix():
    section = _section(TLS_GUIDE.read_text(encoding="utf-8"),
                       "## Outlook add-in configuration")
    lower = section.lower()
    assert "Connection refused" in section, \
        "TLS_SETUP.md add-in section must quote the 'Connection refused' ERROR"
    assert "refuse" in lower
    assert "config.json" in section
    assert "restart outlook" in lower


def test_d4_tls_guide_mentions_enable_logging_for_the_refusal_error():
    # D4: with EnableLogging = false the ERROR is not written to plugin.log;
    # accepted and documented.
    section = _section(TLS_GUIDE.read_text(encoding="utf-8"),
                       "## Outlook add-in configuration")
    assert "EnableLogging" in section


def test_criterion7_addin_config_readme_explains_refusal():
    text = CONFIG_README.read_text(encoding="utf-8")
    assert "Connection refused" in text
    assert "restart" in text.lower()


def test_criterion7_env_variables_doc_use_secure_connection_row_mentions_refusal():
    rows = [line for line in ENV_DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `UseSecureConnection`")]
    assert rows, "UseSecureConnection row missing from doc/ENV_VARIABLES.md"
    assert any("refuse" in row.lower() for row in rows)


def test_criterion7_changelog_unreleased_mentions_issue_47():
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1]
    unreleased = re.split(r"\n## \[", unreleased, maxsplit=1)[0]
    assert "#47" in unreleased


def test_adr_0001_records_the_refusal():
    text = ADR_0001.read_text(encoding="utf-8")
    assert "still only logs a warning and connects" not in text, \
        "ADR 0001 still states the superseded D2 behaviour"
    assert "#47" in text
    assert "refuses to connect" in text.lower()


def test_adr_0001_consequences_mention_installations_that_stop_connecting():
    text = ADR_0001.read_text(encoding="utf-8")
    consequences = _section(text, "## Consequences")
    assert "stop connecting" in consequences.lower()
