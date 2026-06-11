# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import time


def test_sanitize_input_strips_control_chars():
    from src.ui.rate_limiter import sanitize_input
    assert sanitize_input("hello\x00world") == "helloworld"
    assert sanitize_input("test\x1besc") == "testesc"
    assert sanitize_input("\x08backspace") == "backspace"


def test_sanitize_input_preserves_newline_and_tab():
    from src.ui.rate_limiter import sanitize_input
    assert "\n" in sanitize_input("line1\nline2")
    assert "\t" in sanitize_input("col1\tcol2")
    assert "\r" in sanitize_input("cr\rhere")


def test_sanitize_input_truncates_long_input():
    from src.ui.rate_limiter import sanitize_input, MAX_INPUT_LENGTH
    long_text = "a" * (MAX_INPUT_LENGTH + 500)
    assert len(sanitize_input(long_text)) == MAX_INPUT_LENGTH


def test_sanitize_input_strips_leading_trailing_whitespace():
    from src.ui.rate_limiter import sanitize_input
    assert sanitize_input("  hello  ") == "hello"


def test_sanitize_input_normal_text_unchanged():
    from src.ui.rate_limiter import sanitize_input
    text = "Caro cliente, il contratto è pronto."
    assert sanitize_input(text) == text


def test_check_rate_limit_allows_within_limit():
    from src.ui import rate_limiter
    user = "rl_allow_test_001"
    rate_limiter._user_timestamps.pop(user, None)
    assert rate_limiter._check_rate_limit(user) is True


def test_check_rate_limit_blocks_when_limit_reached():
    from src.ui import rate_limiter
    from src.config import RATE_LIMIT_MESSAGES
    user = "rl_block_test_001"
    rate_limiter._user_timestamps.pop(user, None)
    for _ in range(RATE_LIMIT_MESSAGES):
        rate_limiter._check_rate_limit(user)
    assert rate_limiter._check_rate_limit(user) is False


def test_check_rate_limit_independent_per_user():
    from src.ui import rate_limiter
    from src.config import RATE_LIMIT_MESSAGES
    user_a = "rl_indep_a_001"
    user_b = "rl_indep_b_001"
    rate_limiter._user_timestamps.pop(user_a, None)
    rate_limiter._user_timestamps.pop(user_b, None)
    for _ in range(RATE_LIMIT_MESSAGES):
        rate_limiter._check_rate_limit(user_a)
    # user_a is blocked, user_b should still be allowed
    assert rate_limiter._check_rate_limit(user_a) is False
    assert rate_limiter._check_rate_limit(user_b) is True


def test_check_rate_limit_resets_after_window_expires():
    from src.ui import rate_limiter
    from src.config import RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW
    user = "rl_reset_test_001"
    # Plant stale timestamps outside the window
    rate_limiter._user_timestamps[user] = [time.time() - RATE_LIMIT_WINDOW - 10] * RATE_LIMIT_MESSAGES
    assert rate_limiter._check_rate_limit(user) is True
