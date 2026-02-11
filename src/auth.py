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
    """Verifica che la password rispetti i requisiti minimi di sicurezza."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"La password deve essere di almeno {MIN_PASSWORD_LENGTH} caratteri."
    if not PASSWORD_PATTERN.match(password):
        return False, "La password deve contenere almeno una maiuscola, una minuscola e un numero."
    return True, ""


def load_users():
    """Carica il database utenti."""
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users_data):
    """Salva il database utenti su disco."""
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users_data, f, indent=4)

def hash_password(plain_password):
    """Trasforma una password in chiaro in un hash sicuro."""
    pw_bytes = plain_password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password, hashed_password):
    """Controlla se la password corrisponde all'hash."""
    try:
        plain_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except (ValueError, TypeError):
        return False

def add_user(username, password, role, real_name, outlook_id=""):
    """Aggiunge o aggiorna un utente."""
    valid, msg = validate_password(password)
    if not valid:
        slog.warning(f"[{username}] Password non valida: {msg}")
        return False
    users = load_users()
    users[username] = {
        "pw_hash": hash_password(password),
        "role": role,
        "name": real_name,
        "outlook_id": outlook_id
    }
    save_users(users)
    slog.info(f"[{username}] Utente salvato con successo.")
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
        slog.warning(f"[{username}] Password non valida: {msg}")
        return False
    users = load_users()
    if username in users:
        users[username]["pw_hash"] = hash_password(new_password)
        save_users(users)
        return True
    return False