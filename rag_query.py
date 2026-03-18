# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
import sys
from pathlib import Path

# LlamaIndex Core
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine

# Retrievers for Hybrid Search
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

# --- CONFIG IMPORT ---
from src.config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    BM25_PATH,
    ROLE_PROMPTS,           # Import role prompts
    DEFAULT_SYSTEM_PROMPT,
    RETRIEVER_TOP_K,
    init_settings
)

# --- TEST CONFIGURATION ---
# Change the role you want to test here:
# Options: "ADMIN", "LEGAL", "EXECUTIVE", "COMMERCIALISTA", "TEST"
TARGET_ROLE = "TEST"

def get_hybrid_retriever(index):
    """
    Exact replica of the retrieval logic from app.py.
    """
    # A. Vector Retriever
    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)

    # B. BM25 Retriever (Keywords)
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K

            # C. Fusion
            return QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=True,
                verbose=False
            )
        except Exception as e:
            print(f"⚠️ Error loading BM25: {e}. Using vector only.")
            return vector_retriever
    else:
        print("⚠️ BM25 index not found. Using vector only.")
        return vector_retriever

def load_index_manual():
    db = chromadb.PersistentClient(path=str(CHROMA_PATH))
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

async def main():
    # 1. Initialize Settings (Ollama, Embeddings)
    init_settings()

    # 2. Select the System Prompt (The profile's "Brain")
    system_prompt = ROLE_PROMPTS.get(TARGET_ROLE, DEFAULT_SYSTEM_PROMPT)

    print("\n" + "="*60)
    print(f"🧠 ARCUM AI - CLI DIAGNOSTICS")
    print(f"🎭 Active Profile: {TARGET_ROLE}")
    print(f"⚙️  Engine: Hybrid (Vector + BM25)")
    print("="*60 + "\n")

    # 3. Load Index and Retriever
    try:
        index = load_index_manual()
        retriever = get_hybrid_retriever(index)
    except Exception as e:
        print(f"❌ Critical DB error: {e}")
        return

    # 4. Create Chat Engine (identical to app.py)
    chat_engine = ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
        system_prompt=system_prompt, # Profile injection
        llm=Settings.llm
    )

    print("💬 Type your question (or 'exit' to quit)")

    while True:
        try:
            user_input = input("\n👉 You: ").strip()
            if user_input.lower() in ["exit", "quit", "esci"]:
                print("👋 Closing.")
                break

            if not user_input: continue

            print("🤖 ArcumAI: ", end="", flush=True)

            # 5. Streaming Response (with async FIX)
            response = await chat_engine.astream_chat(user_input)

            async for token in response.async_response_gen():
                print(token, end="", flush=True)

            # (Optional) Show sources used for debug
            if response.source_nodes:
                print("\n\n   📚 Relevant sources:")
                seen = set()
                for node in response.source_nodes:
                    fname = node.metadata.get('filename', 'N/A')
                    if fname not in seen:
                        print(f"   - {fname}")
                        seen.add(fname)

        except KeyboardInterrupt:
            print("\n🛑 Interrupted.")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
