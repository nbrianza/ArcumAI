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


# --- IMPORT MODULI PROGETTO ---
from src.logger import log
from src.config import (
    DROP_DIR, 
    INBOX_DIR, 
    WATCH_EXTENSIONS, 
    WATCH_DEBOUNCE,
    WATCHER_LOG_FILE
)
# Ora usiamo la funzione potenziata direttamente da utils
from src.utils import sposta_file_con_struttura, pulisci_cartelle_vuote

# --- CONFIGURAZIONE LOGGER WATCHER (TIME BASED) ---
# Ruota ogni notte a mezzanotte, tiene 7 giorni di storico per il watcher
w_handler = TimedRotatingFileHandler(
    WATCHER_LOG_FILE, 
    when='midnight', 
    interval=1, 
    backupCount=7, 
    encoding='utf-8'
)
w_handler.suffix = "%Y-%m-%d" # Aggiunge la data ai file archiviati
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

            print(f"👀 Rilevato: {filename}   ", end="\r")
            self.last_event_time = time.time()
            self.needs_processing = True
        except Exception:
            pass 

def check_folder_health(folder_path: Path):
    try:
        if not folder_path.exists():
            return False, f"Cartella NON TROVATA: {folder_path}"
        if not os.access(folder_path, os.R_OK):
            return False, f"ACCESSO NEGATO (Lettura): {folder_path}"
        if not os.access(folder_path, os.W_OK):
            return False, f"ACCESSO NEGATO (Scrittura): {folder_path}"
        return True, "OK"
    except Exception as e:
        return False, f"Errore imprevisto check cartella: {e}"

def wait_for_drop_zone():
    first_error_shown = False
    while True:
        is_healthy, msg = check_folder_health(DROP_DIR)
        if is_healthy:
            if first_error_shown:
                log.info(f"✅ Connessione Drop Zone ristabilita!")
            return True
        else:
            if not first_error_shown:
                log.error(f"❌ ERRORE CRITICO DROP ZONE: {msg}")
                log.warning(f"⏳ In attesa che la cartella diventi accessibile (riprovo ogni 5s)...")
                first_error_shown = True
            time.sleep(5)

def process_drop_zone():
    """
    Scansiona Drop Zone ricorsivamente e sposta in Inbox usando utils robusto.
    """
    is_healthy, msg = check_folder_health(DROP_DIR)
    if not is_healthy:
        log.error(f"❌ PERDITA CONNESSIONE: {msg}")
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
                # ORA BASTA QUESTA RIGA: La funzione in utils gestisce retry e struttura
                sposta_file_con_struttura(file_path, DROP_DIR, INBOX_DIR)
                log.info(f"   -> Trasferito: {file_path.name}")
                files_moved_count += 1
            except Exception as e:
                # Se fallisce dopo i retry interni di utils, lo logghiamo qui
                log.error(f"   ❌ Fallito spostamento {file_path.name}: {e}")

        if files_moved_count > 0:
            pulisci_cartelle_vuote(DROP_DIR)

    except Exception as e:
        log.error(f"❌ Errore scansione Drop Zone: {e}")
        
    return files_moved_count

def run_watcher():
    log.info("------------------------------------------------")
    log.info("🔭 AVVIO ARCUM WATCHER (Clean Utils)")
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
        log.critical(f"❌ Impossibile avviare monitoraggio: {e}")
        return

    try:
        while True:
            time.sleep(1)
            if event_handler.needs_processing:
                time_since_last = time.time() - event_handler.last_event_time
                if time_since_last > WATCH_DEBOUNCE:
                    print(" " * 60, end="\r") 
                    log.info(f"🚀 Rilevata quiete. Avvio elaborazione...")
                    event_handler.needs_processing = False
                    
                    count = process_drop_zone()
                    
                    if count > 0:
                        try:
                            log.info(f"⚙️  Lancio main.py per {count} nuovi file...")
                            subprocess.run([sys.executable, "main.py"], check=True)
                            log.info("✅ Ciclo completato. Torno di vedetta.")
                        except subprocess.CalledProcessError:
                            log.error("❌ ERRORE: main.py ha restituito un errore.")
                        except Exception as e:
                            log.error(f"❌ ERRORE CRITICO: {e}")
                    else:
                        log.info("ℹ️ Nessun file valido spostato.")
                    print("\n")
    except KeyboardInterrupt:
        observer.stop()
        log.info("🛑 Watcher fermato dall'utente.")
    observer.join()

if __name__ == "__main__":
    run_watcher()