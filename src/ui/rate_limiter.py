# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import re
import time
from collections import defaultdict

MAX_INPUT_LENGTH = 4000
RATE_LIMIT_MESSAGES = 20   # max messages per window
RATE_LIMIT_WINDOW = 60     # window in seconds
# Control characters except \n \r \t
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# Per-user rate limiter (in-memory)
_user_timestamps: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(username: str) -> bool:
    """Returns True if the user can send, False if they exceeded the limit."""
    now = time.time()
    timestamps = _user_timestamps[username]
    # Purge expired entries
    _user_timestamps[username] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(_user_timestamps[username]) >= RATE_LIMIT_MESSAGES:
        return False
    _user_timestamps[username].append(now)
    return True


def sanitize_input(text: str) -> str:
    """Sanitizes user input: removes control characters, limits length."""
    text = _CONTROL_CHARS.sub('', text)
    text = text.strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
    return text
