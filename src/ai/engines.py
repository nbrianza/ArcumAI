import os
import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.llms.gemini import Gemini

from src.config import (
    DB_PATH, CHROMA_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, ROLE_PROMPTS
)
from src.logger import server_log as slog


def load_rag_engine(user_role="DEFAULT"):
    """RAG Engine: Searches the Vector Database."""
    path_to_use = str(CHROMA_PATH) if 'CHROMA_PATH' in globals() else str(DB_PATH)

    db = chromadb.PersistentClient(path=path_to_use)
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
    retriever = vector_retriever
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            retriever = QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False, verbose=True
            )
        except: pass

    selected_prompt = ROLE_PROMPTS.get(user_role, DEFAULT_SYSTEM_PROMPT)
    slog.info(f"RAG Engine Loaded | Profile: {user_role}")

    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE,
        llm=Settings.llm
    )


def load_simple_local_engine():
    """Local Engine: Llama. Sees uploaded files."""
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un analista di dati. Rispondi basandoti ESCLUSIVAMENTE sul testo fornito, se presente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=16384)
    )


def load_cloud_engine():
    """Cloud Engine: Gemini. Sees chat + Global Knowledge."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: raise ValueError("Missing GOOGLE_API_KEY in .env file")

    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=api_key)

    return SimpleChatEngine.from_defaults(
        system_prompt=(
            "Sei Gemini, un'IA avanzata di Google. "
            "Usa la tua vasta conoscenza per rispondere a domande su aziende, concetti o dati esterni. "
            "Non limitarti a riassumere la chat."
        ),
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192)
    )
