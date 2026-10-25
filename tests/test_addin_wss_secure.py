# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #48: the Outlook add-in must accept a ``wss://``
server address as secure when ``UseSecureConnection`` is false.

The design approved on the issue (D1 to D3) keeps the whole change inside
``Core/PluginConfig.cs``:

- the ``ws://`` / ``wss://`` prefix check in ``Validate()`` becomes
  case-insensitive (D1, criterion 5);
- the TLS rule lives in one predicate ``IsCleartextRemoteConnection()``
  (instance, no parameters, returns bool, callable from ``ThisAddIn``), which
  ``Validate()`` uses with the unchanged error message (D2, criterion 3).

There is no C# test project. To test the real ``Validate()`` instead of
reading its text, these tests compile ``PluginConfig.cs`` on the fly with the
Roslyn ``csc.exe`` shipped with Visual Studio / Build Tools (present on the
``windows-latest`` CI runner), together with two small stubs: a minimal
``PluginConfigLoader`` (only ``SetDefaults`` and the members ``PluginConfig``
calls) and a minimal ``Newtonsoft.Json`` (``JsonConvert``/``Formatting``, used
only by ``Save()``, which is never called). A tiny console harness then calls
``Validate()``, ``IsCleartextRemoteConnection()`` (via reflection, so a
missing method fails the predicate tests and not the whole module) and
``GetWebSocketUrl()``. When no C# compiler is found the behavioural tests are
skipped with an explicit reason; documentation tests still run.

Criteria that cannot be checked here: criterion 2 and the connect half of
criterion 4 (a real TLS handshake by ``ClientWebSocket`` in Outlook) and
criterion 7 (CI compile plus a manual check on Windows). Only the URL that
``GetWebSocketUrl()`` reports is checked automatically.

All host names are synthetic (``*.example.test``); no configuration file, key
or log of a real installation is read.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDIN_DIR = REPO_ROOT / "outlook-plugin" / "ArcumAI.Outlook" / "ArcumAI.OutlookAddIn"
PLUGIN_CONFIG_CS = ADDIN_DIR / "Core" / "PluginConfig.cs"
PLUGIN_CONFIG_LOADER_CS = ADDIN_DIR / "Core" / "PluginConfigLoader.cs"
CONFIG_README = ADDIN_DIR / "config.README.md"
ENV_VARIABLES_DOC = REPO_ROOT / "doc" / "ENV_VARIABLES.md"
TLS_GUIDE = REPO_ROOT / "doc" / "TLS_SETUP.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

TLS_RULE_MESSAGE = "UseSecureConnection must be true for non-localhost connections"
PREFIX_MESSAGE = "ServerUrl must start with ws:// or wss://"

REMOTE_WSS = "wss://arcumai.example.test:8080"
REMOTE_WSS_UPPER = "WSS://arcumai.example.test:8080"
REMOTE_WS = "ws://arcumai.example.test:8080"

# --- C# harness ---------------------------------------------------------------

_STUBS_CS = r"""
namespace Newtonsoft.Json
{
    public enum Formatting { None, Indented }
    public static class JsonConvert
    {
        public static string SerializeObject(object value, Formatting formatting) { return ""; }
    }
}

namespace ArcumAI.OutlookAddIn.Core
{
    // Test stub: only what PluginConfig needs. Defaults pass every Validate()
    // rule except those under test.
    internal static class PluginConfigLoader
    {
        internal static PluginConfig LoadConfiguration() { return new PluginConfig(); }
        internal static string GetConfigFilePath() { return "unused-synthetic-path.json"; }
        internal static void SetDefaults(PluginConfig config)
        {
            config.ServerUrl = "ws://localhost:8080";
            config.UseSecureConnection = false;
            config.ReconnectDelayMs = 5000;
            config.MaxReconnectAttempts = 720;
            config.ConnectionTimeoutMs = 30000;
            config.RequestTimeoutMs = 30000;
            config.MaxEmailResults = 10;
            config.EmailPreviewLength = 200;
            config.EnableLogging = false;
            config.LogLevel = "INFO";
            config.UserId = "synthetic-user";
            config.AutoReconnect = true;
            config.HeartbeatIntervalMs = 30000;
            config.EnableVirtualLoopback = false;
            config.MaxAttachmentSizeMB = 10;
            config.MaxTotalAttachmentsMB = 25;
            config.MaxPayloadSizeMB = 50;
            config.LoopbackTimeoutMs = 120000;
            config.ShowProcessingNotification = true;
        }
    }
}
"""

_HARNESS_CS = r"""
using System;
using System.Reflection;
using ArcumAI.OutlookAddIn.Core;

namespace ArcumAITestHarness
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            var config = new PluginConfig();
            config.ServerUrl = args[0] == "<null>" ? null : args[0];
            config.UseSecureConnection = bool.Parse(args[1]);
            config.UserId = "synthetic-user";

            try
            {
                string message;
                bool ok = config.Validate(out message);
                Console.WriteLine("VALID=" + ok);
                Console.WriteLine("MESSAGE=" + (message ?? ""));
            }
            catch (Exception e)
            {
                Console.WriteLine("VALID=EXCEPTION:" + e.GetType().Name);
            }

            MethodInfo m = typeof(PluginConfig).GetMethod(
                "IsCleartextRemoteConnection",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                null, Type.EmptyTypes, null);
            if (m == null)
            {
                Console.WriteLine("CLEARTEXT=MISSING");
            }
            else
            {
                Console.WriteLine("CLEARTEXT_RETURN=" + m.ReturnType.FullName);
                Console.WriteLine("CLEARTEXT_PRIVATE=" + m.IsPrivate);
                try
                {
                    Console.WriteLine("CLEARTEXT=" + m.Invoke(config, null));
                }
                catch (TargetInvocationException e)
                {
                    Console.WriteLine("CLEARTEXT=EXCEPTION:" + e.InnerException.GetType().Name);
                }
            }

            if (config.ServerUrl != null)
            {
                try { Console.WriteLine("WSURL=" + config.GetWebSocketUrl()); }
                catch (Exception e) { Console.WriteLine("WSURL=EXCEPTION:" + e.GetType().Name); }
            }
            return 0;
        }
    }
}
"""


def _find_csc():
    """Locate the Roslyn csc.exe of a Visual Studio / Build Tools install."""
    explicit = os.environ.get("ARCUMAI_CSC")
    if explicit and Path(explicit).is_file():
        return explicit
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-find",
                 r"MSBuild\**\Bin\Roslyn\csc.exe"],
                capture_output=True, text=True, timeout=60,
            ).stdout
            for line in out.splitlines():
                if line.strip() and Path(line.strip()).is_file():
                    return line.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        hits = sorted(Path(base, "Microsoft Visual Studio").glob(
            "*/*/MSBuild/Current/Bin/Roslyn/csc.exe"))
        if hits:
            return str(hits[-1])
    return shutil.which("csc")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """Compile PluginConfig.cs with the stubs and return a callable
    ``run(server_url, use_secure) -> dict`` that executes the harness."""
    csc = _find_csc()
    if not csc:
        pytest.skip("No C# compiler (Roslyn csc.exe) found; PluginConfig.Validate() "
                    "behaviour is not verified in this run (CI windows-latest has one).")
    work = tmp_path_factory.mktemp("addin_wss_harness")
    stubs = work / "Stubs.cs"
    program = work / "Harness.cs"
    stubs.write_text(_STUBS_CS, encoding="utf-8")
    program.write_text(_HARNESS_CS, encoding="utf-8")
    exe = work / "PluginConfigHarness.exe"
    result = subprocess.run(
        [csc, "/nologo", "/target:exe", f"/out:{exe}",
         str(PLUGIN_CONFIG_CS), str(stubs), str(program)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0 and exe.is_file(), (
        "PluginConfig.cs did not compile with the test stubs. If PluginConfig now "
        "uses another type, extend the stubs in this test.\n" + result.stdout + result.stderr
    )

    def run(server_url, use_secure):
        arg = "<null>" if server_url is None else server_url
        proc = subprocess.run(
            [str(exe), arg, "true" if use_secure else "false"],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        values = {}
        for line in proc.stdout.splitlines():
            key, _, value = line.partition("=")
            values[key] = value
        return values

    return run


def _assert_valid(result):
    assert result["VALID"] == "True", f"Validate() failed: {result.get('MESSAGE')!r}"
    assert result["MESSAGE"] == ""


def _assert_tls_rule_failure(result):
    assert result["VALID"] == "False", "Validate() accepted a cleartext remote address"
    assert result["MESSAGE"] == TLS_RULE_MESSAGE


# --- Criterion 1: wss:// remote address with UseSecureConnection=false -------

def test_criterion1_wss_remote_with_secure_false_passes_validation(harness):
    _assert_valid(harness(REMOTE_WSS, False))


# --- Criterion 2 (partial): the URL is not downgraded to ws:// ----------------

def test_criterion2_wss_remote_with_secure_false_effective_url_stays_wss(harness):
    """Only the URL reported by GetWebSocketUrl(); the real TLS handshake by
    ClientWebSocket in Outlook is a manual check."""
    url = harness(REMOTE_WSS, False)["WSURL"]
    assert url.lower().startswith("wss://arcumai.example.test:8080/ws/outlook/")


# --- Criterion 3: ws:// remote address with UseSecureConnection=false --------

def test_criterion3_ws_remote_with_secure_false_fails_with_same_message(harness):
    _assert_tls_rule_failure(harness(REMOTE_WS, False))


@pytest.mark.parametrize("server_url", [
    "WS://arcumai.example.test:8080",          # upper-case cleartext scheme (D1 risk)
    "ws://wss.example.test:8080",              # "wss" in the host, not the scheme
    "ws://arcumai.example.test:8080/wss://x",  # "wss://" later in the URL
    "ws://localhost.example.test:8080",        # PR #57 rule unchanged
    "ws://192.0.2.10:8080",
])
def test_criterion3_cleartext_remote_variants_still_fail_with_same_message(harness, server_url):
    _assert_tls_rule_failure(harness(server_url, False))


# --- Criterion 4: wss:// with UseSecureConnection=true (unchanged) -----------

def test_criterion4_wss_remote_with_secure_true_passes_validation(harness):
    _assert_valid(harness(REMOTE_WSS, True))


def test_criterion4_wss_remote_with_secure_true_effective_url_is_wss(harness):
    url = harness(REMOTE_WSS, True)["WSURL"]
    assert url.lower().startswith("wss://arcumai.example.test:8080/ws/outlook/")


# --- Criterion 5: upper-case WSS:// is treated like criterion 1 ---------------

def test_criterion5_uppercase_wss_with_secure_false_passes_validation(harness):
    _assert_valid(harness(REMOTE_WSS_UPPER, False))


@pytest.mark.parametrize("server_url", [
    "Wss://arcumai.example.test:8080",
    "wSS://arcumai.example.test:8080",
])
def test_criterion5_mixed_case_wss_with_secure_false_passes_validation(harness, server_url):
    _assert_valid(harness(server_url, False))


def test_criterion5_uppercase_wss_effective_url_stays_wss(harness):
    url = harness(REMOTE_WSS_UPPER, False)["WSURL"]
    assert url.lower().startswith("wss://arcumai.example.test:8080/ws/outlook/")


# --- Design D1: case-insensitive prefix check, other schemes still rejected --

def test_d1_uppercase_ws_localhost_passes_prefix_check(harness):
    _assert_valid(harness("WS://localhost:8080", False))


@pytest.mark.parametrize("server_url", [
    "https://arcumai.example.test:8080",
    "wss:/arcumai.example.test:8080",
    "arcumai.example.test:8080",
])
def test_d1_non_websocket_scheme_still_fails_prefix_check(harness, server_url):
    result = harness(server_url, True)
    assert result["VALID"] == "False"
    assert result["MESSAGE"] == PREFIX_MESSAGE


# --- Design D2: one predicate holds the TLS rule ------------------------------

def test_d2_is_cleartext_remote_connection_exists_and_is_callable_in_assembly(harness):
    result = harness(REMOTE_WSS, False)
    assert result["CLEARTEXT"] != "MISSING", (
        "PluginConfig.IsCleartextRemoteConnection() (instance, no parameters) not found"
    )
    assert result["CLEARTEXT_RETURN"] == "System.Boolean"
    assert result["CLEARTEXT_PRIVATE"] == "False", "must be callable from ThisAddIn (#47)"


def test_d2_is_cleartext_remote_connection_handles_null_server_url(harness):
    """#47 calls the predicate outside Validate(), so a null ServerUrl must not throw."""
    result = harness(None, False)
    assert result["CLEARTEXT"] == "True"


@pytest.mark.parametrize("server_url,use_secure,expected", [
    (REMOTE_WSS, False, "False"),
    (REMOTE_WSS_UPPER, False, "False"),
    (REMOTE_WSS, True, "False"),
    (REMOTE_WS, True, "False"),
    ("ws://localhost:8080", False, "False"),
    ("ws://127.0.0.1:8080", False, "False"),
    ("ws://[::1]:8080", False, "False"),
    (REMOTE_WS, False, "True"),
    ("WS://arcumai.example.test:8080", False, "True"),
    ("ws://wss.example.test:8080", False, "True"),
    ("ws://arcumai.example.test:8080/wss://x", False, "True"),
    ("ws://localhost.example.test:8080", False, "True"),
    ("ws://192.0.2.10:8080", False, "True"),
])
def test_d2_is_cleartext_remote_connection_follows_design_table(
        harness, server_url, use_secure, expected):
    assert harness(server_url, use_secure)["CLEARTEXT"] == expected


def test_d2_validate_and_predicate_agree_on_the_tls_rule(harness):
    cases = [
        (REMOTE_WSS, False), (REMOTE_WSS_UPPER, False), (REMOTE_WSS, True),
        (REMOTE_WS, False), (REMOTE_WS, True), ("ws://localhost:8080", False),
    ]
    for url, secure in cases:
        result = harness(url, secure)
        tls_rule_failed = result["MESSAGE"] == TLS_RULE_MESSAGE
        assert (result["CLEARTEXT"] == "True") == tls_rule_failed, (url, secure, result)


# --- Regressions: rows of the design table that must not change ---------------

def test_regression_ws_remote_with_secure_true_passes_and_upgrades_to_wss(harness):
    result = harness(REMOTE_WS, True)
    _assert_valid(result)
    assert result["WSURL"].startswith("wss://arcumai.example.test:8080/ws/outlook/")


@pytest.mark.parametrize("server_url", [
    "ws://localhost:8080", "ws://127.0.0.1:8080", "ws://[::1]:8080",
])
def test_regression_ws_loopback_with_secure_false_passes(harness, server_url):
    result = harness(server_url, False)
    _assert_valid(result)
    assert result["WSURL"].startswith("ws://")


def test_regression_empty_server_url_still_fails(harness):
    result = harness("", False)
    assert result["VALID"] == "False"
    assert result["MESSAGE"] == "ServerUrl cannot be empty"


# --- Criterion 6: documentation and changelog ---------------------------------

_WSS_SATISFIES = re.compile(r"wss://", re.I)


def _table_row(path, key):
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0].strip("`* ") == key:
            return cells
    pytest.fail(f"No table row for {key} in {path.relative_to(REPO_ROOT)}")


def _rule_cell(cells):
    """The cell that states the TLS rule (the one with 'unless')."""
    rule = [c for c in cells if "unless" in c.lower()]
    assert rule, f"No cell stating the TLS rule ('unless ...') in row {cells}"
    return rule[0]


def test_criterion6_config_readme_rule_says_wss_url_satisfies_tls():
    cell = _rule_cell(_table_row(CONFIG_README, "UseSecureConnection"))
    assert _WSS_SATISFIES.search(cell), cell


def test_criterion6_env_variables_doc_rule_says_wss_url_satisfies_tls():
    cell = _rule_cell(_table_row(ENV_VARIABLES_DOC, "UseSecureConnection"))
    assert _WSS_SATISFIES.search(cell), cell


def test_criterion6_tls_guide_rule_says_wss_url_satisfies_tls():
    text = TLS_GUIDE.read_text(encoding="utf-8")
    rule_lines = [l for l in text.splitlines()
                  if "UseSecureConnection" in l and re.search(r"\bmust be\b", l)]
    assert rule_lines, "doc/TLS_SETUP.md no longer states the UseSecureConnection rule"
    for line in rule_lines:
        assert _WSS_SATISFIES.search(line), line


def test_criterion6_sample_config_readme_rule_says_wss_url_satisfies_tls():
    """The readme text written by PluginConfigLoader.CreateSampleConfig."""
    text = PLUGIN_CONFIG_LOADER_CS.read_text(encoding="utf-8")
    required = [l for l in text.splitlines() if re.match(r"\s*Required:.*\bunless\b", l)]
    assert required, "No 'Required: ... unless ...' line in the sample config readme"
    assert any(_WSS_SATISFIES.search(l) for l in required), required


def test_criterion6_changelog_unreleased_mentions_issue_48():
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1]
    unreleased = re.split(r"\n## \[", unreleased, maxsplit=1)[0]
    assert "#48" in unreleased
