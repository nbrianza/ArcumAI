import asyncio
import sys
from pathlib import Path

# LlamaIndex Core
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine

# Retrievers per Hybrid Search
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

# --- IMPORT CONFIGURAZIONE ---
from src.config import (
    CHROMA_PATH, 
    COLLECTION_NAME, 
    BM25_PATH,          
    ROLE_PROMPTS,           # <--- Importiamo i ruoli
    DEFAULT_SYSTEM_PROMPT, 
    RETRIEVER_TOP_K,
    init_settings
)

# --- CONFIGURAZIONE TEST ---
# Cambia qui il ruolo che vuoi testare oggi:
# Opzioni: "ADMIN", "LEGAL", "EXECUTIVE", "COMMERCIALISTA", "TEST"
TARGET_ROLE = "TEST"  

def get_hybrid_retriever(index):
    """
    Replica ESATTA della logica di retrieval di app.py.
    """
    # A. Retriever Vettoriale
    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
    
    # B. Retriever BM25 (Parole chiave)
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            
            # C. Fusione
            return QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K, 
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=True,
                verbose=False 
            )
        except Exception as e:
            print(f"⚠️ Errore caricamento BM25: {e}. Uso solo vettoriale.")
            return vector_retriever
    else:
        print("⚠️ Indice BM25 non trovato. Uso solo Vettoriale.")
        return vector_retriever

def load_index_manual():
    db = chromadb.PersistentClient(path=str(CHROMA_PATH))
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

async def main():
    # 1. Inizializza Settings (Ollama, Embeddings)
    init_settings()

    # 2. Seleziona il System Prompt (Il "Cervello" del profilo)
    system_prompt = ROLE_PROMPTS.get(TARGET_ROLE, DEFAULT_SYSTEM_PROMPT)
    
    print("\n" + "="*60)
    print(f"🧠 ARCUM AI - CLI DIAGNOSTICA")
    print(f"🎭 Profilo Attivo: {TARGET_ROLE}")
    print(f"⚙️  Motore: Hybrid (Vector + BM25)")
    print("="*60 + "\n")

    # 3. Carica Indice e Retriever
    try:
        index = load_index_manual()
        retriever = get_hybrid_retriever(index)
    except Exception as e:
        print(f"❌ Errore critico DB: {e}")
        return

    # 4. Crea Chat Engine (identico a app.py)
    chat_engine = ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
        system_prompt=system_prompt, # <--- Iniezione Profilo
        llm=Settings.llm
    )

    print("💬 Scrivi la tua domanda (o 'exit' per uscire)")
    
    while True:
        try:
            user_input = input("\n👉 Tu: ").strip()
            if user_input.lower() in ["exit", "quit", "esci"]:
                print("👋 Chiusura.")
                break
            
            if not user_input: continue

            print("🤖 ArcumAI: ", end="", flush=True)
            
            # 5. Streaming Risposta (con FIX asincrono)
            response = await chat_engine.astream_chat(user_input)
            
            # NOTA: Qui ci sono le parentesi () che mancavano prima
            async for token in response.async_response_gen():
                print(token, end="", flush=True)
            
            # (Opzionale) Mostra fonti usate per debug
            if response.source_nodes:
                print("\n\n   📚 Fonti rilevate:")
                seen = set()
                for node in response.source_nodes:
                    fname = node.metadata.get('filename', 'N/A')
                    if fname not in seen:
                        print(f"   - {fname}")
                        seen.add(fname)

        except KeyboardInterrupt:
            print("\n🛑 Interrotto.")
            break
        except Exception as e:
            print(f"\n❌ Errore: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass