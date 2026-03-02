import sys
import re
from pathlib import Path
from pypdf import PdfReader

# --- CONFIGURATION ---
# Put the filename you want to analyze here
FILE_NAME = "testscan.pdf"

# Paths
BASE_DIR = Path(__file__).parent.resolve()
TARGET_FILE = BASE_DIR / "data_nuovi" / FILE_NAME

# Common words for linguistic testing
COMMON_WORDS = {
    "il", "lo", "la", "i", "gli", "le", "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "fattura", "data", "totale", "chf", "svizzera", "ticino", "pagamento", "iva", "euro", "via",
    "the", "and", "of", "to", "in", "is", "for", "on", "invoice", "date", "total", "amount",
    "der", "die", "das", "und", "in", "zu", "den", "von", "rechnung", "datum", "betrag", "mwst"
}

def analyze_text_quality(text):
    """Statistical analysis of extracted text."""
    text_len = len(text)
    if text_len == 0:
        return "EMPTY (0 chars)", 0.0, 0.0

    clean_text = text.lower()
    words = re.findall(r'\b\w+\b', clean_text)
    total_words = len(words)

    valid_count = sum(1 for w in words if w in COMMON_WORDS)
    dict_ratio = valid_count / total_words if total_words > 0 else 0
    alnum_chars = sum(c.isalnum() for c in text)
    clean_ratio = alnum_chars / text_len

    return {
        "length": text_len,
        "word_count": total_words,
        "valid_words": valid_count,
        "dict_match_ratio": dict_ratio,
        "clean_char_ratio": clean_ratio
    }

def main():
    print(f"\n🕵️  PDF DIAGNOSTIC ANALYSIS: {FILE_NAME}")
    print(f"    Path: {TARGET_FILE}")
    print("-" * 60)

    if not TARGET_FILE.exists():
        print(f"❌ ERROR: File does not exist in {TARGET_FILE.parent}")
        return

    try:
        reader = PdfReader(TARGET_FILE)

        # --- 1. METADATA ---
        print("\n[1] 🏷️  METADATA")
        meta = reader.metadata
        if meta:
            print(f"    Producer: {meta.get('/Producer', 'N/A')}")
            print(f"    Creator:  {meta.get('/Creator', 'N/A')}")
        else:
            print("    ❌  No metadata found.")

        # --- 2. TEXT CONTENT ---
        print("\n[2] 📄  TEXT CONTENT (Native Level)")
        full_text = ""
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                full_text += page_text

                count = len(page_text)
                print(f"    ✅ Page {i+1}: {count} characters found.")

                if count > 0:
                    print(f"    👇 --- RAW TEXT START (Page {i+1}) ---")
                    print(page_text)
                    print(f"    👆 --- RAW TEXT END (Page {i+1}) ---\n")
                else:
                    print("    ⚪ (Empty page or pure image)\n")

            except Exception as e:
                print(f"    Page {i+1}: Extraction error ({e})")

        # --- 3. QUALITATIVE ANALYSIS ---
        print("[3] 🧠  QUALITY DIAGNOSIS")
        stats = analyze_text_quality(full_text)

        if isinstance(stats, tuple):
            print("    🔴  EMPTY file (0 characters). OCR will definitely be triggered.")
            return

        print(f"    Total characters:      {stats['length']}")
        print(f"    Total words:           {stats['word_count']}")
        print(f"    Meaningful words:      {stats['valid_words']}")
        print(f"    Dictionary Ratio:      {stats['dict_match_ratio']:.1%} (New Target Threshold: >10%)")
        print(f"    Cleanliness Ratio:     {stats['clean_char_ratio']:.1%} (Target Threshold: >70%)")

        print("\n[4] 👨‍⚕️  VERDICT (Simulated with 10% threshold)")
        if stats['dict_match_ratio'] < 0.10:
            print("    🚨  Rejected: GARBAGE TEXT.")
            print("        Text exists but is unintelligible. OCR will be re-applied.")
        elif stats['clean_char_ratio'] < 0.70:
            print("    🚨  Rejected: TOO MANY SYMBOLS.")
            print("        OCR will be re-applied.")
        else:
            print("    ✅  Accepted: VALID TEXT.")

    except Exception as e:
        print(f"❌ Fatal error: {e}")

if __name__ == "__main__":
    main()
