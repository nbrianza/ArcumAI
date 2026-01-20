import json
import bcrypt
from pathlib import Path
from src.config import BASE_DIR

USERS_FILE = BASE_DIR / "users.json"

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
    # bcrypt richiede byte, quindi codifichiamo
    bytes = plain_password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(bytes, salt)
    return hashed.decode('utf-8') # Salviamo come stringa

def verify_password(plain_password, hashed_password):
    """Controlla se la password corrisponde all'hash."""
    try:
        plain_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except ValueError:
        return False

def add_user(username, password, role, real_name):
    """Aggiunge o aggiorna un utente."""
    users = load_users()
    users[username] = {
        "pw_hash": hash_password(password), # Salviamo SOLO l'hash
        "role": role,
        "name": real_name
    }
    save_users(users)
    print(f"✅ Utente '{username}' salvato con successo (Criptato).")

def delete_user(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        return True
    return False

def update_password(username, new_password):
    users = load_users()
    if username in users:
        users[username]["pw_hash"] = hash_password(new_password)
        save_users(users)
        return True
    return False