# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_mode_off_returns_raw_email_format():
    from src.ai.prompt_optimizer import optimize_prompt_for_rag
    result = asyncio.run(optimize_prompt_for_rag("Test Subject", "Body text", mode="off"))
    assert result == "Email Subject: Test Subject\n\nBody text"


def test_mode_off_empty_body():
    from src.ai.prompt_optimizer import optimize_prompt_for_rag
    result = asyncio.run(optimize_prompt_for_rag("Subject only", "", mode="off"))
    assert "Subject only" in result


def test_raises_on_oversized_input():
    from src.ai.prompt_optimizer import optimize_prompt_for_rag, _MAX_INPUT_CHARS
    big = "x" * (_MAX_INPUT_CHARS + 1)
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(optimize_prompt_for_rag(big, "", mode="off"))


def test_mode_local_calls_llm(monkeypatch):
    from src.ai import prompt_optimizer
    from llama_index.core import Settings
    mock_llm = MagicMock()
    mock_llm.acomplete = AsyncMock(return_value="optimized query")
    # Patch _llm directly to avoid triggering the property getter (which needs openai installed)
    monkeypatch.setattr(Settings, "_llm", mock_llm)
    result = asyncio.run(prompt_optimizer.optimize_prompt_for_rag("subj", "body", mode="local"))
    mock_llm.acomplete.assert_called_once()
    assert isinstance(result, str)


def test_mode_local_falls_back_to_raw_on_llm_error(monkeypatch):
    from src.ai import prompt_optimizer
    from llama_index.core import Settings
    mock_llm = MagicMock()
    mock_llm.acomplete = AsyncMock(side_effect=RuntimeError("Ollama down"))
    monkeypatch.setattr(Settings, "_llm", mock_llm)
    result = asyncio.run(prompt_optimizer.optimize_prompt_for_rag("subj", "body text", mode="local"))
    assert "subj" in result
    assert "body text" in result


def test_unknown_mode_falls_back_to_local(monkeypatch):
    from src.ai import prompt_optimizer
    from llama_index.core import Settings
    mock_llm = MagicMock()
    mock_llm.acomplete = AsyncMock(return_value="result")
    monkeypatch.setattr(Settings, "_llm", mock_llm)
    asyncio.run(prompt_optimizer.optimize_prompt_for_rag("subj", "body", mode="unknown_xyz"))
    mock_llm.acomplete.assert_called_once()


def test_mode_gemini_calls_gemini(monkeypatch):
    from src.ai import prompt_optimizer
    monkeypatch.setattr(prompt_optimizer, "ENABLE_NER_MASKING", False)
    mock_llm = MagicMock()
    mock_llm.acomplete = AsyncMock(return_value="gemini answer")
    monkeypatch.setattr(prompt_optimizer, "_get_gemini_optimizer", lambda: mock_llm)
    result = asyncio.run(prompt_optimizer.optimize_prompt_for_rag("subj", "body", mode="gemini"))
    mock_llm.acomplete.assert_called_once()
    assert result == "gemini answer"


def test_mode_gemini_falls_back_to_raw_on_api_error(monkeypatch):
    from src.ai import prompt_optimizer
    monkeypatch.setattr(prompt_optimizer, "ENABLE_NER_MASKING", False)
    mock_llm = MagicMock()
    mock_llm.acomplete = AsyncMock(side_effect=RuntimeError("API error"))
    monkeypatch.setattr(prompt_optimizer, "_get_gemini_optimizer", lambda: mock_llm)
    result = asyncio.run(prompt_optimizer.optimize_prompt_for_rag("subj", "body", mode="gemini"))
    assert "subj" in result
    assert "body" in result
