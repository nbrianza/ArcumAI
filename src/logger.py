# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Se LOG_DIR è in config bene, altrimenti lo definiamo qui per sicurezza
try:
    from .config import LOG_DIR
except ImportError:
    LOG_DIR = Path("logs")

# Log level configurable via environment variable (default: INFO)
_LOG_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

# Crea cartella logs se non esiste
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _create_custom_logger(logger_name, filename):
    """
    Funzione factory per creare logger configurati con file diversi.
    """
    log_file_path = LOG_DIR / filename
    
    # Formatter comune
    log_format = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler Rotazione File (Ogni notte)
    file_handler = TimedRotatingFileHandler(
        log_file_path, 
        when='midnight', 
        interval=1, 
        backupCount=30, 
        encoding='utf-8'
    )
    file_handler.suffix = "%Y-%m-%d" 
    file_handler.setFormatter(log_format)

    # Handler Console (per vedere i log a video)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)

    # Setup Logger
    logger = logging.getLogger(logger_name)
    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False # Evita duplicati

    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger

# --- ESPORTIAMO I DUE LOGGER ---

# 1. Logger per INGESTION (main.py) - Nome 'log' per compatibilità
log = _create_custom_logger("ArcumIngestion", "ingestion.log")

# 2. Logger per SERVER/BRIDGE (main_nice.py)
server_log = _create_custom_logger("ArcumServer", "server.log")
