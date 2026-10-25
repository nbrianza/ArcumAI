# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #37: brute-force protection on the web login,
and for issue #44: eviction of stale failure records (tests prefixed ``test_evict_``).

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
    """Clear the in-memory login failure stores before and after each test.

    Also resets the periodic sweep timestamp ``_login_last_sweep`` (issue #44),
    because the fake clock restarts at the same instant in every test. The
    attribute is only reset when it exists, so the fixture also works before
    the #44 implementation is in place.
    """
    def _clear():
        for name in ("_login_failures_ip", "_login_failures_user"):
            store = getattr(auth_module, name, None)
            if isinstance(store, dict):
                store.clear()
        if hasattr(auth_module, "_login_last_sweep"):
            auth_module._login_last_sweep = 0.0
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


# =============================================================================
# Issue #44: stale failure records are evicted
# =============================================================================
#
# Owner decision on #44: a stale failure record may remain stored for at most
# 2 x LOGIN_WINDOW, measured at the next login attempt (the approved design uses
# an attempt-driven periodic sweep in check_login). The tests advance the fake
# clock by 2 x LOGIN_WINDOW + 1 so they do not depend on where sweeps fall.
# All usernames and IPs below are synthetic.

def _ip44(n: int) -> str:
    return f"10.0.44.{n}"


def _traffic_ip(k: int) -> str:
    """Distinct synthetic IP for background traffic, never blocked by itself."""
    return f"10.44.{k // 250}.{k % 250 + 1}"


def _ip_store() -> dict:
    return auth_module._login_failures_ip


def _user_store() -> dict:
    return auth_module._login_failures_user


def _retention_bound() -> int:
    return 2 * _window()


def _oldest_age(now: float) -> float:
    ages = [now - t for store in (_ip_store(), _user_store()) for ts in store.values() for t in ts]
    return max(ages, default=0.0)


@pytest.mark.parametrize("first_user", ["evict-user-a", ALICE], ids=["unknown_user", "known_user"])
@pytest.mark.parametrize(
    "second_user, second_password, expected",
    [("evict-user-b", WRONG_PASSWORD, "failed"), (BOB, BOB_PASSWORD, "ok")],
    ids=["next_attempt_failed", "next_attempt_ok"],
)
def test_evict_criterion1_stale_username_and_ip_removed_after_unrelated_attempt(
    clock, first_user, second_user, second_password, expected
):
    first_ip, second_ip = _ip44(1), _ip44(2)
    assert _check(first_user, WRONG_PASSWORD, first_ip) == "failed"
    assert first_user in _user_store() and first_ip in _ip_store(), "precondition: failure recorded"

    clock.advance(_retention_bound() + 1)
    assert _check(second_user, second_password, second_ip) == expected

    leftovers = [k for store in (_ip_store(), _user_store()) for k in store if k in (first_user, first_ip)]
    assert leftovers == [], f"stale records not evicted: {leftovers}"


def test_evict_criterion2_hundred_stale_usernames_leave_at_most_the_new_attempt(clock):
    n = _max_attempts()
    for i in range(100):
        # At most LOGIN_MAX_ATTEMPTS failures per IP, so no attempt is blocked.
        assert _check(f"evict-many-{i}", WRONG_PASSWORD, _ip44(10 + i // n)) == "failed"
    assert len(_user_store()) == 100, "precondition: 100 username records stored"

    clock.advance(_retention_bound() + 1)
    new_user = "evict-new-user"
    assert _check(new_user, WRONG_PASSWORD, _ip44(250)) == "failed"

    assert len(_user_store()) <= 1, f"{len(_user_store())} username entries remain"
    assert set(_user_store()) <= {new_user}
    assert set(_ip_store()) <= {_ip44(250)}


def test_evict_criterion3_one_ip_six_usernames_per_window_for_fifty_windows_stays_bounded(clock):
    n = _max_attempts()  # 6 by default, as in the scenario of the issue
    ip = _ip44(200)
    windows = 50
    for w in range(windows):
        if w:
            clock.advance(_window())
        for i in range(n):
            assert _check(f"evict-w{w}-u{i}", WRONG_PASSWORD, ip) == "failed", f"window {w}, user {i}"

    # Bound 2 x LOGIN_WINDOW: at most the usernames of the current and previous window.
    assert len(_user_store()) <= 2 * n, (
        f"{len(_user_store())} username entries stored after {windows} windows, expected at most {2 * n}"
    )
    assert len(_ip_store()) <= 1


def test_evict_criterion4_fresh_records_survive_forced_sweep_and_still_block(clock, monkeypatch):
    n = _max_attempts()
    blocked_ip = _ip44(210)
    target_user = "evict-target"
    _fail_from_ip_with_distinct_unknown_users(blocked_ip, n, prefix="evict-fresh-")
    for i in range(n):
        assert _check(target_user, WRONG_PASSWORD, _ip44(220 + i)) == "failed"

    clock.advance(_window() - 1)  # still inside LOGIN_WINDOW
    # Force the periodic sweep to run on the next attempt (design: _login_last_sweep).
    monkeypatch.setattr(auth_module, "_login_last_sweep", clock.now - 10 * _window(), raising=False)
    assert _check("evict-other", WRONG_PASSWORD, _ip44(240)) == "failed"

    assert len(_ip_store().get(blocked_ip, [])) == n, "fresh IP records must be kept by the sweep"
    assert len(_user_store().get(target_user, [])) == n, "fresh username records must be kept by the sweep"
    assert _check(ALICE, ALICE_PASSWORD, blocked_ip) == "blocked"
    assert _check(target_user, WRONG_PASSWORD, _ip44(241)) == "blocked"


def test_evict_criterion4_block_holds_for_whole_window_under_continuous_traffic(clock):
    # Background traffic before and during the target's window, so that any
    # periodic sweep falls while the target's records are still fresh.
    n = _max_attempts()
    w = _window()
    step = max(1, w // 60)
    k = 0

    def traffic():
        nonlocal k
        assert _check(f"evict-bg-{k}", WRONG_PASSWORD, _traffic_ip(k)) == "failed"
        k += 1

    start = clock.now
    while clock.now < start + w / 2:
        traffic()
        clock.advance(step)

    blocked_ip = _ip44(230)
    _fail_from_ip_with_distinct_unknown_users(blocked_ip, n, prefix="evict-cont-")
    t_fail = clock.now
    while clock.now + step < t_fail + w:
        clock.advance(step)
        traffic()
        assert _check("evict-probe", WRONG_PASSWORD, blocked_ip) == "blocked", (
            f"block lifted {clock.now - t_fail:.0f}s after the failures, before LOGIN_WINDOW"
        )
        assert len(_ip_store().get(blocked_ip, [])) == n


def test_evict_criterion5_no_record_older_than_two_windows_under_continuous_traffic(clock):
    w = _window()
    step = max(1, w // 60)
    assert _check("evict-old", WRONG_PASSWORD, _ip44(240)) == "failed"
    start = clock.now
    k = 0
    while clock.now < start + 3 * w:
        clock.advance(step)
        assert _check(f"evict-tr-{k}", WRONG_PASSWORD, _traffic_ip(k)) == "failed"
        k += 1
        age = _oldest_age(clock.now)
        assert age <= _retention_bound(), (
            f"a record {age:.0f}s old is still stored, bound is {_retention_bound()}s"
        )
    assert "evict-old" not in _user_store()
    assert _ip44(240) not in _ip_store()


def test_evict_criterion5_eviction_still_happens_after_clock_moves_backwards(clock):
    # Edge case beyond the criteria: the wall clock is set back after a sweep.
    t0 = clock.now
    clock.advance(10 * _window())
    assert _check("evict-future", WRONG_PASSWORD, _ip44(241)) == "failed"

    clock.now = t0
    assert _check("evict-past", WRONG_PASSWORD, _ip44(242)) == "failed"
    clock.advance(_retention_bound() + 1)
    assert _check("evict-after", WRONG_PASSWORD, _ip44(243)) == "failed"

    leftovers = [k for store in (_ip_store(), _user_store()) for k in store if k in ("evict-past", _ip44(242))]
    assert leftovers == [], f"stale records not evicted: {leftovers}"


def test_evict_cleanup_logs_nothing_about_evicted_records(clock, log_records):
    old_user, old_ip = "evict-secret-name", _ip44(244)
    assert _check(old_user, WRONG_PASSWORD, old_ip) == "failed"
    before = len(log_records)

    clock.advance(_retention_bound() + 1)
    assert _check("evict-later", WRONG_PASSWORD, _ip44(245)) == "failed"

    new = log_records[before:]
    assert len(_warnings(new)) == 1, "only the WARNING for the new failed attempt"
    for rec in new:
        text = _all_text(rec)
        assert old_user not in text and old_ip not in text, "eviction must not log usernames or IPs"


# =============================================================================
# Issue #59: throttle timing is independent of wall-clock changes
# =============================================================================
#
# Approved design (D1): check_login measures throttle time with time.monotonic()
# instead of time.time(). The tests below replace the wall clock (time.time) and
# the monotonic clock (time.monotonic) with two SEPARATE fakes: the wall clock
# jumps backwards or forwards, the monotonic clock only moves forward with
# elapsed time. The two fakes start far apart (wall in 2030, monotonic at a few
# thousand seconds, like system uptime), so a call site that still reads
# time.time() mixes time sources and either never expires a block or loses it,
# which fails these tests. All usernames and IPs are synthetic (10.0.59.0/24,
# 10.59.0.0/16).

class _SplitClock:
    """Independent fakes for the wall clock and the monotonic clock."""

    def __init__(self, wall_start: float = 1_900_000_000.0, mono_start: float = 5_000.0):
        self.wall = wall_start
        self.mono = mono_start

    def time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def elapse(self, seconds: float) -> None:
        """Real time passes: both clocks advance."""
        self.wall += seconds
        self.mono += seconds

    def jump_wall(self, seconds: float) -> None:
        """The wall clock is set forwards (positive) or backwards (negative)."""
        self.wall += seconds


@pytest.fixture
def split_clock(monkeypatch):
    fake = _SplitClock()
    monkeypatch.setattr(auth_module.time, "time", fake.time)
    monkeypatch.setattr(auth_module.time, "monotonic", fake.monotonic)
    return fake


def _ip59(n: int) -> str:
    return f"10.0.59.{n}"


def _traffic_ip59(k: int) -> str:
    """Distinct synthetic IP for background traffic, never blocked by itself."""
    return f"10.59.{k // 250}.{k % 250 + 1}"


def _block(kind: str) -> tuple[str, str]:
    """Blocks a synthetic IP (kind "ip") or the username ALICE (kind "user").

    Returns ``(username, ip)`` for a later correct login that the block must
    refuse: for "ip" ALICE from the blocked IP, for "user" ALICE from a fresh IP.
    """
    n = _max_attempts()
    if kind == "ip":
        ip = _ip59(1)
        _fail_from_ip_with_distinct_unknown_users(ip, n, prefix="synthetic-ghost-")
        return ALICE, ip
    for i in range(n):
        assert _check(ALICE, WRONG_PASSWORD, _ip59(10 + i)) == "failed"
    return ALICE, _ip59(10 + n)


_BACKWARD_JUMPS = [pytest.param(-1, id="back_1s"), pytest.param("-10w", id="back_10_windows")]
_FORWARD_JUMPS = [pytest.param(1, id="fwd_1s"), pytest.param("10w", id="fwd_10_windows")]


def _jump_seconds(jump) -> float:
    if jump == "-10w":
        return -10 * _window()
    if jump == "10w":
        return 10 * _window()
    return float(jump)


# --- Criterion 1: records stamped before a backward jump are still evicted ---

def test_monotonic_criterion1_records_evicted_after_backward_wall_jump(split_clock):
    first_user, first_ip = "synthetic-ahead", _ip59(99)
    w = _window()
    t = split_clock.wall
    split_clock.jump_wall(10 * w)                      # wall at T + 10 x LOGIN_WINDOW
    assert _check(first_user, WRONG_PASSWORD, first_ip) == "failed"
    assert first_user in _user_store() and first_ip in _ip_store(), "precondition: failure recorded"

    split_clock.wall = t                               # wall set back to T
    for k in range(2 * w + 2):                         # one attempt per elapsed second
        split_clock.elapse(1)
        assert _check(f"synthetic-other-{k}", WRONG_PASSWORD, _traffic_ip59(k)) == "failed"

    leftovers = [k for store in (_ip_store(), _user_store()) for k in store if k in (first_user, first_ip)]
    assert leftovers == [], f"records stamped before the backward clock change are still stored: {leftovers}"


# --- Criteria 2 and 3: backward jump neither extends nor shortens a block ----

@pytest.mark.parametrize("kind", ["ip", "user"])
@pytest.mark.parametrize("jump", _BACKWARD_JUMPS)
def test_monotonic_criterion2_block_ends_one_window_after_last_failure_despite_backward_jump(
    split_clock, kind, jump
):
    user, ip = _block(kind)
    split_clock.jump_wall(_jump_seconds(jump))

    split_clock.elapse(_window() + 1)
    assert _check(user, ALICE_PASSWORD, ip) == "ok", (
        "block must end LOGIN_WINDOW of elapsed time after the last failure, whatever the wall clock says"
    )


@pytest.mark.parametrize("kind", ["ip", "user"])
@pytest.mark.parametrize("jump", _BACKWARD_JUMPS)
def test_monotonic_criterion3_block_not_lifted_early_by_backward_jump(split_clock, kind, jump):
    user, ip = _block(kind)
    split_clock.jump_wall(_jump_seconds(jump))

    assert _check(user, ALICE_PASSWORD, ip) == "blocked", "block lifted right after the clock change"
    split_clock.elapse(_window() - 1)
    assert _check(user, ALICE_PASSWORD, ip) == "blocked", "block lifted before LOGIN_WINDOW of elapsed time"


# --- Criterion 5: forward jump neither extends nor shortens a block ----------

@pytest.mark.parametrize("kind", ["ip", "user"])
@pytest.mark.parametrize("jump", _FORWARD_JUMPS)
def test_monotonic_criterion5_forward_jump_block_holds_for_window_then_ends(split_clock, kind, jump):
    user, ip = _block(kind)
    split_clock.jump_wall(_jump_seconds(jump))

    assert _check(user, ALICE_PASSWORD, ip) == "blocked", "block lifted right after the clock change"
    split_clock.elapse(_window() - 1)
    assert _check(user, ALICE_PASSWORD, ip) == "blocked", "block lifted before LOGIN_WINDOW of elapsed time"
    split_clock.elapse(2)
    assert _check(user, ALICE_PASSWORD, ip) == "ok", "block lasted longer than LOGIN_WINDOW of elapsed time"


# --- Design guard: every call site in check_login uses the monotonic clock ---

def test_monotonic_guard_wall_clock_alone_never_changes_blocking(split_clock):
    # Only the wall clock moves, by large amounts in both directions, between
    # every failure and every check; the monotonic clock stays still. Blocking
    # must be exactly as if the wall clock had never moved, and the block must
    # end only once LOGIN_WINDOW of monotonic time has elapsed.
    n = _max_attempts()
    w = _window()
    ip = _ip59(120)
    for i in range(n):
        split_clock.jump_wall(10 * w if i % 2 == 0 else -25 * w)
        assert _check(f"synthetic-guard-{i}", WRONG_PASSWORD, ip) == "failed", f"failure {i + 1}"
    for jump in (100 * w, -300 * w, 1, -1):
        split_clock.jump_wall(jump)
        assert _check(BOB, BOB_PASSWORD, ip) == "blocked", f"block changed by a wall clock jump of {jump}s"

    split_clock.mono += w - 1
    split_clock.jump_wall(-50 * w)
    assert _check(BOB, BOB_PASSWORD, ip) == "blocked"
    split_clock.mono += 2
    split_clock.jump_wall(70 * w)
    assert _check(BOB, BOB_PASSWORD, ip) == "ok", "block must end after LOGIN_WINDOW of monotonic time"


def test_monotonic_guard_retention_bound_holds_when_only_monotonic_time_passes(split_clock):
    # The wall clock is frozen (or set back) while elapsed time passes: records
    # must still be evicted within 2 x LOGIN_WINDOW of elapsed time.
    old_user, old_ip = "synthetic-frozen", _ip59(130)
    assert _check(old_user, WRONG_PASSWORD, old_ip) == "failed"
    split_clock.mono += _retention_bound() + 1
    split_clock.jump_wall(-1)
    assert _check("synthetic-later", WRONG_PASSWORD, _ip59(131)) == "failed"

    leftovers = [k for store in (_ip_store(), _user_store()) for k in store if k in (old_user, old_ip)]
    assert leftovers == [], f"stale records not evicted on the monotonic clock: {leftovers}"


# --- Criterion 7: nothing from the throttle records is logged ----------------

def test_monotonic_criterion7_clock_change_logs_no_username_ip_or_password(split_clock, log_records):
    old_user, old_ip = "synthetic-ahead-log", _ip59(140)
    split_clock.jump_wall(10 * _window())
    assert _check(old_user, WRONG_PASSWORD, old_ip) == "failed"
    before = len(log_records)

    split_clock.jump_wall(-10 * _window())
    split_clock.elapse(_retention_bound() + 1)
    assert _check("synthetic-later-log", WRONG_PASSWORD, _ip59(141)) == "failed"

    new = log_records[before:]
    assert len(_warnings(new)) == 1, "only the WARNING for the new failed attempt"
    for rec in new:
        text = _all_text(rec)
        assert old_user not in text and old_ip not in text, "eviction must not log usernames or IPs"
    for rec in log_records:
        text = _all_text(rec)
        assert WRONG_PASSWORD not in text and ALICE_PASSWORD not in text, "password leaked in log"
