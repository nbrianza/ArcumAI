"""
Phase 1 verification: every public module must import without errors.
Run with: python -m pytest tests/test_imports.py -v
"""


def test_config():
    from src import config


def test_auth():
    from src import auth


def test_logger():
    from src import logger


def test_readers():
    from src import readers


def test_utils():
    from src import utils


def test_bridge():
    from src.bridge import bridge_manager


def test_engine():
    from src.engine import UserSession


def test_ai_package():
    import src.ai


def test_ner_masking():
    from src.ai.ner_masking import mask_pii, unmask_pii, is_presidio_available
