# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #39: no client content in server and add-in logs.

Approved design (issue #39 design comment plus the debug-switch addendum):
- Default logs never contain client content (subject, body, prompt, query,
  answer, attachment or upload file name, exception text that can quote them).
  They carry ids, counts, sizes and a sanitized extension (``safe_ext``).
- Client content may only reach a log line through ``src.logger.content()``,
  which returns it (as ``repr``) only when ``src.logger.LOG_CLIENT_CONTENT`` is
  true. ``LOG_CLIENT_CONTENT`` defaults to false, and ``src/config.py`` refuses
  to load when it is true while ``ARCUMAI_ENV`` is not ``dev``.
- The add-in has a local ``LogClientContent`` switch (default false, never
  pushed by the server) and ``PluginConfig.Content(...)``.

How the evidence is gathered:
- The ``ArcumServer`` and ``ArcumIngestion`` loggers have ``propagate = False``,
  so ``caplog`` cannot be used. The ``server_records`` fixture swaps their
  handlers for a list handler at DEBUG (as ``test_login_throttle.py`` does).
  Running at DEBUG proves criteria 1 and 2 together, because DEBUG includes INFO.
  Nothing is written to ``logs/`` or stdout while the fixture is active.
- All data is synthetic and carries ``ZZ-SYNTHETIC-MARKER-123``. ``users.json``
  is never read (``load_users`` is patched) and the pending-results store is a
  mock, so ``temp/`` is never touched.
- The NiceGUI upload and admin handlers (criterion 3) are closures that cannot
  be driven without a NiceGUI harness; they are covered by ``safe_ext`` unit
  tests and a static AST guard (owner decision D3).
- The C# add-in (criterion 5) cannot run here; it is covered by static checks
  on the source files. Runtime evidence for C# is the CI msbuild step only.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.logger as logger_module

# --- Synthetic test data -----------------------------------------------------

MARKER = "ZZ-SYNTHETIC-MARKER-123"
# Any log text containing this fragment (case-insensitive) counts as a leak,
# which also catches truncated or lower-cased copies of the marker.
LEAK_FRAGMENT = "synthetic-marker"

USER = "zz_log_tester"
OUTLOOK_ID = "zz-outlook-39"
REQ_ID = "req-zz39-0001"

SUBJECT = f"Contract review {MARKER}"
BODY = f"Dear team,\nplease check the file for {MARKER} by Friday.\r\nRegards"
CC = f"{MARKER.lower()}@example.invalid"
TXT_NAME = f"{MARKER}.txt"
TXT_TEXT = f"Synthetic document text mentioning {MARKER}."
SKIPPED_ENTRY = f"{MARKER}-scan.pdf (31.0 MB)"
OPTIMIZED = f"optimized search query about {MARKER} contract"

USERS = {USER: {"pw_hash": "x", "role": "DEFAULT", "name": "Zz Tester", "outlook_id": OUTLOOK_ID}}

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
ADDIN_DIR = REPO_ROOT / "outlook-plugin" / "ArcumAI.Outlook" / "ArcumAI.OutlookAddIn"
ADDIN_LOG_FILES = (
    "ThisAddIn.cs",
    "Core/VirtualLoopbackHandler.cs",
    "Core/Loopback/OutlookMailFactory.cs",
    "Core/Loopback/AttachmentExtractor.cs",
    "Core/OutlookDataProvider.cs",
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _txt_attachment() -> dict:
    return {"file_name": TXT_NAME, "content_base64": _b64(TXT_TEXT), "size": len(TXT_TEXT)}


def _params(attachments=(), skipped=(), has_attachments=None) -> dict:
    attachments = list(attachments)
    skipped = list(skipped)
    if has_attachments is None:
        has_attachments = bool(attachments or skipped)
    return {
        "subject": SUBJECT,
        "body": BODY,
        "conversation_id": "conv-zz39",
        "original_message_id": "<msg-zz39@example.invalid>",
        "has_attachments": has_attachments,
        "cc_recipients": [CC],
        "attachments": attachments,
        "skipped_attachments": skipped,
        "importance": 1,
    }


def _logger_attr(name: str):
    """Return src.logger.<name>, failing (not erroring) when it does not exist yet."""
    value = getattr(logger_module, name, None)
    if value is None:
        pytest.fail(f"src.logger.{name} is missing (issue #39 design)")
    return value


# --- Log capture -------------------------------------------------------------

class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def server_records(monkeypatch):
    """Capture every record of ArcumServer and ArcumIngestion at DEBUG.

    Asserts first that the content switch is off, so that every marker-absent
    assertion below proves "off by default means safe".
    """
    assert getattr(logger_module, "LOG_CLIENT_CONTENT", False) is False, (
        "LOG_CLIENT_CONTENT must be off in the test process"
    )
    handler = _ListHandler()
    loggers = [logging.getLogger("ArcumServer"), logging.getLogger("ArcumIngestion")]
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
    assert not leaks, "client content found in log records:\n" + "\n".join(leaks)
    if capsys is not None:
        out = capsys.readouterr()
        assert LEAK_FRAGMENT not in (out.out + out.err).lower(), "client content printed to the console"


def _all_text(records) -> str:
    return "\n".join(_record_text(r) for r in records)


# --- Fakes for the loopback path --------------------------------------------

class _FakeEngine:
    """Chat engine stand-in: records its input, answers without client data."""
    memory = None

    def __init__(self):
        self.inputs: list[str] = []

    async def achat(self, text):
        self.inputs.append(text)
        return "Synthetic answer without client data."


def _mock_ws():
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


@pytest.fixture
def fake_ai(monkeypatch):
    """Synthetic user plus a real UserSession whose engines are fakes."""
    import src.auth
    import src.ai.session as session_module
    monkeypatch.setattr(src.auth, "load_users", lambda: USERS)
    monkeypatch.setattr(session_module, "load_users", lambda: USERS)

    engine = _FakeEngine()

    async def _get_engine(self):
        return engine

    for name in ("get_rag_engine", "get_simple_engine", "get_agent_engine", "get_cloud_engine"):
        monkeypatch.setattr(session_module.UserSession, name, _get_engine)
    return engine


async def _optimizer_ok(subject, body, mode=None):
    return OPTIMIZED


async def _optimizer_fails(subject, body, mode=None):
    raise RuntimeError(f"optimizer failed on input {MARKER}")


def _run_loopback(monkeypatch, params, optimizer, online=True, request_id=REQ_ID):
    import src.engine
    from src.bridge.loopback_processor import LoopbackProcessor
    monkeypatch.setattr(src.engine, "optimize_prompt_for_rag", optimizer)
    ws = _mock_ws()
    connections = {OUTLOOK_ID: ws} if online else {}
    pending = MagicMock()
    pending.save = AsyncMock()
    processor = LoopbackProcessor(connections, pending)
    asyncio.run(processor._process_loopback_email(OUTLOOK_ID, request_id, params))
    return processor, ws, pending


def _attachment_read_error(monkeypatch):
    from src.bridge.loopback_processor import LoopbackProcessor

    def _raise(self, att_data):
        raise ValueError(f"cannot parse document {MARKER}")

    monkeypatch.setattr(LoopbackProcessor, "_process_attachment", _raise)


def _tiny_attachment_limit(monkeypatch):
    import src.config
    monkeypatch.setattr(src.config, "VSTO_MAX_ATTACHMENT_MB", 0.00001)


# Each case: (params factory, optimizer, online, extra setup)
_LOOPBACK_CASES = {
    "no_attachments_optimizer_ok": (lambda: _params(), _optimizer_ok, True, None),
    "no_attachments_optimizer_fails": (lambda: _params(), _optimizer_fails, True, None),
    "txt_attachment_optimizer_ok": (lambda: _params([_txt_attachment()]), _optimizer_ok, True, None),
    "txt_attachment_optimizer_fails": (lambda: _params([_txt_attachment()]), _optimizer_fails, True, None),
    "attachment_read_error": (lambda: _params([_txt_attachment()]), _optimizer_ok, True, _attachment_read_error),
    "attachment_plus_skipped_entry": (
        lambda: _params([_txt_attachment()], [SKIPPED_ENTRY]), _optimizer_fails, True, None),
    "all_attachments_skipped": (
        lambda: _params([], [SKIPPED_ENTRY], has_attachments=True), _optimizer_ok, True, None),
    "oversized_attachment": (lambda: _params([_txt_attachment()]), _optimizer_ok, True, _tiny_attachment_limit),
    "client_offline": (lambda: _params(), _optimizer_fails, False, None),
}


def _run_case(monkeypatch, case):
    make_params, optimizer, online, setup = _LOOPBACK_CASES[case]
    if setup:
        setup(monkeypatch)
    return _run_loopback(monkeypatch, make_params(), optimizer, online=online)


# =============================================================================
# Criteria 1 and 2: virtual loopback, LOG_LEVEL=DEBUG (covers INFO as well)
# =============================================================================

@pytest.mark.parametrize("case", list(_LOOPBACK_CASES))
def test_criterion1_2_loopback_email_marker_not_logged_at_debug(case, server_records, fake_ai, monkeypatch, capsys):
    _, ws, pending = _run_case(monkeypatch, case)

    # Non-vacuous: the request really went through and was answered or stored.
    assert ws.send_text.called or pending.save.called
    if case not in ("all_attachments_skipped",):
        assert fake_ai.inputs, "the AI engine was not reached"
        assert MARKER in "".join(fake_ai.inputs), "client content did not reach the engine (scenario broken)"
    assert server_records, "no log records captured"
    _assert_no_leak(server_records, capsys)


@pytest.mark.parametrize("case", list(_LOOPBACK_CASES))
def test_criterion6_loopback_logs_request_id_for_correlation(case, server_records, fake_ai, monkeypatch):
    _run_case(monkeypatch, case)
    assert REQ_ID in _all_text(server_records), "request id not logged: support cannot follow the request"


def test_criterion1_manager_send_email_path_marker_not_logged(server_records, monkeypatch, capsys):
    """manager.py RX preview (:154-155), 'Email received' (:184), enqueue (:301-302)."""
    from src.bridge.manager import OutlookBridgeManager

    async def run():
        mgr = OutlookBridgeManager()
        pending = MagicMock()
        pending.find = MagicMock(return_value=None)
        mgr._pending = pending
        mgr._process_loopback_email = AsyncMock()
        await mgr.connect(_mock_ws(), OUTLOOK_ID)
        message = json.dumps({
            "jsonrpc": "2.0",
            "method": "virtual_loopback/send_email",
            "id": REQ_ID,
            "params": _params([_txt_attachment()], [SKIPPED_ENTRY]),
        })
        await mgr.handle_incoming_message(OUTLOOK_ID, message)
        for _ in range(20):
            await asyncio.sleep(0)
        calls = mgr._process_loopback_email.await_count
        tasks = [uq.worker_task for uq in mgr._user_queues.values() if uq.worker_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return calls

    assert asyncio.run(run()) == 1, "the email was not enqueued and processed"
    _assert_no_leak(server_records, capsys)
    assert REQ_ID in _all_text(server_records), "request id not logged on the manager path"


def test_criterion2_gemini_optimizer_missed_entity_not_logged_at_debug(server_records, monkeypatch, capsys):
    """prompt_optimizer.py :128 (masked email) and :169 (Gemini output) at DEBUG."""
    from src.ai import prompt_optimizer
    import src.ai.ner_masking as ner

    def fake_mask(text, language="it", score_threshold=0.35):
        # Simulates a missed entity: the marker stays unmasked.
        return text, {"entity_counts": {"PERSON": 1}, "mapping": {"__PII_0000abcd__": "Synthetic"}}

    monkeypatch.setattr(prompt_optimizer, "ENABLE_NER_MASKING", True)
    monkeypatch.setattr(ner, "is_presidio_available", lambda: True)
    monkeypatch.setattr(ner, "mask_pii", fake_mask)
    monkeypatch.setattr(ner, "unmask_pii", lambda text, meta: text)
    llm = MagicMock()
    llm.acomplete = AsyncMock(return_value=f"query echoing {MARKER}")
    monkeypatch.setattr(prompt_optimizer, "_get_gemini_optimizer", lambda: llm)

    result = asyncio.run(prompt_optimizer.optimize_prompt_for_rag(SUBJECT, BODY, mode="gemini"))

    assert MARKER in result
    _assert_no_leak(server_records, capsys)


def test_d6_web_chat_question_not_logged(server_records, fake_ai, capsys):
    """session.py:309 also logs the start of every web chat question (owner decision D6)."""
    from src.ai.session import UserSession

    session = UserSession(username=USER, role="DEFAULT")
    _, text, mode = asyncio.run(session.run_chat_action(f"@simple question about {MARKER}"))

    assert fake_ai.inputs and MARKER in fake_ai.inputs[-1]
    _assert_no_leak(server_records, capsys)


# =============================================================================
# Criterion 4: Outlook email search tool (server side)
# =============================================================================

def _auto_responding_ws(mgr, user_id, result, sent: list, tasks: list):
    async def send_text(text):
        sent.append(text)
        data = json.loads(text)
        if data.get("method") == "tools/call":
            reply = json.dumps({"jsonrpc": "2.0", "id": data["id"], "result": result})
            tasks.append(asyncio.get_running_loop().create_task(mgr.handle_incoming_message(user_id, reply)))

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock(side_effect=send_text)
    return ws


def _search_through_tool(monkeypatch, query: str):
    import src.ai.session as session_module
    from src.bridge.manager import OutlookBridgeManager

    result = {"emails": [{"subject": f"Re: {MARKER}", "from": CC, "body": f"Synthetic mail body {MARKER}"}]}
    sent: list = []

    async def run():
        mgr = OutlookBridgeManager()
        tasks: list = []
        await mgr.connect(_auto_responding_ws(mgr, OUTLOOK_ID, result, sent, tasks), OUTLOOK_ID)
        monkeypatch.setattr(session_module, "bridge_manager", mgr)
        session = session_module.UserSession(username=USER, role="DEFAULT")
        tool = next(t for t in session.tools if t.metadata.name == "tool_read_email")
        output = await tool.acall(query=query)
        await asyncio.gather(*tasks, return_exceptions=True)
        return output

    output = asyncio.run(run())
    return output, sent


def test_criterion4_short_search_query_and_result_not_logged(server_records, fake_ai, monkeypatch, capsys):
    """manager.py :119 (TX payload with the query) and :155 (RX preview with email data)."""
    output, sent = _search_through_tool(monkeypatch, f"from:{MARKER}")

    assert any(MARKER in s for s in sent), "the query was not sent to Outlook (scenario broken)"
    assert MARKER in str(getattr(output, "raw_output", output))
    _assert_no_leak(server_records, capsys)


def test_criterion4_complex_search_query_autofix_not_logged(server_records, fake_ai, monkeypatch, capsys):
    """session.py :113 (AUTO-FIX EMAIL warning) with a query of more than 6 words."""
    output, _ = _search_through_tool(monkeypatch, f"what did {MARKER} write about the contract last week")

    assert any(r.levelno == logging.WARNING and "AUTO-FIX" in r.getMessage() for r in server_records), (
        "the auto-fix warning is still expected (with a length instead of the query)"
    )
    _assert_no_leak(server_records, capsys)


# =============================================================================
# Criterion 3 (D3): safe_ext unit tests and static guards
# =============================================================================

@pytest.mark.parametrize("name, expected", [
    ("contract.pdf", ".pdf"),
    ("CONTRACT.PDF", ".pdf"),
    (f"{MARKER}.txt", ".txt"),
    ("archive.tar.gz", ".gz"),
    ("archive.abcdefgh", "other"),           # not a known extension (allow-list, owner decision on PR #67)
    ("archive.abcdefghi", "other"),          # 9 characters: too long
    (f"report.{MARKER}", "other"),           # the suffix itself would leak content
    (MARKER, "other"),                       # no extension
    ("", "other"),
    ("notes.p-d", "other"),                  # non-alphanumeric suffix
])
def test_criterion3_safe_ext_returns_sanitized_extension(name, expected):
    safe_ext = _logger_attr("safe_ext")
    assert safe_ext(name) == expected


# --- Static AST guard over src/ ----------------------------------------------

_DENIED_NAMES = {
    "subject", "body", "query", "clean_query", "optimized_query", "optimized_instruction",
    "masked_email", "masked_optimized", "json_str", "log_preview", "message",
    "filename", "fname", "file_name", "skipped_attachments",
}
_DENIED_ATTRS = {"name", "subject", "body", "filename", "file_name", "query"}
_DENIED_KEYS = {"subject", "body", "filename", "file_name", "query", "skipped_attachments"}
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
# Wrappers whose arguments are safe to interpolate: lengths and sanitized extensions.
_FULLY_EXEMPT_CALLS = {"len", "safe_ext"}
# ``<expr>.name`` objects whose name is technical, not client data (checked on main
# at 0b2b9d7): pending-result files named from user_id and request_id
# (pending_results.py), conversation id files (conversations.py) and the trigger
# configuration files (utils.py). Any other ``.name`` in a server log call is flagged.
_TECHNICAL_NAME_OBJECTS = {"path", "Path(p)", "file_path"}


def _call_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _denied_in(node) -> list[str]:
    """Denied client values reachable from ``node`` outside content()/len()/safe_ext()."""
    found: list[str] = []

    def visit(n):
        if isinstance(n, ast.Call):
            name = _call_name(n.func)
            if name in _FULLY_EXEMPT_CALLS:
                return
            if name == "content":
                # Only the value is switch-protected; the redacted summary is always logged.
                for arg in n.args[1:]:
                    visit(arg)
                for kw in n.keywords:
                    if kw.arg != "value":
                        visit(kw.value)
                return
            if (isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args
                    and isinstance(n.args[0], ast.Constant) and n.args[0].value in _DENIED_KEYS):
                found.append(f".get({n.args[0].value!r})")
        elif isinstance(n, ast.Name) and n.id in _DENIED_NAMES:
            found.append(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in _DENIED_ATTRS:
            if not (n.attr == "name" and ast.unparse(n.value) in _TECHNICAL_NAME_OBJECTS):
                found.append(f".{n.attr}")
        elif (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
              and n.slice.value in _DENIED_KEYS):
            found.append(f"[{n.slice.value!r}]")
        for child in ast.iter_child_nodes(n):
            visit(child)

    visit(node)
    return found


def _server_log_aliases(tree) -> set[str]:
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "logger":
            for alias in node.names:
                if alias.name == "server_log":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _guard_violations(source: str, filename: str = "<string>") -> list[str]:
    """Server-log calls that interpolate a denied value outside content().

    Only calls on names bound to ``server_log`` are checked: ``ingestion.log``
    file names are out of scope (owner decision D4, follow-up issue).
    """
    tree = ast.parse(source, filename=filename)
    aliases = _server_log_aliases(tree)
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases):
            continue
        denied = []
        for arg in node.args:
            denied += _denied_in(arg)
        for kw in node.keywords:
            denied += _denied_in(kw.value)
        if denied:
            violations.append(f"{filename}:{node.lineno}: {sorted(set(denied))}")
    return violations


_GUARD_PREAMBLE = "from src.logger import server_log as slog, content, safe_ext\n"


def test_static_guard_self_test_flags_raw_and_allows_wrapped_values():
    bad = _GUARD_PREAMBLE + (
        "slog.info(f'Subject={subject}')\n"
        "slog.warning('Skipped: %s', skipped_attachments)\n"
        "slog.info(f'Deleted {row[\"filename\"]}')\n"
        "slog.info(f'Queued {full.name}')\n"
        "slog.info(f'Got {params.get(\"subject\", \"?\")}')\n"
        "slog.info(f'x {content(fname, fname)}')\n"
    )
    assert len(_guard_violations(bad)) == 6

    good = _GUARD_PREAMBLE + (
        "slog.info(f'req={request_id} Subject {content(subject)} ({len(subject)} chars)')\n"
        "slog.info(f'att #{i} ({content(fname, f\"ext={safe_ext(fname)}\")}): {type(e).__name__}')\n"
        "slog.info(f'MCP RX: ({len(message)} bytes) {content(log_preview, \"\")}')\n"
        "other_logger.info(f'{subject}')\n"
    )
    assert _guard_violations(good) == []


def test_criterion1_to_4_static_guard_no_client_value_in_server_log_calls_outside_content():
    violations = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        if path.name == "logger.py":
            continue
        violations += _guard_violations(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
    assert not violations, "server_log calls interpolating client values:\n" + "\n".join(violations)


def _log_call_texts(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    aliases = _server_log_aliases(tree)
    texts = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases):
            texts.append(ast.get_source_segment(source, node) or "")
    return texts


def test_criterion3_upload_and_admin_log_lines_drop_file_names_but_keep_the_action():
    footer = SRC_DIR / "ui" / "footer.py"
    admin = SRC_DIR / "ui" / "admin.py"
    violations = (_guard_violations(footer.read_text(encoding="utf-8"), "src/ui/footer.py")
                  + _guard_violations(admin.read_text(encoding="utf-8"), "src/ui/admin.py"))
    assert not violations, "\n".join(violations)

    footer_calls = _log_call_texts(footer)
    admin_calls = _log_call_texts(admin)
    assert any("UPLOAD START" in t and "username" in t for t in footer_calls), "upload start no longer logged with the user"
    assert any("Deleted" in t and "username" in t for t in admin_calls), "admin delete no longer logged with the user"
    assert any("re-ingest" in t and "username" in t for t in admin_calls), "admin re-ingest no longer logged with the user"


def test_d5_legacy_react_agent_is_not_verbose():
    """verbose=True prints tool arguments (search queries) and email data to stdout."""
    tree = ast.parse((SRC_DIR / "ai" / "session.py").read_text(encoding="utf-8"))
    verbose_calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == "from_tools"
        and any(kw.arg == "verbose" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords)
    ]
    assert not verbose_calls, f"LegacyReActAgent.from_tools(verbose=True) at session.py lines {verbose_calls}"


# =============================================================================
# Debug switch: content() helper, startup warning, defaults, refusal outside dev
# =============================================================================

def test_switch_content_off_without_summary_returns_char_count(monkeypatch):
    content = _logger_attr("content")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", False, raising=False)
    assert content(SUBJECT) == f"<{len(SUBJECT)} chars>"


def test_switch_content_off_with_summary_returns_summary(monkeypatch):
    content = _logger_attr("content")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", False, raising=False)
    assert content(TXT_NAME, "ext=.txt") == "ext=.txt"
    assert content(BODY, "") == ""


def test_switch_content_on_returns_repr_of_value(monkeypatch):
    content = _logger_attr("content")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    assert content(SUBJECT) == repr(SUBJECT)
    assert content(TXT_NAME, "ext=.txt") == repr(TXT_NAME)


def test_switch_content_on_escapes_newlines_so_one_log_line_stays_one_line(monkeypatch):
    content = _logger_attr("content")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    result = content(BODY)
    assert MARKER in result
    assert "\n" not in result and "\r" not in result


def _recorder(name: str):
    handler = _ListHandler()
    recorder = logging.getLogger(name)
    recorder.handlers = [handler]
    recorder.propagate = False
    recorder.setLevel(logging.DEBUG)
    return recorder, handler.records


def test_switch_startup_warning_once_when_on(monkeypatch):
    warn = _logger_attr("warn_if_content_logging")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    recorder, records = _recorder("tests.log_privacy.warn_on")
    warn(recorder)
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "LOG_CLIENT_CONTENT" in warnings[0].getMessage()


def test_switch_no_startup_warning_when_off(monkeypatch):
    warn = _logger_attr("warn_if_content_logging")
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", False, raising=False)
    recorder, records = _recorder("tests.log_privacy.warn_off")
    warn(recorder)
    assert [r for r in records if r.levelno >= logging.WARNING] == []


def test_switch_server_startup_calls_warning_and_reports_content_logging_state():
    source = (REPO_ROOT / "main_nice.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n.func) == "warn_if_content_logging"]
    assert len(calls) == 1, "main_nice.py must call warn_if_content_logging once at startup"
    assert "Content logging" in source, "startup info line must report 'Content logging: ON/OFF'"


# --- Off by default / refuse outside dev (fresh interpreter) -----------------
# src/config.py is executed in a subprocess under a throwaway module name so that
# no constant of this test session is rebound (same pattern as test_config.py).

_SWITCH_CONFIG_PROBE = r"""
import importlib.util, json, os, sys
cases = json.loads(sys.argv[1])
path = os.path.join(os.getcwd(), "src", "config.py")
results = {}
for label, overrides in cases.items():
    for key in ("LOG_CLIENT_CONTENT", "ARCUMAI_ENV"):
        os.environ.pop(key, None)
    os.environ.update(overrides)
    spec = importlib.util.spec_from_file_location("config_probe_" + label, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        results[label] = {"ok": True, "value": getattr(mod, "LOG_CLIENT_CONTENT", None)}
    except Exception as exc:
        results[label] = {"ok": False, "type": type(exc).__name__, "error": str(exc)}
print("PROBE_RESULT=" + json.dumps(results))
"""

_SWITCH_CASES = {
    "unset_dev": {"ARCUMAI_ENV": "dev"},
    "unset_no_env": {},
    "false_dev": {"LOG_CLIENT_CONTENT": "false", "ARCUMAI_ENV": "dev"},
    "one_dev": {"LOG_CLIENT_CONTENT": "1", "ARCUMAI_ENV": "dev"},
    "yes_dev": {"LOG_CLIENT_CONTENT": "yes", "ARCUMAI_ENV": "dev"},
    "typo_dev": {"LOG_CLIENT_CONTENT": "ture", "ARCUMAI_ENV": "dev"},
    "false_prod": {"LOG_CLIENT_CONTENT": "false", "ARCUMAI_ENV": "prod"},
    "true_dev": {"LOG_CLIENT_CONTENT": "true", "ARCUMAI_ENV": "dev"},
    "TRUE_dev": {"LOG_CLIENT_CONTENT": "TRUE", "ARCUMAI_ENV": "dev"},
    "true_no_env": {"LOG_CLIENT_CONTENT": "true"},
    "true_prod": {"LOG_CLIENT_CONTENT": "true", "ARCUMAI_ENV": "prod"},
}

_switch_probe_cache: dict = {}


def _base_env() -> dict:
    import os
    env = dict(os.environ)
    env.pop("LOG_CLIENT_CONTENT", None)
    env["ARCUMAI_ENV"] = "dev"
    return env


def _switch_probe() -> dict:
    if not _switch_probe_cache:
        proc = subprocess.run(
            [sys.executable, "-c", _SWITCH_CONFIG_PROBE, json.dumps(_SWITCH_CASES)],
            cwd=str(REPO_ROOT), env=_base_env(), capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, f"probe crashed: {proc.stderr[-2000:]}"
        line = [l for l in proc.stdout.splitlines() if l.startswith("PROBE_RESULT=")][-1]
        _switch_probe_cache.update(json.loads(line[len("PROBE_RESULT="):]))
    return _switch_probe_cache


@pytest.mark.parametrize("label", ["unset_dev", "unset_no_env", "false_dev", "one_dev", "yes_dev",
                                   "typo_dev", "false_prod"])
def test_switch_off_by_default_and_fail_safe_parsing(label):
    assert _switch_probe()[label] == {"ok": True, "value": False}


@pytest.mark.parametrize("label", ["true_dev", "TRUE_dev"])
def test_switch_on_with_true_in_dev(label):
    assert _switch_probe()[label] == {"ok": True, "value": True}


@pytest.mark.parametrize("label", ["true_no_env", "true_prod"])
def test_switch_refuses_to_start_when_on_outside_dev(label):
    result = _switch_probe()[label]
    assert result["ok"] is False, f"{label}: config loaded with LOG_CLIENT_CONTENT=true outside dev"
    assert result["type"] == "ValueError", result
    assert "LOG_CLIENT_CONTENT" in result["error"], result


_LOGGER_DEFAULT_PROBE = r"""
import json, sys
import src.logger as lg
content = getattr(lg, "content", None)
print("PROBE_RESULT=" + json.dumps({
    "flag": getattr(lg, "LOG_CLIENT_CONTENT", None),
    "content": content(sys.argv[1]) if content else None,
}))
"""


def test_switch_off_by_default_in_fresh_server_process():
    proc = subprocess.run(
        [sys.executable, "-c", _LOGGER_DEFAULT_PROBE, SUBJECT],
        cwd=str(REPO_ROOT), env=_base_env(), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"probe crashed: {proc.stderr[-2000:]}"
    line = [l for l in proc.stdout.splitlines() if l.startswith("PROBE_RESULT=")][-1]
    result = json.loads(line[len("PROBE_RESULT="):])
    assert result == {"flag": False, "content": f"<{len(SUBJECT)} chars>"}


# --- On means detailed (monkeypatched, never via the process environment) ---

SUBJ_ON = "ZZ-ON-SUBJECT-123"
BODY_ON = "ZZ-ON-BODY-123"
QUERY_ON = "ZZ-ON-QUERY-123"
SKIPPED_ON = "ZZ-ON-SKIPPED-123"


def _on_params(attachments=(), skipped=()):
    p = _params(attachments, skipped)
    p["subject"] = f"Subject {SUBJ_ON}"
    p["body"] = f"Line one {BODY_ON}\nLine two"
    return p


async def _optimizer_on(subject, body, mode=None):
    return f"query {QUERY_ON}"


def test_switch_on_loopback_logs_subject_body_query_and_request_id(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    _run_loopback(monkeypatch, _on_params(), _optimizer_on)
    text = _all_text(server_records)
    for value in (SUBJ_ON, BODY_ON, QUERY_ON, REQ_ID):
        assert value in text, f"{value} missing from the detailed log"


def test_switch_on_loopback_with_attachments_logs_skipped_file_name(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    _run_loopback(monkeypatch, _on_params([_txt_attachment()], [f"{SKIPPED_ON}.pdf (31.0 MB)"]), _optimizer_on)
    text = _all_text(server_records)
    for value in (SUBJ_ON, BODY_ON, QUERY_ON, SKIPPED_ON, REQ_ID):
        assert value in text, f"{value} missing from the detailed log"


def test_switch_on_body_newlines_do_not_forge_log_lines(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    params = _on_params()
    _run_loopback(monkeypatch, params, _optimizer_on)
    raw_body = params["body"]
    assert any(BODY_ON in r.getMessage() for r in server_records)
    assert not any(raw_body in r.getMessage() for r in server_records), "raw body with newline written to the log"


def test_switch_on_manager_rx_preview_is_logged(server_records, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    from src.bridge.manager import OutlookBridgeManager

    async def run():
        mgr = OutlookBridgeManager()
        await mgr.connect(_mock_ws(), OUTLOOK_ID)
        future = asyncio.get_running_loop().create_future()
        mgr.pending_requests[REQ_ID] = future
        await mgr.handle_incoming_message(
            OUTLOOK_ID, json.dumps({"jsonrpc": "2.0", "id": REQ_ID, "result": {"emails": [{"subject": SUBJ_ON}]}}))
        return future.done()

    assert asyncio.run(run())
    text = _all_text(server_records)
    assert SUBJ_ON in text and REQ_ID in text


def test_switch_on_chat_query_is_logged(server_records, fake_ai, monkeypatch):
    monkeypatch.setattr(logger_module, "LOG_CLIENT_CONTENT", True, raising=False)
    from src.ai.session import UserSession
    session = UserSession(username=USER, role="DEFAULT")
    asyncio.run(session.run_chat_action(f"@simple a fairly long question that ends with {QUERY_ON}"))
    assert QUERY_ON in _all_text(server_records)


def test_switch_not_pushed_to_addin_in_client_config():
    from src.bridge.manager import OutlookBridgeManager
    config_block = OutlookBridgeManager()._build_client_config("vsto_outlook")
    assert not [k for k in config_block if "content" in k.lower() or "log" in k.lower()]


# =============================================================================
# Criterion 5: C# add-in (static checks only; runtime evidence is CI msbuild)
# =============================================================================

def _cs_code_view(source: str) -> str:
    """Same-length copy of C# source with comments and string literal text blanked.

    Interpolation holes ({expr}) of $"..." strings are kept as code, so what
    remains is exactly the code that can put a value into a log message.
    """
    out = list(source)
    n = len(source)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    def scan_string(i, prefix_len, interpolated, verbatim):
        blank(i, i + prefix_len)
        i += prefix_len
        while i < n:
            c = source[i]
            if not verbatim and c == "\\":
                blank(i, i + 2)
                i += 2
                continue
            if c == '"':
                if verbatim and source.startswith('""', i):
                    blank(i, i + 2)
                    i += 2
                    continue
                blank(i, i + 1)
                return i + 1
            if interpolated and c == "{":
                if source.startswith("{{", i):
                    blank(i, i + 2)
                    i += 2
                    continue
                blank(i, i + 1)
                j = scan_code(i + 1, inside_hole=True)
                blank(j, j + 1)
                i = j + 1
                continue
            blank(i, i + 1)
            i += 1
        return i

    def scan_code(i, inside_hole=False):
        depth = 0
        while i < n:
            c = source[i]
            if source.startswith("//", i):
                j = source.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
                continue
            if source.startswith("/*", i):
                j = source.find("*/", i + 2)
                j = n if j < 0 else j + 2
                blank(i, j)
                i = j
                continue
            if c == "'":
                j = i + 1
                while j < n and source[j] != "'":
                    j += 2 if source[j] == "\\" else 1
                blank(i, j + 1)
                i = j + 1
                continue
            m = re.match(r'(\$@|@\$|\$|@)?"', source[i:i + 3])
            if m:
                prefix = m.group(1) or ""
                i = scan_string(i, len(m.group(0)), "$" in prefix, "@" in prefix)
                continue
            if inside_hole:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i
                    depth -= 1
            i += 1
        return i

    scan_code(0)
    return "".join(out)


def _matching_close(view: str, open_index: int, open_ch="(", close_ch=")") -> int:
    depth = 0
    for k in range(open_index, len(view)):
        if view[k] == open_ch:
            depth += 1
        elif view[k] == close_ch:
            depth -= 1
            if depth == 0:
                return k
    raise AssertionError("unbalanced C# source")


_CS_LOG_CALL = re.compile(r"(_logAction|_logger\.Log)\s*\(")
_CS_EXEMPT_CALL = re.compile(r"(PluginConfig\.Content|SafeExt)\s*\(")
_CS_LENGTH = re.compile(r"[\w.\[\]]+\??\.Length\b")
_CS_DENIED = {
    "subject": re.compile(r"(?<![\w.])subject\b"),
    ".Subject": re.compile(r"\.Subject\b"),
    "OriginalSubject": re.compile(r"\bOriginalSubject\b"),
    "fileName": re.compile(r"(?<![\w.])fileName\b"),
    ".FileName": re.compile(r"\.FileName\b"),
    "name": re.compile(r"(?<![\w.])name\b"),
    "query": re.compile(r"(?<![\w.])query\b"),
}
# AttachmentExtractor exception text can quote the temp path built from the file name.
_CS_DENIED_PER_FILE = {"AttachmentExtractor.cs": {"ex.Message": re.compile(r"\bex\.Message\b")}}


def _cs_log_calls(source: str):
    """Yield (line, code_view_of_call) for every add-in log call in ``source``."""
    view = _cs_code_view(source)
    for m in _CS_LOG_CALL.finditer(view):
        open_index = m.end() - 1
        close = _matching_close(view, open_index)
        yield view.count("\n", 0, m.start()) + 1, view[m.start():close + 1], source[m.start():close + 1]


def _cs_violations(source: str, filename: str) -> list[str]:
    denied = dict(_CS_DENIED)
    denied.update(_CS_DENIED_PER_FILE.get(Path(filename).name, {}))
    violations = []
    for line, code, _ in _cs_log_calls(source):
        # Remove switch-protected values (PluginConfig.Content(value, redacted): only
        # the first argument is protected) and sanitized extensions.
        while True:
            m = _CS_EXEMPT_CALL.search(code)
            if not m:
                break
            close = _matching_close(code, m.end() - 1)
            inner = code[m.end():close]
            kept = ""
            if m.group(1) == "PluginConfig.Content":
                depth, split = 0, None
                for k, ch in enumerate(inner):
                    if ch in "([{":
                        depth += 1
                    elif ch in ")]}":
                        depth -= 1
                    elif ch == "," and depth == 0:
                        split = k
                        break
                kept = inner[split + 1:] if split is not None else ""
            code = code[:m.start()] + " " + kept + " " + code[close + 1:]
        code = _CS_LENGTH.sub(" ", code)
        hits = [label for label, rx in denied.items() if rx.search(code)]
        if hits:
            violations.append(f"{filename}:{line}: {hits}")
    return violations


def test_addin_static_guard_self_test():
    bad = (
        '_logAction("INFO", $"Processing email \'{subject}\' (ID: {requestId})");\n'
        '_logger.Log("INFO", $"Intercepting: \'{mail.Subject}\'");\n'
        '_logAction("WARNING", $"Timeout ({req.OriginalSubject})");\n'
        '_logAction("WARNING", "Skipping " + fileName);\n'
        '_logAction("WARNING", $"Could not extract \'{name}\': {ex.Message}");\n'
        '_logAction("INFO", $"found {n} for \'{query}\'");\n'
        '_logAction("INFO", $"x {PluginConfig.Content(fileName, fileName)}");\n'
    )
    assert len(_cs_violations(bad, "AttachmentExtractor.cs")) == 7

    good = (
        '_logAction("INFO", $"Processing email (ID: {requestId}, subject {subject.Length} chars)");\n'
        '_logAction("INFO", $"Subject {PluginConfig.Content(subject, $"{subject.Length} chars")}");\n'
        '_logAction("INFO", $"#{i} ({PluginConfig.Content(fileName, $"ext={SafeExt(fileName)}")}) {mimeType}");\n'
        '_logAction("INFO", $"found {n} emails (query {query?.Length ?? 0} chars)");\n'
        '_logAction("INFO", "subject and file name and query in literal text are fine");\n'
        '// _logAction("INFO", $"{subject}");\n'
    )
    assert _cs_violations(good, "AttachmentExtractor.cs") == []


def test_criterion5_addin_log_calls_do_not_interpolate_subject_file_name_or_query():
    violations = []
    for rel in ADDIN_LOG_FILES:
        path = ADDIN_DIR / rel
        violations += _cs_violations(path.read_text(encoding="utf-8-sig"), rel)
    assert not violations, "add-in log calls with client values outside PluginConfig.Content:\n" + "\n".join(violations)


def _cs_calls_containing(rel: str, literal: str) -> list[str]:
    source = (ADDIN_DIR / rel).read_text(encoding="utf-8-sig")
    return [raw for _, _, raw in _cs_log_calls(source) if literal in raw]


@pytest.mark.parametrize("rel, literal, id_expr", [
    ("Core/VirtualLoopbackHandler.cs", "Processing email", "requestId"),
    ("Core/VirtualLoopbackHandler.cs", "All attachments skipped", "requestId"),
    ("Core/VirtualLoopbackHandler.cs", "Timeout for request", "requestId"),
    ("Core/VirtualLoopbackHandler.cs", "Received AI response", "requestId"),
    ("Core/Loopback/OutlookMailFactory.cs", "injected into Inbox", "request_id"),
])
def test_criterion5_6_addin_steps_are_correlated_by_request_id(rel, literal, id_expr):
    calls = _cs_calls_containing(rel, literal)
    assert calls, f"no log call containing {literal!r} in {rel}"
    assert all(id_expr in raw for raw in calls), f"{rel}: {literal!r} log line(s) without {id_expr}: {calls}"


def _cs_method_body(rel: str, signature: str) -> str:
    source = (ADDIN_DIR / rel).read_text(encoding="utf-8-sig")
    view = _cs_code_view(source)
    m = re.search(signature, view)
    assert m, f"{signature!r} not found in {rel}"
    open_index = view.index("{", m.end())
    close = _matching_close(view, open_index, "{", "}")
    return source[open_index:close + 1]


def test_addin_switch_property_and_content_helper_exist():
    source = (ADDIN_DIR / "Core" / "PluginConfig.cs").read_text(encoding="utf-8-sig")
    assert re.search(r"public\s+bool\s+LogClientContent\s*\{\s*get;\s*set;\s*\}", source), \
        "PluginConfig.LogClientContent property missing"
    assert re.search(r"static\s+string\s+Content\s*\(\s*string\s+\w+\s*,\s*string\s+\w+", source), \
        "PluginConfig.Content(string value, string redacted) helper missing"


def test_addin_switch_defaults_to_false_and_is_read_from_app_config():
    defaults = _cs_method_body("Core/PluginConfigLoader.cs", r"static\s+void\s+SetDefaults\s*\(")
    assert re.search(r"\.LogClientContent\s*=\s*false\s*;", defaults), "SetDefaults must set LogClientContent = false"
    app_config_reader = _cs_method_body("Core/PluginConfigLoader.cs", r"static\s+void\s+LoadFromAppConfig\s*\(")
    assert 'appSettings["LogClientContent"]' in app_config_reader, "LoadFromAppConfig must read LogClientContent"
    assert re.search(r"bool\.TryParse\s*\(\s*appSettings\[\"LogClientContent\"\]", app_config_reader), \
        "LogClientContent must be parsed with bool.TryParse"


def test_addin_app_config_ships_log_client_content_false():
    text = (ADDIN_DIR / "App.config").read_text(encoding="utf-8-sig")
    assert re.search(r'<add\s+key="LogClientContent"\s+value="false"\s*/>', text), \
        'App.config must contain <add key="LogClientContent" value="false" />'


def test_addin_switch_is_local_only_not_server_managed():
    apply_values = _cs_method_body("Core/PluginConfigLoader.cs", r"static\s+int\s+ApplyServerValues\s*\(")
    assert "LogClientContent" not in apply_values and "log_client_content" not in apply_values
    loader = (ADDIN_DIR / "Core" / "PluginConfigLoader.cs").read_text(encoding="utf-8-sig")
    assert re.search(r"ServerManagedKeyCount\s*=\s*8\s*;", loader), "ServerManagedKeyCount must stay 8"


def test_addin_startup_warns_when_log_client_content_is_on():
    calls = _cs_calls_containing("ThisAddIn.cs", "LogClientContent")
    assert any('"WARNING"' in raw for raw in calls), \
        "ThisAddIn startup must log a WARNING mentioning LogClientContent when it is on"
