import sys
import os
import chromadb
from pathlib import Path
from nicegui import run

# LlamaIndex Imports
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.llms.gemini import Gemini

# Import Configurazione Centralizzata
from src.config import (
    DB_PATH, CHROMA_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, FINAL_TOP_K, init_settings,
    DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, ROLE_PROMPTS
)

# --- FUNZIONI DI CARICAMENTO MOTORI ---

def load_rag_engine(user_role="DEFAULT"):
    """Carica il motore RAG (Chat) con prompt specifico per il ruolo."""
    path_to_use = str(CHROMA_PATH) if 'CHROMA_PATH' in globals() else str(DB_PATH)
    
    db = chromadb.PersistentClient(path=path_to_use)
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    
    # Retriever Ibrido
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
                use_async=False, 
                verbose=True
            )
        except: pass

    selected_prompt = ROLE_PROMPTS.get(user_role, DEFAULT_SYSTEM_PROMPT)
    print(f"🎭 Engine Caricato | Profilo: {user_role}")

    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE, 
        llm=Settings.llm 
    )

def load_simple_local_engine():
    """Motore leggero per chiacchiere veloci."""
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente utile e conciso. Rispondi direttamente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192)
    )

def load_cloud_engine():
    """Motore Gemini (Cloud)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: raise ValueError("Manca GOOGLE_API_KEY nel file .env")
    
    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=api_key)
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente AI avanzato (Gemini). Rispondi con precisione.",
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8000)
    )

# --- CLASSE SESSIONE & ROUTER ---

class UserSession:
    def __init__(self, role="DEFAULT"):
        self.role = role
        self.is_cloud = False
        self.rag_engine = None    
        self.simple_engine = None 
        self.cloud_engine = None  
        self.uploaded_context = "" 
        
    async def get_rag_engine(self):
        if not self.rag_engine: 
            self.rag_engine = await run.io_bound(load_rag_engine, self.role)
        return self.rag_engine

    async def get_simple_engine(self):
        if not self.simple_engine: self.simple_engine = await run.io_bound(load_simple_local_engine)
        return self.simple_engine

    async def get_cloud_engine(self):
        if not self.cloud_engine: self.cloud_engine = await run.io_bound(load_cloud_engine)
        return self.cloud_engine

    async def decide_engine(self, text):
        """Router Intelligente: RAG vs SIMPLE (con supporto @comandi)"""
        text_lower = text.lower()
        
        # 1. OVERRIDE MANUALE (@MENTION)
        # Controlliamo il testo GREZZO per i comandi
        if "@rag" in text_lower or "@cerca" in text_lower: return "RAG"
        if "@simple" in text_lower or "@chat" in text_lower: return "SIMPLE"
        
        # 2. Controllo Parole Chiave (Trigger)
        triggers_law = ['legge', 'art', 'articolo', 'regolamento', 'decreto', 'pdf', 'documento', 'sentenza', 'comma', 'cerca']
        if any(t in text_lower for t in triggers_law): return "RAG"
        
        triggers_chat = ['ciao', 'buongiorno', 'come stai', 'quanto fa', 'chi sei', 'grazie', 'calcola', 'aiutami']
        if any(t in text_lower for t in triggers_chat): return "SIMPLE"

        # 3. Fallback AI
        try:
            prompt = (f"Analizza: '{text}'. Se riguarda leggi/docs rispondi 'RAG'. "
                      "Se mate/saluti rispondi 'SIMPLE'. Rispondi SOLO 1 parola.")
            resp = await Settings.llm.acomplete(prompt)
            decision = str(resp).strip().upper()
            if "RAG" in decision: return "RAG"
            return "SIMPLE"
        except: return "RAG"