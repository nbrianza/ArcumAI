import sys
import re
from pathlib import Path
from pypdf import PdfReader

# --- CONFIGURAZIONE ---
# Metti qui il nome del file che vuoi analizzare
FILE_NAME = "testscan.pdf" 

# Percorsi
BASE_DIR = Path(__file__).parent.resolve()
TARGET_FILE = BASE_DIR / "data_nuovi" / FILE_NAME

# Parole comuni per il test linguistico
COMMON_WORDS = {
    "il", "lo", "la", "i", "gli", "le", "di", "a", "da", "in", "con", "su", "per", "tra", "fra", 
    "fattura", "data", "totale", "chf", "svizzera", "ticino", "pagamento", "iva", "euro", "via",
    "the", "and", "of", "to", "in", "is", "for", "on", "invoice", "date", "total", "amount",
    "der", "die", "das", "und", "in", "zu", "den", "von", "rechnung", "datum", "betrag", "mwst"
}

def analyze_text_quality(text):
    """Analisi statistica del testo estratto."""
    text_len = len(text)
    if text_len == 0:
        return "VUOTO (0 chars)", 0.0, 0.0

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
    print(f"\n🕵️  ANALISI DIAGNOSTICA PDF: {FILE_NAME}")
    print(f"    Path: {TARGET_FILE}")
    print("-" * 60)

    if not TARGET_FILE.exists():
        print(f"❌ ERRORE: Il file non esiste in {TARGET_FILE.parent}")
        return

    try:
        reader = PdfReader(TARGET_FILE)
        
        # --- 1. METADATI ---
        print("\n[1] 🏷️  METADATI")
        meta = reader.metadata
        if meta:
            print(f"    Producer: {meta.get('/Producer', 'N/A')}")
            print(f"    Creator:  {meta.get('/Creator', 'N/A')}")
        else:
            print("    ❌  Nessun metadato trovato.")

        # --- 2. CONTENUTO TESTUALE (MODIFICATO) ---
        print("\n[2] 📄  CONTENUTO TESTUALE (Livello 'Nativo')")
        full_text = ""
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                full_text += page_text
                
                count = len(page_text)
                print(f"    ✅ Pagina {i+1}: {count} caratteri trovati.")
                
                if count > 0:
                    print(f"    👇 --- INIZIO TESTO GREGGIO (Pagina {i+1}) ---")
                    print(page_text)
                    print(f"    👆 --- FINE TESTO GREGGIO (Pagina {i+1}) ---\n")
                else:
                    print("    ⚪ (Pagina vuota o pura immagine)\n")

            except Exception as e:
                print(f"    Pagina {i+1}: Errore estrazione ({e})")

        # --- 3. ANALISI QUALITATIVA ---
        print("[3] 🧠  DIAGNOSI QUALITÀ")
        stats = analyze_text_quality(full_text)
        
        if isinstance(stats, tuple):
            print("    🔴  File VUOTO (0 caratteri). L'OCR partirà sicuramente.")
            return

        print(f"    Caratteri totali:      {stats['length']}")
        print(f"    Parole totali:         {stats['word_count']}")
        print(f"    Parole 'sensate':      {stats['valid_words']}")
        print(f"    Ratio Dizionario:      {stats['dict_match_ratio']:.1%} (Nuova Soglia Target: >10%)")
        print(f"    Ratio Pulizia:         {stats['clean_char_ratio']:.1%} (Soglia Target: >70%)")

        print("\n[4] 👨‍⚕️  VERDETTO (Simulato con soglia 10%)")
        if stats['dict_match_ratio'] < 0.10:
            print("    🚨  Rifiutato: TESTO SPAZZATURA.")
            print("        Il testo c'è, ma è incomprensibile. Verrà rifatto l'OCR.")
        elif stats['clean_char_ratio'] < 0.70:
            print("    🚨  Rifiutato: TROPPI SIMBOLI.")
            print("        Verrà rifatto l'OCR.")
        else:
            print("    ✅  Accettato: TESTO VALIDO.")

    except Exception as e:
        print(f"❌ Errore fatale: {e}")

if __name__ == "__main__":
    main()