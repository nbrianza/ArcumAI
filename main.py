import sys
import os
import gc
import time
import shutil
from pathlib import Path

# LlamaIndex & Chroma
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.readers.file import DocxReader, PandasExcelReader
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from tqdm import tqdm  # Barra di caricamento

# Moduli Interni
from src.readers import SmartPDFReader, MyOutlookReader, MyEmlReader
from src.logger import log
from src.config import (
    INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, 
    DUPLICATES_DIR, DB_PATH, COLLECTION_NAME, BM25_PATH, 
    PROFILE, LLM_MODEL_NAME, WATCH_EXTENSIONS,
    init_settings
)
from src.utils import (
    calcola_hash_file, 
    sposta_file_con_struttura, 
    pulisci_cartelle_vuote,
    get_all_nodes_from_chroma
)

# --- LOCK FILE CONFIG (WINDOWS SAFE) ---
LOCK_FILE = Path(__file__).parent / "ingestion.lock"

def acquire_lock():
    try:
        with open(LOCK_FILE, 'x') as f:
            f.write(f"LOCKED by PID {os.getpid()}")
        return True
    except FileExistsError:
        return False

def release_lock():
    try:
        if LOCK_FILE.exists(): LOCK_FILE.unlink()
    except Exception as e:
        log.warning(f"⚠️ Impossibile rimuovere lock file: {e}")

init_settings()

def get_db_components():
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    chroma_collection = db_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    return index, chroma_collection

def read_and_chunk_file(file_path):
    """
    Legge il file, crea i nodi e LOGGA il successo.
    """
    try:
        docs = []
        ext = file_path.suffix.lower()
        
        # Selezione Reader
        if ext == ".pdf": docs = SmartPDFReader().load_data(file_path)
        elif ext == ".docx": docs = DocxReader().load_data(file_path)
        elif ext == ".xlsx": docs = PandasExcelReader(pandas_config={"header": 0}).load_data(file_path)
        elif ext == ".msg": docs = MyOutlookReader().load_data(file_path)
        elif ext == ".eml": docs = MyEmlReader().load_data(file_path)
        else: return None, "SKIP_EXT"

        if not docs: return None, "EMPTY"

        # LOG DETTAGLIATO LETTURA
        try: 
            rel_path = file_path.relative_to(INBOX_DIR)
        except: 
            rel_path = file_path.name
            
        log.info(f"   📖 LETTO: {rel_path} | Estensione: {ext} | Elementi: {len(docs)}")

        current_hash = calcola_hash_file(file_path)
        unique_id = str(rel_path).replace(os.sep, "_").replace(" ", "_")

        for doc in docs:
            doc.metadata["file_hash"] = current_hash
            doc.metadata["filename"] = file_path.name
            doc.metadata["file_path"] = str(rel_path)
            doc.metadata.pop("file_name", None)
            
            if hasattr(doc, "text") and doc.text and "Soggetto:" in doc.text[:100]:
                doc.metadata["tipo"] = "email"

        nodes = Settings.text_splitter.get_nodes_from_documents(docs)
        return nodes, current_hash

    except Exception as e:
        log.error(f"❌ Errore lettura {file_path.name}: {e}")
        return None, "ERROR"

def main():
    if not acquire_lock():
        log.warning("⏳ Ingestion già in corso (Lock file presente). Esco.")
        return

    try:
        log.info(f"🔄 AVVIO BATCH INGESTION ({PROFILE})")
        
        for p in [INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, DUPLICATES_DIR, BM25_PATH]:
            p.mkdir(parents=True, exist_ok=True)

        junk = ["Thumbs.db", "desktop.ini", ".DS_Store"]
        files = [f for f in INBOX_DIR.rglob('*') 
                 if f.is_file() and f.name not in junk and not f.name.startswith("._") and f.suffix in WATCH_EXTENSIONS]

        if not files:
            log.info("ℹ️ Nessun file da processare.")
            return

        index, collection = get_db_components()
        
        BATCH_SIZE = 10
        accumulated_nodes = []
        files_to_move_ok = []
        processed_count = 0

        log.info(f"🚀 Inizio elaborazione di {len(files)} file (Batch size: {BATCH_SIZE})...")
        
        for file_path in tqdm(files, desc="Elaborazione", unit="file"):
            
            # Check Duplicati
            current_hash = calcola_hash_file(file_path)
            existing = collection.get(where={"file_hash": current_hash}, limit=1)
            if existing and existing["ids"]:
                log.info(f"♻️ Duplicato saltato: {file_path.name}")
                sposta_file_con_struttura(file_path, INBOX_DIR, DUPLICATES_DIR)
                continue

            # Lettura
            nodes, status = read_and_chunk_file(file_path)
            
            if status == "ERROR" or status == "EMPTY":
                sposta_file_con_struttura(file_path, INBOX_DIR, ERROR_DIR)
                continue
            elif status == "SKIP_EXT":
                continue

            accumulated_nodes.extend(nodes)
            files_to_move_ok.append(file_path)

            # Scrittura Batch
            if len(files_to_move_ok) >= BATCH_SIZE:
                try:
                    # --- NUOVO FEEDBACK LOG ---
                    log.info(f"   ⏳ Inserimento Batch: Embedding e scrittura di {len(accumulated_nodes)} chunk nel DB...")
                    # --------------------------
                    
                    index.insert_nodes(accumulated_nodes) 
                    
                    for f in files_to_move_ok:
                        sposta_file_con_struttura(f, INBOX_DIR, ARCHIVE_DIR)
                    processed_count += len(files_to_move_ok)
                    
                    log.info(f"   ✅ Batch completato. {len(files_to_move_ok)} file archiviati.")
                    
                    accumulated_nodes = []
                    files_to_move_ok = []
                    gc.collect()
                except Exception as e:
                    log.error(f"🔥 Errore scrittura Batch DB: {e}")
                    for f in files_to_move_ok:
                        sposta_file_con_struttura(f, INBOX_DIR, ERROR_DIR)
                    accumulated_nodes = []
                    files_to_move_ok = []

        # Scrittura Residui Finali
        if accumulated_nodes:
            try:
                # --- NUOVO FEEDBACK LOG ---
                log.info(f"   ⏳ Inserimento Finale: Embedding e scrittura di {len(accumulated_nodes)} chunk nel DB...")
                # --------------------------

                index.insert_nodes(accumulated_nodes)
                for f in files_to_move_ok:
                    sposta_file_con_struttura(f, INBOX_DIR, ARCHIVE_DIR)
                processed_count += len(files_to_move_ok)
                
                log.info(f"   ✅ Batch finale completato.")

            except Exception as e:
                log.error(f"🔥 Errore scrittura Batch Finale: {e}")
                for f in files_to_move_ok:
                    sposta_file_con_struttura(f, INBOX_DIR, ERROR_DIR)

        if processed_count > 0:
            log.info("🧠 Aggiornamento Indice Ibrido (BM25)...")
            try:
                all_nodes = get_all_nodes_from_chroma(collection)
                if all_nodes:
                    bm25 = BM25Retriever.from_defaults(
                        nodes=all_nodes, 
                        similarity_top_k=5, 
                        language="italian"
                    )
                    bm25.persist(str(BM25_PATH))
            except Exception as e: log.error(f"❌ Errore BM25: {e}")

        pulisci_cartelle_vuote(INBOX_DIR)
        log.info(f"🏁 Completato: {processed_count}/{len(files)} file archiviati.")

    except Exception as e:
        log.critical(f"🔥 CRASH MAIN: {e}", exc_info=True)
    finally:
        release_lock()

if __name__ == "__main__":
    main()