# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Se LOG_DIR è in config bene, altrimenti lo definiamo qui per sicurezza
try:
    from .config import LOG_DIR, LOG_CLIENT_CONTENT
except ImportError:
    LOG_DIR = Path("logs")
    LOG_CLIENT_CONTENT = False

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


# --- CLIENT CONTENT IN LOGS (issue #39) ---
# Default logs never contain client content (subject, body, prompt, query, answer,
# file name, or exception text that can quote them). Client values may reach a log
# line only through content(), which returns them only when LOG_CLIENT_CONTENT is on.

# Extensions that may appear in default logs: the formats the readers and the loopback
# processor handle, plus common office, image and archive formats. Keep in sync with
# SafeExtensions in the add-in (Core/Loopback/AttachmentExtractor.cs).
SAFE_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx",
    ".msg", ".eml", ".txt", ".md", ".csv", ".rtf",
    ".odt", ".ods", ".odp",
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff",
    ".zip", ".gz",
})


def safe_ext(name) -> str:
    """Lower-cased file extension of `name` if it is in SAFE_EXTENSIONS, otherwise
    "other". Allow-list (owner decision on PR #67): an arbitrary suffix, including one
    with trailing whitespace or a line break, could itself carry client content."""
    suffix = os.path.splitext(str(name or ""))[1].lower()
    return suffix if suffix in SAFE_EXTENSIONS else "other"


def content(value, redacted: str | None = None) -> str:
    """Client data for a log line: repr(value) when LOG_CLIENT_CONTENT is on,
    otherwise `redacted`, or "<N chars>" when no summary is given.

    repr() keeps the value on one log line (newlines are escaped). The flag is read
    at call time, so it can be switched in tests without reloading the config.
    """
    if LOG_CLIENT_CONTENT:
        return repr(value)
    if redacted is not None:
        return redacted
    return f"<{len(str(value)) if value is not None else 0} chars>"


def warn_if_content_logging(logger) -> None:
    """Log one WARNING at startup when client content logging is on."""
    if LOG_CLIENT_CONTENT:
        logger.warning(
            "LOG_CLIENT_CONTENT is ON: server.log will contain client data (subjects, bodies, "
            "prompts, queries, file names). Development only. Turn it off and purge logs/ after use."
        )
