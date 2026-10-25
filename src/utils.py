# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import os
import shutil
import time
import hashlib
import stat
import errno
from pathlib import Path
from . import logger as _logger
from .logger import log, server_log as slog, content, safe_ext

# LlamaIndex Schema
from llama_index.core.schema import TextNode

def calcola_hash_file(file_path):
    """Calculates the MD5 hash of the file."""
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            buf = f.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = f.read(65536)
        return hasher.hexdigest()
    except Exception:
        return "hash_error"

def file_ref(path, file_hash=None, shown=None) -> str:
    """Reference to a client file for ingestion log lines (issue #52).

    Returns "id=<first 12 hex of the content MD5> ext=<safe_ext>", or
    "id=unavailable ext=..." when the hash cannot be computed. The file name never
    appears in default logs: only when LOG_CLIENT_CONTENT is on is
    " file=<repr(shown or path.name)>" appended. When file_hash is None the hash is
    computed here (failure paths only, so successful runs read each file once).
    """
    path = Path(path)
    if file_hash is None:
        file_hash = calcola_hash_file(path)
    file_id = "unavailable" if not file_hash or file_hash == "hash_error" else file_hash[:12]
    ref = f"id={file_id} ext={safe_ext(path.name)}"
    if _logger.LOG_CLIENT_CONTENT:
        ref += f" file={content(shown if shown is not None else path.name)}"
    return ref

def sposta_file_con_struttura(file_path: Path, root_src: Path, root_dst: Path, max_retries=3):
    """
    Moves a file while preserving the folder structure.
    Includes RETRY logic for busy files (PermissionError).
    """
    # 1. Calculate relative destination path
    try:
        rel_path = file_path.relative_to(root_src)
    except ValueError:
        rel_path = file_path.name

    target_path = root_dst / rel_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Handle name collisions (Rename with timestamp)
    if target_path.exists():
        timestamp = int(time.time())
        new_name = f"{target_path.stem}_{timestamp}{target_path.suffix}"
        target_path = target_path.with_name(new_name)

    # 3. Move attempt with Retry
    last_error = None
    ref = None  # file reference for log lines, computed on the first failure only
    for attempt in range(max_retries):
        try:
            shutil.move(str(file_path), str(target_path))
            return target_path # Success!
        except PermissionError as e:
            last_error = e
            if ref is None:
                ref = file_ref(file_path)
            log.warning(f"   ⏳ File in use: {ref} (Attempt {attempt+1}/{max_retries})...")
            time.sleep(2)
        except Exception as e:
            # Other errors (e.g. full disk) no point retrying immediately
            raise e

    # If we're here, all attempts exhausted
    if ref is None:
        ref = file_ref(file_path)
    log.error(f"   💀 Unable to move {ref} after {max_retries} attempts.")
    raise last_error

def handle_remove_readonly(func, path, exc):
    """Callback to unlock Read-Only files on Windows."""
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and excvalue.errno == errno.EACCES:
        os.chmod(path, stat.S_IWRITE)
        func(path)

def pulisci_cartelle_vuote(root_dir: Path):
    """Recursively removes empty folders."""
    junk_files = {"Thumbs.db", "desktop.ini", ".DS_Store"}

    candidates = []
    for root, dirs, files in os.walk(root_dir):
        for name in dirs:
            candidates.append(Path(root) / name)

    candidates.sort(key=lambda p: len(p.parts), reverse=True)

    removed_count = 0
    for folder in candidates:
        if not folder.exists(): continue
        try:
            is_effectively_empty = True
            if folder.exists():
                for item in folder.iterdir():
                    if item.is_dir():
                        is_effectively_empty = False
                        break
                    if item.is_file() and item.name not in junk_files and not item.name.startswith("._"):
                        is_effectively_empty = False
                        break

            if is_effectively_empty:
                shutil.rmtree(folder, ignore_errors=False, onerror=handle_remove_readonly)
                removed_count += 1
        except Exception as e:
            log.warning(f"Could not remove empty folder {content(str(folder), '(name hidden)')}: "
                        f"{content(str(e), type(e).__name__)}")

    if removed_count > 0:
        log.info(f"   🧹 Cleanup: Removed {removed_count} empty folders in {root_dir.name}.")

def get_all_nodes_from_chroma(chroma_collection):
    """Retrieves ALL data from the ChromaDB collection.

    Nodes are rebuilt exactly as the vector store rebuilds them (issue #71), so a
    chunk has the same id, text, metadata and hash in BM25 and in vector results
    and the hybrid fusion keeps one entry per chunk.
    """
    log.info("   ⏳ Retrieving data from ChromaDB...")
    try:
        from llama_index.vector_stores.chroma import ChromaVectorStore
        return ChromaVectorStore(chroma_collection=chroma_collection).get_nodes(None)
    except Exception as e:
        log.error(f"❌ Error retrieving nodes: {content(str(e), type(e).__name__)}")
        return []

def rebuild_bm25_index(chroma_collection, bm25_path, *, nodes_loader=None) -> int:
    """Rebuilds the BM25 index at bm25_path from every chunk in the collection.

    Returns the number of chunks indexed; 0 means nothing was written. Exceptions
    propagate to the caller. nodes_loader defaults to get_all_nodes_from_chroma.
    Logs the chunk count only (no file names, paths or chunk text).
    """
    from llama_index.retrievers.bm25 import BM25Retriever
    loader = nodes_loader or get_all_nodes_from_chroma
    all_nodes = loader(chroma_collection)
    if not all_nodes:
        log.info("   BM25 index not rebuilt: 0 chunks in the collection.")
        return 0
    bm25 = BM25Retriever.from_defaults(
        nodes=all_nodes,
        similarity_top_k=5,
        language="italian"
    )
    bm25.persist(str(bm25_path))
    log.info(f"   BM25 index rebuilt: {len(all_nodes)} chunks.")
    return len(all_nodes)

    # --- ADDITIONS FOR ARCUM AI HYBRID UI ---

from src.config import ARCHIVE_DIR

def find_relative_path(filename: str) -> str:
    """
    Searches for the file recursively inside ARCHIVE_DIR and returns
    the normalized relative path for the web.
    Used by the UI to generate links to PDFs.
    """
    safe_name = Path(filename).name  # strip any directory components (e.g. ../../etc/passwd → passwd)
    if not safe_name or any(c in safe_name for c in ('*', '?', '[', ']')):
        return filename  # reject glob metacharacters
    archive_resolved = ARCHIVE_DIR.resolve()
    try:
        for match in ARCHIVE_DIR.rglob(safe_name):
            resolved = match.resolve()
            if resolved.is_relative_to(archive_resolved):
                return str(resolved.relative_to(archive_resolved)).replace('\\', '/')
    except Exception:
        pass
    return filename


# --- UPDATE ON src/utils.py ---
from pathlib import Path

def load_global_triggers():
    """
    Loads RAG (Document) keywords from .txt files in the 'triggers' folder,
    EXCLUDING the 'chat.txt' file.
    """
    trigger_path = Path(__file__).parent.parent / "triggers"
    all_keywords = set()

    if not trigger_path.exists():
        return []

    slog.info(f"Loading RAG Triggers from: {trigger_path}")

    for file_path in trigger_path.glob("*.txt"):
        # --- IMPORTANT: Exclude the chat file ---
        if "chat.txt" in file_path.name:
            continue
        # ---------------------------------------------------------

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                count = 0
                for line in f:
                    word = line.strip().lower()
                    if word and not word.startswith("#"):
                        all_keywords.add(word)
                        count += 1
                slog.info(f"   Loaded {count} RAG triggers from {file_path.name}")
        except Exception as e:
            slog.error(f"Error reading {file_path.name}: {e}")

    return list(all_keywords)

def load_chat_triggers():
    """
    Loads ONLY the keywords for Simple Chat from triggers/chat.txt
    """
    chat_file = Path(__file__).parent.parent / "triggers" / "chat.txt"
    chat_keywords = set()

    if not chat_file.exists():
        slog.warning("File triggers/chat.txt not found. Using defaults.")
        return ['ciao', 'hello', 'hallo', 'bonjour'] # Minimal fallback

    try:
        with open(chat_file, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip().lower()
                if word and not word.startswith("#"):
                    chat_keywords.add(word)
        slog.info(f"Loaded {len(chat_keywords)} CHAT triggers from chat.txt")
    except Exception as e:
        slog.error(f"Error reading chat.txt: {e}")

    return list(chat_keywords)
