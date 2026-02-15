import pytesseract
import re
from pdf2image import convert_from_path
from pathlib import Path
from llama_index.core import Document
import extract_msg
from email import policy
from email.parser import BytesParser

# Import config for paths
from src.config import TESSERACT_CMD, POPPLER_PATH, OCR_ENABLED
from src.logger import log

# Configure Tesseract only if paths are valid
if OCR_ENABLED:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

class SmartPDFReader:
    """
    Hybrid PDF Reader v4 (High Precision Strategy):
    Dictionary threshold raised to 10% with extended vocabulary.
    """

    KNOWN_SCANNERS = [
        "scannerpro", "camscanner", "adobe scan", "lens",
        "iphone", "android", "samsung", "hp officejet",
        "canon", "xerox", "brother", "epson"
        # Note: "Quartz PDFContext" is too generic (Mac uses it for legitimate files),
        # so we can't include it here, we must rely on linguistic checks.
    ]

    # EXTENDED VOCABULARY (Business & Common)
    COMMON_WORDS = {
        # Italian
        "il", "lo", "la", "i", "gli", "le", "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
        "fattura", "data", "totale", "chf", "svizzera", "ticino", "pagamento", "iva", "euro", "via",
        "piazza", "tel", "telefono", "cellulare", "email", "e-mail", "spett", "egr", "signor",
        # English
        "the", "and", "of", "to", "in", "is", "for", "on", "invoice", "date", "total", "amount",
        "vat", "tax", "phone", "mobile", "mr", "mrs", "street", "road", "due", "payment",
        # German
        "der", "die", "das", "und", "in", "zu", "den", "von", "rechnung", "datum", "betrag", "mwst",
        "strasse", "tel", "herr", "frau", "total", "chf"
        # French
        "le", "la", "les", "un", "une", "de", "du", "des", "et", "ou", "à", "au", "par", "pour", "dans", "sur",
        "facture", "date", "montant", "tva", "paiement", "prix", "rue", "place", "tél", "téléphone",
        "monsieur", "madame", "mme", "société", "francs"
    }

    def _check_metadata_signatures(self, file_path: Path) -> bool:
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            meta = reader.metadata
            if not meta: return False

            producer = (meta.get("/Producer", "") or "").lower()
            creator = (meta.get("/Creator", "") or "").lower()
            combined_meta = f"{producer} {creator}"

            for scanner in self.KNOWN_SCANNERS:
                if scanner in combined_meta:
                    log.info(f"      🕵️ Scanner signature detected: '{scanner}' in metadata")
                    return True
            return False
        except Exception:
            return False

    def _is_text_meaningful(self, text: str) -> bool:
        text = text.lower()
        words = re.findall(r'\b\w+\b', text)

        total_words = len(words)
        if total_words < 10: return False

        valid_words = 0
        for word in words:
            if word in self.COMMON_WORDS:
                valid_words += 1

        ratio = valid_words / total_words

        # --- CRITICAL CHANGE: THRESHOLD RAISED TO 10% (0.10) ---
        # Your ScannerPro file scored 6.7% (0.067).
        # Now 0.067 < 0.10 -> Returns FALSE -> Forces OCR.
        if ratio < 0.10:
            log.info(f"      🔸 Rejected: Low linguistic score ({ratio:.1%}). Valid words: {valid_words}/{total_words}")
            return False

        return True

    def load_data(self, file_path: Path, extra_info=None):
        documents = []
        try:
            # 1. Check Metadata
            force_ocr_metadata = self._check_metadata_signatures(file_path)

            if not force_ocr_metadata:
                # 2. Check Native Text
                from llama_index.readers.file import PDFReader
                base_reader = PDFReader()
                base_docs = base_reader.load_data(file_path, extra_info=extra_info)
                full_text = "\n".join([d.text for d in base_docs])

                # Here the stricter check kicks in (10%)
                if self._is_text_meaningful(full_text):
                    log.info("   ✅ Valid native text (Metadata OK + Linguistics OK).")
                    return base_docs

                log.info("   ⚠️ Native text present but low quality (below 10% threshold).")
            else:
                log.info("   ⚠️ Scanner metadata detected -> Ignoring native text.")

            # --- OCR ZONE ---
            if not OCR_ENABLED:
                log.warning(f"   ❌ OCR required but not configured! Returning raw text.")
                if not force_ocr_metadata: return base_docs
                return []

            log.info(f"   🔍 OCR Tesseract in progress... (Please wait)")

            images = convert_from_path(str(file_path), dpi=200, poppler_path=POPPLER_PATH)
            ocr_text = ""
            for i, image in enumerate(images):
                page_text = pytesseract.image_to_string(image, lang='ita+eng+deu+fra')
                ocr_text += f"\n\n--- Page {i+1} (OCR) ---\n{page_text}"

            metadata = extra_info or {}
            metadata["ocr_applied"] = "true"
            documents.append(Document(text=ocr_text, metadata=metadata))
            return documents

        except Exception as e:
            log.error(f"❌ SmartPDFReader error on {file_path.name}: {e}", exc_info=True)
            return []

# --- Other classes (Outlook/EML) remain the same below ---
class MyOutlookReader:
    def load_data(self, file: Path, extra_info=None):
        msg = None
        try:
            msg = extract_msg.Message(str(file))
            metadata = extra_info or {}
            metadata["tipo"] = "email"
            metadata["soggetto"] = str(msg.subject) if msg.subject else "No Subject"
            metadata["mittente"] = str(msg.sender) if msg.sender else "Unknown"
            metadata["data_email"] = str(msg.date) if msg.date else ""

            msg_text = f"Subject: {metadata['soggetto']}\n" \
                       f"From: {metadata['mittente']}\n" \
                       f"Date: {metadata['data_email']}\n" \
                       f"To: {msg.to}\n\n" \
                       f"{msg.body}"
            return [Document(text=msg_text, metadata=metadata)]
        except Exception as e:
            log.warning(f"⚠️ MSG Error {file.name}: {e}")
            return []
        finally:
            if msg: msg.close()

class MyEmlReader:
    def load_data(self, file: Path, extra_info=None):
        try:
            with open(file, 'rb') as f:
                msg = BytesParser(policy=policy.default).parse(f)
            metadata = extra_info or {}
            metadata["tipo"] = "email"
            metadata["soggetto"] = str(msg['subject']) if msg['subject'] else "No Subject"
            metadata["mittente"] = str(msg['from']) if msg['from'] else "Unknown"
            metadata["data_email"] = str(msg['date']) if msg['date'] else ""

            body_text = ""
            body = msg.get_body(preferencelist=('plain', 'html'))
            if body:
                try: body_text = body.get_content()
                except: body_text = str(body)
            else:
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try: body_text += part.get_content()
                        except: pass

            full_text = f"Subject: {metadata['soggetto']}\n" \
                        f"From: {metadata['mittente']}\n" \
                        f"Date: {metadata['data_email']}\n\n" \
                        f"{body_text}"
            return [Document(text=full_text, metadata=metadata)]
        except Exception as e:
            log.warning(f"⚠️ EML Error {file.name}: {e}")
            return []
