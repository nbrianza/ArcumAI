# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import re
import threading
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
_lock = threading.Lock()


def _check_rate_limit(username: str) -> bool:
    """Returns True if the user can send, False if they exceeded the limit."""
    global _last_cleanup
    with _lock:
        now = time.time()
        recent = [t for t in _user_timestamps[username] if now - t < RATE_LIMIT_WINDOW]
        _user_timestamps[username] = recent
        if len(recent) >= RATE_LIMIT_MESSAGES:
            return False
        recent.append(now)

        # Periodic cleanup: evict users idle longer than RATE_LIMIT_STALE_TTL
        if now - _last_cleanup > RATE_LIMIT_CLEANUP_INT:
            _last_cleanup = now
            stale = [u for u, ts in list(_user_timestamps.items())
                     if not ts or now - max(ts) > RATE_LIMIT_STALE_TTL]
            for u in stale:
                _user_timestamps.pop(u, None)

        return True


def sanitize_input(text: str) -> str:
    """Sanitizes user input: removes control characters, limits length."""
    text = _CONTROL_CHARS.sub('', text)
    text = text.strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
    return text
