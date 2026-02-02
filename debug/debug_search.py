import sys
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.retrievers import QueryFusionRetriever
import chromadb
from pathlib import Path

# --- IMPORT FONDAMENTALE MANCANTE ---
# Importiamo la configurazione per caricare il modello locale (bge-m3)
from src.config import (
    BASE_DIR, DB_PATH, BM25_PATH, COLLECTION_NAME, 
    init_settings
)

def test_retrieval(query_text):
    # 1. Inizializziamo i modelli (CRUCIALE per evitare errore OpenAI)
    init_settings()
    
    print(f"\n🔍 DEBUG RICERCA: '{query_text}'")
    print("-" * 50)
    
    # 2. Carichiamo il DB
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    chroma_collection = db_client.get_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    
    # 3. Ricostruiamo il Retriever Ibrido
    vector_retriever = index.as_retriever(similarity_top_k=5)
    
    retriever = vector_retriever
    mode = "SOLO VETTORIALE"
    
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = 5
            
            # Fusione
            retriever = QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=5,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False,
                verbose=False
            )
            mode = "IBRIDO (Vector + BM25)"
        except Exception as e:
            print(f"⚠️ Errore caricamento BM25: {e}")

    print(f"⚙️  Modalità Retriever: {mode}")

    # 4. Eseguiamo la ricerca
    results = retriever.retrieve(query_text)
    
    if not results:
        print("❌ NESSUN RISULTATO TROVATO.")
        return

    print(f"✅ Trovati {len(results)} risultati rilevanti:\n")
    
    for i, node in enumerate(results, 1):
        filename = node.metadata.get('filename', 'N/A')
        score = node.score if node.score else 0.0
        # Pulizia testo per visualizzazione
        content_preview = node.text[:150].replace('\n', ' ')
        
        print(f"{i}. [Score: {score:.4f}] FILE: {filename}")
        print(f"   Testo: \"{content_preview}...\"")
        print("-" * 30)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python debug_search.py \"La tua domanda qui\"")
    else:
        test_retrieval(sys.argv[1])