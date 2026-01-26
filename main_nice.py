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
    DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, RETRIEVER_TOP_K, init_settings
)
from src.readers import SmartPDFReader

# --- 2. CONFIGURAZIONE INIZIALE ---
init_settings()
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Configura la cartella statica (Root dell'archivio)
if not ARCHIVE_DIR.exists():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files('/documents', str(ARCHIVE_DIR))

# --- FUNZIONI HELPER BACKEND ---

# 1. HELPER RICERCA FILE (Per link robusti)
def find_relative_path(filename: str) -> str:
    """Cerca il file nelle sottocartelle per generare link corretti"""
    try:
        matches = list(ARCHIVE_DIR.rglob(filename))
        if matches:
            rel_path = matches[0].relative_to(ARCHIVE_DIR)
            return str(rel_path).replace('\\', '/')
    except Exception: pass
    return filename

# 2. MOTORE RAG (Pesante - Per domande sui documenti)
def load_rag_engine():
    """Carica il motore che legge il database (Lento ma preciso sui file)"""
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

    # Usa il template permissivo definito in config.py
    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=4000),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        context_template=CUSTOM_CONTEXT_TEMPLATE, # Fondamentale per non bloccare risposte miste
        llm=Settings.llm 
    )

# 3. MOTORE CHAT SEMPLICE (Veloce - Per saluti/matematica)
def load_simple_local_engine():
    """Motore leggero senza accesso al database vettoriale (Velocissimo)"""
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente utile e conciso. Rispondi direttamente senza cercare documenti.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=2000)
    )

# 4. MOTORE CLOUD (Gemini)
def load_cloud_engine():
    if not GOOGLE_API_KEY: raise ValueError("Manca GOOGLE_API_KEY nel file .env")
    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=GOOGLE_API_KEY)
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un assistente AI avanzato (Gemini). Rispondi con precisione.",
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8000)
    )

# --- 3. GESTIONE STATO UTENTE E ROUTING ---
class UserSession:
    def __init__(self):
        self.is_cloud = False
        self.rag_engine = None    # Motore con documenti
        self.simple_engine = None # Motore veloce
        self.cloud_engine = None  # Gemini
        self.uploaded_context = "" 
        
    async def get_rag_engine(self):
        if not self.rag_engine: self.rag_engine = await run.io_bound(load_rag_engine)
        return self.rag_engine

    async def get_simple_engine(self):
        if not self.simple_engine: self.simple_engine = await run.io_bound(load_simple_local_engine)
        return self.simple_engine

    async def get_cloud_engine(self):
        if not self.cloud_engine: self.cloud_engine = await run.io_bound(load_cloud_engine)
        return self.cloud_engine

    # --- IL PORTIERE (ROUTER) ---
    async def decide_engine(self, text):
        """Decide se usare il motore RAG (Legge) o Simple (Chat)"""
        text_lower = text.lower()
        
        # 1. Controllo rapido parole chiave (Istantaneo)
        triggers_law = ['legge', 'art', 'articolo', 'regolamento', 'decreto', 'pdf', 'documento', 'sentenza', 'comma', 'cerca', 'contratto']
        if any(t in text_lower for t in triggers_law):
            return "RAG"
        
        # 2. Controllo rapido matematica/saluti (Istantaneo)
        triggers_chat = ['ciao', 'buongiorno', 'come stai', 'quanto fa', 'chi sei', 'grazie', 'calcola', 'aiutami' , '#insta']
        if any(t in text_lower for t in triggers_chat):
            return "SIMPLE"

        # 3. Fallback: Se incerto, chiediamo all'LLM (Routing Intelligente)
        try:
            # Usiamo una chiamata 'complete' leggera
            prompt = (
                f"Analizza la richiesta: '{text}'.\n"
                "Se riguarda leggi, normative o documenti rispondi 'RAG'.\n"
                "Se è matematica, saluti o cultura generale rispondi 'SIMPLE'.\n"
                "Rispondi SOLO con una parola."
            )
            # Nota: usiamo Settings.llm direttamente per una risposta lampo
            resp = await Settings.llm.acomplete(prompt)
            decision = str(resp).strip().upper()
            if "RAG" in decision: return "RAG"
            return "SIMPLE"
        except:
            return "RAG" # Nel dubbio, meglio cercare

# --- 4. INTERFACCIA NICEGUI ---
@ui.page('/')
async def main_page():
    session = UserSession()
    
    # --- HEADER ---
    with ui.header().classes('bg-slate-900 text-white items-center gap-4 shadow-lg'):
        ui.label('🛡️ Arcum AI Hybrid').classes('text-xl font-bold tracking-wide')
        ui.space()
        status_label = ui.label('MODE: SAFE LOCAL').classes('font-bold text-green-400')

        async def toggle_mode(e):
            session.is_cloud = e.value
            if session.is_cloud:
                status_label.text = 'MODE: CLOUD WARNING'
                status_label.classes(replace='text-orange-400')
                input_field.props('bg-color=orange-8 label="⚠️ CLOUD MODE (Gemini)" label-color=white')
                input_field.classes('text-white')
                upload_btn.set_visibility(False)
                context_preview.set_visibility(False) 
                ui.notify('ATTENZIONE: Passaggio al Cloud. Upload disabilitato!', type='warning')
            else:
                status_label.text = 'MODE: SAFE LOCAL'
                status_label.classes(replace='text-green-400')
                input_field.props('bg-color=white label="Scrivi qui..." label-color=black')
                input_field.classes('text-black')
                upload_btn.set_visibility(True)
                if session.uploaded_context: context_preview.set_visibility(True)
                ui.notify('Tornato in Locale (Safe).', type='positive')

        ui.switch('Cloud Mode', on_change=toggle_mode).props('color=orange')

    # --- AREA CHAT ---
    chat_container = ui.column().classes('w-full max-w-4xl mx-auto p-4 flex-grow gap-4')

    # --- FOOTER ---
    with ui.footer().classes('bg-slate-100 p-4 border-t'):
        
        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):
            
            # GESTIONE UPLOAD
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
                    ui.notify('File letto con successo!', type='positive')
                except Exception as err:
                    ui.notify(f'Errore lettura: {str(err)}', type='negative')

            upload_element = ui.upload(auto_upload=True, on_upload=handle_upload).props('hide-upload-btn').classes('hidden')
            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')

            # FUNZIONE INVIO MESSAGGIO (ROUTER EDITION)
            async def send_message():
                text = input_field.value
                if not text: return
                
                print(f"\n💬 USER: {text}")
                
                full_query = text
                # Se c'è un file allegato, forziamo il RAG engine
                has_attachment = False
                if session.uploaded_context and not session.is_cloud:
                    full_query = f"CONTESTO FILE:\n{session.uploaded_context}\n\nDOMANDA: {text}"
                    session.uploaded_context = "" 
                    context_label.text = ""
                    context_preview.set_visibility(False)
                    has_attachment = True

                input_field.value = '' 
                
                # 1. UI UTENTE
                with chat_container:
                    ui.chat_message(text, name='Tu', sent=True, 
                                    avatar='https://ui-avatars.com/api/?name=Tu&background=gray&color=fff')
                
                # 2. UI BOT
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
                
                # 3. LOGICA DI ROUTING INTELLIGENTE
                try:
                    engine = None
                    used_mode = ""

                    if session.is_cloud:
                        engine = await session.get_cloud_engine()
                        used_mode = "CLOUD"
                    else:
                        # SELEZIONE INTELLIGENTE LOCALE
                        if has_attachment:
                            # Se ho caricato un file, uso sempre il motore RAG
                            engine = await session.get_rag_engine()
                            used_mode = "RAG (File)"
                        else:
                            # Altrimenti chiedo al Router
                            decision = await session.decide_engine(text)
                            print(f"🚦 ROUTER DECISION: {decision}")
                            
                            if decision == "RAG":
                                engine = await session.get_rag_engine()
                                used_mode = "RAG (DB)"
                            else:
                                engine = await session.get_simple_engine()
                                used_mode = "SIMPLE (No DB)"

                    print(f"⚙️ Motore attivo: {used_mode}")
                    
                    # 4. GENERAZIONE
                    response = await engine.achat(full_query)
                    
                    spinner.delete()
                    response_text = str(response)
                    if not response_text: response_text = "⚠️ Risposta vuota."
                    response_area.set_content(response_text)
                    print("✅ Risposta generata.")
                    
                    # 5. FONTI (Solo se RAG e Locale)
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
                                    
                                    # Ricerca Path
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
    print("🚀 Avvio Arcum AI (Router Edition)...")
    ui.run(title='Arcum AI', host='0.0.0.0', port=8080, favicon='🛡️', reload=False)