"""
Named Entity Recognition (NER) based PII masking using Presidio.

This module provides privacy-preserving masking for sensitive data before
sending emails to cloud APIs (e.g., Gemini).

Features:
- Standard PII detection (PERSON, ORG, EMAIL, PHONE, etc.)
- Swiss/Italian domain-specific entities (legal entities, fiscal codes, IBAN)
- Reversible anonymization with de-anonymization support
"""

import os
from typing import Dict, List, Tuple, Optional
from src.logger import server_log as log

# Lazy imports for optional Presidio dependency
_analyzer = None
_anonymizer = None
_deanonymizer = None
_recognizers_loaded = False


def _init_presidio():
    """Lazy initialization of Presidio components."""
    global _analyzer, _anonymizer, _deanonymizer, _recognizers_loaded

    if _analyzer is not None:
        return

    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine

        # Configure NLP engine with Italian spaCy model
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "it", "model_name": "it_core_news_lg"},  # Italian
                {"lang_code": "en", "model_name": "en_core_web_lg"},   # English (if available)
            ]
        }

        try:
            nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        except Exception as e:
            # Fallback to just Italian if English model not available
            log.warning(f"NER: Could not load all models ({e}), falling back to Italian only")
            nlp_config["models"] = [{"lang_code": "it", "model_name": "it_core_news_lg"}]
            nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()

        # Initialize engines
        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        _anonymizer = AnonymizerEngine()
        _deanonymizer = DeanonymizeEngine()

        # Add custom Swiss/Italian recognizers
        _add_custom_recognizers()
        _recognizers_loaded = True

        log.info("NER: Presidio initialized with Swiss/Italian custom recognizers")

    except ImportError as e:
        log.warning(f"NER: Presidio not available ({e}). Install: pip install presidio-analyzer presidio-anonymizer")
        log.warning("NER: Falling back to no masking (privacy risk if using cloud optimization)")
    except Exception as e:
        log.error(f"NER: Failed to initialize Presidio: {e}")


def _add_custom_recognizers():
    """Add Swiss and Italian domain-specific recognizers to Presidio."""
    from presidio_analyzer import Pattern, PatternRecognizer

    # Supported languages: Italian, English, French, German
    SUPPORTED_LANGUAGES = ["it", "en", "fr", "de"]

    # Pattern definitions (language-agnostic regex)
    patterns_config = [
        {
            "entity": "SWISS_LEGAL_ENTITY",
            "patterns": [
                Pattern("SA", r"\b[A-Z][a-zA-Z\s&\-]+\s+SA\b", 0.85),
                Pattern("SAGL", r"\b[A-Z][a-zA-Z\s&\-]+\s+Sagl\b", 0.85),
                Pattern("AG", r"\b[A-Z][a-zA-Z\s&\-]+\s+AG\b", 0.85),
                Pattern("GMBH", r"\b[A-Z][a-zA-Z\s&\-]+\s+GmbH\b", 0.85),
            ]
        },
        {
            "entity": "IT_FISCAL_CODE",
            "patterns": [
                Pattern("fiscal_code", r"\b[A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z]\b", 0.95)
            ]
        },
        {
            "entity": "CH_IBAN",
            "patterns": [
                Pattern("ch_iban", r"\bCHE\d{12}\b", 0.98)
            ]
        },
        {
            "entity": "NOTARIAL_REFERENCE",
            "patterns": [
                Pattern("rep_number", r"\b(?:Atto\s+)?Rep\.?\s*\d{3,6}/\d{4}\b", 0.9),
                Pattern("racc_number", r"\bRacc\.?\s*\d{3,6}/\d{4}\b", 0.9),
            ]
        },
        {
            "entity": "CH_VAT_NUMBER",
            "patterns": [
                Pattern("ch_vat", r"\bCHE-\d{3}\.\d{3}\.\d{3}(?:\s+(?:IVA|MWST|TVA))?\b", 0.95)
            ]
        }
    ]

    # Create one recognizer per entity per language
    for config in patterns_config:
        for lang in SUPPORTED_LANGUAGES:
            recognizer = PatternRecognizer(
                supported_entity=config["entity"],
                name=f"{config['entity'].lower()}_{lang}_recognizer",
                supported_language=lang,  # Singular: one language per recognizer
                patterns=config["patterns"]
            )
            _analyzer.registry.add_recognizer(recognizer)

    # Total: 5 entity types × 4 languages = 20 custom recognizers
    total_recognizers = len(patterns_config) * len(SUPPORTED_LANGUAGES)
    log.info(f"NER: Added {total_recognizers} custom recognizers ({len(patterns_config)} entities × {len(SUPPORTED_LANGUAGES)} languages)")


def mask_pii(text: str, language: str = "it", score_threshold: float = 0.35) -> Tuple[str, Dict]:
    """
    Mask PII entities in text using Presidio.

    Args:
        text: Raw text to analyze
        language: Language code (it, en, de, fr)
        score_threshold: Confidence threshold (lower = more aggressive masking)

    Returns:
        Tuple of (masked_text, anonymization_mapping)

    Example:
        >>> mask_pii("Email da Mario Rossi per Acme SA")
        ("Email da <PERSON> per <SWISS_LEGAL_ENTITY>", {...})
    """
    _init_presidio()

    if _analyzer is None:
        log.warning("NER: Presidio not available, returning original text (NO MASKING)")
        return text, {}

    try:
        from presidio_anonymizer.entities import OperatorConfig

        # Define entity types to detect
        entities = [
            # Standard PII
            "PERSON", "ORGANIZATION", "LOCATION",
            "EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN",
            "CREDIT_CARD", "DATE_TIME",
            # Custom Swiss/Italian entities
            "SWISS_LEGAL_ENTITY", "IT_FISCAL_CODE", "CH_IBAN",
            "NOTARIAL_REFERENCE", "CH_VAT_NUMBER"
        ]

        # Analyze text
        results = _analyzer.analyze(
            text=text,
            language=language,
            entities=entities,
            score_threshold=score_threshold
        )

        if not results:
            log.debug("NER: No PII entities detected")
            return text, {}

        # Count entities by type
        entity_counts = {}
        for result in results:
            entity_type = result.entity_type
            entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1

        log.info(f"NER: Detected {len(results)} entities: {entity_counts}")

        # Build numbered placeholders directly from analyzer results
        # We'll do manual replacement instead of using Presidio's template (which doesn't work with OperatorConfig)
        masked_text = text
        entity_counter = {}
        placeholder_map = {}

        # Sort entities by position (reverse order to preserve positions during replacement)
        sorted_results = sorted(results, key=lambda x: x.start, reverse=True)

        for result in sorted_results:
            entity_type = result.entity_type
            start = result.start
            end = result.end

            # Extract original value
            original_value = text[start:end]

            # Count occurrences of this entity type
            count = entity_counter.get(entity_type, 0) + 1
            entity_counter[entity_type] = count

            # Create numbered placeholder (e.g., <PERSON_1>, <CH_IBAN_1>)
            numbered_placeholder = f"<{entity_type}_{count}>"

            # Replace in text (reverse order so positions stay valid)
            masked_text = masked_text[:start] + numbered_placeholder + masked_text[end:]

            # Store mapping for de-anonymization (store in reverse order)
            # Using a list to preserve order: (placeholder, original_value)
            placeholder_map[numbered_placeholder] = original_value

        # Sort placeholders for cleaner logging (by entity type and number)
        sorted_placeholders = sorted(placeholder_map.keys())
        log.info(f"NER: Created {len(placeholder_map)} numbered placeholders: {sorted_placeholders}")

        # Return masked text with numbered placeholders and mapping
        return masked_text, {
            "placeholder_map": placeholder_map,
            "entity_counts": entity_counts,
            "original_length": len(text),
            "masked_length": len(masked_text)
        }

    except Exception as e:
        log.error(f"NER: Masking failed: {e}", exc_info=True)
        return text, {}


def unmask_pii(masked_text: str, anonymization_metadata: Dict) -> str:
    """
    Restore original PII entities in masked text.

    This performs a best-effort de-anonymization. If the cloud API has
    rephrased the text significantly, some entities may not be restored.

    Args:
        masked_text: Text with <ENTITY_TYPE> placeholders
        anonymization_metadata: Metadata from mask_pii()

    Returns:
        Text with original entities restored
    """
    if not anonymization_metadata:
        return masked_text

    try:
        # Use text-based replacement instead of position-based (works even after text rewriting)
        placeholder_map = anonymization_metadata.get("placeholder_map", {})
        if not placeholder_map:
            log.debug("NER: No placeholder map found, returning masked text")
            return masked_text

        # Replace each numbered placeholder with its original value
        result = masked_text
        restored_count = 0

        for placeholder, original_value in placeholder_map.items():
            if placeholder in result:
                result = result.replace(placeholder, original_value)
                restored_count += 1

        if restored_count > 0:
            log.debug(f"NER: De-anonymized text - restored {restored_count}/{len(placeholder_map)} placeholders ({len(masked_text)} → {len(result)} chars)")
        else:
            log.debug("NER: No placeholders found in text (Gemini may have removed them)")

        return result

    except Exception as e:
        log.warning(f"NER: De-anonymization failed ({e}), returning masked text")
        return masked_text


def is_presidio_available() -> bool:
    """Check if Presidio is installed and working."""
    _init_presidio()
    return _analyzer is not None
