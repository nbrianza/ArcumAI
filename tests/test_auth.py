# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import json
import time


def test_hash_password_produces_bcrypt_hash():
    from src.auth import hash_password
    h = hash_password("Password1")
    assert h.startswith("$2b$")


def test_hash_password_produces_different_hashes_for_same_input():
    from src.auth import hash_password
    h1 = hash_password("Password1")
    h2 = hash_password("Password1")
    assert h1 != h2


def test_verify_password_correct():
    from src.auth import hash_password, verify_password
    h = hash_password("Correct1")
    assert verify_password("Correct1", h) is True


def test_verify_password_wrong():
    from src.auth import hash_password, verify_password
    h = hash_password("Correct1")
    assert verify_password("WrongPass1", h) is False


def test_verify_password_handles_garbage():
    from src.auth import verify_password
    assert verify_password("anything", "not-a-hash") is False


def test_load_users_returns_empty_dict_for_missing_file(tmp_path, monkeypatch):
    import src.auth as auth_module
    monkeypatch.setattr(auth_module, "USERS_FILE", tmp_path / "nonexistent.json")
    assert auth_module.load_users() == {}


def test_load_users_reads_json(tmp_path, monkeypatch):
    import src.auth as auth_module
    data = {"alice": {"pw_hash": "x", "role": "ADMIN", "name": "Alice", "outlook_id": ""}}
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps(data))
    monkeypatch.setattr(auth_module, "USERS_FILE", users_file)
    assert "alice" in auth_module.load_users()


def test_add_user_persists_and_verifies(tmp_path, monkeypatch):
    import src.auth as auth_module
    monkeypatch.setattr(auth_module, "USERS_FILE", tmp_path / "users.json")
    assert auth_module.add_user("bob", "BobPass1", "LEGAL", "Bob Smith") is True
    users = auth_module.load_users()
    assert "bob" in users
    assert auth_module.verify_password("BobPass1", users["bob"]["pw_hash"]) is True


def test_add_user_rejects_weak_password(tmp_path, monkeypatch):
    import src.auth as auth_module
    monkeypatch.setattr(auth_module, "USERS_FILE", tmp_path / "users.json")
    assert auth_module.add_user("bob", "weak", "LEGAL", "Bob Smith") is False


def test_ws_auth_not_rate_limited_for_fresh_ip():
    from src.auth import ws_auth_is_rate_limited
    assert ws_auth_is_rate_limited("192.168.250.1") is False


def test_ws_auth_rate_limited_after_max_attempts():
    import src.auth as auth_module
    from src.config import WS_AUTH_MAX_ATTEMPTS
    ip = "10.0.255.1"
    auth_module._ws_auth_failures.pop(ip, None)
    for _ in range(WS_AUTH_MAX_ATTEMPTS):
        auth_module.ws_auth_record_failure(ip)
    assert auth_module.ws_auth_is_rate_limited(ip) is True


def test_ws_auth_resets_after_window_expires():
    import src.auth as auth_module
    from src.config import WS_AUTH_MAX_ATTEMPTS, WS_AUTH_WINDOW
    ip = "10.0.255.2"
    # Plant stale timestamps outside the window
    auth_module._ws_auth_failures[ip] = [time.time() - WS_AUTH_WINDOW - 10] * WS_AUTH_MAX_ATTEMPTS
    assert auth_module.ws_auth_is_rate_limited(ip) is False
