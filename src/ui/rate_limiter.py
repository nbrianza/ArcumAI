# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import re
import time
from collections import defaultdict

from src.config import (
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW,
    RATE_LIMIT_STALE_TTL, RATE_LIMIT_CLEANUP_INT,
)

MAX_INPUT_LENGTH = 4000
# Control characters except \n \r \t
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# Per-user rate limiter (in-memory)
_user_timestamps: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0


def _check_rate_limit(username: str) -> bool:
    """Returns True if the user can send, False if they exceeded the limit."""
    global _last_cleanup
    now = time.time()
    timestamps = _user_timestamps[username]
    # Purge expired entries for this user
    _user_timestamps[username] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(_user_timestamps[username]) >= RATE_LIMIT_MESSAGES:
        return False
    _user_timestamps[username].append(now)

    # Periodic cleanup: evict users idle longer than RATE_LIMIT_STALE_TTL
    if now - _last_cleanup > RATE_LIMIT_CLEANUP_INT:
        _last_cleanup = now
        stale = [u for u, ts in _user_timestamps.items()
                 if not ts or now - max(ts) > RATE_LIMIT_STALE_TTL]
        for u in stale:
            del _user_timestamps[u]

    return True


def sanitize_input(text: str) -> str:
    """Sanitizes user input: removes control characters, limits length."""
    text = _CONTROL_CHARS.sub('', text)
    text = text.strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
    return text
