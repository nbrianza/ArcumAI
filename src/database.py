# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import chromadb
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core import VectorStoreIndex
from .config import DB_PATH, COLLECTION_NAME # <--- Uses the constant

def get_vector_index():
    """Initializes and returns the index and collection."""
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    # Use get_or_create to avoid errors if it doesn't exist
    chroma_collection = db_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)

    return index, chroma_collection, db_client
