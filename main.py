import sys
import os
import gc
import time
from pathlib import Path

# LlamaIndex & Readers
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.readers.file import DocxReader, PandasExcelReader
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb

# Moduli Interni
from src.readers import SmartPDFReader, MyOutlookReader, MyEmlReader
from src.logger import log
from src.config import (
    INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, 
    DUPLICATES_DIR, 
    DB_PATH, COLLECTION_NAME, BM25_PATH, 
    PROFILE, LLM_MODEL_NAME, WATCH_EXTENSIONS,
    init_settings
)
from src.utils import (
    calcola_hash_file, 
    sposta_file_con_struttura, 
    pulisci_cartelle_vuote,
    get_all_nodes_from_chroma
)

# Inizializza settings globali (Chunking, Embedding, LLM)
init_settings()

def get_db_components():
    """Inizializza connessione DB una volta sola."""
    # Uso get_or_create_collection per evitare errori al primo avvio
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    chroma_collection = db_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    return index, chroma_collection

def process_single_file(file_path, index, collection):
    """
    Processa un singolo file.
    Ritorna True se processato con successo, False se saltato/errore.
    """
    filename = file_path.name
    
    # 1. CONTROLLO HASH (Anticipato per efficienza)
    # Calcoliamo l'hash prima di leggere il file pesante
    current_hash = calcola_hash_file(file_path)
    
    # Check rapido nel DB se l'hash esiste già
    existing = collection.get(where={"file_hash": current_hash}, limit=1, include=["metadatas"])
    if existing and existing["ids"]:
        log.info(f"   ♻️  DUPLICATO RILEVATO (Hash esistente). Sposto in Duplicati.")
        sposta_file_con_struttura(file_path, INBOX_DIR, DUPLICATES_DIR)
        return False

    # 2. SELEZIONE READER (Manteniamo supporto completo Office/Email)
    docs = []
    ext = file_path.suffix.lower()
    
    try:
        if ext == ".pdf":
            reader = SmartPDFReader()
            docs = reader.load_data(file_path)
        elif ext == ".docx":
            reader = DocxReader()
            docs = reader.load_data(file_path)
        elif ext == ".xlsx":
            # Configurazione Pandas specifica per header (come nel tuo codice originale)
            reader = PandasExcelReader(pandas_config={"header": 0})
            docs = reader.load_data(file_path)
        elif ext == ".msg":
            reader = MyOutlookReader()
            docs = reader.load_data(file_path)
        elif ext == ".eml":
            reader = MyEmlReader()
            docs = reader.load_data(file_path)
        else:
            # Fallback di sicurezza
            log.warning(f"   ⚠️ Estensione {ext} non gestita esplicitamente. Salto.")
            return False

    except Exception as e:
        log.error(f"   ❌ Errore lettura file ({ext}): {e}")
        # Se il reader fallisce (file corrotto), spostiamo in Error
        sposta_file_con_struttura(file_path, INBOX_DIR, ERROR_DIR)
        return False

    if not docs:
        log.warning("   ⚠️ File letto ma nessun contenuto estratto (Vuoto?).")
        sposta_file_con_struttura(file_path, INBOX_DIR, ERROR_DIR)
        return False

    # 3. ARRICCHIMENTO METADATI (Logica originale)
    try:
        rel_path = file_path.relative_to(INBOX_DIR)
    except ValueError:
        rel_path = file_path.name

    unique_id = str(rel_path).replace(os.sep, "_").replace(" ", "_")

    for doc in docs:
        # Assegnazione ID e Hash
        doc.metadata["my_custom_id"] = unique_id
        doc.metadata["file_hash"] = current_hash
        doc.metadata["filename"] = filename
        doc.metadata["file_path"] = str(rel_path)
        
        # Pulizia metadati sporchi generati dai reader
        doc.metadata.pop("file_name", None) 
        
        # Rilevamento Email (Logica originale con check sicurezza)
        if hasattr(doc, "text") and doc.text and "Soggetto:" in doc.text[:100]:
            doc.metadata["tipo"] = "email"

    # 4. INSERIMENTO NEL DB (Logica FIXATA per LlamaIndex moderno)
    try:
        # Lo splitter usa i chunk_size definiti in config.py
        nodes = Settings.text_splitter.get_nodes_from_documents(docs)
        
        # Inseriamo i nodi (evita l'errore 'insert_documents')
        index.insert_nodes(nodes)
        
        log.info(f"   ✅ Indicizzato: {filename} ({len(nodes)} nodi)")
        sposta_file_con_struttura(file_path, INBOX_DIR, ARCHIVE_DIR)
        return True
        
    except Exception as e:
        log.error(f"   ❌ Errore scrittura DB: {e}")
        # Non spostiamo in error qui, perché potrebbe essere un errore temporaneo del DB
        return False

def main():
    # --- SETUP E LOGGING ---
    log.info(f"🔄 AVVIO INGESTION ARCUM AI (Versione Optimized)")
    log.info(f"   ⚙️  Profilo: {PROFILE}")
    log.info(f"   🧠 Modello AI: {LLM_MODEL_NAME}")
    
    # Creazione cartelle
    for p in [INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, DUPLICATES_DIR, BM25_PATH]:
        if not p.exists(): p.mkdir(parents=True)

    # Connessione DB
    index, collection = get_db_components()

    # --- RICERCA FILE (Ricorsiva) ---
    log.info("📂 Scansione nuovi file...")
    junk_files = ["Thumbs.db", "desktop.ini", ".DS_Store"]
    
    # Troviamo tutti i file validi ricorsivamente usando i filtri del Config
    files_to_process = [
        f for f in INBOX_DIR.rglob('*') 
        if f.is_file() and f.name not in junk_files and not f.name.startswith("._") and f.suffix in WATCH_EXTENSIONS
    ]

    if not files_to_process:
        log.info("ℹ️ Nessun file da processare.")
        return

    log.info(f"📄 Trovati {len(files_to_process)} file da elaborare.")

    # --- LOOP DI ELABORAZIONE ---
    processed_count = 0
    total_files = len(files_to_process)
    
    for i, file_path in enumerate(files_to_process, 1):
        # Print per feedback visivo nel terminale
        print(f"[{i}/{total_files}] Elaborazione: {file_path.name}...", end="\r")
        
        # Skip file vuoti (0 byte)
        if file_path.stat().st_size == 0:
            log.warning(f"   ⚠️ FILE VUOTO: {file_path.name}")
            sposta_file_con_struttura(file_path, INBOX_DIR, ERROR_DIR)
            continue

        if process_single_file(file_path, index, collection):
            processed_count += 1
            
        # Garbage Collection proattivo per tenere bassa la RAM
        gc.collect()

    # --- RIGENERAZIONE INDICE BM25 (Ibrido) ---
    # Solo se abbiamo elaborato qualcosa, rigeneriamo l'indice keyword
    if processed_count > 0:
        log.info("\n🧠 Aggiornamento Indice Ibrido (BM25)...")
        try:
            # Recuperiamo tutti i nodi dal DB per creare l'indice completo
            all_nodes = get_all_nodes_from_chroma(collection)
            if all_nodes:
                bm25_retriever = BM25Retriever.from_defaults(
                    nodes=all_nodes, 
                    similarity_top_k=5, 
                    language="italian" 
                )
                bm25_retriever.persist(str(BM25_PATH))
                log.info(f"   💾 Indice BM25 salvato ({len(all_nodes)} nodi).")
        except Exception as e:
            log.error(f"❌ Errore rigenerazione BM25: {e}")
    
    # --- PULIZIA FINALE ---
    pulisci_cartelle_vuote(INBOX_DIR)
    log.info(f"\n🏁 Finito. Processati {processed_count} file.")

if __name__ == "__main__":
    main()