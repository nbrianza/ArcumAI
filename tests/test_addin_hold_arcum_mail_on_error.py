# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #68: a mail addressed to ArcumAI is never sent
through the mail server when the connected add-in hits an error in the send
handler before the ArcumAI address is confirmed removed.

The add-in is a C# VSTO project. There is no C# test project and it can only
be compiled on Windows CI, so its runtime behaviour (send cancelled, compose
window kept open, balloon shown, nothing delivered) is verified manually in
classic Outlook with a local, uncommitted fault-injection build (design D4).

What is automated here, following the approved design (D1 to D6, option a
for D2) on #68:

- Static source checks on ``ThisAddIn.Application_ItemSend``, the new private
  ``ThisAddIn.ReportSendHeldOnError`` and the new public
  ``VirtualLoopbackHandler.NotifyHeldOnError``. They do not prove runtime
  behaviour; they pin the shape the design relies on: a local boolean flag,
  set as the first statement of each connected ArcumAI branch and cleared
  right after ``RemoveArcumRecipient`` returns; an outer catch that sets
  ``Cancel = true`` first when the flag is set and otherwise behaves as
  today; one fixed-text ERROR line with only the exception type and HResult;
  a fixed-text notice without mail data.
- Documentation checks for criterion 8 and design D5.

The checks are structural on purpose (no exact wording). The #60 tests in
``test_addin_hold_arcum_mail_disconnected.py`` must keep passing as they are,
in particular ``test_d4_outer_catch_still_lets_the_mail_send_on_error``.
"""
import re

from tests.test_addin_hold_arcum_mail_disconnected import (
    ADR_0001,
    CANCEL_FALSE,
    CANCEL_TRUE,
    CHANGELOG,
    CONFIG_README,
    LOG_CALL,
    LOOPBACK_HANDLER,
    MAIL_DATA_TOKENS,
    THIS_ADDIN,
    _code,
    _connected_part,
    _if_statements,
    _item_send_body,
    _log_statements,
    _markdown_section,
    _matching,
    _method_body,
    _statement_after,
    _string_literals,
    _strip_comments,
    _try_blocks,
)

HELPER = "ReportSendHeldOnError"
NOTICE = "NotifyHeldOnError"
HELPER_SIGNATURE = (
    rf"\bprivate\s+void\s+{HELPER}\s*\(\s*(?:System\s*\.\s*)?Exception\s+(\w+)\s*\)")
NOTICE_SIGNATURE = rf"\bpublic\s+void\s+{NOTICE}\s*\(\s*\)"


# --- Helpers -------------------------------------------------------------------

def _skip_ws(masked, k):
    while k < len(masked) and masked[k].isspace():
        k += 1
    return k


def _connected_branches():
    """(intercept_block, parallel_block) of the connected path, as ``{ ... }``
    texts with literals and comments kept."""
    part = _connected_part()
    full = parallel = None
    for condition, begin, end in _if_statements(part):
        if full is None and re.search(r"\bShouldIntercept\s*\(", condition):
            full = part[begin:end]
        elif parallel is None and re.search(r"\bShouldProcessInParallel\s*\(", condition):
            parallel = part[begin:end]
    assert full is not None, "connected path has no ShouldIntercept branch"
    assert parallel is not None, "connected path has no ShouldProcessInParallel branch"
    assert full.lstrip().startswith("{") and parallel.lstrip().startswith("{"), \
        "the connected ArcumAI branches must be { ... } blocks"
    return full, parallel


def _flag_name():
    """Name of the boolean local declared ``= false`` before the ``try`` of
    Application_ItemSend and assigned ``true`` in the ShouldIntercept branch."""
    body = _item_send_body()
    masked = _code(body)
    first_try = re.search(r"\btry\s*\{", masked)
    assert first_try, "Application_ItemSend has no try block"
    candidates = re.findall(r"\bbool\s+(\w+)\s*=\s*false\s*;", masked[:first_try.start()])
    assert candidates, (
        "Application_ItemSend must declare a local 'bool <flag> = false;' before its "
        "try block, recording that a mail to ArcumAI is not yet cleared (#68, D1)")
    full, _ = _connected_branches()
    full_code = _code(full)
    for name in candidates:
        if re.search(rf"\b{re.escape(name)}\s*=\s*true\s*;", full_code):
            return name
    raise AssertionError(
        f"none of the bool locals {candidates} is set to true in the ShouldIntercept branch")


def _first_statement(block):
    """Masked text of the first statement inside a ``{ ... }`` block."""
    masked = _code(block)
    k = _skip_ws(masked, masked.index("{") + 1)
    begin, end = _statement_after(block, masked, k)
    return masked[begin:end]


def _outer_catch():
    """(catch_variable, catch_block_text) of the last catch in Application_ItemSend."""
    body = _item_send_body()
    masked = _code(body)
    catches = list(re.finditer(r"\bcatch\b[^{]*\{", masked))
    assert catches, "Application_ItemSend lost its outer catch"
    last = catches[-1]
    var = re.search(r"\(\s*(?:System\s*\.\s*)?Exception\s+(\w+)\s*\)", last.group(0))
    assert var, "the outer catch must still be 'catch (Exception <name>)'"
    open_index = last.end() - 1
    return var.group(1), body[open_index:_matching(masked, open_index, "{", "}") + 1]


def _flagged_if_and_else():
    """(if_block, else_block, rest_after_else) inside the outer catch, where the
    if condition is the flag alone."""
    flag = _flag_name()
    _, catch = _outer_catch()
    masked = _code(catch)
    for condition, begin, end in _if_statements(catch):
        if re.fullmatch(rf"\(\s*{re.escape(flag)}\s*(?:==\s*true\s*)?\)", condition):
            k = _skip_ws(masked, end)
            assert masked.startswith("else", k), \
                "the flagged if in the outer catch must have an else branch (criterion 7)"
            e_begin, e_end = _statement_after(catch, masked, k + len("else"))
            return catch[begin:end], catch[e_begin:e_end], catch[e_end:]
    raise AssertionError(
        f"the outer catch of Application_ItemSend has no 'if ({flag})' branch (#68, D1)")


def _helper():
    src = _code(THIS_ADDIN.read_text(encoding="utf-8"))
    m = re.search(HELPER_SIGNATURE, src)
    assert m, f"ThisAddIn must define 'private void {HELPER}(Exception ex)' (#68)"
    return m.group(1), _method_body(THIS_ADDIN, HELPER_SIGNATURE)


def _notice_body():
    src = _code(LOOPBACK_HANDLER.read_text(encoding="utf-8"))
    assert re.search(NOTICE_SIGNATURE, src), \
        f"VirtualLoopbackHandler must expose 'public void {NOTICE}()' (no parameters, #68)"
    return _method_body(LOOPBACK_HANDLER, NOTICE_SIGNATURE)


def _expression_code(call):
    """Code of a call that can carry values: everything outside string
    literals plus the holes of interpolated strings."""
    holes = []
    for m in re.finditer(r'(\$@?|@\$)"((?:[^"\\]|\\.)*)"', call):
        holes.extend(re.findall(r"(?<!\{)\{([^{}]*)\}", m.group(2)))
    return _code(call) + " " + " ".join(holes)


# --- Criteria 1 and 2 (D1): the flag -------------------------------------------

def test_d1_flag_is_a_bool_local_declared_false_before_the_try():
    assert _flag_name()


def test_criterion2_flag_is_the_first_statement_of_the_arcum_only_branch():
    flag = _flag_name()
    full, _ = _connected_branches()
    first = _first_statement(full)
    assert re.fullmatch(rf"{re.escape(flag)}\s*=\s*true\s*;", first), (
        f"'{flag} = true;' must be the first statement of the ShouldIntercept branch, "
        "before the subject is read for the log line and before Cancel = true")


def test_criterion1_flag_is_the_first_statement_of_the_parallel_branch():
    flag = _flag_name()
    _, parallel = _connected_branches()
    first = _first_statement(parallel)
    assert re.fullmatch(rf"{re.escape(flag)}\s*=\s*true\s*;", first), (
        f"'{flag} = true;' must be the first statement of the ShouldProcessInParallel "
        "branch, before the log line and before RemoveArcumRecipient")


def test_criterion1_flag_cleared_right_after_remove_arcum_recipient_returns():
    flag = _flag_name()
    _, parallel = _connected_branches()
    code = _code(parallel)
    remove = re.search(r"\bRemoveArcumRecipient\s*\(", code)
    assert remove, "the parallel branch no longer calls RemoveArcumRecipient()"
    clear = re.compile(rf"\b{re.escape(flag)}\s*=\s*false\s*;")
    assert not clear.search(code, 0, remove.start()), \
        f"'{flag}' must not be cleared before RemoveArcumRecipient() has returned"
    statement_end = code.index(";", _matching(code, remove.end() - 1, "(", ")")) + 1
    k = _skip_ws(code, statement_end)
    assert clear.match(code, k), (
        f"'{flag} = false;' must be the statement right after RemoveArcumRecipient(mail);")


def test_criterion2_flag_not_cleared_before_cancel_in_arcum_only_branch():
    flag = _flag_name()
    full, _ = _connected_branches()
    code = _code(full)
    cancel = CANCEL_TRUE.search(code)
    assert cancel, "the ShouldIntercept branch must still set Cancel = true"
    clear = re.search(rf"\b{re.escape(flag)}\s*=\s*false\b", code)
    assert clear is None or clear.start() > cancel.start(), \
        f"'{flag}' must not be cleared before Cancel = true in the ArcumAI-only branch"


def test_criterion7_flag_set_only_in_the_two_connected_arcum_branches():
    # Errors in recipient analysis and mails without ArcumAI keep fail open
    # (out of scope, #60 D4): the flag may become true only inside the branches.
    flag = _flag_name()
    full, parallel = _connected_branches()
    sets = re.compile(rf"\b{re.escape(flag)}\s*=\s*true\b")
    total = len(sets.findall(_code(_item_send_body())))
    inside = len(sets.findall(_code(full))) + len(sets.findall(_code(parallel)))
    assert total == inside == 2, (
        f"'{flag} = true' must appear exactly once in each connected ArcumAI branch "
        f"and nowhere else (found {total} in total, {inside} in the branches)")


# --- Criteria 1, 2 and 7: the outer catch ------------------------------------

def test_criteria1_2_outer_catch_sets_cancel_true_first_when_flag_is_set():
    flagged, _, _ = _flagged_if_and_else()
    first = _first_statement(flagged)
    assert re.fullmatch(r"Cancel\s*=\s*true\s*;", first), (
            "the first statement of the flagged branch in the outer catch must be "
            "Cancel = true, before any call that could fail")


def test_criteria1_2_outer_catch_reports_through_the_helper_after_cancel():
    var, _ = _outer_catch()
    flagged, _, _ = _flagged_if_and_else()
    code = _code(flagged)
    cancel = CANCEL_TRUE.search(code)
    call = re.search(rf"\b{HELPER}\s*\(\s*{re.escape(var)}\s*\)", code)
    assert call, f"the flagged branch must call {HELPER}({var})"
    assert cancel and cancel.start() < call.start(), \
        f"Cancel = true must be set before {HELPER}() is called"


def test_criteria1_2_flagged_catch_branch_cannot_undo_the_hold():
    flagged, _, rest = _flagged_if_and_else()
    code = _code(flagged)
    assert not CANCEL_FALSE.search(code), "the flagged branch must never set Cancel = false"
    assert not LOG_CALL.search(code), (
        f"the flagged branch must not log directly: the one ERROR line is written by "
        f"{HELPER}() (criterion 5)")
    assert not re.search(r"\b(?:try|catch|throw)\b", code), (
        "no nested try/catch/throw in the flagged branch: the last catch of "
        "Application_ItemSend must stay the outer one (#60 D4 test)")
    assert not re.search(r"\bCancel\b", _code(rest)), \
        "nothing after the if/else in the outer catch may change Cancel"


def test_criterion7_outer_catch_else_branch_unchanged_fail_open():
    _, otherwise, _ = _flagged_if_and_else()
    code = _code(otherwise)
    assert CANCEL_FALSE.search(code), \
        "the else branch of the outer catch must keep Cancel = false (criterion 7, #60 D4)"
    assert not CANCEL_TRUE.search(code), "the else branch must not cancel the send"
    assert HELPER not in code and NOTICE not in code, \
        "the else branch must not report a held send"
    calls = _log_statements(otherwise)
    assert len(calls) == 1 and re.match(r'_logger\s*\.\s*Log\(\s*"ERROR"\s*,', calls[0]), \
        "the else branch must keep its single ERROR log line"
    assert CANCEL_FALSE.search(code).start() > LOG_CALL.search(code).start(), \
        "the else branch must keep its order: log, then Cancel = false"


# --- Criterion 5: one ERROR line without mail data or exception message -------

def test_criterion5_helper_logs_exactly_one_error_line():
    _, body = _helper()
    calls = _log_statements(body)
    assert len(calls) == 1, f"{HELPER}() must write exactly one log line"
    assert re.match(r'_logger\s*\.\s*Log\(\s*"ERROR"\s*,', calls[0]), \
        f"the {HELPER}() log line must be written at ERROR level (D3)"


def test_criterion5_helper_log_line_has_no_mail_data_and_no_exception_message():
    var, body = _helper()
    calls = _log_statements(body)
    assert calls, f"{HELPER}() writes no log line"
    expr = _expression_code(calls[0])
    assert not re.search(r"\bmail\b", expr), "the held-send log line must not read the mail"
    for token in MAIL_DATA_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", expr), \
            f"the held-send log line must not include {token}"
    for use in re.finditer(rf"\b{re.escape(var)}\b", expr):
        tail = expr[use.end():]
        assert re.match(r"\s*\.\s*(?:GetType\s*\(\s*\)\s*\.\s*Name|HResult)\b", tail), (
            f"the held-send log line may use only {var}.GetType().Name and {var}.HResult; "
            f"never {var}.Message, {var}.ToString() or {{{var}}} (D3: a COM message can "
            "quote a recipient)")
    for forbidden in ("Message", "ToString", "StackTrace", "InnerException", "Source", "Data"):
        assert not re.search(rf"\.\s*{forbidden}\b", expr), \
            f"the held-send log line must not include .{forbidden}"


def test_criterion5_helper_reads_no_mail_at_all():
    _, body = _helper()
    code = _code(body)
    assert not re.search(r"\bmail\b|\bItem\b", code), f"{HELPER}() must not touch the mail"
    for token in MAIL_DATA_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", code), \
            f"{HELPER}() must not use {token}"


def test_criterion5_helper_log_line_says_arcum_mail_not_sent():
    _, body = _helper()
    calls = _log_statements(body)
    assert calls, f"{HELPER}() writes no log line"
    text = " ".join(_string_literals(_strip_comments(calls[0]))[1:]).lower()
    assert "arcum" in text, "the held-send log line must say the mail is addressed to ArcumAI"
    assert re.search(r"not sent|cancel|held", text), \
        "the held-send log line must say the send was cancelled"


def test_criterion5_connected_branches_write_no_error_or_warning_line():
    # Exactly one ERROR/WARNING line per held send: the branch log lines and
    # RemoveArcumRecipient must stay below WARNING.
    full, parallel = _connected_branches()
    for block in (full, parallel):
        for call in _log_statements(block):
            assert not re.match(r'_logger\s*\.\s*Log\(\s*"(?:ERROR|WARNING)"', call), \
                "a connected ArcumAI branch must not write an ERROR or WARNING line"
    remove = _method_body(LOOPBACK_HANDLER, r"\bvoid\s+RemoveArcumRecipient\s*\(")
    assert not re.search(r'"(?:ERROR|WARNING)"', remove), \
        "RemoveArcumRecipient must not write an ERROR or WARNING line"


# --- Criteria 1, 2 and 4: helper behaviour --------------------------------------

def test_criterion4_helper_shows_the_on_error_notice():
    _, body = _helper()
    code = _code(body)
    assert re.search(rf"\b_loopbackHandler\s*\??\.\s*{NOTICE}\s*\(\s*\)", code), \
        f"{HELPER}() must call _loopbackHandler.{NOTICE}()"
    assert "NotifyHeldWhileDisconnected" not in code, \
        "the on-error hold must not reuse the 'not connected' notice"


def test_criteria1_2_helper_swallows_its_own_errors():
    _, body = _helper()
    code = _code(body)
    tries = list(_try_blocks(body))
    for label, pattern in (("log call", LOG_CALL),
                           ("notice call", re.compile(rf"\b{NOTICE}\s*\("))):
        m = pattern.search(code)
        assert m, f"no {label} in {HELPER}()"
        assert any(b <= m.start() < e for b, e in tries), \
            f"the {label} in {HELPER}() must be inside a try/catch"
    assert re.search(r"\bcatch\b", code), f"{HELPER}() must catch its own errors"
    assert not re.search(r"\bthrow\b", code), f"{HELPER}() must not rethrow"


# --- Criterion 4: the notice ------------------------------------------------------

def test_criterion4_notice_uses_the_existing_tray_balloon():
    assert re.search(r"\bShowToastNotification\s*\(", _code(_notice_body())), \
        f"{NOTICE}() must use ShowToastNotification (tray balloon, as #60 D2 option a)"


def test_criterion4_notice_text_is_fixed_and_has_no_mail_data():
    body = _notice_body()
    code = _code(body)
    for token in MAIL_DATA_TOKENS:
        assert not re.search(rf"\b{re.escape(token)}\b", code), \
            f"the notice must not use {token}"
    assert '$"' not in body and '$@"' not in body and '@$"' not in body, \
        "the notice text must be fixed (no string interpolation)"
    assert not re.search(r"\bstring\s*\.\s*(?:Format|Concat|Join)\s*\(", code), \
        "the notice text must be fixed (no string formatting)"


def test_criterion4_notice_says_not_sent_and_check_recipients_and_send_again():
    text = " ".join(_string_literals(_strip_comments(_notice_body()))).lower()
    assert re.search(r"not (?:been )?sent|wasn't sent", text), \
        "the notice must say that the mail was not sent"
    assert "recipient" in text, \
        "the notice must ask the user to check the recipients (D2 option a)"
    assert "again" in text, "the notice must ask the user to send the mail again"
    assert "connect" not in text, \
        "the on-error notice must not say ArcumAI is not connected (it is connected)"


def test_criterion4_notice_shown_regardless_of_show_processing_notification():
    _, helper = _helper()
    flagged, _, _ = _flagged_if_and_else()
    for code in (_code(_notice_body()), _code(helper), _code(flagged)):
        assert "ShowProcessingNotification" not in code, \
            "the on-error hold notice must not depend on ShowProcessingNotification"


def test_criterion5_notice_does_not_write_a_second_log_line():
    assert not re.search(r"\b_log(?:Action|ger)\b", _code(_notice_body())), \
        f"{NOTICE}() must not log a second line for the same held send"


def test_criterion4_no_blocking_dialog_in_the_send_path():
    for path in (THIS_ADDIN, LOOPBACK_HANDLER):
        assert "MessageBox" not in _code(path.read_text(encoding="utf-8")), \
            f"{path.name} must not use MessageBox"


# --- Criterion 6: connected success path unchanged ----------------------------

def test_criterion6_arcum_only_branch_keeps_log_cancel_process_order():
    full, _ = _connected_branches()
    code = _code(full)
    log = LOG_CALL.search(code)
    cancel = CANCEL_TRUE.search(code)
    process = re.search(r"\bProcessInterceptedEmail\s*\(", code)
    assert log and cancel and process, \
        "the ArcumAI-only branch must still log, cancel and process the mail"
    assert log.start() < cancel.start() < process.start()
    assert not re.search(r"\bRemoveArcumRecipient\s*\(", code)


def test_criterion6_parallel_branch_keeps_remove_then_process_and_does_not_cancel():
    _, parallel = _connected_branches()
    code = _code(parallel)
    log = LOG_CALL.search(code)
    remove = re.search(r"\bRemoveArcumRecipient\s*\(", code)
    process = re.search(r"\bProcessInterceptedEmail\s*\(", code)
    assert log and remove and process, \
        "the parallel branch must still log, remove the ArcumAI address and process the mail"
    assert log.start() < remove.start() < process.start()
    assert not re.search(r"\bCancel\b", code), \
        "the parallel branch must not touch Cancel: the mail goes to the real recipients"


def test_d2_remove_arcum_recipient_has_no_catch():
    # Clearing the flag after a normal return is safe only if a failure inside
    # RemoveArcumRecipient always propagates (D2 option a: method unchanged).
    remove = _code(_method_body(LOOPBACK_HANDLER, r"\bvoid\s+RemoveArcumRecipient\s*\("))
    assert not re.search(r"\bcatch\b", remove), (
        "RemoveArcumRecipient must not swallow errors: a normal return is what "
        "clears the hold flag")


# --- Criterion 8 and D5: documentation --------------------------------------------

def test_criterion8_changelog_unreleased_security_mentions_issue_68():
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1]
    unreleased = re.split(r"\n## \[", unreleased, maxsplit=1)[0]
    security = re.search(r"### Security\s*\n(.*?)(?=\n### |\n---|\Z)", unreleased, re.DOTALL)
    assert security, "CHANGELOG [Unreleased] has no Security subsection"
    assert "#68" in security.group(1), "CHANGELOG [Unreleased] Security must reference #68"


def test_d5_addin_readme_hold_section_covers_errors_while_connected():
    text = CONFIG_README.read_text(encoding="utf-8")
    section = _markdown_section(
        text, r"(?=.*(?:disconnect|not connected|offline))(?=.*(?:send|sending|sent|mail))")
    assert section is not None, "config.README.md lost the #60 section on held sends"
    sentences = re.split(r"(?<=[.!?])\s+", section)
    assert any(re.search(r"\berror\b", s) and "recipient" in s.lower() and "connected" in s.lower()
               for s in sentences), (
        "the #60 section of config.README.md must add a sentence saying that the mail "
        "also stays open when an error occurs while connected, and that the user should "
        "check the recipients before sending again (#68, D5)")


def test_d5_adr_0001_hold_bullet_extended_to_send_handler_errors():
    text = ADR_0001.read_text(encoding="utf-8")
    decision = _markdown_section(text, r"^Decision\b")
    assert decision is not None
    bullets = [line for line in decision.splitlines() if "#60" in line]
    assert bullets, "ADR 0001 Decision lost the #60 bullet"
    assert any("#68" in b and re.search(r"\berror\b", b, re.IGNORECASE) for b in bullets), (
        "the #60 bullet in ADR 0001 Decision must gain one sentence on errors in the "
        "send handler, referencing #68 (D5)")
