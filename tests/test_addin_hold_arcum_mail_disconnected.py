# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #60: a mail addressed to ArcumAI is never sent
through the mail server while the Outlook add-in is disconnected.

The add-in is a C# VSTO project. There is no C# test project and it can only
be compiled on Windows CI, so its runtime behaviour (send cancelled, compose
window kept open, balloon shown, nothing delivered) is verified manually in
classic Outlook, as criterion 10 of the issue requires.

What is automated here, following the approved design (D1 to D8) on #60:

- Static source checks on ``ThisAddIn.Application_ItemSend`` and
  ``VirtualLoopbackHandler.NotifyHeldWhileDisconnected``. They do not prove
  runtime behaviour; they pin the shape the design relies on: the
  disconnected branch recognises the ArcumAI recipient with the existing
  ``ShouldIntercept`` / ``ShouldProcessInParallel`` rule, sets
  ``Cancel = true`` before anything that could throw, logs one fixed-text
  line and shows a fixed-text notice, both guarded so a failure cannot undo
  the hold, and leaves the mail untouched. The connected path keeps its
  current shape.
- Documentation checks for criterion 9 and the ADR 0001 bullet (D6).

The checks are structural on purpose: they do not depend on the exact
wording of log lines or comments, so the nearby edits of #39 (log lines in
the connected path) and #47 (refusal guard in ``ConnectToArcum``) do not
affect them.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDIN_DIR = REPO_ROOT / "outlook-plugin" / "ArcumAI.Outlook" / "ArcumAI.OutlookAddIn"
THIS_ADDIN = ADDIN_DIR / "ThisAddIn.cs"
LOOPBACK_HANDLER = ADDIN_DIR / "Core" / "VirtualLoopbackHandler.cs"
CONFIG_README = ADDIN_DIR / "config.README.md"
ADR_0001 = REPO_ROOT / "doc" / "decisions" / "0001-transport-security.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

NOTICE_METHOD = "NotifyHeldWhileDisconnected"
CANCEL_TRUE = re.compile(r"\bCancel\s*=\s*true\b")
CANCEL_FALSE = re.compile(r"\bCancel\s*=\s*false\b")
LOG_CALL = re.compile(r"\b_logger\s*\.\s*Log\s*\(")
DISCONNECTED_TEST = re.compile(
    r"!\s*_transport\s*\??\.\s*IsConnected\b"
    r"|_transport\s*\??\.\s*IsConnected\s*(?:==\s*false|!=\s*true)")
RECIPIENT_RULE = re.compile(r"\bShould(?:Intercept|ProcessInParallel)\s*\(")

# Mail data that must never reach the log line or the notice (criteria 3, 7).
MAIL_DATA_TOKENS = (
    "Subject", "Body", "HTMLBody", "RTFBody", "Recipients", "Recipient",
    "Address", "SenderEmailAddress", "SenderName", "Attachments",
    "Attachment", "FileName", "DisplayName", "PluginConfig.Content",
)


# --- Minimal C# source helpers ------------------------------------------------

def _mask_literals_and_comments(src):
    """Return ``src`` with the content of string/char literals and comments
    replaced by spaces, keeping offsets, so braces and words inside them do
    not affect the checks. Good enough for the hand-written add-in code."""
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


def _strip_comments(src):
    """Remove comments but keep string literals (to inspect message texts)."""
    masked = _mask_literals_and_comments(src)
    out = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i) and masked[i] == " ":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i) and masked[i] == " ":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _matching(masked, open_index, opener, closer):
    assert masked[open_index] == opener
    depth = 0
    for k in range(open_index, len(masked)):
        if masked[k] == opener:
            depth += 1
        elif masked[k] == closer:
            depth -= 1
            if depth == 0:
                return k
    raise AssertionError(f"unbalanced {opener}{closer}")


def _method_body(path, signature_regex):
    """Return the ``{ ... }`` body of the first method matching the regex."""
    src = path.read_text(encoding="utf-8")
    masked = _mask_literals_and_comments(src)
    match = re.search(signature_regex, masked)
    assert match, f"{signature_regex!r} not found in {path.name}"
    open_index = masked.index("{", match.end())
    return src[open_index:_matching(masked, open_index, "{", "}") + 1]


def _statement_after(src, masked, start):
    """Span (begin, end) of the statement that starts at or after ``start``:
    a ``{ ... }`` block or a single statement ending with ``;``."""
    k = start
    while k < len(masked) and masked[k].isspace():
        k += 1
    if masked[k] == "{":
        return k, _matching(masked, k, "{", "}") + 1
    return k, masked.index(";", k) + 1


def _if_statements(src):
    """Yield (condition_code, body_begin, body_end) for every ``if`` in src,
    offsets relative to ``src``."""
    masked = _mask_literals_and_comments(src)
    for m in re.finditer(r"\bif\s*\(", masked):
        open_paren = m.end() - 1
        close_paren = _matching(masked, open_paren, "(", ")")
        begin, end = _statement_after(src, masked, close_paren + 1)
        yield masked[open_paren:close_paren + 1], begin, end


def _try_blocks(src):
    """Yield (begin, end) of every ``try { ... }`` block in src."""
    masked = _mask_literals_and_comments(src)
    for m in re.finditer(r"\btry\s*\{", masked):
        open_index = m.end() - 1
        yield open_index, _matching(masked, open_index, "{", "}") + 1


def _code(text):
    return _mask_literals_and_comments(text)


def _item_send_body():
    return _method_body(
        THIS_ADDIN, r"\bvoid\s+Application_ItemSend\s*\(\s*object\s+\w+\s*,\s*ref\s+bool\s+Cancel\s*\)")


def _disconnected_branch():
    """(method_body, begin, end) of the statement run when the transport is
    not connected, inside ``Application_ItemSend``."""
    body = _item_send_body()
    for condition, begin, end in _if_statements(body):
        if DISCONNECTED_TEST.search(condition):
            return body, begin, end
    raise AssertionError(
        "Application_ItemSend has no 'if (!_transport.IsConnected)' branch")


def _disconnected_block():
    body, begin, end = _disconnected_branch()
    return body[begin:end]


def _connected_part():
    """Everything in Application_ItemSend after the disconnected branch."""
    body, _, end = _disconnected_branch()
    return body[end:]


def _notice_method_body():
    return _method_body(
        LOOPBACK_HANDLER, rf"\b{NOTICE_METHOD}\s*\(\s*\)")


def _log_statements(block):
    """Full text (with literals) of every ``_logger.Log(...)`` call in block."""
    masked = _code(block)
    calls = []
    for m in LOG_CALL.finditer(masked):
        open_paren = m.end() - 1
        close_paren = _matching(masked, open_paren, "(", ")")
        calls.append(block[m.start():close_paren + 1])
    return calls


def _string_literals(code_with_literals):
    return re.findall(r'"((?:[^"\\]|\\.)*)"', code_with_literals)


def _hold_conditions(block):
    """Identifiers assigned from the recipient rule, plus the rule itself, so
    both ``if (ShouldIntercept(mail) || ...)`` and
    ``bool hold = ShouldIntercept(mail) || ...; if (hold)`` are accepted."""
    code = _code(block)
    names = set(re.findall(
        r"\b(\w+)\s*=\s*[^;=][^;]*\bShould(?:Intercept|ProcessInParallel)\s*\(", code))
    return names


def _guarded_by_recipient_rule(block, position):
    """True if ``position`` lies in the body of an ``if`` whose condition uses
    the ArcumAI recipient rule (directly or through a local variable)."""
    names = _hold_conditions(block)
    for condition, begin, end in _if_statements(block):
        if not (begin <= position < end):
            continue
        if RECIPIENT_RULE.search(condition):
            return True
        if any(re.search(rf"\b{re.escape(n)}\b", condition) for n in names):
            return True
    return False


# --- Criteria 1 and 4: hold every mail to ArcumAI while disconnected ---------

def test_criterion1_disconnected_branch_cancels_the_send():
    code = _code(_disconnected_block())
    assert CANCEL_TRUE.search(code), (
        "the disconnected branch of Application_ItemSend never sets Cancel = true: "
        "a mail to ArcumAI is still sent through the mail server (#60)")


def test_criterion1_disconnected_branch_detects_arcum_only_recipient():
    code = _code(_disconnected_block())
    assert re.search(r"\bShouldIntercept\s*\(", code), \
        "the disconnected branch must use ShouldIntercept (ArcumAI is the only recipient)"


def test_criterion4_disconnected_branch_detects_arcum_plus_real_recipients():
    code = _code(_disconnected_block())
    assert re.search(r"\bShouldProcessInParallel\s*\(", code), (
        "the disconnected branch must use ShouldProcessInParallel "
        "(ArcumAI in To/CC/BCC next to real recipients, owner option a)")


def test_criteria1_4_cancel_only_when_arcum_is_a_recipient():
    # Criterion 6 depends on this: mails without ArcumAI are not held.
    block = _disconnected_block()
    code = _code(block)
    positions = [m.start() for m in CANCEL_TRUE.finditer(code)]
    assert positions, "no Cancel = true in the disconnected branch"
    for pos in positions:
        assert _guarded_by_recipient_rule(block, pos), (
            "Cancel = true in the disconnected branch must depend on the ArcumAI "
            "recipient rule (ShouldIntercept / ShouldProcessInParallel)")


def test_criterion1_cancel_is_set_before_log_and_notice():
    code = _code(_disconnected_block())
    cancel = CANCEL_TRUE.search(code)
    assert cancel, "no Cancel = true in the disconnected branch"
    log = LOG_CALL.search(code)
    notice = re.search(rf"\b{NOTICE_METHOD}\s*\(", code)
    assert log and notice, "the hold must log and show the notice"
    assert cancel.start() < log.start(), "Cancel = true must be set before the log call"
    assert cancel.start() < notice.start(), "Cancel = true must be set before the notice"


def test_criterion1_log_and_notice_failures_cannot_undo_the_hold():
    # An exception from the log or the notice must not reach the outer catch,
    # which sets Cancel = false and lets the mail go out.
    block = _disconnected_block()
    code = _code(block)
    tries = list(_try_blocks(block))
    for label, pattern in (("log call", LOG_CALL),
                           ("notice call", re.compile(rf"\b{NOTICE_METHOD}\s*\("))):
        m = pattern.search(code)
        assert m, f"no {label} in the disconnected branch"
        assert any(b <= m.start() < e for b, e in tries), \
            f"the {label} of the hold must be inside a try/catch"
    assert not CANCEL_FALSE.search(code), \
        "the disconnected branch must never set Cancel = false"


def test_criterion1_disconnected_branch_still_returns_before_connected_path():
    code = _code(_disconnected_block())
    assert re.search(r"\breturn\b", code), \
        "the disconnected branch must return so the connected path is not run"


# --- Criterion 2 and 8: the held mail is kept as it is ------------------------

def test_criterion2_hold_does_not_modify_send_or_delete_the_mail():
    code = _code(_disconnected_block())
    for forbidden in ("RemoveArcumRecipient", "ProcessInterceptedEmail",
                      "SimulateSentItem", "DeleteInterceptedItem", "DeleteFromOutbox",
                      "SendAsync"):
        assert not re.search(rf"\b{forbidden}\s*\(", code), \
            f"the hold must not call {forbidden}(): the mail stays unchanged in Outlook"
    for forbidden in (r"\.\s*Send\s*\(", r"\.\s*Delete\s*\(", r"\.\s*Close\s*\(",
                      r"\.\s*Move\s*\(", r"\.\s*Remove\s*\("):
        assert not re.search(forbidden, code), \
            f"the hold must not modify the mail ({forbidden})"


# --- Criterion 3: visible notice without mail data ---------------------------

def test_criterion3_hold_shows_the_notice():
    code = _code(_disconnected_block())
    assert re.search(rf"\b_loopbackHandler\s*\??\.\s*{NOTICE_METHOD}\s*\(\s*\)", code), \
        f"the disconnected branch must call _loopbackHandler.{NOTICE_METHOD}()"


def test_criterion3_notice_method_is_public_and_takes_no_mail():
    src = _code(LOOPBACK_HANDLER.read_text(encoding="utf-8"))
    assert re.search(rf"\bpublic\s+void\s+{NOTICE_METHOD}\s*\(\s*\)", src), \
        f"VirtualLoopbackHandler must expose 'public void {NOTICE_METHOD}()' (no parameters)"


def test_criterion3_notice_uses_the_existing_tray_balloon():
    code = _code(_notice_method_body())
    assert re.search(r"\bShowToastNotification\s*\(", code), \
        "the notice must use OutlookMailFactory.ShowToastNotification (D2 option a)"


def test_criterion3_notice_text_contains_no_mail_data():
    body = _notice_method_body()
    code = _code(body)
    for token in MAIL_DATA_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", code), \
            f"the notice must not use {token}"
    assert '$"' not in body and "$@\"" not in body and "@$\"" not in body, \
        "the notice text must be fixed (no string interpolation)"
    assert not re.search(r"\bstring\s*\.\s*(?:Format|Concat|Join)\s*\(", code), \
        "the notice text must be fixed (no string formatting)"


def test_criterion3_notice_says_not_connected_and_not_sent():
    text = " ".join(_string_literals(_strip_comments(_notice_method_body()))).lower()
    assert "connect" in text, "the notice must say that ArcumAI is not connected"
    assert re.search(r"not (?:been )?sent|was not sent|wasn't sent|has not been sent", text), \
        "the notice must say that the mail was not sent"


def test_d3_notice_is_shown_regardless_of_show_processing_notification():
    for code in (_code(_notice_method_body()), _code(_disconnected_block())):
        assert "ShowProcessingNotification" not in code, \
            "the hold notice is a safety notice and must not depend on ShowProcessingNotification"


def test_d2_no_blocking_dialog_in_the_send_path():
    for path in (THIS_ADDIN, LOOPBACK_HANDLER):
        code = _code(path.read_text(encoding="utf-8"))
        assert "MessageBox" not in code, f"{path.name} must not use MessageBox (D2 option a)"


# --- Criterion 7: one log line per held send, without personal data ---------

def test_criterion7_hold_logs_exactly_one_line():
    code = _code(_disconnected_block())
    assert len(LOG_CALL.findall(code)) == 1, \
        "the disconnected branch must write exactly one log line per held send"
    notice = _code(_notice_method_body())
    assert not re.search(r"\b_log(?:Action|ger)\b", notice), \
        f"{NOTICE_METHOD}() must not log a second line for the same held send"


def test_criterion7_hold_log_line_is_a_warning():
    calls = _log_statements(_disconnected_block())
    assert calls, "no log call in the disconnected branch"
    assert re.match(r'_logger\s*\.\s*Log\(\s*"WARNING"\s*,', calls[0]), \
        "the hold log line must be written at WARNING level (D5)"


def test_criterion7_hold_log_line_contains_no_mail_data():
    calls = _log_statements(_disconnected_block())
    assert calls, "no log call in the disconnected branch"
    call = calls[0]
    code = _code(call)
    assert '$"' not in call and '$@"' not in call and '@$"' not in call, \
        "the hold log line must be fixed text (no string interpolation)"
    assert not re.search(r"\bmail\b", code), "the hold log line must not read the mail"
    for token in MAIL_DATA_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", code), \
            f"the hold log line must not include {token}"
    # Only string literals as arguments (literal concatenation is fine): no
    # variable, property or method call can add data to the line.
    arguments = code[code.index("(") + 1:-1]
    residual = re.sub(r'"[^"\n]*"', "", arguments)
    assert not re.search(r"[A-Za-z_]", residual), \
        "the hold log line must contain fixed text only (no values appended)"


def test_criterion7_hold_log_line_states_arcum_mail_held_while_disconnected():
    calls = _log_statements(_disconnected_block())
    assert calls, "no log call in the disconnected branch"
    message = " ".join(_string_literals(calls[0])[1:]).lower()
    assert "arcum" in message, "the hold log line must say the mail is addressed to ArcumAI"
    assert "not connected" in message or "disconnected" in message, \
        "the hold log line must say the add-in is not connected"


def test_criterion7_hold_branch_reads_no_mail_content():
    code = _code(_disconnected_block())
    assert not re.search(
        r"\.\s*(?:Subject|Body|HTMLBody|RTFBody|Attachments|SenderEmailAddress|SenderName|To|CC|BCC)\b",
        code), "the hold branch must not read mail content or addresses"


# --- D4: recipient analysis errors keep today's fail-open rule --------------

def test_d4_outer_catch_still_lets_the_mail_send_on_error():
    body = _item_send_body()
    masked = _code(body)
    catches = [m for m in re.finditer(r"\bcatch\b[^{]*\{", masked)]
    assert catches, "Application_ItemSend lost its outer catch"
    last = catches[-1]
    open_index = last.end() - 1
    outer_catch = masked[open_index:_matching(masked, open_index, "{", "}") + 1]
    assert CANCEL_FALSE.search(outer_catch), \
        "the outer catch must keep Cancel = false (D4: fail open on analysis errors)"


def test_d4_no_fail_closed_catch_in_the_disconnected_branch():
    block = _disconnected_block()
    masked = _code(block)
    for m in re.finditer(r"\bcatch\b[^{]*\{", masked):
        open_index = m.end() - 1
        catch_body = masked[open_index:_matching(masked, open_index, "{", "}") + 1]
        assert not CANCEL_TRUE.search(catch_body), \
            "D4: an error must not turn into a hold; only the recipient rule decides"


# --- Criterion 5: connected path unchanged ------------------------------------

def test_criterion5_connected_path_still_intercepts_and_processes_in_parallel():
    code = _code(_connected_part())
    order = ["ShouldIntercept", "ProcessInterceptedEmail", "ShouldProcessInParallel",
             "RemoveArcumRecipient", "ProcessInterceptedEmail"]
    pos = 0
    for name in order:
        m = re.compile(rf"\b{name}\s*\(").search(code, pos)
        assert m, f"connected path no longer calls {name}() in the expected order"
        pos = m.end()


def test_criterion5_connected_full_intercept_cancels_and_parallel_does_not():
    part = _connected_part()
    full = parallel = None
    for condition, begin, end in _if_statements(part):
        if full is None and re.search(r"\bShouldIntercept\s*\(", condition):
            full = part[begin:end]
        elif parallel is None and re.search(r"\bShouldProcessInParallel\s*\(", condition):
            parallel = part[begin:end]
    assert full is not None, "connected path has no ShouldIntercept branch"
    assert parallel is not None, "connected path has no ShouldProcessInParallel branch"
    assert CANCEL_TRUE.search(_code(full)), "connected full intercept must still cancel the send"
    assert not CANCEL_TRUE.search(_code(parallel)), \
        "connected parallel case must still send to the real recipients"
    assert NOTICE_METHOD not in _code(part), "the hold notice must not appear in the connected path"


def test_criterion5_disconnected_check_runs_before_connected_handling():
    body, begin, _ = _disconnected_branch()
    code = _code(body)
    first_rule_outside = None
    for m in RECIPIENT_RULE.finditer(code):
        if m.start() < begin:
            first_rule_outside = m
            break
    assert first_rule_outside is None, \
        "the recipient rule must not run before the connection check"
    null_check = re.search(r"\bmail\s*==\s*null\b", code)
    assert null_check and null_check.start() < begin, \
        "non-MailItem items must still return before the connection check"


# --- Criterion 9: documentation, changelog and ADR ---------------------------

def _markdown_section(text, heading_regex):
    """Return the section under the first heading matching ``heading_regex``
    (case-insensitive), up to the next heading of the same or higher level."""
    for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.MULTILINE):
        if re.search(heading_regex, m.group(2), re.IGNORECASE):
            level = len(m.group(1))
            rest = text[m.end():]
            end = re.search(rf"^#{{1,{level}}}\s", rest, re.MULTILINE)
            return rest[:end.start()] if end else rest
    return None


def _readme_hold_section():
    text = CONFIG_README.read_text(encoding="utf-8")
    section = _markdown_section(
        text, r"(?=.*(?:disconnect|not connected|offline))(?=.*(?:send|sending|sent|mail))")
    assert section is not None, (
        "config.README.md needs a section (heading mentioning sending and the "
        "disconnected state) explaining what happens to a mail to ArcumAI while the "
        "add-in is disconnected (#60)")
    return section


def test_criterion9_addin_readme_explains_the_held_send():
    lower = _readme_hold_section().lower()
    assert "arcumai" in lower
    assert re.search(r"not sent|cancel|held|hold", lower), \
        "the section must say the mail is not sent"
    assert re.search(r"open|draft", lower), \
        "the section must say the mail stays available (compose window or Drafts)"
    assert re.search(r"notice|notification|balloon", lower), \
        "the section must mention the notice shown to the user"
    assert "reconnect" in lower or "connected again" in lower or "again" in lower, \
        "the section must say how to send the mail later"


def test_criterion9_addin_readme_covers_cc_and_bcc():
    section = _readme_hold_section()
    assert "CC" in section and "BCC" in section, \
        "the section must say the whole send is held also with ArcumAI in CC or BCC (owner option a)"


def test_d5_addin_readme_mentions_the_log_line_and_enable_logging():
    section = _readme_hold_section()
    assert "plugin.log" in section or "WARNING" in section, \
        "the section must mention the WARNING line in plugin.log"
    assert "EnableLogging" in section, \
        "the section must say the line is not written when EnableLogging is false (D5)"


def test_criterion9_changelog_unreleased_security_mentions_issue_60():
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1]
    unreleased = re.split(r"\n## \[", unreleased, maxsplit=1)[0]
    security = re.search(r"### Security\s*\n(.*?)(?=\n### |\n---|\Z)", unreleased, re.DOTALL)
    assert security, "CHANGELOG [Unreleased] has no Security subsection"
    assert "#60" in security.group(1), "CHANGELOG [Unreleased] Security must reference #60"


def test_d6_adr_0001_decision_records_the_hold_rule():
    text = ADR_0001.read_text(encoding="utf-8")
    decision = _markdown_section(text, r"^Decision\b")
    assert decision is not None
    assert "#60" in decision, "ADR 0001 Decision must record the hold rule (#60)"
    lower = decision.lower()
    assert "not connected" in lower or "disconnected" in lower


def test_d6_adr_0001_consequences_mention_blocked_sends_while_disconnected():
    text = ADR_0001.read_text(encoding="utf-8")
    consequences = _markdown_section(text, r"^Consequences\b")
    assert consequences is not None
    lower = consequences.lower()
    assert "not connected" in lower or "disconnected" in lower, \
        "ADR 0001 Consequences must say mails to ArcumAI cannot be sent while disconnected"
