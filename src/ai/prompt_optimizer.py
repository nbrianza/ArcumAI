# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
import os
from llama_index.core import Settings
from llama_index.llms.gemini import Gemini

from src.config import (
    PROMPT_OPTIMIZATION, ENABLE_NER_MASKING, NER_SCORE_THRESHOLD,
    LLM_MODEL_NAME, CONTEXT_WINDOW, GEMINI_TIMEOUT
)
from src.logger import server_log as slog


_gemini_optimizer = None


def _get_gemini_optimizer():
    """Lazy-init a Gemini LLM instance for prompt optimization (reused across calls)."""
    global _gemini_optimizer
    if _gemini_optimizer is None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Missing GOOGLE_API_KEY in .env file for Gemini optimization")
        _gemini_optimizer = Gemini(model="models/gemini-2.5-flash", api_key=api_key)
    return _gemini_optimizer


async def optimize_prompt_for_rag(subject: str, body: str, mode: str = None) -> str:
    """
    Optimize a raw email into a query for the RAG engine.

    Modes (from config.PROMPT_OPTIMIZATION):
      - "local": Use local LLM (Ollama) - 100% private, slower, lower quality
      - "gemini": Use Gemini Cloud with NER-based PII masking
      - "off": No optimization, return raw email as-is

    Args:
        subject: Email subject line
        body: Email body text
        mode: Override mode (if None, uses config.PROMPT_OPTIMIZATION)

    Returns:
        Optimized query string
    """
    if mode is None:
        mode = PROMPT_OPTIMIZATION.lower()

    if mode == "off":
        slog.debug("PromptOptimization: Disabled (using raw email)")
        return f"Email Subject: {subject}\n\n{body}"

    elif mode == "local":
        return await _optimize_with_local_llm(subject, body)

    elif mode == "gemini":
        return await _optimize_with_gemini(subject, body)

    else:
        slog.warning(f"PromptOptimization: Unknown mode '{mode}', falling back to 'local'")
        return await _optimize_with_local_llm(subject, body)


async def _optimize_with_local_llm(subject: str, body: str) -> str:
    """
    Use local Ollama LLM to optimize the prompt (100% private).
    Trade-off: slower and lower quality than Gemini, but no data leaves the machine.
    """
    slog.info("PromptOptimization: Using LOCAL LLM (100% private)")

    meta_prompt = (
        f"Rewrite this email as a concise search query for a document database.\n"
        f"Extract the main question, key terms, names, dates, and legal references.\n"
        f"Remove greetings and signatures. Output ONLY the query.\n\n"
        f"Subject: {subject}\n\n{body}"
    )

    try:
        # Use the global Settings.llm (Ollama instance)
        response = await Settings.llm.acomplete(meta_prompt)
        optimized = str(response).strip()
        slog.info(f"PromptOptimization: Local LLM completed ({len(optimized)} chars)")
        return optimized
    except Exception as e:
        slog.error(f"PromptOptimization: Local LLM failed: {e}", exc_info=True)
        # Fallback to raw email
        return f"Email Subject: {subject}\n\n{body}"


async def _optimize_with_gemini(subject: str, body: str) -> str:
    """
    Use Gemini Cloud to optimize the prompt.
    If NER masking is enabled, masks PII before sending and restores after.
    """
    slog.info(f"PromptOptimization: Using GEMINI CLOUD (NER masking: {ENABLE_NER_MASKING})")

    raw_email = f"Subject: {subject}\n\n{body}"
    anonymization_metadata = {}

    # Step 1: Mask PII if enabled
    if ENABLE_NER_MASKING:
        try:
            from src.ai.ner_masking import mask_pii, is_presidio_available

            if not is_presidio_available():
                slog.warning("PromptOptimization: Presidio not available, sending unmasked to Gemini (PRIVACY RISK)")
                masked_email = raw_email
            else:
                masked_email, anonymization_metadata = mask_pii(
                    raw_email,
                    language="it",
                    score_threshold=NER_SCORE_THRESHOLD
                )

                entity_counts = anonymization_metadata.get("entity_counts", {})
                if entity_counts:
                    slog.info(f"PromptOptimization: Masked PII before Gemini: {entity_counts}")
                    slog.debug(f"PromptOptimization: Masked email text:\n{masked_email}")
                else:
                    slog.debug("PromptOptimization: No PII detected in email")

        except Exception as e:
            slog.error(f"PromptOptimization: NER masking failed: {e}", exc_info=True)
            slog.warning("PromptOptimization: Sending unmasked to Gemini (PRIVACY RISK)")
            masked_email = raw_email
    else:
        masked_email = raw_email

    # Step 2: Send to Gemini
    meta_prompt = (
        f"You are a prompt engineer. Rewrite the following email into an optimized search query "
        f"for a RAG system.\n\n"
        f"RAG system details:\n"
        f"- Retrieval: BM25 + ChromaDB hybrid search over legal, notarial, and fiduciary documents\n"
        f"- Generation LLM: {LLM_MODEL_NAME} (context window: {CONTEXT_WINDOW} tokens)\n"
        f"- Language: respond in the same language as the email\n\n"
        f"Your job:\n"
        f"1. Extract the core intent / question from the email\n"
        f"2. Identify key entities, names, dates, legal references\n"
        f"3. Reformulate as a clear, concise query optimized for document retrieval\n"
        f"4. Include relevant keywords that would match document chunks\n"
        f"5. Remove greetings, pleasantries, signatures, and noise\n\n"
        f"CRITICAL PRIVACY RULE:\n"
        f"The email contains privacy-masked placeholders in the format <ENTITY_TYPE_NUMBER>.\n"
        f"Examples: <PERSON_1>, <PERSON_2>, <CH_IBAN_1>, <ORGANIZATION_1>, <EMAIL_ADDRESS_1>\n"
        f"You MUST preserve these placeholders EXACTLY as they appear - do not modify, generalize, or remove them.\n"
        f"These placeholders will be replaced with real values after your optimization.\n\n"
        f"Output ONLY the optimized query, nothing else.\n\n"
        f"--- EMAIL ---\n"
        f"{masked_email}\n"
        f"--- END EMAIL ---"
    )

    try:
        llm = _get_gemini_optimizer()
        response = await asyncio.wait_for(llm.acomplete(meta_prompt), timeout=GEMINI_TIMEOUT)
        masked_optimized = str(response).strip()
        slog.info(f"PromptOptimization: Gemini completed ({len(masked_optimized)} chars)")
        slog.debug(f"PromptOptimization: Gemini output (before unmask):\n{masked_optimized}")

        # Step 3: Restore PII if we masked it
        if ENABLE_NER_MASKING and anonymization_metadata:
            try:
                from src.ai.ner_masking import unmask_pii
                optimized = unmask_pii(masked_optimized, anonymization_metadata)
                slog.debug("PromptOptimization: PII restored after Gemini")
                return optimized
            except Exception as e:
                slog.warning(f"PromptOptimization: De-anonymization failed ({e}), returning masked result")
                return masked_optimized
        else:
            return masked_optimized

    except Exception as e:
        slog.error(f"PromptOptimization: Gemini API failed: {e}", exc_info=True)
        # Fallback to raw email (unmasked)
        slog.warning("PromptOptimization: Falling back to raw email (no optimization)")
        return raw_email
