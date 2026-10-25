# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #51: the Outlook bridge (WebSocket) authentication
throttle in ``src.auth`` must not keep an entry for every connecting IP.

Design (architect comment on #51, D1-D4 as recommended): ``ws_auth_is_rate_limited``
stores no empty list, and runs a sweep of ``_ws_auth_failures`` at most once per
``WS_AUTH_WINDOW`` (module attribute ``_ws_auth_last_sweep``), also when the clock
moved backwards. A failure record therefore stays at most 2 x ``WS_AUTH_WINDOW``,
measured at the next bridge connection from any IP.

``outlook_endpoint`` in ``main_nice.py`` calls ``ws_auth_is_rate_limited(ip)`` for
every connection before authenticating, and ``ws_auth_record_failure(ip)`` on a
wrong API key or unknown Outlook ID. There is no WebSocket test harness, so a
"connection" is modelled by a call to ``ws_auth_is_rate_limited`` and a failed
authentication by a following ``ws_auth_record_failure``.

All IPs are synthetic (``10.0.51.0/24`` and ``10.51.0.0/16``) and distinct from the
IPs used in ``tests/test_auth.py``. Time is controlled by a fake clock replacing
``time.time`` (and ``time.monotonic``), starting in 2030, so the autouse fixture
clears ``_ws_auth_failures`` and resets ``_ws_auth_last_sweep`` (when it exists)
before and after every test, so that no future-stamped state leaks into the
real-clock tests in ``tests/test_auth.py``.
"""
import logging
import re
from pathlib import Path

import pytest

import src.auth as auth_module

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --- Helpers -----------------------------------------------------------------

def _ip(n: int) -> str:
    """Synthetic IPs for named actors."""
    return f"10.0.51.{n}"


def _bulk_ip(i: int) -> str:
    """Synthetic IPs for bulk or background traffic (up to 65 536 distinct)."""
    return f"10.51.{i // 256}.{i % 256}"


def _window() -> int:
    from src.config import WS_AUTH_WINDOW
    return WS_AUTH_WINDOW


def _max_attempts() -> int:
    from src.config import WS_AUTH_MAX_ATTEMPTS
    return WS_AUTH_MAX_ATTEMPTS


def _retention_bound() -> int:
    """Criterion 5: a stale entry may remain at most 2 x WS_AUTH_WINDOW."""
    return 2 * _window()


def _store() -> dict:
    return auth_module._ws_auth_failures


def _connect(ip: str) -> bool:
    """A bridge connection: the check that outlook_endpoint runs before authenticating."""
    return auth_module.ws_auth_is_rate_limited(ip)


def _failed_connection(ip: str) -> None:
    """A connection that is not blocked and then fails authentication."""
    assert _connect(ip) is False, f"precondition: {ip} must not be blocked"
    auth_module.ws_auth_record_failure(ip)


def _oldest_age(now: float) -> float:
    stamps = [t for lst in _store().values() for t in lst]
    return max((now - t for t in stamps), default=0.0)


# --- Fixtures ----------------------------------------------------------------

class _FakeClock:
    def __init__(self, start: float = 1_900_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_ws_auth_state():
    """Clear the WebSocket throttle store and the sweep timestamp around each test.

    ``_ws_auth_last_sweep`` is only reset when it exists, so the fixture also works
    before the #51 implementation is in place (tests then fail on their assertions).
    """
    def _clear():
        store = getattr(auth_module, "_ws_auth_failures", None)
        if isinstance(store, dict):
            store.clear()
        if hasattr(auth_module, "_ws_auth_last_sweep"):
            auth_module._ws_auth_last_sweep = 0.0
    _clear()
    yield
    _clear()


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(auth_module.time, "time", fake)
    monkeypatch.setattr(auth_module.time, "monotonic", fake)
    return fake


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def log_records(monkeypatch):
    """Captures records sent to src.auth.slog or the ArcumServer logger.

    ``caplog`` cannot be used because ``server_log`` has ``propagate = False``.
    Nothing is written to logs/ or stdout while the fixture is active.
    """
    handler = _ListHandler()

    recorder = logging.getLogger("tests.ws_auth_throttle.recorder")
    monkeypatch.setattr(recorder, "handlers", [handler])
    monkeypatch.setattr(recorder, "propagate", False)
    recorder.setLevel(logging.DEBUG)
    if hasattr(auth_module, "slog"):
        monkeypatch.setattr(auth_module, "slog", recorder)

    server_logger = logging.getLogger("ArcumServer")
    old_level = server_logger.level
    monkeypatch.setattr(server_logger, "handlers", [handler])
    server_logger.setLevel(logging.DEBUG)
    yield handler.records
    server_logger.setLevel(old_level)


# --- Criterion 1: a successful connection leaves no entry ----------------------

def test_ws_evict_criterion1_clean_connection_leaves_no_entry(clock):
    ip = _ip(1)
    assert _connect(ip) is False
    assert ip not in _store(), "an IP with no failed attempt must not be stored"


def test_ws_evict_criterion1_repeated_clean_connections_leave_no_entry(clock):
    ip = _ip(2)
    for _ in range(5):
        assert _connect(ip) is False
        clock.advance(1)
    assert ip not in _store()
    assert len(_store()) == 0


def test_ws_evict_criterion1_entry_removed_when_last_failure_expires(clock):
    # The IP failed once, then connects successfully after the window: its own
    # check must drop the entry instead of storing an empty list.
    ip = _ip(3)
    _failed_connection(ip)
    assert ip in _store(), "precondition: failure recorded"
    clock.advance(_window() + 1)
    assert _connect(ip) is False
    assert ip not in _store(), "an expired entry must be removed, not kept as an empty list"


# --- Criterion 2: a failed IP that never returns is evicted --------------------

def test_ws_evict_criterion2_stale_failures_removed_after_connection_from_other_ip(clock):
    ip_a, ip_b = _ip(10), _ip(11)
    for _ in range(_max_attempts() - 1):
        _failed_connection(ip_a)
    assert len(_store().get(ip_a, [])) == _max_attempts() - 1, "precondition: failures recorded"

    clock.advance(_retention_bound() + 1)
    assert _connect(ip_b) is False

    assert ip_a not in _store(), "stale failures of an IP that never reconnected must be evicted"


def test_ws_evict_criterion2_blocked_ip_evicted_after_bound(clock):
    ip_a, ip_b = _ip(12), _ip(13)
    for _ in range(_max_attempts()):
        _failed_connection(ip_a)
    assert _connect(ip_a) is True, "precondition: IP blocked"

    clock.advance(_retention_bound() + 1)
    assert _connect(ip_b) is False

    assert ip_a not in _store()


def test_ws_evict_criterion2_stale_failures_removed_when_next_connection_fails_too(clock):
    ip_a, ip_b = _ip(14), _ip(15)
    _failed_connection(ip_a)
    clock.advance(_retention_bound() + 1)
    _failed_connection(ip_b)

    assert ip_a not in _store()
    assert set(_store()) == {ip_b}


# --- Criterion 3: 1000 clean IPs, then one more connection ---------------------

def test_ws_evict_criterion3_thousand_clean_ips_leave_at_most_one_entry(clock):
    for i in range(1000):
        assert _connect(_bulk_ip(i)) is False

    clock.advance(_retention_bound() + 1)
    assert _connect(_ip(20)) is False

    assert len(_store()) <= 1, f"{len(_store())} entries remain after 1000 clean connections"


def test_ws_evict_criterion3_thousand_clean_ips_store_nothing_even_before_bound(clock):
    # Design part 1: empty lists are never stored, so the store stays empty at once.
    for i in range(1000):
        assert _connect(_bulk_ip(i)) is False
    assert len(_store()) == 0, f"{len(_store())} entries stored for IPs that never failed"


def test_ws_evict_criterion3_thousand_failed_ips_leave_at_most_the_new_one(clock):
    for i in range(1000):
        _failed_connection(_bulk_ip(i))
    assert len(_store()) == 1000, "precondition: 1000 failure records stored"

    clock.advance(_retention_bound() + 1)
    assert _connect(_ip(21)) is False

    assert len(_store()) <= 1, f"{len(_store())} entries remain"
    assert set(_store()) <= {_ip(21)}


# --- Criterion 4: failures inside the window are kept and still block ----------

def test_ws_evict_criterion4_fresh_failures_survive_forced_sweep_and_still_block(clock, monkeypatch):
    n = _max_attempts()
    blocked_ip = _ip(30)
    for _ in range(n):
        _failed_connection(blocked_ip)

    clock.advance(_window() - 1)  # still inside WS_AUTH_WINDOW
    # Force the periodic sweep on the next connection (design: _ws_auth_last_sweep).
    monkeypatch.setattr(auth_module, "_ws_auth_last_sweep", clock.now - 10 * _window(), raising=False)
    assert _connect(_ip(31)) is False

    assert len(_store().get(blocked_ip, [])) == n, "fresh failures must be kept by the sweep"
    assert _connect(blocked_ip) is True


def test_ws_evict_criterion4_blocked_exactly_after_max_attempts(clock):
    n = _max_attempts()
    ip = _ip(32)
    for i in range(n - 1):
        _failed_connection(ip)
        clock.advance(1)
    assert _connect(ip) is False, "one attempt below the limit must not be blocked"
    auth_module.ws_auth_record_failure(ip)
    assert _connect(ip) is True, "blocked after WS_AUTH_MAX_ATTEMPTS failures"


def test_ws_evict_criterion4_block_holds_until_window_ends_then_lifts(clock):
    n = _max_attempts()
    w = _window()
    blocked_ip = _ip(33)
    for _ in range(n):
        _failed_connection(blocked_ip)
    t_fail = clock.now

    # Connections from other IPs during the window, which may trigger sweeps.
    clock.advance(w - 1)
    assert _connect(_ip(34)) is False
    assert _connect(blocked_ip) is True, "block lifted before WS_AUTH_WINDOW"
    assert len(_store().get(blocked_ip, [])) == n

    clock.now = t_fail + w + 1
    assert _connect(_ip(35)) is False
    assert _connect(blocked_ip) is False, "block must end after WS_AUTH_WINDOW"


def test_ws_evict_criterion4_block_holds_for_whole_window_under_continuous_traffic(clock):
    # Background connections before and during the target's window, so that any
    # periodic sweep falls while the target's failures are still fresh.
    n = _max_attempts()
    w = _window()
    step = max(1, w // 60)
    k = 0

    def traffic():
        nonlocal k
        _failed_connection(_bulk_ip(k))
        k += 1

    start = clock.now
    while clock.now < start + w / 2:
        traffic()
        clock.advance(step)

    blocked_ip = _ip(36)
    for _ in range(n):
        _failed_connection(blocked_ip)
    t_fail = clock.now
    while clock.now + step < t_fail + w:
        clock.advance(step)
        traffic()
        assert _connect(blocked_ip) is True, (
            f"block lifted {clock.now - t_fail:.0f}s after the failures, before WS_AUTH_WINDOW"
        )
        assert len(_store().get(blocked_ip, [])) == n


# --- Criterion 5: retention bound 2 x WS_AUTH_WINDOW ---------------------------

def test_ws_evict_criterion5_no_record_older_than_two_windows_under_continuous_traffic(clock):
    w = _window()
    step = max(1, w // 60)
    old_ip = _ip(40)
    _failed_connection(old_ip)
    start = clock.now
    k = 0
    while clock.now < start + 3 * w:
        clock.advance(step)
        _failed_connection(_bulk_ip(k))
        k += 1
        age = _oldest_age(clock.now)
        assert age <= _retention_bound(), (
            f"a record {age:.0f}s old is still stored, bound is {_retention_bound()}s"
        )
    assert old_ip not in _store()


def test_ws_evict_criterion5_eviction_still_happens_after_clock_moves_backwards(clock):
    # Design D4: the sweep must not stall after the wall clock is set back.
    t0 = clock.now
    clock.advance(10 * _window())
    _failed_connection(_ip(41))

    clock.now = t0
    _failed_connection(_ip(42))
    clock.advance(_retention_bound() + 1)
    assert _connect(_ip(43)) is False

    assert _ip(42) not in _store(), "stale record not evicted after a backward clock jump"


# --- Privacy: cleanup logs nothing about evicted IPs ---------------------------

def test_ws_evict_cleanup_logs_no_ip(clock, log_records):
    old_ip = _ip(44)
    _failed_connection(old_ip)
    for i in range(50):
        assert _connect(_bulk_ip(i)) is False
    before = len(log_records)

    clock.advance(_retention_bound() + 1)
    assert _connect(_ip(45)) is False

    for rec in log_records[before:]:
        text = f"{rec.getMessage()} {rec.msg!r} {rec.args!r}"
        assert old_ip not in text and "10.51." not in text, "eviction must not log IPs"


# --- Criterion 6: close codes and reasons in outlook_endpoint unchanged --------

@pytest.mark.parametrize(
    "code, reason",
    [
        (4002, "Too many failed attempts"),
        (4003, "Invalid API key"),
        (4001, "Outlook ID not authorized"),
    ],
)
def test_ws_evict_criterion6_close_codes_and_reasons_unchanged(code, reason):
    # Static check of main_nice.py: there is no WebSocket test harness for
    # outlook_endpoint, so the close calls are matched in the source text.
    source = (PROJECT_ROOT / "main_nice.py").read_text(encoding="utf-8")
    pattern = rf"websocket\.close\(\s*code\s*=\s*{code}\s*,\s*reason\s*=\s*\"{re.escape(reason)}\"\s*\)"
    assert re.search(pattern, source), f"close({code}, {reason!r}) not found in outlook_endpoint"


def test_ws_evict_criterion6_endpoint_still_uses_throttle_functions():
    source = (PROJECT_ROOT / "main_nice.py").read_text(encoding="utf-8")
    assert "ws_auth_is_rate_limited(client_ip)" in source
    assert source.count("ws_auth_record_failure(client_ip)") == 2


# =============================================================================
# Issue #59: WebSocket throttle timing is independent of wall-clock changes
# =============================================================================
#
# Approved design (D1, D2): ws_auth_is_rate_limited and ws_auth_record_failure
# measure time with time.monotonic(). The wall clock (time.time) and the
# monotonic clock (time.monotonic) are replaced by two SEPARATE fakes that start
# far apart, so a call site that still reads time.time() (ws_auth_record_failure
# is the easy one to miss) makes a test fail. IPs are synthetic (10.0.59.0/24
# for named actors, 10.59.0.0/16 for traffic).

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
    return f"10.59.{k // 256}.{k % 256}"


def _jump_seconds(jump) -> float:
    if jump == "-10w":
        return -10 * _window()
    if jump == "10w":
        return 10 * _window()
    return float(jump)


_ALL_JUMPS = [
    pytest.param(-1, id="back_1s"),
    pytest.param("-10w", id="back_10_windows"),
    pytest.param(1, id="fwd_1s"),
    pytest.param("10w", id="fwd_10_windows"),
]


def _block_ws_ip(ip: str) -> None:
    for _ in range(_max_attempts()):
        _failed_connection(ip)
    assert _connect(ip) is True, "precondition: IP blocked"


@pytest.mark.parametrize("jump", _ALL_JUMPS)
def test_ws_monotonic_block_not_lifted_early_by_wall_jump(split_clock, jump):
    # Criteria 3 and 5 applied to the WebSocket throttle (design D2).
    ip = _ip59(1)
    _block_ws_ip(ip)
    split_clock.jump_wall(_jump_seconds(jump))

    assert _connect(ip) is True, "block lifted right after the clock change"
    split_clock.elapse(_window() - 1)
    assert _connect(_ip59(2)) is False
    assert _connect(ip) is True, "block lifted before WS_AUTH_WINDOW of elapsed time"


@pytest.mark.parametrize("jump", _ALL_JUMPS)
def test_ws_monotonic_block_ends_one_window_after_last_failure_despite_wall_jump(split_clock, jump):
    # Criteria 2 and 5 applied to the WebSocket throttle (design D2).
    ip = _ip59(3)
    _block_ws_ip(ip)
    split_clock.jump_wall(_jump_seconds(jump))

    split_clock.elapse(_window() + 1)
    assert _connect(ip) is False, "block must end WS_AUTH_WINDOW of elapsed time after the last failure"


def test_ws_monotonic_records_evicted_after_backward_wall_jump(split_clock):
    # Criterion 1 applied to the WebSocket throttle: failure at wall T + 10 x
    # WS_AUTH_WINDOW, wall set back to T, connections from other IPs once per
    # elapsed second for 2 x WS_AUTH_WINDOW + 2 seconds.
    w = _window()
    first_ip = _ip59(10)
    t = split_clock.wall
    split_clock.jump_wall(10 * w)
    _failed_connection(first_ip)
    assert first_ip in _store(), "precondition: failure recorded"

    split_clock.wall = t
    for k in range(2 * w + 2):
        split_clock.elapse(1)
        _failed_connection(_traffic_ip59(k))

    assert first_ip not in _store(), "record stamped before the backward clock change is still stored"


def test_ws_monotonic_guard_wall_clock_alone_never_changes_blocking(split_clock):
    # Only the wall clock moves, between every failure and every check; the
    # monotonic clock stays still. Fails if either ws_auth_is_rate_limited or
    # ws_auth_record_failure still reads time.time().
    n = _max_attempts()
    w = _window()
    ip = _ip59(20)
    for i in range(n):
        split_clock.jump_wall(10 * w if i % 2 == 0 else -25 * w)
        _failed_connection(ip)
    for jump in (100 * w, -300 * w, 1, -1):
        split_clock.jump_wall(jump)
        assert _connect(ip) is True, f"block changed by a wall clock jump of {jump}s"

    split_clock.mono += w - 1
    split_clock.jump_wall(-50 * w)
    assert _connect(ip) is True
    split_clock.mono += 2
    split_clock.jump_wall(70 * w)
    assert _connect(ip) is False, "block must end after WS_AUTH_WINDOW of monotonic time"


def test_ws_monotonic_guard_retention_bound_holds_when_only_monotonic_time_passes(split_clock):
    old_ip = _ip59(30)
    _failed_connection(old_ip)
    split_clock.mono += _retention_bound() + 1
    split_clock.jump_wall(-1)
    assert _connect(_ip59(31)) is False

    assert old_ip not in _store(), "stale record not evicted on the monotonic clock"


def test_ws_monotonic_clock_change_logs_no_ip(split_clock, log_records):
    # Criterion 7 applied to the WebSocket throttle.
    old_ip = _ip59(40)
    split_clock.jump_wall(10 * _window())
    _failed_connection(old_ip)
    before = len(log_records)

    split_clock.jump_wall(-10 * _window())
    split_clock.elapse(_retention_bound() + 1)
    assert _connect(_ip59(41)) is False

    for rec in log_records[before:]:
        text = f"{rec.getMessage()} {rec.msg!r} {rec.args!r}"
        assert old_ip not in text, "eviction must not log IPs"
