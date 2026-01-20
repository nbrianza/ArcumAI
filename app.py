import chainlit as cl
from pathlib import Path

# LlamaIndex Core
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from llama_index.core.memory import ChatMemoryBuffer

# Import Retrievers per Hybrid Search
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.chat_engine import ContextChatEngine

# --- IMPORT CONFIGURAZIONE ---
from src.config import (
    CHROMA_PATH, 
    COLLECTION_NAME, 
    BM25_PATH,          
    ARCHIVE_DIR, 
    ROLE_PROMPTS, 
    DEFAULT_SYSTEM_PROMPT, 
    RETRIEVER_TOP_K,    
    init_settings
)

from src.readers import SmartPDFReader
from src.auth import verify_password, load_users, update_password

# --- 1. SETUP INIZIALE ---
# Inizializza i settings globali (Ollama, Embeddings)
init_settings()

def get_hybrid_retriever(index):
    """Costruisce il retriever Ibrido (Vector + BM25)."""
    # A. Retriever Vettoriale
    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
    
    # B. Retriever BM25 (Parole chiave)
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            
            return QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K, 
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=True,
                verbose=True
            )
        except Exception as e:
            print(f"⚠️ Errore caricamento BM25: {e}. Uso solo vettoriale.")
            return vector_retriever
    else:
        print("⚠️ Indice BM25 non trovato. Uso solo Vettoriale.")
        return vector_retriever

def load_index():
    db = chromadb.PersistentClient(path=str(CHROMA_PATH))
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

# --- 2. AUTENTICAZIONE ---
@cl.password_auth_callback
def auth_callback(username, password):
    users = load_users() 
    if username in users:
        stored_hash = users[username]['pw_hash']
        if verify_password(password, stored_hash):
            user_data = users[username]
            return cl.User(
                identifier=username, 
                metadata={
                    "role": user_data.get("role", "ADMIN"), 
                    "name": user_data.get("name", username)
                }
            )
    return None

# --- 3. AVVIO SESSIONE ---
@cl.on_chat_start
async def start():
    user = cl.user_session.get("user")
    role = user.metadata["role"]
    real_name = user.metadata["name"]
    
    system_prompt = ROLE_PROMPTS.get(role, DEFAULT_SYSTEM_PROMPT)
    
    try:
        msg = cl.Message(content=f"⚙️ Avvio profilo **{role}** (Hybrid Engine)...")
        await msg.send()
        
        index = load_index()
        retriever = get_hybrid_retriever(index)
        
        # Setup Chat Engine
        # FIX: Passiamo Settings.llm per sicurezza
        chat_engine = ContextChatEngine.from_defaults(
            retriever=retriever,
            memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
            system_prompt=system_prompt,
            llm=Settings.llm 
        )
        
        cl.user_session.set("chat_engine", chat_engine)
        
        actions = [
            cl.Action(name="change_pw", payload={"action": "change_pw"}, label="🔑 Cambia Password")
        ]
        
        msg.content = (
            f"👋 **Benvenuto, {real_name}.**\n\n"
            f"🔰 Profilo Attivo: **{role}**\n"
            f"🧠 Motore: **Hybrid (Vector + Keywords)**\n"
            f"🤖 Sistema pronto."
        )
        msg.actions = actions
        await msg.update()
        
    except Exception as e:
        await cl.Message(content=f"❌ Errore critico avvio: {e}").send()

# --- 4. GESTIONE AZIONI ---
@cl.action_callback("change_pw")
async def on_action(action):
    user = cl.user_session.get("user")
    res = await cl.AskUserMessage(content="🔒 Inserisci la nuova password:", timeout=60).send()
    if res:
        new_pw = res['output']
        if len(new_pw) < 3:
            await cl.Message(content="⚠️ Password troppo corta.").send()
        else:
            update_password(user.identifier, new_pw)
            await cl.Message(content="✅ **Password aggiornata.**").send()

# --- 5. GESTIONE MESSAGGI ---
@cl.on_message
async def main(message: cl.Message):
    chat_engine = cl.user_session.get("chat_engine")
    
    # Gestione Contesto da Allegati (MANTENUTA ORIGINALE)
    context_text = ""
    if message.elements:
        processing_msg = cl.Message(content="📂 Analisi allegati...")
        await processing_msg.send()
        for element in message.elements:
            if "text" in element.mime or "pdf" in element.mime:
                file_path = Path(element.path)
                try:
                    if file_path.suffix.lower() == ".pdf":
                        reader = SmartPDFReader()
                        docs = reader.load_data(file_path)
                        text_content = "\n".join([d.text for d in docs])
                    else:
                        text_content = file_path.read_text(encoding="utf-8", errors="ignore")
                    context_text += f"\n--- DOCUMENTO UTENTE: {element.name} ---\n{text_content}\n--- FINE DOC ---\n"
                except Exception as e:
                    print(f"Errore lettura file: {e}")
        await processing_msg.remove()

    if context_text:
        full_query = (
            f"ISTRUZIONE: L'utente ha allegato un contenuto. Usa questo come contesto primario.\n"
            f"{context_text}\n\n"
            f"RICHIESTA UTENTE: {message.content}"
        )
    else:
        full_query = message.content

    # --- ESECUZIONE QUERY (FIX ASYNC) ---
    msg = cl.Message(content="")
    
    try:
        # Usiamo astream_chat che è nativo asincrono e più stabile
        response = await chat_engine.astream_chat(full_query)
        
        # --- FIX QUI: Aggiunte parentesi () dopo async_response_gen ---
        async for token in response.async_response_gen(): 
            await msg.stream_token(token)

        # --- VISUALIZZAZIONE FONTI (FIX CRASH) ---
        source_nodes = response.source_nodes
        elements = []
        text_sources = []
        
        if source_nodes:
            seen = set()
            for node in source_nodes:
                fname = node.metadata.get("filename", "Sconosciuto")
                rel_path = node.metadata.get("file_path", fname)
                
                if fname and fname not in seen:
                    path = ARCHIVE_DIR / rel_path
                    exists = path.exists()
                    
                    if exists:
                        # FIX: Controlliamo se è un PDF vero prima di usare cl.Pdf
                        # Questo impedisce il crash se il file è .msg, .docx, ecc.
                        if path.suffix.lower() == ".pdf":
                            elements.append(cl.Pdf(name=fname, display="side", path=str(path)))
                        else:
                            # Per Email (.msg, .eml) e Word (.docx), usiamo cl.File (Download icon)
                            elements.append(cl.File(name=fname, display="inline", path=str(path)))
                    
                    text_sources.append(fname)
                    seen.add(fname)
        
        if elements:
            msg.elements = elements
        
        if text_sources:
            footer = "\n\n**📚 Fonti utilizzate:**\n" + "\n".join([f"- {s}" for s in text_sources])
            msg.content += footer

        await msg.update()
        
    except Exception as e:
        print(f"❌ ERRORE RISPOSTA: {e}")
        await cl.Message(content=f"⚠️ Si è verificato un errore durante la generazione: {e}").send()