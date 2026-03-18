# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import json
import re
import bcrypt
from pathlib import Path
from src.config import BASE_DIR
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
