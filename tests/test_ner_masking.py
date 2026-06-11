# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.


def test_is_presidio_available_returns_bool():
    from src.ai.ner_masking import is_presidio_available
    assert isinstance(is_presidio_available(), bool)


def test_mask_pii_empty_string():
    from src.ai.ner_masking import mask_pii
    masked, metadata = mask_pii("")
    assert masked == ""
    assert isinstance(metadata, dict)


def test_mask_pii_returns_text_and_dict():
    from src.ai.ner_masking import mask_pii
    masked, metadata = mask_pii("Buongiorno, come stai?")
    assert isinstance(masked, str)
    assert isinstance(metadata, dict)


def test_unmask_pii_empty_metadata_returns_original():
    from src.ai.ner_masking import unmask_pii
    text = "some masked text"
    assert unmask_pii(text, {}) == text


def test_unmask_pii_restores_placeholder():
    from src.ai.ner_masking import unmask_pii
    metadata = {"placeholder_map": {"__PII_abc12345__": "Mario Rossi"}}
    masked = "Caro __PII_abc12345__ cordiali saluti"
    assert unmask_pii(masked, metadata) == "Caro Mario Rossi cordiali saluti"


def test_unmask_pii_restores_multiple_placeholders():
    from src.ai.ner_masking import unmask_pii
    metadata = {
        "placeholder_map": {
            "__PII_aaa00001__": "Mario Rossi",
            "__PII_bbb00002__": "mario@example.com",
        }
    }
    masked = "__PII_aaa00001__ ha scritto a __PII_bbb00002__"
    result = unmask_pii(masked, metadata)
    assert result == "Mario Rossi ha scritto a mario@example.com"


def test_unmask_pii_missing_placeholder_left_unchanged():
    from src.ai.ner_masking import unmask_pii
    metadata = {"placeholder_map": {"__PII_aaa00000__": "Unknown"}}
    text = "No placeholder here"
    assert unmask_pii(text, metadata) == text


def test_mask_unmask_roundtrip():
    """mask+unmask should recover the original text when Presidio is available,
    or pass through unchanged when it is not."""
    from src.ai.ner_masking import mask_pii, unmask_pii, is_presidio_available
    original = "Contatto Mario Rossi all'indirizzo mario@example.com"
    masked, metadata = mask_pii(original)
    recovered = unmask_pii(masked, metadata)
    if is_presidio_available() and metadata:
        assert recovered == original
    else:
        assert masked == original
        assert metadata == {}
