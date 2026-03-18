# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
# --- 1. ENGINE LOADING ---
from src.ai.engines import load_rag_engine, load_simple_local_engine, load_cloud_engine  # noqa: F401


# --- PROMPT OPTIMIZER (Privacy-First: Local → Gemini with NER masking) ---
from src.ai.prompt_optimizer import optimize_prompt_for_rag  # noqa: F401


# --- 2. SESSION CLASS ---
from src.ai.session import UserSession  # noqa: F401  (re-export for backward compat)
