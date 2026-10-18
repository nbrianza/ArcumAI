# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
from pathlib import Path


def test_base_dir_is_path_and_exists():
    from src.config import BASE_DIR
    assert isinstance(BASE_DIR, Path)
    assert BASE_DIR.exists()


def test_key_path_constants_are_path_objects():
    from src.config import CHROMA_PATH, BM25_PATH, INBOX_DIR, ARCHIVE_DIR, LOG_DIR
    for constant in (CHROMA_PATH, BM25_PATH, INBOX_DIR, ARCHIVE_DIR, LOG_DIR):
        assert isinstance(constant, Path), f"{constant} should be a Path"


def test_rate_limit_constants_are_positive_ints():
    from src.config import RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, RATE_LIMIT_STALE_TTL, RATE_LIMIT_CLEANUP_INT
    for val in (RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, RATE_LIMIT_STALE_TTL, RATE_LIMIT_CLEANUP_INT):
        assert isinstance(val, int) and val > 0


def test_ws_auth_constants_are_positive():
    from src.config import WS_AUTH_MAX_ATTEMPTS, WS_AUTH_WINDOW
    assert WS_AUTH_MAX_ATTEMPTS > 0
    assert WS_AUTH_WINDOW > 0


def test_chunk_size_greater_than_overlap():
    from src.config import CHUNK_SIZE, CHUNK_OVERLAP
    assert CHUNK_SIZE > CHUNK_OVERLAP > 0


def test_valid_roles_contains_expected_roles():
    from src.config import VALID_ROLES
    for role in ("ADMIN", "LEGAL", "EXECUTIVE", "DEFAULT"):
        assert role in VALID_ROLES, f"Expected role '{role}' missing from VALID_ROLES"


def test_default_system_prompt_is_nonempty_string():
    from src.config import DEFAULT_SYSTEM_PROMPT
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert len(DEFAULT_SYSTEM_PROMPT) > 50


def test_model_name_constants_are_nonempty_strings():
    from src.config import LLM_MODEL_NAME, EMBED_MODEL_NAME
    assert isinstance(LLM_MODEL_NAME, str) and LLM_MODEL_NAME
    assert isinstance(EMBED_MODEL_NAME, str) and EMBED_MODEL_NAME


def test_vsto_email_looks_like_email():
    from src.config import VSTO_ARCUMAI_EMAIL
    assert "@" in VSTO_ARCUMAI_EMAIL


def test_pending_result_ttl_is_positive():
    from src.config import PENDING_RESULT_TTL_HOURS
    assert isinstance(PENDING_RESULT_TTL_HOURS, int) and PENDING_RESULT_TTL_HOURS > 0


def test_env_int_accepts_value_in_range(monkeypatch):
    from src.config import _env_int
    monkeypatch.setenv("ARCUM_TEST_INT", "42")
    assert _env_int("ARCUM_TEST_INT", "1", 1, 100) == 42


def test_env_int_uses_default_when_unset(monkeypatch):
    from src.config import _env_int
    monkeypatch.delenv("ARCUM_TEST_INT", raising=False)
    assert _env_int("ARCUM_TEST_INT", "7", 1, 100) == 7


def test_env_int_rejects_out_of_range(monkeypatch):
    import pytest
    from src.config import _env_int
    for bad in ("-1", "0", "999999999"):
        monkeypatch.setenv("ARCUM_TEST_INT", bad)
        with pytest.raises(ValueError, match="ARCUM_TEST_INT"):
            _env_int("ARCUM_TEST_INT", "1", 1, 100)


def test_env_int_rejects_non_numeric(monkeypatch):
    import pytest
    from src.config import _env_int
    monkeypatch.setenv("ARCUM_TEST_INT", "abc")
    with pytest.raises(ValueError, match="not a valid int"):
        _env_int("ARCUM_TEST_INT", "1", 1, 100)


def test_env_float_rejects_out_of_range(monkeypatch):
    import pytest
    from src.config import _env_float
    monkeypatch.setenv("ARCUM_TEST_FLOAT", "1.5")
    with pytest.raises(ValueError, match="out of range"):
        _env_float("ARCUM_TEST_FLOAT", "0.5", 0.0, 1.0)


# --- Issue #37: web login throttle settings -----------------------------------
# Out-of-range values are checked in a fresh interpreter so that re-executing
# src/config.py never rebinds constants used by other modules in this session.
# One subprocess runs every case (importing src.config is slow), executing a
# fresh copy of src/config.py per case under a throwaway module name.

_LOGIN_CONFIG_PROBE = r"""
import importlib.util, json, os, sys
cases = json.loads(sys.argv[1])
path = os.path.join(os.getcwd(), "src", "config.py")
results = {}
for label, overrides in cases.items():
    for key in ("LOGIN_MAX_ATTEMPTS", "LOGIN_WINDOW"):
        os.environ.pop(key, None)
    os.environ.update(overrides)
    spec = importlib.util.spec_from_file_location("config_probe_" + label, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        results[label] = {"ok": True,
                          "max": getattr(mod, "LOGIN_MAX_ATTEMPTS", None),
                          "window": getattr(mod, "LOGIN_WINDOW", None)}
    except Exception as exc:
        results[label] = {"ok": False, "type": type(exc).__name__, "error": str(exc)}
print("PROBE_RESULT=" + json.dumps(results))
"""

_LOGIN_CONFIG_CASES = {
    "defaults": {},
    "max_zero": {"LOGIN_MAX_ATTEMPTS": "0"},
    "max_negative": {"LOGIN_MAX_ATTEMPTS": "-1"},
    "max_too_high": {"LOGIN_MAX_ATTEMPTS": "1001"},
    "window_zero": {"LOGIN_WINDOW": "0"},
    "window_negative": {"LOGIN_WINDOW": "-1"},
    "window_too_high": {"LOGIN_WINDOW": "86401"},
    "valid_override": {"LOGIN_MAX_ATTEMPTS": "3", "LOGIN_WINDOW": "300"},
}


def _login_config_probe_results():
    import json
    import os
    import subprocess
    import sys
    from src.config import BASE_DIR
    env = dict(os.environ)
    env["ARCUMAI_ENV"] = "dev"
    proc = subprocess.run(
        [sys.executable, "-c", _LOGIN_CONFIG_PROBE, json.dumps(_LOGIN_CONFIG_CASES)],
        cwd=str(BASE_DIR), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"probe crashed: {proc.stderr[-2000:]}"
    line = [l for l in proc.stdout.splitlines() if l.startswith("PROBE_RESULT=")][-1]
    return json.loads(line[len("PROBE_RESULT="):])


_probe_cache: dict = {}


def _probe():
    if not _probe_cache:
        _probe_cache.update(_login_config_probe_results())
    return _probe_cache


def test_login_throttle_constants_are_positive_ints():
    from src.config import LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW
    for val in (LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW):
        assert isinstance(val, int) and val > 0


def test_login_throttle_defaults_are_6_attempts_per_60_seconds():
    result = _probe()["defaults"]
    assert result == {"ok": True, "max": 6, "window": 60}


def test_login_throttle_valid_override_is_applied():
    result = _probe()["valid_override"]
    assert result == {"ok": True, "max": 3, "window": 300}


def test_criterion7_out_of_range_login_settings_stop_startup_with_clear_error():
    results = _probe()
    for label, var in (
        ("max_zero", "LOGIN_MAX_ATTEMPTS"),
        ("max_negative", "LOGIN_MAX_ATTEMPTS"),
        ("max_too_high", "LOGIN_MAX_ATTEMPTS"),
        ("window_zero", "LOGIN_WINDOW"),
        ("window_negative", "LOGIN_WINDOW"),
        ("window_too_high", "LOGIN_WINDOW"),
    ):
        result = results[label]
        assert result["ok"] is False, f"{label}: config accepted an out-of-range value"
        assert result["type"] == "ValueError", f"{label}: {result}"
        assert var in result["error"] and "out of range" in result["error"], f"{label}: {result['error']}"
