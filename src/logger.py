import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from .config import LOG_DIR

# Crea cartella logs se non esiste
LOG_DIR.mkdir(parents=True, exist_ok=True)

# File di log principale
LOG_FILE = LOG_DIR / "ingestion.log"

# --- 1. CONFIGURAZIONE FORMATTER ---
log_format = logging.Formatter(
    "%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# --- 2. HANDLER ROTAZIONE GIORNALIERA (TIME BASED) ---
# when='midnight': Ruota ogni notte a mezzanotte
# interval=1: Ogni 1 giorno
# backupCount=30: Mantiene lo storico degli ultimi 30 giorni (poi cancella i vecchi)
file_handler = TimedRotatingFileHandler(
    LOG_FILE, 
    when='midnight', 
    interval=1, 
    backupCount=30, 
    encoding='utf-8'
)
file_handler.suffix = "%Y-%m-%d" # Aggiunge la data al nome del file vecchio (es. ingestion.log.2023-10-27)
file_handler.setFormatter(log_format)

# --- 3. HANDLER CONSOLE (Per vedere i log a video) ---
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)

# --- 4. CREAZIONE LOGGER ARCUM AI ---
logger = logging.getLogger("ArcumAI")
logger.setLevel(logging.INFO)

# Evita duplicazione handler se lo script viene ricaricato
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Alias breve per usarlo negli altri file
log = logger