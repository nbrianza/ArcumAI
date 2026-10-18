# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #37: brute-force protection on the web login.

The tests exercise ``src.auth.check_login(users_db, username, password, client_ip)``
as specified in the approved design, which returns ``"ok"``, ``"failed"`` or
``"blocked"``. Owner decisions on the issue: one shared pair of settings
``LOGIN_MAX_ATTEMPTS`` (default 6) and ``LOGIN_WINDOW`` (default 60 s), failures
are counted per client IP and per submitted username, the attempt after
``LOGIN_MAX_ATTEMPTS`` failures within the window is blocked, and a successful
login resets only the username counter.

All users, passwords and IPs are synthetic. ``users.json`` is never read.
Time is controlled by a fake clock that replaces ``time.time`` (and
``time.monotonic``) for the duration of a test, so no test sleeps.
Log records are captured by replacing ``src.auth.slog`` with a recording logger
and by swapping the handlers of the ``ArcumServer`` logger; ``caplog`` cannot
be used because ``server_log`` has ``propagate = False``.
"""
import logging

import pytest

import src.auth as auth_module
from src.auth import hash_password

# --- Synthetic test data -----------------------------------------------------

ALICE = "alice"
ALICE_PASSWORD = "AlicePass1234"
BOB = "bob"
BOB_PASSWORD = "BobPass5678x"
WRONG_PASSWORD = "Wr0ngGuess77"
UNKNOWN_USER = "Secret99x"  # a "username" that looks like a typed password

# Build bcrypt hashes once per module (12 rounds is slow).
_ALICE_HASH = hash_password(ALICE_PASSWORD)
_BOB_HASH = hash_password(BOB_PASSWORD)


def _users_db() -> dict:
    return {
        ALICE: {"pw_hash": _ALICE_HASH, "role": "LEGAL", "name": "Alice Example", "outlook_id": ""},
        BOB: {"pw_hash": _BOB_HASH, "role": "DEFAULT", "name": "Bob Example", "outlook_id": ""},
    }


def _ip(n: int) -> str:
    return f"10.0.37.{n}"


def _max_attempts() -> int:
    from src.config import LOGIN_MAX_ATTEMPTS
    return LOGIN_MAX_ATTEMPTS


def _window() -> int:
    from src.config import LOGIN_WINDOW
    return LOGIN_WINDOW


def _check(username: str, password: str, ip: str) -> str:
    from src.auth import check_login
    return check_login(_users_db(), username, password, ip)


# --- Fixtures ----------------------------------------------------------------

class _FakeClock:
    def __init__(self, start: float = 1_900_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_login_state():
    """Clear the in-memory login failure stores before and after each test."""
    def _clear():
        for name in ("_login_failures_ip", "_login_failures_user"):
            store = getattr(auth_module, name, None)
            if isinstance(store, dict):
                store.clear()
    _clear()
    yield
    _clear()


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(auth_module.time, "time", fake)
    monkeypatch.setattr(auth_module.time, "monotonic", fake)
    return fake


@pytest.fixture
def password_spy(monkeypatch):
    """Counts every password verification (verify_password and raw bcrypt.checkpw)."""
    calls = {"verify_password": 0, "checkpw": 0}
    real_verify = auth_module.verify_password
    real_checkpw = auth_module.bcrypt.checkpw

    def spy_verify(*args, **kwargs):
        calls["verify_password"] += 1
        return real_verify(*args, **kwargs)

    def spy_checkpw(*args, **kwargs):
        calls["checkpw"] += 1
        return real_checkpw(*args, **kwargs)

    monkeypatch.setattr(auth_module, "verify_password", spy_verify)
    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy_checkpw)
    return calls


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def log_records(monkeypatch):
    """Captures records sent to src.auth.slog or the ArcumServer logger.

    Nothing is written to logs/server.log or stdout while the fixture is active.
    """
    handler = _ListHandler()

    recorder = logging.getLogger("tests.login_throttle.recorder")
    monkeypatch.setattr(recorder, "handlers", [handler])
    monkeypatch.setattr(recorder, "propagate", False)
    recorder.setLevel(logging.DEBUG)
    monkeypatch.setattr(auth_module, "slog", recorder)

    server_logger = logging.getLogger("ArcumServer")
    old_level = server_logger.level
    monkeypatch.setattr(server_logger, "handlers", [handler])
    server_logger.setLevel(logging.DEBUG)
    yield handler.records
    server_logger.setLevel(old_level)


def _warnings(records) -> list[logging.LogRecord]:
    return [r for r in records if r.levelno >= logging.WARNING]


def _all_text(record: logging.LogRecord) -> str:
    return f"{record.getMessage()} {record.msg!r} {record.args!r}"


def _fail_from_ip_with_distinct_unknown_users(ip: str, count: int, prefix: str = "ghost") -> None:
    """Records `count` failures from one IP without touching any single username twice."""
    for i in range(count):
        assert _check(f"{prefix}{i}", WRONG_PASSWORD, ip) == "failed"


# --- Defaults (owner decision Q3) ---------------------------------------------

def test_login_throttle_constants_exist_and_are_positive_ints():
    from src.config import LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW
    assert isinstance(LOGIN_MAX_ATTEMPTS, int) and LOGIN_MAX_ATTEMPTS > 0
    assert isinstance(LOGIN_WINDOW, int) and LOGIN_WINDOW > 0


# --- Criterion 1: per-IP block, password not checked -------------------------

def test_criterion1_ip_blocked_after_max_failures_even_with_correct_password(clock, password_spy):
    ip = _ip(1)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())
    password_spy["verify_password"] = 0
    password_spy["checkpw"] = 0

    assert _check(ALICE, ALICE_PASSWORD, ip) == "blocked"
    assert password_spy == {"verify_password": 0, "checkpw": 0}, "password must not be checked when blocked"


def test_criterion1_same_user_wrong_passwords_then_correct_is_blocked(clock, password_spy):
    ip = _ip(2)
    for _ in range(_max_attempts()):
        assert _check(ALICE, WRONG_PASSWORD, ip) == "failed"
    password_spy["verify_password"] = 0
    password_spy["checkpw"] = 0

    assert _check(ALICE, ALICE_PASSWORD, ip) == "blocked"
    assert password_spy == {"verify_password": 0, "checkpw": 0}


def test_criterion1_attempt_at_limit_is_still_failed_not_blocked(clock):
    # Owner decision: with LOGIN_MAX_ATTEMPTS=N, attempts 1..N are evaluated, N+1 is blocked.
    ip = _ip(3)
    results = [_check(f"ghost{i}", WRONG_PASSWORD, ip) for i in range(_max_attempts())]
    assert results == ["failed"] * _max_attempts()
    assert _check("ghost-next", WRONG_PASSWORD, ip) == "blocked"


def test_criterion1_blocked_result_does_not_reveal_username_existence(clock):
    ip = _ip(4)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())

    known = _check(ALICE, ALICE_PASSWORD, ip)
    unknown = _check(UNKNOWN_USER, WRONG_PASSWORD, ip)
    assert known == unknown == "blocked"


def test_criterion1_other_ip_is_not_affected_by_ip_block(clock):
    _fail_from_ip_with_distinct_unknown_users(_ip(5), _max_attempts())
    assert _check(BOB, BOB_PASSWORD, _ip(6)) == "ok"


# --- Criterion 2: per-username block across IPs ------------------------------

def test_criterion2_username_blocked_after_max_failures_from_different_ips(clock, password_spy):
    n = _max_attempts()
    for i in range(n):
        assert _check(ALICE, WRONG_PASSWORD, _ip(20 + i)) == "failed"
    password_spy["verify_password"] = 0
    password_spy["checkpw"] = 0

    assert _check(ALICE, ALICE_PASSWORD, _ip(20 + n)) == "blocked"
    assert password_spy == {"verify_password": 0, "checkpw": 0}


def test_criterion2_unknown_username_is_also_counted_per_username(clock):
    # Design: every submitted username is counted, so "blocked" is not an existence oracle.
    n = _max_attempts()
    for i in range(n):
        assert _check(UNKNOWN_USER, WRONG_PASSWORD, _ip(40 + i)) == "failed"
    assert _check(UNKNOWN_USER, WRONG_PASSWORD, _ip(40 + n)) == "blocked"


def test_criterion2_username_block_does_not_affect_other_users(clock):
    n = _max_attempts()
    for i in range(n):
        _check(ALICE, WRONG_PASSWORD, _ip(60 + i))
    assert _check(BOB, BOB_PASSWORD, _ip(60 + n)) == "ok"


# --- Criterion 3: block expires after the window ------------------------------

def test_criterion3_ip_block_expires_after_window(clock):
    ip = _ip(80)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())
    assert _check(ALICE, ALICE_PASSWORD, ip) == "blocked"

    clock.advance(_window() + 1)
    assert _check(ALICE, ALICE_PASSWORD, ip) == "ok"


def test_criterion3_username_block_expires_after_window(clock):
    n = _max_attempts()
    for i in range(n):
        _check(ALICE, WRONG_PASSWORD, _ip(90 + i))
    assert _check(ALICE, ALICE_PASSWORD, _ip(90 + n)) == "blocked"

    clock.advance(_window() + 1)
    assert _check(ALICE, ALICE_PASSWORD, _ip(90 + n)) == "ok"


def test_criterion3_block_still_active_just_before_window_ends(clock):
    ip = _ip(110)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())

    clock.advance(_window() - 1)
    assert _check(ALICE, ALICE_PASSWORD, ip) == "blocked"


def test_criterion3_blocked_attempts_do_not_extend_the_block(clock):
    # Design: blocked attempts are not recorded, so the block ends LOGIN_WINDOW
    # seconds after the last real failure, however often the client retries.
    ip = _ip(111)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())
    clock.advance(_window() - 1)
    assert _check(ALICE, WRONG_PASSWORD, ip) == "blocked"

    clock.advance(2)
    assert _check(ALICE, ALICE_PASSWORD, ip) == "ok"


# --- Criterion 4: no delay for a clean user ----------------------------------

def test_criterion4_clean_user_succeeds_first_attempt_without_delay(clock, monkeypatch, password_spy):
    sleeps = []
    monkeypatch.setattr(auth_module.time, "sleep", lambda s: sleeps.append(s))

    assert _check(ALICE, ALICE_PASSWORD, _ip(120)) == "ok"
    assert sleeps == [], "a successful login must not sleep"
    assert password_spy["verify_password"] + password_spy["checkpw"] >= 1, "password must be checked"


def test_criterion4_wrong_password_for_clean_user_is_failed(clock):
    assert _check(ALICE, WRONG_PASSWORD, _ip(121)) == "failed"


# --- Criterion 5: success resets the username counter only --------------------

def test_criterion5_success_resets_username_counter(clock):
    n = _max_attempts()
    # n - 1 failures from different IPs: alice is one failure away from the block.
    for i in range(n - 1):
        assert _check(ALICE, WRONG_PASSWORD, _ip(130 + i)) == "failed"
    assert _check(ALICE, ALICE_PASSWORD, _ip(150)) == "ok"

    # After the reset alice must again tolerate n failures before being blocked.
    for i in range(n):
        assert _check(ALICE, WRONG_PASSWORD, _ip(160 + i)) == "failed", f"failure {i + 1} after reset"
    assert _check(ALICE, ALICE_PASSWORD, _ip(160 + n)) == "blocked"


def test_criterion5_success_does_not_reset_ip_counter(clock):
    # Owner decision Q4: only the username counter is reset on success.
    n = _max_attempts()
    ip = _ip(200)
    _fail_from_ip_with_distinct_unknown_users(ip, n - 1)
    assert _check(ALICE, ALICE_PASSWORD, ip) == "ok"

    assert _check("ghost-last", WRONG_PASSWORD, ip) == "failed"
    assert _check(BOB, BOB_PASSWORD, ip) == "blocked"


# --- Criterion 6: one WARNING per failed / blocked attempt, no secrets --------

def test_criterion6_failed_known_user_logs_one_warning_with_ip_user_and_outcome(clock, log_records):
    ip = _ip(210)
    assert _check(ALICE, WRONG_PASSWORD, ip) == "failed"

    warnings = _warnings(log_records)
    assert len(warnings) == 1
    rec = warnings[0]
    msg = rec.getMessage()
    assert rec.levelno == logging.WARNING
    assert ip in msg
    assert "failed" in msg.lower()
    assert "blocked" not in msg.lower()
    assert ALICE in msg


def test_criterion6_blocked_attempt_logs_one_warning_with_ip_and_outcome(clock, log_records):
    ip = _ip(211)
    _fail_from_ip_with_distinct_unknown_users(ip, _max_attempts())
    before = len(_warnings(log_records))
    assert before == _max_attempts(), "one WARNING per failed attempt"

    assert _check(ALICE, ALICE_PASSWORD, ip) == "blocked"
    new = _warnings(log_records)[before:]
    assert len(new) == 1
    assert new[0].levelno == logging.WARNING
    assert ip in new[0].getMessage()
    assert "blocked" in new[0].getMessage().lower()


def test_criterion6_successful_login_logs_no_warning(clock, log_records):
    assert _check(ALICE, ALICE_PASSWORD, _ip(212)) == "ok"
    assert _warnings(log_records) == []


def test_criterion6_password_never_logged_at_any_level(clock, log_records):
    ip = _ip(213)
    n = _max_attempts()
    for i in range(n):
        _check(ALICE, f"{WRONG_PASSWORD}{i}", ip)
    _check(ALICE, ALICE_PASSWORD, ip)           # blocked, correct password submitted
    _check(BOB, BOB_PASSWORD, _ip(214))         # ok

    assert log_records, "expected log records to inspect"
    secrets = [f"{WRONG_PASSWORD}{i}" for i in range(n)] + [WRONG_PASSWORD, ALICE_PASSWORD, BOB_PASSWORD]
    for rec in log_records:
        text = _all_text(rec)
        for secret in secrets:
            assert secret not in text, f"password leaked in log at level {rec.levelname}"


def test_criterion6_unknown_username_not_logged_for_failed_and_blocked(clock, log_records):
    n = _max_attempts()
    for i in range(n):
        assert _check(UNKNOWN_USER, WRONG_PASSWORD, _ip(220 + i)) == "failed"
    assert _check(UNKNOWN_USER, WRONG_PASSWORD, _ip(220 + n)) == "blocked"

    warnings = _warnings(log_records)
    assert len(warnings) == n + 1
    for i, rec in enumerate(warnings):
        assert _ip(220 + i) in rec.getMessage()
    for rec in log_records:
        assert UNKNOWN_USER not in _all_text(rec), "unknown submitted username must not be logged"
