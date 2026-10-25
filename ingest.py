# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import sys
import os
import gc
import time
import shutil
from pathlib import Path

# LlamaIndex & Chroma
from llama_index.core import VectorStoreIndex, StorageContext, Settings, Document
from llama_index.readers.file import DocxReader, PandasExcelReader
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from tqdm import tqdm  # Progress bar

# Internal Modules
from src.readers import SmartPDFReader, MyOutlookReader, MyEmlReader
import src.logger as _logger
from src.logger import log, content
from src.config import (
    INBOX_DIR, ARCHIVE_DIR, ERROR_DIR,
    DUPLICATES_DIR, DB_PATH, COLLECTION_NAME, BM25_PATH,
    PROFILE, LLM_MODEL_NAME, WATCH_EXTENSIONS,
    init_settings
)
from src.utils import (
    calcola_hash_file,
    file_ref,
    sposta_file_con_struttura,
    pulisci_cartelle_vuote,
    get_all_nodes_from_chroma,
    rebuild_bm25_index
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
    current_hash = None
    try:
        # Hash first: it identifies the file in every log line (issue #52)
        current_hash = calcola_hash_file(file_path)
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
            log.warning(f"   ⚠️ No content extracted from: {file_ref(file_path, current_hash)}")
            return None, "EMPTY"

        # DETAILED READ LOG
        try:
            rel_path = file_path.relative_to(INBOX_DIR)
        except ValueError:
            rel_path = file_path.name

        log.info(f"   📖 READ: {file_ref(file_path, current_hash, shown=str(rel_path))} | Elements: {len(docs)}")

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
        log.error(f"❌ Error reading {file_ref(file_path, current_hash)}: {content(str(e), type(e).__name__)}")
        return None, "ERROR"

def main():
    """Runs one batch ingestion, or only the BM25 rebuild with --rebuild-bm25.

    Returns 1 when the requested BM25 rebuild did not run or failed, else None.
    """
    rebuild_only = "--rebuild-bm25" in sys.argv[1:]
    if not acquire_lock():
        log.warning("⏳ Ingestion already running (Lock file present). Exiting.")
        return 1 if rebuild_only else None

    try:
        # Manual one-time rebuild of the BM25 index (#71). Unknown arguments are
        # ignored, so callers that pass their own argv still run an ingestion.
        if rebuild_only:
            log.info("🧠 Rebuilding Hybrid Index (BM25) from the database...")
            try:
                BM25_PATH.mkdir(parents=True, exist_ok=True)
                _index, collection = get_db_components()
                rebuild_bm25_index(collection, BM25_PATH, nodes_loader=get_all_nodes_from_chroma)
            except Exception as e:
                log.error(f"❌ BM25 Error: {content(str(e), type(e).__name__)}")
                return 1
            return None

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
                log.info(f"♻️ Duplicate skipped: {file_ref(file_path, current_hash)}")
                sposta_file_con_struttura(file_path, INBOX_DIR, DUPLICATES_DIR)
                continue

            # Reading
            nodes, status = read_and_chunk_file(file_path)

            if status == "ERROR" or status == "EMPTY":
                failed_files.append((file_ref(file_path, current_hash), status))
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
                    log.error(f"🔥 Error writing Batch to DB: {content(str(e), type(e).__name__)}")
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
                log.error(f"🔥 Error writing Final Batch to DB: {content(str(e), type(e).__name__)}")
                for f in files_to_move_ok:
                    sposta_file_con_struttura(f, INBOX_DIR, ERROR_DIR)

        if processed_count > 0:
            log.info("🧠 Updating Hybrid Index (BM25)...")
            try:
                rebuild_bm25_index(collection, BM25_PATH, nodes_loader=get_all_nodes_from_chroma)
            except Exception as e: log.error(f"❌ BM25 Error: {content(str(e), type(e).__name__)}")

        pulisci_cartelle_vuote(INBOX_DIR)
        log.info(f"🏁 Completed: {processed_count}/{len(files)} files archived.")

        if failed_files:
            log.warning(f"⚠️ {len(failed_files)} files failed:")
            for ref, reason in failed_files:
                log.warning(f"   - {ref} ({reason})")
            log.warning(f"   Failed files have been moved to: {ERROR_DIR}")

    except Exception as e:
        log.critical(f"🔥 CRASH MAIN: {content(str(e), type(e).__name__)}",
                     exc_info=_logger.LOG_CLIENT_CONTENT)
    finally:
        release_lock()

if __name__ == "__main__":
    sys.exit(main())
