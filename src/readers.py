import pytesseract
import re
from pdf2image import convert_from_path
from pathlib import Path
from llama_index.core import Document
import extract_msg
from email import policy
from email.parser import BytesParser

# Import config per i percorsi
from src.config import TESSERACT_CMD, POPPLER_PATH, OCR_ENABLED
from src.logger import log

# Configura Tesseract sulo se i path sono validi
if OCR_ENABLED:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

class SmartPDFReader:
    """
    Lettore PDF Ibrido v4 (High Precision Strategy):
    Soglia dizionario alzata al 10% e vocabolario esteso.
    """

    KNOWN_SCANNERS = [
        "scannerpro", "camscanner", "adobe scan", "lens", 
        "iphone", "android", "samsung", "hp officejet", 
        "canon", "xerox", "brother", "epson"
        # Nota: "Quartz PDFContext" è troppo generico (lo usa anche il Mac per salvare file legittimi),
        # quindi non possiamo metterlo qui, dobbiamo affidarci al controllo linguistico.
    ]

    # VOCABOLARIO ESTESO (Business & Comune)
    COMMON_WORDS = {
        # Italiano
        "il", "lo", "la", "i", "gli", "le", "di", "a", "da", "in", "con", "su", "per", "tra", "fra", 
        "fattura", "data", "totale", "chf", "svizzera", "ticino", "pagamento", "iva", "euro", "via", 
        "piazza", "tel", "telefono", "cellulare", "email", "e-mail", "spett", "egr", "signor",
        # Inglese
        "the", "and", "of", "to", "in", "is", "for", "on", "invoice", "date", "total", "amount", 
        "vat", "tax", "phone", "mobile", "mr", "mrs", "street", "road", "due", "payment",
        # Tedesco
        "der", "die", "das", "und", "in", "zu", "den", "von", "rechnung", "datum", "betrag", "mwst",
        "strasse", "tel", "herr", "frau", "total", "chf"
        # Francese
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
                    log.info(f"      🕵️ Rilevata firma scanner: '{scanner}' in metadata")
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
        
        # --- MODIFICA CRITICA: SOGLIA ALZATA AL 10% (0.10) ---
        # Il tuo file ScannerPro faceva 6.7% (0.067).
        # Ora 0.067 < 0.10 -> Ritorna FALSE -> Forza OCR.
        if ratio < 0.10: 
            log.info(f"      🔸 Rifiutato: Punteggio linguistico basso ({ratio:.1%}). Parole valide: {valid_words}/{total_words}")
            return False
        
        return True

    def load_data(self, file_path: Path, extra_info=None):
        documents = []
        try:
            # 1. Check Metadati
            force_ocr_metadata = self._check_metadata_signatures(file_path)

            if not force_ocr_metadata:
                # 2. Check Testo Nativo
                from llama_index.readers.file import PDFReader
                base_reader = PDFReader()
                base_docs = base_reader.load_data(file_path, extra_info=extra_info)
                full_text = "\n".join([d.text for d in base_docs])
                
                # Qui scatta il controllo più severo (10%)
                if self._is_text_meaningful(full_text):
                    log.info("   ✅ Testo nativo valido (Metadati OK + Linguistica OK).")
                    return base_docs
                
                log.info("   ⚠️ Testo nativo presente ma di bassa qualità (sotto soglia 10%).")
            else:
                log.info("   ⚠️ Metadati Scanner rilevati -> Ignoro testo nativo.")

            # --- ZONA OCR ---
            if not OCR_ENABLED:
                log.warning(f"   ❌ OCR richiesto ma non configurato! Ritorno testo grezzo.")
                if not force_ocr_metadata: return base_docs
                return [] 

            log.info(f"   🔍 OCR Tesseract in corso... (Attendere)")
            
            images = convert_from_path(str(file_path), dpi=200, poppler_path=POPPLER_PATH)
            ocr_text = ""
            for i, image in enumerate(images):
                page_text = pytesseract.image_to_string(image, lang='ita+eng+deu+fra')
                ocr_text += f"\n\n--- Pagina {i+1} (OCR) ---\n{page_text}"

            metadata = extra_info or {}
            metadata["ocr_applied"] = "true"
            documents.append(Document(text=ocr_text, metadata=metadata))
            return documents

        except Exception as e:
            log.error(f"❌ Errore SmartPDFReader su {file_path.name}: {e}")
            return []

# --- Le altre classi (Outlook/EML) rimangono uguali sotto ---
class MyOutlookReader:
    # ... (codice esistente) ...
    def load_data(self, file: Path, extra_info=None):
        msg = None
        try:
            msg = extract_msg.Message(str(file))
            metadata = extra_info or {}
            metadata["tipo"] = "email"
            metadata["soggetto"] = str(msg.subject) if msg.subject else "Nessun Oggetto"
            metadata["mittente"] = str(msg.sender) if msg.sender else "Sconosciuto"
            metadata["data_email"] = str(msg.date) if msg.date else ""
            
            msg_text = f"Soggetto: {metadata['soggetto']}\n" \
                       f"Da: {metadata['mittente']}\n" \
                       f"Data: {metadata['data_email']}\n" \
                       f"A: {msg.to}\n\n" \
                       f"{msg.body}"
            return [Document(text=msg_text, metadata=metadata)]
        except Exception as e:
            log.warning(f"⚠️ Errore MSG {file.name}: {e}")
            return []
        finally:
            if msg: msg.close()

class MyEmlReader:
    # ... (codice esistente) ...
    def load_data(self, file: Path, extra_info=None):
        try:
            with open(file, 'rb') as f:
                msg = BytesParser(policy=policy.default).parse(f)
            metadata = extra_info or {}
            metadata["tipo"] = "email"
            metadata["soggetto"] = str(msg['subject']) if msg['subject'] else "Nessun Oggetto"
            metadata["mittente"] = str(msg['from']) if msg['from'] else "Sconosciuto"
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

            full_text = f"Soggetto: {metadata['soggetto']}\n" \
                        f"Da: {metadata['mittente']}\n" \
                        f"Data: {metadata['data_email']}\n\n" \
                        f"{body_text}"
            return [Document(text=full_text, metadata=metadata)]
        except Exception as e:
            log.warning(f"⚠️ Errore EML {file.name}: {e}")
            return []