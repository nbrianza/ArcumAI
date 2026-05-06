# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import os
import json
from pathlib import Path
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
from llama_index.core.node_parser import SentenceSplitter

# --- 1. HARDWARE PROFILES ---
# Profile selects defaults; individual values can be overridden via .env
PROFILE = os.getenv("PROFILE", "LOW_RESOURCE")

if PROFILE == "HIGH_RESOURCE":
    _defaults = {
        "LLM_MODEL": "llama3.3:70b", "EMBED_MODEL": "BAAI/bge-m3",
        "CONTEXT_WINDOW": "16384", "REQUEST_TIMEOUT": "120.0",
        "CHUNK_SIZE": "1024", "CHUNK_OVERLAP": "128",
        "RETRIEVER_TOP_K": "20", "FINAL_TOP_K": "10",
    }
else:  # LOW_RESOURCE (default)
    _defaults = {
        "LLM_MODEL": "llama3.2:3b", "EMBED_MODEL": "BAAI/bge-m3",
        "CONTEXT_WINDOW": "4096", "REQUEST_TIMEOUT": "3600.0",
        "CHUNK_SIZE": "512", "CHUNK_OVERLAP": "64",
        "RETRIEVER_TOP_K": "10", "FINAL_TOP_K": "5",
    }

LLM_MODEL_NAME = os.getenv("LLM_MODEL", _defaults["LLM_MODEL"])
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", _defaults["EMBED_MODEL"])
CONTEXT_WINDOW = int(os.getenv("CONTEXT_WINDOW", _defaults["CONTEXT_WINDOW"]))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", _defaults["REQUEST_TIMEOUT"]))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", _defaults["CHUNK_SIZE"]))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", _defaults["CHUNK_OVERLAP"]))
RETRIEVER_TOP_K = int(os.getenv("RETRIEVER_TOP_K", _defaults["RETRIEVER_TOP_K"]))
FINAL_TOP_K = int(os.getenv("FINAL_TOP_K", _defaults["FINAL_TOP_K"]))

# --- 2. PATHS (Centralized) ---
BASE_DIR = Path(__file__).parent.parent.resolve()

INBOX_DIR = BASE_DIR / "data_nuovi"
ARCHIVE_DIR = BASE_DIR / "data_archivio"
ERROR_DIR = BASE_DIR / "data_error"
DUPLICATES_DIR = BASE_DIR / "data_duplicati"

# --- FIX HERE ---
CHROMA_PATH = BASE_DIR / "chroma_db"  # Name used by app.py
DB_PATH = CHROMA_PATH                 # Alias for compatibility with main.py
# ----------------------

BM25_PATH = BASE_DIR / "storage_bm25"
DROP_DIR = BASE_DIR / "input_utente"


# --- LOGGING CONFIGURATION ---
LOG_DIR = BASE_DIR / "logs"
WATCHER_LOG_FILE = LOG_DIR / "watcher.log"
INGESTION_LOG_FILE = LOG_DIR / "ingestion.log"
LOG_RETENTION_DAYS = 30

# --- 3. DB SETTINGS ---
COLLECTION_NAME = "arcum_docs"

# --- 4. AI CONFIGURATION ---
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

# --- 5. OCR CONFIGURATION (Windows) ---
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\Program Files\Poppler\Library\bin"
OCR_ENABLED = Path(TESSERACT_CMD).exists() and Path(POPPLER_PATH).exists()
#OCR_ENABLED = False # <--- Force OFF for massive ingestion without OCR

# --- 6. WATCHER CONFIGURATION ---
WATCH_EXTENSIONS = {
    ".pdf", ".PDF",
    ".msg", ".MSG",
    ".eml", ".EML",
    ".txt", ".TXT",
    ".xlsx", ".XLSX",
    ".docx", ".DOCX"
}
WATCH_DEBOUNCE = 5

# --- 7. SECURITY & USER LOADING ---
USERS_FILE = BASE_DIR / "users.json"

# --- 8. PROMPT OPTIMIZATION & PRIVACY ---
PROMPT_OPTIMIZATION = os.getenv("PROMPT_OPTIMIZATION", "local")  # "local" | "gemini" | "off"
ENABLE_NER_MASKING = os.getenv("ENABLE_NER_MASKING", "true").lower() == "true"
NER_SCORE_THRESHOLD = float(os.getenv("NER_SCORE_THRESHOLD", "0.35"))  # Low threshold for privacy
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "60.0"))

# --- 9. SERVER-PUSHED CLIENT CONFIG ---
# Sent to clients during the client/identify handshake, keyed by client_type.
# Each client type has its own set of env vars — add new types here as needed.

# VSTO Outlook plugin
VSTO_MAX_ATTACHMENT_MB       = int(os.getenv("VSTO_MAX_ATTACHMENT_MB", "25"))
VSTO_MAX_TOTAL_MB            = int(os.getenv("VSTO_MAX_TOTAL_MB", "50"))
VSTO_MAX_PAYLOAD_MB          = int(os.getenv("VSTO_MAX_PAYLOAD_MB", "30"))
VSTO_ARCUMAI_EMAIL           = os.getenv("VSTO_ARCUMAI_EMAIL", "assistant@arcumai.ch")
VSTO_ARCUMAI_DISPLAY_NAME    = os.getenv("VSTO_ARCUMAI_DISPLAY_NAME", "ArcumAI Assistant")
VSTO_LOOPBACK_TIMEOUT_MS     = int(os.getenv("VSTO_LOOPBACK_TIMEOUT_MS", "3600000"))
VSTO_ENABLE_VIRTUAL_LOOPBACK = os.getenv("VSTO_ENABLE_VIRTUAL_LOOPBACK", "true").lower() == "true"
VSTO_SHOW_NOTIFICATION       = os.getenv("VSTO_SHOW_NOTIFICATION", "true").lower() == "true"

# --- 10a. RATE LIMITING ---
RATE_LIMIT_MESSAGES    = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))     # max messages per window
RATE_LIMIT_WINDOW      = int(os.getenv("RATE_LIMIT_WINDOW", "60"))       # window in seconds
RATE_LIMIT_STALE_TTL   = int(os.getenv("RATE_LIMIT_STALE_TTL", "3600"))  # remove idle users after (seconds)
RATE_LIMIT_CLEANUP_INT = int(os.getenv("RATE_LIMIT_CLEANUP_INT", "300")) # cleanup interval (seconds)

# WebSocket auth rate limiting (per IP)
WS_AUTH_MAX_ATTEMPTS   = int(os.getenv("WS_AUTH_MAX_ATTEMPTS", "5"))     # max failed attempts per window
WS_AUTH_WINDOW         = int(os.getenv("WS_AUTH_WINDOW", "60"))          # window in seconds
WS_RECEIVE_TIMEOUT     = int(os.getenv("WS_RECEIVE_TIMEOUT", "120"))     # inactivity timeout in seconds (4× heartbeat)
WS_API_KEY             = os.getenv("WS_API_KEY", "")                    # shared secret for plugin auth; empty = disabled

# --- 10b. LOOPBACK QUEUE & RESILIENCE ---
LOOPBACK_MAX_CONCURRENT  = int(os.getenv("LOOPBACK_MAX_CONCURRENT", "3"))
PENDING_RESULT_TTL_HOURS = int(os.getenv("PENDING_RESULT_TTL_HOURS", "48"))
PENDING_RESULTS_DIR      = os.getenv("PENDING_RESULTS_DIR", "temp/pending_results")

# --- 10. DYNAMIC INTELLIGENCE (SYSTEM PROMPTS) ---

CUSTOM_CONTEXT_TEMPLATE = (
        "Di seguito sono riportate le informazioni di contesto recuperate dai documenti:\n"
        "---------------------\n"
        "{context_str}\n"
        "---------------------\n"
        "Usa le informazioni qui sopra per rispondere alla domanda dell'utente.\n"
        "TUTTAVIA, se la domanda è di cultura generale (es. matematica, saluti, domande generiche) "
        "e il contesto non è rilevante, IGNORA il contesto e rispondi usando la tua conoscenza interna."
    )

DEFAULT_SYSTEM_PROMPT = ("Sei Arcum AI, un assistente legale, notarile e fiduciario intelligente.\n"
        "ISTRUZIONI:\n"
        "1. Ti verranno forniti dei frammenti di contesto (leggi, regolamenti, contratti e simili) qui sotto.\n"
        "2. Usa PRIMA DI TUTTO il contesto per rispondere alle domande dell'utente.\n"
        "3. Cita sempre le fonti se usi il contesto.\n"
        "4. ECCEZIONE: Se l'utente ti fa una domanda di cultura generale, matematica semplice, saluti o chiacchiere (che non richiedono documenti legali, notarili o simili, specifici), RISPONDI DIRETTAMENTE usando la tua conoscenza, senza forzare l'uso del contesto.\n"
        "5. Se la domanda richiede documenti ma non li trovi nel contesto, dì chiaramente che non hai le informazioni.\n"
)

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

    "DEFAULT": (DEFAULT_SYSTEM_PROMPT)
}


# This list auto-updates if you add keys to ROLE_PROMPTS above
VALID_ROLES = list(ROLE_PROMPTS.keys())
