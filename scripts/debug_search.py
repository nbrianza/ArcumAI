import sys
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.retrievers import QueryFusionRetriever
import chromadb
from pathlib import Path

# --- ESSENTIAL MISSING IMPORT ---
# Import config to load the local model (bge-m3)
from src.config import (
    BASE_DIR, DB_PATH, BM25_PATH, COLLECTION_NAME,
    init_settings
)

def test_retrieval(query_text):
    # 1. Initialize models (CRUCIAL to avoid OpenAI error)
    init_settings()

    print(f"\n🔍 DEBUG SEARCH: '{query_text}'")
    print("-" * 50)

    # 2. Load the DB
    db_client = chromadb.PersistentClient(path=str(DB_PATH))
    chroma_collection = db_client.get_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    # 3. Rebuild the Hybrid Retriever
    vector_retriever = index.as_retriever(similarity_top_k=5)

    retriever = vector_retriever
    mode = "VECTOR ONLY"

    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = 5

            # Fusion
            retriever = QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=5,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False,
                verbose=False
            )
            mode = "HYBRID (Vector + BM25)"
        except Exception as e:
            print(f"⚠️ Error loading BM25: {e}")

    print(f"⚙️  Retriever Mode: {mode}")

    # 4. Execute the search
    results = retriever.retrieve(query_text)

    if not results:
        print("❌ NO RESULTS FOUND.")
        return

    print(f"✅ Found {len(results)} relevant results:\n")

    for i, node in enumerate(results, 1):
        filename = node.metadata.get('filename', 'N/A')
        score = node.score if node.score else 0.0
        # Clean text for display
        content_preview = node.text[:150].replace('\n', ' ')

        print(f"{i}. [Score: {score:.4f}] FILE: {filename}")
        print(f"   Text: \"{content_preview}...\"")
        print("-" * 30)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_search.py \"Your query here\"")
    else:
        test_retrieval(sys.argv[1])
