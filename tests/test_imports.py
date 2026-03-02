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


# --- Phase 2 additions ---

def test_engines_module():
    from src.ai import engines
    from src.ai.engines import load_rag_engine, load_simple_local_engine, load_cloud_engine


def test_prompt_optimizer_module():
    from src.ai import prompt_optimizer
    from src.ai.prompt_optimizer import optimize_prompt_for_rag


def test_bridge_package():
    import src.bridge
    from src.bridge import bridge_manager


def test_pending_results_module():
    from src.bridge import pending_results
    from src.bridge.pending_results import PendingResultStore


def test_loopback_queue_module():
    from src.bridge import loopback_queue
    from src.bridge.loopback_queue import _EmailTask, _UserQueue
