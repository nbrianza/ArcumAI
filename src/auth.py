# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import json
import re
import threading
import time
import bcrypt
from pathlib import Path
from src.config import (
    BASE_DIR, WS_AUTH_MAX_ATTEMPTS, WS_AUTH_WINDOW, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW,
)
from src.logger import server_log as slog

USERS_FILE = BASE_DIR / "users.json"
BCRYPT_ROUNDS = 12

# Password policy
MIN_PASSWORD_LENGTH = 8
PASSWORD_PATTERN = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$')


def validate_password(password: str) -> tuple[bool, str]:
    """Validates that the password meets minimum security requirements."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if not PASSWORD_PATTERN.match(password):
        return False, "Password must contain at least one uppercase, one lowercase and one number."
    return True, ""


def load_users():
    """Loads the user database."""
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users_data):
    """Saves the user database to disk."""
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users_data, f, indent=4)

def hash_password(plain_password):
    """Transforms a plaintext password into a secure hash."""
    pw_bytes = plain_password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password, hashed_password):
    """Checks if the password matches the hash."""
    try:
        plain_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except (ValueError, TypeError):
        return False

def add_user(username, password, role, real_name, outlook_id=""):
    """Adds or updates a user."""
    valid, msg = validate_password(password)
    if not valid:
        slog.warning(f"[{username}] Invalid password: {msg}")
        return False
    users = load_users()
    users[username] = {
        "pw_hash": hash_password(password),
        "role": role,
        "name": real_name,
        "outlook_id": outlook_id
    }
    save_users(users)
    slog.info(f"[{username}] User saved successfully.")
    return True

def delete_user(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        return True
    return False

def update_password(username, new_password):
    valid, msg = validate_password(new_password)
    if not valid:
        slog.warning(f"[{username}] Invalid password: {msg}")
        return False
    users = load_users()
    if username in users:
        users[username]["pw_hash"] = hash_password(new_password)
        save_users(users)
        return True
    return False


# --- WebSocket Auth Rate Limiting (per-IP) ---
_ws_auth_failures: dict[str, list[float]] = {}
# Time of the last full sweep of _ws_auth_failures. No lock: the only callers
# run on the event loop, with no await between check and record.
_ws_auth_last_sweep: float = 0.0


def _ws_recent(ip: str, now: float) -> list[float]:
    """Prunes failures older than WS_AUTH_WINDOW for ip; deletes the key when empty."""
    attempts = [t for t in _ws_auth_failures.get(ip, []) if now - t < WS_AUTH_WINDOW]
    if attempts:
        _ws_auth_failures[ip] = attempts
    else:
        _ws_auth_failures.pop(ip, None)
    return attempts


def _ws_sweep_stale(now: float) -> None:
    """Evicts stale failures for every IP in _ws_auth_failures.

    Uses the same predicate as the throttle check (_ws_recent), so a failure still
    inside WS_AUTH_WINDOW is never removed. Logs nothing.
    """
    global _ws_auth_last_sweep
    for ip in list(_ws_auth_failures):
        _ws_recent(ip, now)
    _ws_auth_last_sweep = now


def ws_auth_is_rate_limited(ip: str) -> bool:
    """Returns True if this IP has exceeded WS_AUTH_MAX_ATTEMPTS in the last WS_AUTH_WINDOW seconds."""
    now = time.time()
    # Periodic sweep, at most once per WS_AUTH_WINDOW, so a failure record is
    # gone by the first connection at or after 2 x WS_AUTH_WINDOW. Also sweeps
    # when the clock moved backwards, so eviction does not stall.
    if now - _ws_auth_last_sweep >= WS_AUTH_WINDOW or now < _ws_auth_last_sweep:
        _ws_sweep_stale(now)
    return len(_ws_recent(ip, now)) >= WS_AUTH_MAX_ATTEMPTS


def ws_auth_record_failure(ip: str) -> None:
    """Record a failed WebSocket auth attempt for this IP."""
    _ws_auth_failures.setdefault(ip, []).append(time.time())


# --- Web Login Rate Limiting (per IP and per username) ---
# Every submitted username is counted, known or not, so a "blocked" result
# does not reveal whether a username exists. Empty keys are deleted.
_login_failures_ip: dict[str, list[float]] = {}
_login_failures_user: dict[str, list[float]] = {}
_login_lock = threading.Lock()
# Time of the last full sweep of both stores (protected by _login_lock).
_login_last_sweep: float = 0.0


def _recent(store: dict[str, list[float]], key: str, now: float) -> list[float]:
    """Prunes failures older than LOGIN_WINDOW for key; deletes the key when empty."""
    attempts = [t for t in store.get(key, []) if now - t < LOGIN_WINDOW]
    if attempts:
        store[key] = attempts
    else:
        store.pop(key, None)
    return attempts


def _sweep_stale(now: float) -> None:
    """Evicts stale failures for every key in both stores. Caller holds _login_lock.

    Uses the same predicate as the throttle check (_recent), so a failure still
    inside LOGIN_WINDOW is never removed. Logs nothing.
    """
    global _login_last_sweep
    for store in (_login_failures_ip, _login_failures_user):
        for key in list(store):
            _recent(store, key, now)
    _login_last_sweep = now


def check_login(users_db: dict, username: str, password: str, client_ip: str) -> str:
    """Checks a web login attempt with brute-force protection.

    Returns "ok", "failed" or "blocked". A blocked attempt does not check the
    password and is not recorded. Never logs the password, and logs the
    username only if it exists in users_db.
    """
    known = username in users_db
    user_label = f"'{username}'" if known else "<unknown>"
    with _login_lock:
        now = time.time()
        # Periodic sweep, at most once per LOGIN_WINDOW, so a failure record is
        # gone by the first attempt at or after 2 x LOGIN_WINDOW. Also sweeps
        # immediately if the clock went backwards.
        if now - _login_last_sweep >= LOGIN_WINDOW or now < _login_last_sweep:
            _sweep_stale(now)
        if (len(_recent(_login_failures_ip, client_ip, now)) >= LOGIN_MAX_ATTEMPTS
                or len(_recent(_login_failures_user, username, now)) >= LOGIN_MAX_ATTEMPTS):
            slog.warning(f"Web login blocked (IP: {client_ip}, user: {user_label})")
            return "blocked"

    if known and verify_password(password, users_db[username].get('pw_hash', '')):
        with _login_lock:
            _login_failures_user.pop(username, None)
        return "ok"

    with _login_lock:
        now = time.time()
        _login_failures_ip.setdefault(client_ip, []).append(now)
        _login_failures_user.setdefault(username, []).append(now)
    slog.warning(f"Web login failed (IP: {client_ip}, user: {user_label})")
    return "failed"
