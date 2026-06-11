# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
from pathlib import Path


# --- calcola_hash_file (src/utils.py) ---

def test_hash_same_content_produces_same_hash(tmp_path):
    from src.utils import calcola_hash_file
    f = tmp_path / "file.txt"
    f.write_bytes(b"hello world")
    assert calcola_hash_file(f) == calcola_hash_file(f)


def test_hash_is_32_char_md5_hex(tmp_path):
    from src.utils import calcola_hash_file
    f = tmp_path / "file.txt"
    f.write_bytes(b"some content")
    h = calcola_hash_file(f)
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_different_content_different_hash(tmp_path):
    from src.utils import calcola_hash_file
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"content A")
    f2.write_bytes(b"content B")
    assert calcola_hash_file(f1) != calcola_hash_file(f2)


def test_hash_missing_file_returns_error_sentinel(tmp_path):
    from src.utils import calcola_hash_file
    assert calcola_hash_file(tmp_path / "nonexistent.txt") == "hash_error"


# --- SmartPDFReader (src/readers.py) ---

def test_smart_pdf_reader_missing_file_returns_empty():
    from src.readers import SmartPDFReader
    reader = SmartPDFReader()
    result = reader.load_data(Path("definitely_not_a_real_file.pdf"))
    assert result == []


def test_is_text_meaningful_accepts_rich_italian_text():
    from src.readers import SmartPDFReader
    reader = SmartPDFReader()
    text = (
        "La fattura è stata emessa il giorno indicato. "
        "Il totale comprende l'IVA. Il pagamento deve essere "
        "effettuato entro 30 giorni dalla data indicata."
    )
    assert reader._is_text_meaningful(text) is True


def test_is_text_meaningful_rejects_short_text():
    from src.readers import SmartPDFReader
    reader = SmartPDFReader()
    assert reader._is_text_meaningful("hello world") is False


def test_is_text_meaningful_rejects_garbage():
    from src.readers import SmartPDFReader
    reader = SmartPDFReader()
    garbage = "xyz123 qwerty asdf poiu lkjh mnbv xcvb 1234 5678 9012 3456"
    assert reader._is_text_meaningful(garbage) is False


# --- _validate_file_content (src/ui/footer.py) ---

def test_validate_accepts_valid_pdf_magic_bytes():
    from src.ui.footer import _validate_file_content
    assert _validate_file_content(b'%PDF-1.4 rest of file', '.pdf') is True


def test_validate_rejects_wrong_bytes_as_pdf():
    from src.ui.footer import _validate_file_content
    assert _validate_file_content(b'not a pdf at all', '.pdf') is False


def test_validate_accepts_valid_utf8_txt():
    from src.ui.footer import _validate_file_content
    assert _validate_file_content(b'Hello, world!', '.txt') is True


def test_validate_accepts_valid_utf8_md():
    from src.ui.footer import _validate_file_content
    assert _validate_file_content(b'# Heading\n\nContent here.', '.md') is True


def test_validate_rejects_binary_as_txt():
    from src.ui.footer import _validate_file_content
    assert _validate_file_content(b'\x80\x81\x82\x83', '.txt') is False
