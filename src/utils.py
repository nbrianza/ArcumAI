import os
import shutil
import time
import hashlib
import stat
import errno
from pathlib import Path
from .logger import log

# LlamaIndex Schema
from llama_index.core.schema import TextNode

def calcola_hash_file(file_path):
    """Calcola l'hash MD5 del file."""
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
    Sposta un file mantenendo la struttura delle cartelle.
    Include logica di RETRY in caso di file occupato (PermissionError).
    """
    # 1. Calcola percorso destinazione relativo
    try:
        rel_path = file_path.relative_to(root_src)
    except ValueError:
        rel_path = file_path.name

    target_path = root_dst / rel_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Gestione collisione nomi (Rinomina con timestamp)
    if target_path.exists():
        timestamp = int(time.time())
        new_name = f"{target_path.stem}_{timestamp}{target_path.suffix}"
        target_path = target_path.with_name(new_name)

    # 3. Tentativo di spostamento con Retry
    last_error = None
    for attempt in range(max_retries):
        try:
            shutil.move(str(file_path), str(target_path))
            return target_path # Successo!
        except PermissionError as e:
            last_error = e
            log.warning(f"   ⏳ File in uso: {file_path.name} (Tentativo {attempt+1}/{max_retries})...")
            time.sleep(2)
        except Exception as e:
            # Altri errori (es. disco pieno) non ha senso riprovare subito
            raise e

    # Se siamo qui, i tentativi sono finiti
    log.error(f"   💀 Impossibile spostare {file_path.name} dopo {max_retries} tentativi.")
    raise last_error

def handle_remove_readonly(func, path, exc):
    """Callback per sbloccare i file Read-Only su Windows."""
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and excvalue.errno == errno.EACCES:
        os.chmod(path, stat.S_IWRITE)
        func(path)

def pulisci_cartelle_vuote(root_dir: Path):
    """Rimuove ricorsivamente le cartelle vuote."""
    # (Logica identica a prima, riduco log per pulizia console)
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
        except Exception:
            pass
            
    if removed_count > 0:
        log.info(f"   🧹 Pulizia: Rimosse {removed_count} cartelle vuote in {root_dir.name}.")

def get_all_nodes_from_chroma(chroma_collection):
    """Recupera TUTTI i dati dalla collezione ChromaDB."""
    log.info("   ⏳ Recupero dati da ChromaDB...")
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
        log.error(f"❌ Errore recupero nodi: {e}")
        return []