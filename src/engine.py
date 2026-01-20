import sys
import chromadb
from pathlib import Path

# LlamaIndex Imports
from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.query_engine import RetrieverQueryEngine

# Import Configurazione Centralizzata
from src.config import (
    DB_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, FINAL_TOP_K, init_settings
)

def get_hybrid_engine(streaming=True):
    """
    Costruisce e restituisce il motore di ricerca ibrido (Vector + BM25)
    già configurato con i parametri del config.py.
    """
    # 1. Assicuriamoci che i Settings (Ollama/Embed) siano caricati
    init_settings()

    # 2. Check Esistenza DB
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database Chroma non trovato in: {DB_PATH}")
    
    if not BM25_PATH.exists():
        raise FileNotFoundError(f"Indice BM25 non trovato in: {BM25_PATH}. Esegui ingestion!")

    # 3. Caricamento Vector Store (Concetti)
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    chroma_collection = db_client.get_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    vector_index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    
    vector_retriever = vector_index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)

    # 4. Caricamento BM25 (Parole Chiave)
    bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
    bm25_retriever.similarity_top_k = RETRIEVER_TOP_K

    # 5. Fusione (Hybrid)
    fusion_retriever = QueryFusionRetriever(
        [vector_retriever, bm25_retriever],
        similarity_top_k=FINAL_TOP_K, # Usa il valore dal profilo config
        num_queries=1,                # 1 query = più veloce
        mode="reciprocal_rerank",
        use_async=True,
        verbose=False
    )

    # 6. Creazione Engine finale
    query_engine = RetrieverQueryEngine.from_args(
        retriever=fusion_retriever,
        streaming=streaming
    )

    return query_engine