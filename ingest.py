import sys
import os
import gc
import time
import shutil
from pathlib import Path

# LlamaIndex & Chroma
from llama_index.core import VectorStoreIndex, StorageContext, Settings, Document
from llama_index.readers.file import DocxReader, PandasExcelReader
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from tqdm import tqdm  # Progress bar

# Internal Modules
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
        log.warning(f"⚠️ Unable to remove lock file: {e}")

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
    Reads the file, creates nodes and logs the result.
    """
    try:
        docs = []
        ext = file_path.suffix.lower()

        # Reader selection
        if ext == ".pdf": docs = SmartPDFReader().load_data(file_path)
        elif ext == ".docx": docs = DocxReader().load_data(file_path)
        elif ext == ".xlsx": docs = PandasExcelReader(pandas_config={"header": 0}).load_data(file_path)
        elif ext == ".msg": docs = MyOutlookReader().load_data(file_path)
        elif ext == ".eml": docs = MyEmlReader().load_data(file_path)
        elif ext in [".txt", ".md"]:
            # Plain text and Markdown files
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            if text.strip():  # Only if not empty
                docs = [Document(text=text)]
        else: return None, "SKIP_EXT"

        if not docs:
            log.warning(f"   ⚠️ No content extracted from: {file_path.name} (ext: {ext})")
            return None, "EMPTY"

        # DETAILED READ LOG
        try:
            rel_path = file_path.relative_to(INBOX_DIR)
        except:
            rel_path = file_path.name

        log.info(f"   📖 READ: {rel_path} | Extension: {ext} | Elements: {len(docs)}")

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
        log.error(f"❌ Error reading {file_path.name}: {e}")
        return None, "ERROR"

def main():
    if not acquire_lock():
        log.warning("⏳ Ingestion already running (Lock file present). Exiting.")
        return

    try:
        log.info(f"🔄 STARTING BATCH INGESTION ({PROFILE})")

        for p in [INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, DUPLICATES_DIR, BM25_PATH]:
            p.mkdir(parents=True, exist_ok=True)

        junk = ["Thumbs.db", "desktop.ini", ".DS_Store"]
        files = [f for f in INBOX_DIR.rglob('*')
                 if f.is_file() and f.name not in junk and not f.name.startswith("._") and f.suffix in WATCH_EXTENSIONS]

        if not files:
            log.info("ℹ️ No files to process.")
            return

        index, collection = get_db_components()

        BATCH_SIZE = 10
        accumulated_nodes = []
        files_to_move_ok = []
        processed_count = 0
        failed_files = []

        log.info(f"🚀 Starting processing of {len(files)} files (Batch size: {BATCH_SIZE})...")

        for file_path in tqdm(files, desc="Processing", unit="file"):

            # Check Duplicates
            current_hash = calcola_hash_file(file_path)
            existing = collection.get(where={"file_hash": current_hash}, limit=1)
            if existing and existing["ids"]:
                log.info(f"♻️ Duplicate skipped: {file_path.name}")
                sposta_file_con_struttura(file_path, INBOX_DIR, DUPLICATES_DIR)
                continue

            # Reading
            nodes, status = read_and_chunk_file(file_path)

            if status == "ERROR" or status == "EMPTY":
                failed_files.append((file_path.name, status))
                sposta_file_con_struttura(file_path, INBOX_DIR, ERROR_DIR)
                continue
            elif status == "SKIP_EXT":
                continue

            accumulated_nodes.extend(nodes)
            files_to_move_ok.append(file_path)

            # Batch Write
            if len(files_to_move_ok) >= BATCH_SIZE:
                try:
                    log.info(f"   ⏳ Batch Insert: Embedding and writing {len(accumulated_nodes)} chunks to DB...")

                    index.insert_nodes(accumulated_nodes)

                    for f in files_to_move_ok:
                        sposta_file_con_struttura(f, INBOX_DIR, ARCHIVE_DIR)
                    processed_count += len(files_to_move_ok)

                    log.info(f"   ✅ Batch completed. {len(files_to_move_ok)} files archived.")

                    accumulated_nodes = []
                    files_to_move_ok = []
                    gc.collect()
                except Exception as e:
                    log.error(f"🔥 Error writing Batch to DB: {e}")
                    for f in files_to_move_ok:
                        sposta_file_con_struttura(f, INBOX_DIR, ERROR_DIR)
                    accumulated_nodes = []
                    files_to_move_ok = []

        # Write Remaining
        if accumulated_nodes:
            try:
                log.info(f"   ⏳ Final Insert: Embedding and writing {len(accumulated_nodes)} chunks to DB...")

                index.insert_nodes(accumulated_nodes)
                for f in files_to_move_ok:
                    sposta_file_con_struttura(f, INBOX_DIR, ARCHIVE_DIR)
                processed_count += len(files_to_move_ok)

                log.info(f"   ✅ Final batch completed.")

            except Exception as e:
                log.error(f"🔥 Error writing Final Batch to DB: {e}")
                for f in files_to_move_ok:
                    sposta_file_con_struttura(f, INBOX_DIR, ERROR_DIR)

        if processed_count > 0:
            log.info("🧠 Updating Hybrid Index (BM25)...")
            try:
                all_nodes = get_all_nodes_from_chroma(collection)
                if all_nodes:
                    bm25 = BM25Retriever.from_defaults(
                        nodes=all_nodes,
                        similarity_top_k=5,
                        language="italian"
                    )
                    bm25.persist(str(BM25_PATH))
            except Exception as e: log.error(f"❌ BM25 Error: {e}")

        pulisci_cartelle_vuote(INBOX_DIR)
        log.info(f"🏁 Completed: {processed_count}/{len(files)} files archived.")

        if failed_files:
            log.warning(f"⚠️ {len(failed_files)} files failed:")
            for fname, reason in failed_files:
                log.warning(f"   - {fname} ({reason})")
            log.warning(f"   Failed files have been moved to: {ERROR_DIR}")

    except Exception as e:
        log.critical(f"🔥 CRASH MAIN: {e}", exc_info=True)
    finally:
        release_lock()

if __name__ == "__main__":
    main()
