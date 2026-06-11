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
