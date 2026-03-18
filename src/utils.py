# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import os
import shutil
import time
import hashlib
import stat
import errno
from pathlib import Path
from .logger import log, server_log as slog

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
    for attempt in range(max_retries):
        try:
            shutil.move(str(file_path), str(target_path))
            return target_path # Success!
        except PermissionError as e:
            last_error = e
            log.warning(f"   ⏳ File in use: {file_path.name} (Attempt {attempt+1}/{max_retries})...")
            time.sleep(2)
        except Exception as e:
            # Other errors (e.g. full disk) no point retrying immediately
            raise e

    # If we're here, all attempts exhausted
    log.error(f"   💀 Unable to move {file_path.name} after {max_retries} attempts.")
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
            log.warning(f"Could not remove empty folder '{folder}': {e}")

    if removed_count > 0:
        log.info(f"   🧹 Cleanup: Removed {removed_count} empty folders in {root_dir.name}.")

def get_all_nodes_from_chroma(chroma_collection):
    """Retrieves ALL data from the ChromaDB collection."""
    log.info("   ⏳ Retrieving data from ChromaDB...")
    try:
        db_data = chroma_collection.get(include=["documents", "metadatas"])
        ids = db_data.get("ids", [])
        texts = db_data.get("documents", [])
        metadatas = db_data.get("metadatas", [])

        if not ids:
            return []

        all_nodes = []
        for i in range(len(ids)):
            node = TextNode(
                text=texts[i],
                metadata=metadatas[i],
                id_=ids[i]
            )
            all_nodes.append(node)
        return all_nodes
    except Exception as e:
        log.error(f"❌ Error retrieving nodes: {e}")
        return []

    # --- ADDITIONS FOR ARCUM AI HYBRID UI ---

from src.config import ARCHIVE_DIR

def find_relative_path(filename: str) -> str:
    """
    Searches for the file recursively inside ARCHIVE_DIR and returns
    the normalized relative path for the web.
    Used by the UI to generate links to PDFs.
    """
    try:
        # rglob searches in all subfolders
        matches = list(ARCHIVE_DIR.rglob(filename))
        if matches:
            # Take the first match and calculate the relative path
            rel_path = matches[0].relative_to(ARCHIVE_DIR)
            # Normalize slashes for Windows/Web
            return str(rel_path).replace('\\', '/')
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
