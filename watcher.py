import sys
import time
import shutil
import os
import subprocess
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# --- PROJECT MODULE IMPORTS ---
from src.logger import log
from src.config import (
    DROP_DIR,
    INBOX_DIR,
    WATCH_EXTENSIONS,
    WATCH_DEBOUNCE,
    WATCHER_LOG_FILE
)
# Using the enhanced function directly from utils
from src.utils import sposta_file_con_struttura, pulisci_cartelle_vuote

# --- WATCHER LOGGER CONFIGURATION (TIME BASED) ---
# Rotates every midnight, keeps 7 days of history for the watcher
w_handler = TimedRotatingFileHandler(
    WATCHER_LOG_FILE,
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
w_handler.suffix = "%Y-%m-%d" # Appends date to archived files
w_formatter = logging.Formatter('%(asctime)s - WATCHER - %(message)s', datefmt='%H:%M:%S')
w_handler.setFormatter(w_formatter)
log.addHandler(w_handler)
# ------------------------------------------------

class StagingHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_event_time = 0
        self.needs_processing = False

    def on_created(self, event): self._trigger(event)
    def on_modified(self, event): self._trigger(event)
    def on_moved(self, event): self._trigger(event)

    def _trigger(self, event):
        if event.is_directory: return
        try:
            filename = Path(event.src_path).name
            if filename.startswith("~$") or filename.startswith("."): return
            if Path(event.src_path).suffix not in WATCH_EXTENSIONS: return

            print(f"👀 Detected: {filename}   ", end="\r")
            self.last_event_time = time.time()
            self.needs_processing = True
        except Exception:
            pass

def check_folder_health(folder_path: Path):
    try:
        if not folder_path.exists():
            return False, f"Folder NOT FOUND: {folder_path}"
        if not os.access(folder_path, os.R_OK):
            return False, f"ACCESS DENIED (Read): {folder_path}"
        if not os.access(folder_path, os.W_OK):
            return False, f"ACCESS DENIED (Write): {folder_path}"
        return True, "OK"
    except Exception as e:
        return False, f"Unexpected error checking folder: {e}"

def wait_for_drop_zone():
    first_error_shown = False
    while True:
        is_healthy, msg = check_folder_health(DROP_DIR)
        if is_healthy:
            if first_error_shown:
                log.info(f"✅ Drop Zone connection restored!")
            return True
        else:
            if not first_error_shown:
                log.error(f"❌ CRITICAL DROP ZONE ERROR: {msg}")
                log.warning(f"⏳ Waiting for folder to become accessible (retrying every 5s)...")
                first_error_shown = True
            time.sleep(5)

def process_drop_zone():
    """
    Scans Drop Zone recursively and moves files to Inbox using robust utils.
    """
    is_healthy, msg = check_folder_health(DROP_DIR)
    if not is_healthy:
        log.error(f"❌ CONNECTION LOST: {msg}")
        return 0

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    files_moved_count = 0

    try:
        files_found = [
            f for f in DROP_DIR.rglob('*')
            if f.is_file() and f.suffix in WATCH_EXTENSIONS and not f.name.startswith("~$")
        ]

        for file_path in files_found:
            try:
                # This single line handles retry and structure preservation
                sposta_file_con_struttura(file_path, DROP_DIR, INBOX_DIR)
                log.info(f"   -> Transferred: {file_path.name}")
                files_moved_count += 1
            except Exception as e:
                # If it fails after internal retries in utils, log it here
                log.error(f"   ❌ Failed to move {file_path.name}: {e}")

        if files_moved_count > 0:
            pulisci_cartelle_vuote(DROP_DIR)

    except Exception as e:
        log.error(f"❌ Error scanning Drop Zone: {e}")

    return files_moved_count

def run_watcher():
    log.info("------------------------------------------------")
    log.info("🔭 STARTING ARCUM WATCHER (Clean Utils)")
    log.info("------------------------------------------------")

    wait_for_drop_zone()
    if not INBOX_DIR.exists(): INBOX_DIR.mkdir(parents=True)

    log.info(f"📂 Drop Zone: {DROP_DIR}")
    log.info(f"📂 System In: {INBOX_DIR}")
    log.info(f"📝 WatchLog:  {WATCHER_LOG_FILE}")

    event_handler = StagingHandler()
    observer = Observer()

    try:
        observer.schedule(event_handler, str(DROP_DIR), recursive=True)
        observer.start()
    except OSError as e:
        log.critical(f"❌ Unable to start monitoring: {e}")
        return

    try:
        while True:
            time.sleep(1)
            if event_handler.needs_processing:
                time_since_last = time.time() - event_handler.last_event_time
                if time_since_last > WATCH_DEBOUNCE:
                    print(" " * 60, end="\r")
                    log.info(f"🚀 Quiet period detected. Starting processing...")
                    event_handler.needs_processing = False

                    count = process_drop_zone()

                    if count > 0:
                        try:
                            log.info(f"⚙️  Launching ingest.py for {count} new files...")
                            subprocess.run([sys.executable, "ingest.py"], check=True)
                            log.info("✅ Cycle completed. Back on watch.")
                        except subprocess.CalledProcessError:
                            log.error("❌ ERROR: ingest.py returned an error.")
                        except Exception as e:
                            log.error(f"❌ CRITICAL ERROR: {e}")
                    else:
                        log.info("ℹ️ No valid files moved.")
                    print("\n")
    except KeyboardInterrupt:
        observer.stop()
        log.info("🛑 Watcher stopped by user.")
    observer.join()

if __name__ == "__main__":
    run_watcher()
