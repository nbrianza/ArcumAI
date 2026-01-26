import os
import asyncio
import traceback
from pathlib import Path
from urllib.parse import quote 
from nicegui import ui, app, run

# --- CARICAMENTO VARIABILI D'AMBIENTE (.env) ---
from dotenv import load_dotenv
load_dotenv() 

# --- 1. IMPORT BACKEND ---
import nest_asyncio
nest_asyncio.apply()

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.llms.gemini import Gemini

# Import dai tuoi moduli src
from src.config import (
    CHROMA_PATH, COLLECTION_NAME, BM25_PATH, ARCHIVE_DIR, 
    DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, RETRIEVER_TOP_K, 
    ROLE_PROMPTS, init_settings
)
from src.readers import SmartPDFReader

# --- IMPORT MODULO AUTH CONDIVISO ---
# Ora usiamo le funzioni di src/auth.py per coerenza totale con admin_tool
from src.auth import load_users, verify_password

# --- 2. CONFIGURAZIONE INIZIALE ---
init_settings()
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Configura la cartella statica
if not ARCHIVE_DIR.exists():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files('/documents', str(ARCHIVE_DIR))


# --- FUNZIONI HELPER BACKEND ---

def find_relative_path(filename: str) -> str:
    """Cerca il file nelle sottocartelle per generare link corretti"""
    try:
        matches = list(ARCHIVE_DIR.rglob(filename))
        if matches:
            rel_path = matches[0].relative_to(ARCHIVE_DIR)
            return str(rel_path).replace('\\', '/')
    except Exception: pass
    return filename

# 2. MOTORE RAG (Pesante - Sensibile al Ruolo)
def load_rag_engine(user_role="DEFAULT"):
    """Carica il motore RAG applicando il System Prompt specifico del Ruolo"""
    db = chromadb.PersistentClient(path=str(CHROMA_PATH))
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

    # --- SELEZIONE PROMPT IN BASE AL RUOLO ---
    selected_prompt = ROLE_PROMPTS.get(user_role, DEFAULT_SYSTEM_PROMPT)
    print(f"🎭 Caricamento profilo: {user_role}")

    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE, 
        llm=Settings.llm 
    )

# 3. MOTORE CHAT SEMPLICE
def load_simple_local_engine():
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente utile e conciso. Rispondi direttamente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=2000)
    )

# 4. MOTORE CLOUD
def load_cloud_engine():
    if not GOOGLE_API_KEY: raise ValueError("Manca GOOGLE_API_KEY nel file .env")
    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=GOOGLE_API_KEY)
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente AI avanzato (Gemini). Rispondi con precisione.",
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8000)
    )

# --- 3. GESTIONE SESSIONE ---
class UserSession:
    def __init__(self, role="DEFAULT"):
        self.role = role
        self.is_cloud = False
        self.rag_engine = None    
        self.simple_engine = None 
        self.cloud_engine = None  
        self.uploaded_context = "" 
        
    async def get_rag_engine(self):
        # Passiamo il ruolo al caricatore del motore
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
        """Router Intelligente"""
        text_lower = text.lower()
        triggers_law = ['legge', 'art', 'articolo', 'regolamento', 'decreto', 'pdf', 'documento', 'sentenza', 'comma', 'cerca']
        if any(t in text_lower for t in triggers_law): return "RAG"
        
        triggers_chat = ['ciao', 'buongiorno', 'come stai', 'quanto fa', 'chi sei', 'grazie', 'calcola', 'aiutami']
        if any(t in text_lower for t in triggers_chat): return "SIMPLE"

        try:
            prompt = (f"Analizza: '{text}'. Se riguarda leggi/docs rispondi 'RAG'. "
                      "Se mate/saluti rispondi 'SIMPLE'. Rispondi SOLO 1 parola.")
            resp = await Settings.llm.acomplete(prompt)
            decision = str(resp).strip().upper()
            if "RAG" in decision: return "RAG"
            return "SIMPLE"
        except: return "RAG"

# --- 4. INTERFACCIA NICEGUI ---

@ui.page('/login')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    users_db = load_users()

    with ui.card().classes('absolute-center w-96 p-8 shadow-2xl'):
        ui.label('🛡️ Arcum AI Login').classes('text-2xl font-bold text-center w-full mb-4 text-slate-800')
        
        username = ui.input('Username').classes('w-full').on('keydown.enter', lambda: try_login())
        password = ui.input('Password', password=True).classes('w-full').on('keydown.enter', lambda: try_login())
        
        error_msg = ui.label('').classes('text-red-500 text-sm hidden')

        def try_login():
            user = username.value
            pwd = password.value
            
            if user in users_db:
                # --- CORREZIONE CRUCIALE ---
                # auth.py usa la chiave "pw_hash", non "password"!
                # Questo allinea main_nice.py con i dati generati da admin_tool.py
                stored_hash = users_db[user].get('pw_hash', '') 
                
                # Verifica delegata a src/auth.py (che usa bcrypt)
                if verify_password(pwd, stored_hash):
                    app.storage.user['authenticated'] = True
                    app.storage.user['username'] = user
                    app.storage.user['role'] = users_db[user].get('role', 'DEFAULT')
                    app.storage.user['full_name'] = users_db[user].get('name', user)
                    
                    ui.notify(f'Benvenuto {app.storage.user["full_name"]}!', type='positive')
                    ui.navigate.to('/')
                    return

            error_msg.text = 'Credenziali non valide'
            error_msg.classes(remove='hidden')
            ui.notify('Errore Login', type='negative')

        ui.button('Accedi', on_click=try_login).props('color=slate-900').classes('w-full mt-4')

@ui.page('/')
async def main_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/login')
        return

    current_user = app.storage.user['username']
    current_role = app.storage.user['role']
    full_name = app.storage.user.get('full_name', current_user)

    session = UserSession(role=current_role)
    
    with ui.header().classes('bg-slate-900 text-white items-center gap-4 shadow-lg'):
        ui.label('🛡️ Arcum AI').classes('text-xl font-bold tracking-wide')
        
        with ui.row().classes('items-center gap-2 bg-slate-800 rounded-full px-3 py-1'):
            ui.icon('person').classes('text-orange-400')
            ui.label(f"{full_name}").classes('text-sm font-bold')
            ui.label(f"[{current_role}]").classes('text-xs text-gray-400')

        ui.space()
        status_label = ui.label('MODE: SAFE LOCAL').classes('font-bold text-green-400')

        def logout():
            app.storage.user.clear()
            ui.navigate.to('/login')
        
        ui.button(icon='logout', on_click=logout).props('flat round color=white')

        async def toggle_mode(e):
            session.is_cloud = e.value
            if session.is_cloud:
                status_label.text = 'MODE: CLOUD WARNING'
                status_label.classes(replace='text-orange-400')
                input_field.props('bg-color=orange-8 label="⚠️ CLOUD MODE (Gemini)" label-color=white')
                input_field.classes('text-white')
                upload_btn.set_visibility(False)
                ui.notify('Passaggio al Cloud.', type='warning')
            else:
                status_label.text = 'MODE: SAFE LOCAL'
                status_label.classes(replace='text-green-400')
                input_field.props('bg-color=white label="Scrivi qui..." label-color=black')
                input_field.classes('text-black')
                upload_btn.set_visibility(True)
                ui.notify('Tornato in Locale (Safe).', type='positive')

        ui.switch('Cloud', on_change=toggle_mode).props('color=orange')

    chat_container = ui.column().classes('w-full max-w-4xl mx-auto p-4 flex-grow gap-4')

    with ui.footer().classes('bg-slate-100 p-4 border-t'):
        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):
            
            async def handle_upload(e):
                file = e.content
                filename = e.name
                ui.notify(f'Analisi {filename}...', type='info')
                try:
                    text_content = ""
                    if filename.lower().endswith(".pdf"):
                        temp_path = Path(f"temp_{filename}")
                        with open(temp_path, "wb") as f: f.write(file.read())
                        reader = SmartPDFReader()
                        docs = reader.load_data(temp_path)
                        text_content = "\n".join([d.text for d in docs])
                        if temp_path.exists(): temp_path.unlink()
                    else:
                        text_content = file.read().decode("utf-8", errors="ignore")
                    
                    if len(text_content) > 10000: text_content = text_content[:10000] + "\n...[TRONCATO]"
                    session.uploaded_context = f"FILE UTENTE ({filename}):\n{text_content}\n"
                    context_label.text = f"📎 Allegato pronto: {filename}"
                    context_preview.set_visibility(True)
                    ui.notify('File letto!', type='positive')
                except Exception as err:
                    ui.notify(f'Errore: {str(err)}', type='negative')

            upload_element = ui.upload(auto_upload=True, on_upload=handle_upload).props('hide-upload-btn').classes('hidden')
            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')

            async def send_message():
                text = input_field.value
                if not text: return
                
                print(f"\n💬 [{current_user}] USER: {text}")
                
                full_query = text
                has_attachment = False
                if session.uploaded_context and not session.is_cloud:
                    full_query = f"CONTESTO FILE:\n{session.uploaded_context}\n\nDOMANDA: {text}"
                    session.uploaded_context = "" 
                    context_label.text = ""
                    context_preview.set_visibility(False)
                    has_attachment = True

                input_field.value = '' 
                
                with chat_container:
                    user_avatar = f'https://ui-avatars.com/api/?name={quote(full_name)}&background=gray&color=fff'
                    ui.chat_message(text, name='Tu', sent=True, avatar=user_avatar)
                
                with chat_container:
                    if session.is_cloud:
                        avatar_name, avatar_bg, msg_props = "Cloud", "E67E22", 'bg-color=orange-2 text-color=black'
                    else:
                        avatar_name, avatar_bg, msg_props = "AI", "2ECC71", 'bg-color=green-2 text-color=black'

                    avatar_url = f'https://ui-avatars.com/api/?name={avatar_name}&background={avatar_bg}&color=fff'
                    bot_msg = ui.chat_message(name='Arcum AI', avatar=avatar_url).props(msg_props)
                    
                    with bot_msg:
                        with ui.column().classes('w-full gap-2'): 
                            spinner = ui.spinner(size='sm')
                            response_area = ui.markdown()
                            sources_row = ui.row().classes('gap-2 mt-2 flex-wrap')
                
                try:
                    engine = None
                    used_mode = ""

                    if session.is_cloud:
                        engine = await session.get_cloud_engine()
                        used_mode = "CLOUD"
                    else:
                        if has_attachment:
                            engine = await session.get_rag_engine()
                            used_mode = "RAG (File)"
                        else:
                            decision = await session.decide_engine(text)
                            print(f"🚦 ROUTER DECISION: {decision}")
                            if decision == "RAG":
                                engine = await session.get_rag_engine()
                                used_mode = f"RAG ({current_role})" 
                            else:
                                engine = await session.get_simple_engine()
                                used_mode = "SIMPLE"

                    print(f"⚙️ Motore attivo: {used_mode}")
                    response = await engine.achat(full_query)
                    
                    spinner.delete()
                    response_text = str(response)
                    if not response_text: response_text = "⚠️ Risposta vuota."
                    response_area.set_content(response_text)
                    
                    if not session.is_cloud and "RAG" in used_mode and hasattr(response, "source_nodes") and response.source_nodes:
                        seen = set()
                        with sources_row: 
                            ui.label("📚 Fonti:").classes('text-xs font-bold text-gray-700 mr-2 self-center opacity-70')
                            for node in response.source_nodes:
                                fname = node.metadata.get("filename", "Doc")
                                meta_path = node.metadata.get("file_path") 
                                if fname not in seen:
                                    seen.add(fname)
                                    icon = 'description' 
                                    if fname.lower().endswith('.pdf'): icon = 'picture_as_pdf'
                                    elif fname.lower().endswith(('.msg', '.eml')): icon = 'mail'
                                    
                                    relative_path = None
                                    if meta_path:
                                        try:
                                            p_meta = Path(meta_path)
                                            if p_meta.exists(): relative_path = p_meta.relative_to(ARCHIVE_DIR.absolute())
                                        except: pass
                                    if not relative_path:
                                        found = find_relative_path(fname)
                                        if found != fname: relative_path = found
                                        else: relative_path = fname 
                                    
                                    path_str = str(relative_path).replace('\\', '/')
                                    safe_path = quote(path_str, safe='/')
                                    url_link = f'/documents/{safe_path}'
                                    
                                    with ui.link(target=url_link, new_tab=True).classes('no-underline decoration-none'):
                                        with ui.row().classes('items-center border border-gray-400/30 rounded px-2 py-1 bg-white/50 hover:bg-white/80 cursor-pointer gap-1 transition-colors'):
                                            ui.icon(icon).classes('text-gray-700 text-xs')
                                            ui.label(fname).classes('text-xs text-gray-800 max-w-[150px] truncate')

                except Exception as e:
                    spinner.delete()
                    print(f"\n❌ ERRORE:\n{traceback.format_exc()}\n")
                    ui.notify(f"Errore: {str(e)}", type='negative')
                    with bot_msg: ui.label(f"❌ Errore: {str(e)}").classes('text-red-600 font-bold')

            input_field = ui.input(placeholder='Scrivi qui...').classes('w-full text-black').props('outlined rounded bg-color=white').on('keydown.enter', send_message)
            ui.button(icon='send', on_click=send_message).props('flat round color=primary')

if __name__ in {"__main__", "__mp_main__"}:
    print("🚀 Avvio Arcum AI (Login + Router Edition)...")
    ui.run(title='Arcum AI', host='0.0.0.0', port=8080, favicon='🛡️', reload=False, storage_secret='CHIAVE_SUPER_SEGRETA_ARCUM_AI')