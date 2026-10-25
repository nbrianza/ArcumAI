# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Issue #39, owner decision on PR #67: exception text and tracebacks.

The remaining error lines that logged the full exception with a traceback must
follow the rule of the rest of #39: by default only the exception type; the
exception text and the traceback only with the content-logging switch on
(``LOG_CLIENT_CONTENT`` on the server, ``LogClientContent`` in the add-in).

Server lines (behavioural):
- ``loopback_processor.py`` "Processing failed" and "AI engine error";
- ``session.py`` "Engine execution error".

Add-in lines (static source checks; runtime evidence is the CI msbuild step):
- ``VirtualLoopbackHandler.cs`` "Error processing email";
- ``OutlookMailFactory.cs`` "Failed to create response email";
- ``ThisAddIn.cs`` "ItemSend handler error", "Request ... failed", "Error handling message".

Fixtures, fakes and the C# source scanner come from ``test_log_privacy.py``.
All data is synthetic.
"""
import asyncio
import logging
import re

import pytest

import src.logger as logger_module
from tests.test_log_privacy import (  # noqa: F401  (fixtures are used by name)
    ADDIN_DIR,
    LEAK_FRAGMENT,
    MARKER,
    OUTLOOK_ID,
    REQ_ID,
    USER,
    _cs_log_calls,
    _optimizer_ok,
    _params,
    _record_text,
    _run_loopback,
    fake_ai,
    server_records,
)


class _SyntheticError(RuntimeError):
    pass


def _error_records(records):
    return [r for r in records if r.levelno >= logging.ERROR]


def _assert_default_type_only(records, line_fragment: str):
    errors = [r for r in _error_records(records) if line_fragment in r.getMessage()]
    assert errors, f"no ERROR record containing {line_fragment!r}"
    for r in errors:
        assert "_SyntheticError" in r.getMessage(), "exception type missing from the error line"
        assert not r.exc_info, "traceback logged while the content switch is off"
    leaks = [r.getMessage() for r in records if LEAK_FRAGMENT in _record_text(r).lower()]
    assert not leaks, f"exception text logged while the content switch is off: {leaks}"


def _assert_switch_on_details(records, line_fragment: str):
    errors = [r for r in _error_records(records) if line_fragment in r.getMessage()]
    assert errors, f"no ERROR record containing {line_fragment!r}"
    assert any(MARKER in r.getMessage() for r in errors), "exception text missing with the switch on"
    assert any(r.exc_info for r in errors), "traceback missing with the switch on"


# --- loopback_processor.py: "Processing failed" -----------------------------

def _route_raises(monkeypatch):
    from src.bridge.loopback_processor import LoopbackProcessor

    async def _raise(self, *args, **kwargs):
        raise _SyntheticError(f"routing failed on {MARKER}")

    monkeypatch.setattr(LoopbackProcessor, "_route_to_ai_engine", _raise)


def test_processing_failed_logs_type_only_by_default(server_records, fake_ai, monkeypatch):
    _route_raises(monkeypatch)
    _, ws, _ = _run_loopback(monkeypatch, _params(), _optimizer_ok)
    assert ws.send_text.called, "the error reply was not sent (scenario broken)"
    _assert_default_type_only(server_records, "Processing failed")
    assert any(REQ_ID in r.getMessage() for r in _error_records(server_records))


def test_processing_failed_logs_text_and_traceback_with_switch_on(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True)
    _route_raises(monkeypatch)
    _run_loopback(monkeypatch, _params(), _optimizer_ok)
    _assert_switch_on_details(server_records, "Processing failed")


# --- loopback_processor.py: "AI engine error" -------------------------------

def _chat_action_raises(monkeypatch):
    import src.ai.session as session_module

    async def _raise(self, *args, **kwargs):
        raise _SyntheticError(f"engine failed on {MARKER}")

    monkeypatch.setattr(session_module.UserSession, "run_chat_action", _raise)


def test_ai_engine_error_logs_type_only_by_default(server_records, fake_ai, monkeypatch):
    _chat_action_raises(monkeypatch)
    _run_loopback(monkeypatch, _params(), _optimizer_ok)
    _assert_default_type_only(server_records, "AI engine error")


def test_ai_engine_error_logs_text_and_traceback_with_switch_on(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True)
    _chat_action_raises(monkeypatch)
    _run_loopback(monkeypatch, _params(), _optimizer_ok)
    _assert_switch_on_details(server_records, "AI engine error")


# --- session.py: "Engine execution error" -----------------------------------

def _engine_raises(engine):
    async def _raise(text):
        raise _SyntheticError(f"model failed on {MARKER}")

    engine.achat = _raise


def test_engine_execution_error_logs_type_only_by_default(server_records, fake_ai):
    from src.ai.session import UserSession
    _engine_raises(fake_ai)
    _, text, _ = asyncio.run(UserSession(username=USER, role="DEFAULT").run_chat_action("@simple a question"))
    assert "technical error" in text, "the engine error path was not reached (scenario broken)"
    _assert_default_type_only(server_records, "Engine execution error")


def test_engine_execution_error_logs_text_and_traceback_with_switch_on(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True)
    from src.ai.session import UserSession
    _engine_raises(fake_ai)
    asyncio.run(UserSession(username=USER, role="DEFAULT").run_chat_action("@simple a question"))
    _assert_switch_on_details(server_records, "Engine execution error")


# --- Add-in: exception text and stack trace only through PluginConfig.Content -

_CS_FULL_EXCEPTION = re.compile(r"\{\s*ex\s*\}|\bex\.ToString\s*\(|\bex\.StackTrace\b|\bex\.Message\b")

_ADDIN_EXCEPTION_LINES = [
    ("Core/VirtualLoopbackHandler.cs", "Error processing email"),
    ("Core/Loopback/OutlookMailFactory.cs", "Failed to create response email"),
    ("ThisAddIn.cs", "ItemSend handler error"),
    ("ThisAddIn.cs", "({method}) failed: "),
    ("ThisAddIn.cs", "Error handling message"),
]


def _strip_protected_value(raw: str) -> str:
    """Remove the first argument of every PluginConfig.Content(...) call."""
    out = raw
    while True:
        m = re.search(r"PluginConfig\.Content\s*\(", out)
        if not m:
            return out
        depth, k, split, end = 0, m.end() - 1, None, None
        for k in range(m.end() - 1, len(out)):
            ch = out[k]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            elif ch == "," and depth == 1 and split is None:
                split = k
        assert end is not None, "unbalanced PluginConfig.Content call"
        kept = out[split + 1:end] if split is not None else ""
        out = out[:m.start()] + " " + kept + " " + out[end + 1:]


def _addin_calls(rel: str, literal: str) -> list[str]:
    source = (ADDIN_DIR / rel).read_text(encoding="utf-8-sig")
    return [raw for _, _, raw in _cs_log_calls(source) if literal in raw]


@pytest.mark.parametrize("rel, literal", _ADDIN_EXCEPTION_LINES)
def test_addin_exception_lines_log_type_only_unless_switch_on(rel, literal):
    calls = _addin_calls(rel, literal)
    assert calls, f"no log call containing {literal!r} in {rel}"
    for raw in calls:
        assert "PluginConfig.Content(" in raw, f"{rel}: {literal!r} does not use PluginConfig.Content: {raw}"
        assert re.search(r"ex\.GetType\(\)\.Name", raw), f"{rel}: {literal!r} does not log the exception type: {raw}"
        unprotected = _strip_protected_value(raw)
        assert not _CS_FULL_EXCEPTION.search(unprotected), (
            f"{rel}: {literal!r} logs exception text or stack trace outside PluginConfig.Content: {raw}"
        )


def test_addin_exception_guard_self_test():
    assert _CS_FULL_EXCEPTION.search(_strip_protected_value('_logAction("ERROR", $"x: {ex}");'))
    assert _CS_FULL_EXCEPTION.search(_strip_protected_value('_logAction("ERROR", $"x: {ex.Message}");'))
    ok = '_logAction("ERROR", $"x: {PluginConfig.Content(ex.ToString(), ex.GetType().Name + " 0x" + ex.HResult.ToString("X8"))}");'
    assert not _CS_FULL_EXCEPTION.search(_strip_protected_value(ok))
