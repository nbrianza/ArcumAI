import chromadb
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core import VectorStoreIndex
from .config import DB_PATH, COLLECTION_NAME # <--- Usa la costante

def get_vector_index():
    """Inizializza e restituisce l'indice e la collezione."""
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    # Usa get_or_create per evitare errori se non esiste
    chroma_collection = db_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    
    return index, chroma_collection, db_client