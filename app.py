import os
import chainlit as cl
from chainlit.input_widget import Switch # <--- FIX: Import corretto per le nuove versioni
from pathlib import Path
import nest_asyncio

# FIX: Patch per loop asincroni
nest_asyncio.apply()

# LlamaIndex Core & Cloud
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from llama_index.core.memory import ChatMemoryBuffer

# Import Retrievers per Hybrid Search
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine

# Gemini
from llama_index.llms.gemini import Gemini

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
init_settings()

# Chiave Google (per il Cloud)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

def get_hybrid_retriever(index):
    """Costruisce il retriever Ibrido (Vector + BM25)."""
    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
    
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            
            return QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K, 
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False, # Async False per stabilità su Windows
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

# --- GESTIONE SETTINGS (SWITCH CLOUD) ---
@cl.on_settings_update
async def setup_agent(settings):
    is_cloud = settings["cloud_mode"]
    cl.user_session.set("cloud_mode", is_cloud)
    
    if is_cloud:
        if not GOOGLE_API_KEY:
            await cl.Message(content="❌ **Errore:** Manca GOOGLE_API_KEY nelle variabili d'ambiente.").send()
            return

        await cl.Message(content="☁️ **Modalità Cloud Attiva (Gemini)**\nUpload file disabilitato per privacy.").send()

        try:
            llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=GOOGLE_API_KEY)
            cloud_engine = SimpleChatEngine.from_defaults(
                system_prompt="Sei un assistente AI avanzato. Rispondi con precisione.",
                llm=llm_cloud,
                memory=ChatMemoryBuffer.from_defaults(token_limit=8000)
            )
            cl.user_session.set("chat_engine", cloud_engine)
        except Exception as e:
             await cl.Message(content=f"❌ Errore Gemini: {e}").send()
    else:
        await cl.Message(content="🔒 **Ritorno a Modalità Locale (Safe).**").send()
        # Per ricaricare il locale, l'utente dovrà ricaricare la pagina o reimpostiamo qui
        # Per semplicità, chiediamo di ricaricare se serve cambiare motore "al volo" 
        # oppure si può richiamare la logica di start(), ma richiede refactoring.
        # Soluzione rapida: Avviso.
        await cl.Message(content="ℹ️ Per riattivare completamente il contesto locale, ricarica la pagina (F5).").send()

# --- 3. AVVIO SESSIONE ---
@cl.on_chat_start
async def start():
    user = cl.user_session.get("user")
    role = user.metadata["role"]
    real_name = user.metadata["name"]
    
    cl.user_session.set("cloud_mode", False)

    # --- FIX QUI: Uso Switch importato correttamente ---
    settings = await cl.ChatSettings(
        [
            Switch(id="cloud_mode", label="☁️ Usa Cloud AI (Gemini)", initial=False),
        ]
    ).send()

    system_prompt = ROLE_PROMPTS.get(role, DEFAULT_SYSTEM_PROMPT)
    
    try:
        msg = cl.Message(content=f"⚙️ Avvio profilo **{role}** (Hybrid Engine)...")
        await msg.send()
        
        index = load_index()
        retriever = get_hybrid_retriever(index)
        
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

# --- 4. CAMBIO PASSWORD ---
@cl.action_callback("change_pw")
async def on_action(action):
    user = cl.user_session.get("user")
    res = await cl.AskUserMessage(content="🔒 Nuova password:", timeout=60).send()
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
    is_cloud_mode = cl.user_session.get("cloud_mode", False)
    
    # Check Sicurezza Cloud
    if is_cloud_mode and message.elements:
        await cl.Message(content="⛔ **Upload disabilitato in modalità Cloud per sicurezza.**").send()
        return

    # Gestione Allegati (Solo Locale)
    context_text = ""
    if message.elements and not is_cloud_mode:
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
                    
                    # Limite sicurezza caratteri
                    if len(text_content) > 10000:
                        text_content = text_content[:10000] + "\n...[TRONCATO]..."
                        
                    context_text += f"\n--- DOC UTENTE: {element.name} ---\n{text_content}\n"
                except Exception as e:
                    print(f"Errore file: {e}")
        await processing_msg.remove()

    if context_text:
        full_query = f"FILE CONTESTO:\n{context_text}\n\nRICHIESTA: {message.content}"
    else:
        full_query = message.content

    msg = cl.Message(content="")
    
    try:
        if is_cloud_mode:
            # Cloud (Gemini)
            response = await cl.make_async(chat_engine.stream_chat)(full_query)
            full_resp = ""
            for token in response.response_gen:
                full_resp += token
                await msg.stream_token(token)
            
            # --- FIX: USA MARKDOWN INVECE DI HTML ---
            # ### Crea un titolo H3
            # > Crea un blocco citazione (barra verticale a sinistra)
            msg.content = f"### ☁️ **Gemini (Cloud)**\n> {full_resp}"
            await msg.update()
        
        else:
            # Locale (Llama)
            response = await cl.make_async(chat_engine.stream_chat)(full_query)
            
            for token in response.response_gen:
                await msg.stream_token(token)

            # Fonti
            if hasattr(response, "source_nodes") and response.source_nodes:
                seen = set()
                text_sources = []
                elements = []
                
                for node in response.source_nodes:
                    fname = node.metadata.get("filename", "Sconosciuto")
                    rel_path = node.metadata.get("file_path", fname)
                    
                    if fname and fname not in seen:
                        path = ARCHIVE_DIR / rel_path
                        if path.exists():
                            if path.suffix.lower() == ".pdf":
                                elements.append(cl.Pdf(name=fname, display="side", path=str(path)))
                            else:
                                elements.append(cl.File(name=fname, display="inline", path=str(path)))
                        
                        text_sources.append(fname)
                        seen.add(fname)
                
                if elements: msg.elements = elements
                if text_sources: msg.content += "\n\n**📚 Fonti:**\n" + "\n".join([f"- {s}" for s in text_sources])

            await msg.update()
        
    except Exception as e:
        await cl.Message(content=f"⚠️ Errore generazione: {str(e)}").send()