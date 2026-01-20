import os
import json
from pathlib import Path
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
from llama_index.core.node_parser import SentenceSplitter

# --- 1. PROFILI HARDWARE ---
PROFILE = "LOW_RESOURCE" 

if PROFILE == "LOW_RESOURCE":
    LLM_MODEL_NAME = "llama3.2:1b"
    EMBED_MODEL_NAME = "BAAI/bge-m3"
    CONTEXT_WINDOW = 2048
    REQUEST_TIMEOUT = 300.0
    CHUNK_SIZE = 512
    CHUNK_OVERLAP = 64
    RETRIEVER_TOP_K = 5
    FINAL_TOP_K = 3
else: 
    LLM_MODEL_NAME = "llama3.3:70b"
    EMBED_MODEL_NAME = "BAAI/bge-m3"
    CONTEXT_WINDOW = 16384
    REQUEST_TIMEOUT = 120.0
    CHUNK_SIZE = 1024     
    CHUNK_OVERLAP = 128
    RETRIEVER_TOP_K = 20
    FINAL_TOP_K = 10

# --- 2. PATHS (Centralizzati) ---
BASE_DIR = Path(__file__).parent.parent.resolve()

INBOX_DIR = BASE_DIR / "data_nuovi"
ARCHIVE_DIR = BASE_DIR / "data_archivio"
ERROR_DIR = BASE_DIR / "data_error"
DUPLICATES_DIR = BASE_DIR / "data_duplicati"

# --- CORREZIONE QUI ---
CHROMA_PATH = BASE_DIR / "chroma_db"  # Nome usato da app.py
DB_PATH = CHROMA_PATH                 # Alias per compatibilità con main.py (se serve)
# ----------------------

BM25_PATH = BASE_DIR / "storage_bm25"
DROP_DIR = BASE_DIR / "input_utente"


# --- CONFIGURAZIONE LOGGING ---
LOG_DIR = BASE_DIR / "logs"
WATCHER_LOG_FILE = LOG_DIR / "watcher.log"
INGESTION_LOG_FILE = LOG_DIR / "ingestion.log"
LOG_RETENTION_DAYS = 30

# --- 3. SETTINGS DB ---
COLLECTION_NAME = "arcum_docs"

# --- 4. CONFIGURAZIONE AI ---
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"

def init_settings():
    Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)
    Settings.llm = Ollama(
        model=LLM_MODEL_NAME, 
        request_timeout=REQUEST_TIMEOUT,
        keep_alive="60m", 
        context_window=CONTEXT_WINDOW
    )
    Settings.text_splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE, 
        chunk_overlap=CHUNK_OVERLAP
    )

# --- 5. CONFIGURAZIONE OCR (Windows) ---
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\Program Files\Poppler\Library\bin"
OCR_ENABLED = Path(TESSERACT_CMD).exists() and Path(POPPLER_PATH).exists()

# --- 6. CONFIGURAZIONE WATCHER ---
WATCH_EXTENSIONS = {
    ".pdf", ".PDF", 
    ".msg", ".MSG", 
    ".eml", ".EML",
    ".txt", ".TXT", 
    ".xlsx", ".XLSX", 
    ".docx", ".DOCX"
}
WATCH_DEBOUNCE = 5

# --- 7. SICUREZZA & CARICAMENTO UTENTI ---
USERS_FILE = BASE_DIR / "users.json"

# --- 8. INTELLIGENZA DINAMICA (SYSTEM PROMPTS) ---
ROLE_PROMPTS = {
    "ADMIN": (
        "Sei un assistente amministrativo efficiente e cortese per uno studio fiduciario svizzero.\n"
        "Il tuo compito è: redigere bozze di email, verificare scadenze e cercare dati anagrafici.\n"
        "Tono: Formale ma gentile. Formatta le date chiaramente.\n"
        "Regola d'oro: Se non trovi l'informazione nei documenti, chiedi chiarimenti, non inventare."
    ),
    
    "LEGAL": (
        "Sei un esperto giurista svizzero (Canton Ticino).\n"
        "Il tuo compito è: Analisi contrattuale, pareri legali e ricerca di clausole critiche.\n"
        "Tono: Tecnico, rigoroso, distaccato.\n"
        "Regola d'oro: Cita sempre gli articoli di legge (CO/CC) se pertinenti. Evidenzia rischi legali."
    ),
    
    "EXECUTIVE": (
        "Sei il consulente strategico del Titolare dello studio.\n"
        "Il tuo compito è: Fornire sintesi decisionali rapide.\n"
        "Tono: Diretto, sintetico (Bullet points).\n"
        "Regola d'oro: Focus su soldi (importi), rischi e scadenze. Non dilungarti in dettagli tecnici."
    ),

    "COMMERCIALISTA": (
        "Sei il commercialista della azienda.\n"
        "Tono: Tecnico, rigoroso, distaccato.\n"        
        "Regola d'oro: Focus su aspetti commerciali."
    ),

    "TEST": (
        "Sei ArcumAI, un assistente IA documentale sicuro.\n"
        "Tono: Tecnico, rigoroso, distaccato.\n"
        "Rispondi alle domande basandoti ESCLUSIVAMENTE sui documenti forniti nel contesto."
    )
}

DEFAULT_SYSTEM_PROMPT = (
    "Sei ArcumAI, un assistente IA documentale sicuro.\n"
    "Tono: Tecnico, rigoroso, distaccato.\n"
    "Rispondi alle domande basandoti ESCLUSIVAMENTE sui documenti forniti nel contesto."
)

# Questa lista si aggiorna automaticamente se aggiungi chiavi a ROLE_PROMPTS sopra
VALID_ROLES = list(ROLE_PROMPTS.keys())