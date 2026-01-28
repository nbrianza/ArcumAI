import traceback
from pathlib import Path
from urllib.parse import quote 
from nicegui import ui, app

# Setup Ambiente
from dotenv import load_dotenv
load_dotenv() 
import nest_asyncio
nest_asyncio.apply()

# Import Moduli Interni (Refactored)
from src.config import ARCHIVE_DIR, init_settings
from src.readers import SmartPDFReader
from src.auth import load_users, verify_password
from src.utils import find_relative_path
from src.engine import UserSession

# Inizializza Settings LlamaIndex
init_settings()

# --- SETUP ASSETS STATICI ---
if not ARCHIVE_DIR.exists(): ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files('/documents', str(ARCHIVE_DIR))

# Cartella Assets per il Logo (FONDAMENTALE)
ASSETS_DIR = Path("assets")
if not ASSETS_DIR.exists(): ASSETS_DIR.mkdir()
app.add_static_files('/assets', str(ASSETS_DIR))


# --- PAGINA LOGIN ---
@ui.page('/login')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    users_db = load_users()

    # Card scura per far risaltare il logo bianco
    with ui.card().classes('absolute-center w-96 p-8 shadow-2xl bg-slate-900 border border-slate-700'):
        # LOGO
        ui.image('/assets/logow.png').classes('w-32 mx-auto mb-4 object-contain')
        
        ui.label('Accesso Riservato').classes('text-xl font-bold text-center w-full mb-4 text-white')
        
        # Input scuri
        username = ui.input('Username').classes('w-full text-white').props('dark outlined input-class=text-white').on('keydown.enter', lambda: try_login())
        password = ui.input('Password', password=True).classes('w-full text-white').props('dark outlined input-class=text-white').on('keydown.enter', lambda: try_login())
        error_msg = ui.label('').classes('text-red-400 text-sm hidden mt-2 text-center w-full')

        def try_login():
            user = username.value
            pwd = password.value
            if user in users_db:
                stored_hash = users_db[user].get('pw_hash', '') 
                if verify_password(pwd, stored_hash):
                    app.storage.user.update({
                        'authenticated': True,
                        'username': user,
                        'role': users_db[user].get('role', 'DEFAULT'),
                        'full_name': users_db[user].get('name', user)
                    })
                    ui.notify(f'Benvenuto {app.storage.user["full_name"]}!', type='positive')
                    ui.navigate.to('/')
                    return
            error_msg.text = 'Credenziali non valide'
            error_msg.classes(remove='hidden')
            ui.notify('Errore Login', type='negative')

        ui.button('Accedi', on_click=try_login).props('color=orange-500 text-color=white').classes('w-full mt-6 font-bold')

# --- PAGINA PRINCIPALE ---
@ui.page('/')
async def main_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/login')
        return

    user_data = app.storage.user
    session = UserSession(role=user_data['role']) 
    
    # --- HEADER CON LOGO (Area Rossa) ---
    with ui.header().classes('bg-slate-900 text-white items-center gap-4 shadow-lg h-20 px-4 border-b border-slate-800'):
        # LOGO (Assicurati che il file si chiami logow.png in assets/)
        ui.image('/assets/logow.png').classes('h-16 w-16 object-contain')
        
        # Badge Utente
        with ui.row().classes('items-center gap-2 bg-slate-800 rounded-full px-3 py-1 ml-4 hidden md:flex border border-slate-700'):
            ui.icon('person').classes('text-orange-400')
            ui.label(f"{user_data['full_name']}").classes('text-sm font-bold text-slate-200')
            ui.label(f"[{user_data['role']}]").classes('text-[10px] text-slate-400 uppercase tracking-wider')

        ui.space()
        
        status_label = ui.label('MODE: SAFE LOCAL').classes('font-bold text-green-400 text-sm md:text-base')

        def logout():
            app.storage.user.clear()
            ui.navigate.to('/login')
        ui.button(icon='logout', on_click=logout).props('flat round color=white size=sm')

        async def toggle_mode(e):
            session.is_cloud = e.value
            if session.is_cloud:
                status_label.text = 'MODE: CLOUD WARNING'
                status_label.classes(replace='text-orange-400')
                input_field.props('bg-color=orange-8 label="⚠️ CLOUD MODE" label-color=white text-color=white')
                upload_btn.set_visibility(False)
            else:
                status_label.text = 'MODE: SAFE LOCAL'
                status_label.classes(replace='text-green-400')
                input_field.props('bg-color=white label="Scrivi qui..." label-color=black text-color=black')
                upload_btn.set_visibility(True)
        ui.switch('Cloud', on_change=toggle_mode).props('color=orange')

    # --- SIDEBAR LEGENDA (Area Blu) ---
    with ui.right_drawer(fixed=True, value=True).classes('bg-white border-l border-gray-200 p-4').props('width=280'):
        ui.label('💡 Guida Comandi').classes('text-lg font-bold text-slate-800 mb-4')

        # Card RAG
        with ui.card().classes('w-full mb-3 bg-blue-50 border-l-4 border-blue-600 shadow-sm'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('search').classes('text-blue-700')
                ui.label('@cerca / @rag').classes('font-bold text-blue-800')
            ui.markdown('Cerca nei **documenti PDF**.').classes('text-xs text-gray-700 mt-1 leading-tight')
            ui.label('Es: @cerca legge funghi').classes('text-[10px] text-gray-500 mt-1 italic')

        # Card CHAT
        with ui.card().classes('w-full mb-3 bg-green-50 border-l-4 border-green-600 shadow-sm'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('chat').classes('text-green-700')
                ui.label('@chat / @simple').classes('font-bold text-green-800')
            ui.markdown('Solo **Chat** (No documenti).').classes('text-xs text-gray-700 mt-1 leading-tight')
            ui.label('Es: @chat scrivi una mail').classes('text-[10px] text-gray-500 mt-1 italic')

        ui.separator().classes('my-4')
        ui.label('Sessione').classes('font-bold text-gray-600 text-sm')
        ui.label(f'Utente: {user_data["username"]}').classes('text-xs text-gray-500')
        
    # --- CHAT AREA ---
    # Margine destro per non finire sotto la sidebar
    chat_container = ui.column().classes('w-full max-w-4xl mx-auto p-4 flex-grow gap-4 pr-[300px]')

# --- FOOTER (Versione Ghost: Invisibile ma Funzionante) ---
    with ui.footer().classes('bg-slate-50 p-4 border-t border-gray-200 pr-[300px]'): 
        
        # Anteprima contesto (riga sopra l'input)
        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):
            
            async def handle_upload(e):
                # 1. Recupero robusto del file (gestisce versioni diverse di NiceGUI)
                file_obj = getattr(e, 'content', None) or getattr(e, 'file', None)
                
                # 2. Recupero del Nome Reale (FIX CRUCIALE)
                # Prima cerchiamo nell'evento, poi nell'oggetto file.
                # Inizializziamo a None per far funzionare i fallback.
                filename = getattr(e, 'name', None) or getattr(e, 'filename', None)
                
                if not filename and hasattr(e, 'file'):
                    filename = getattr(e.file, 'name', None) or getattr(e.file, 'filename', None)
                
                # 3. Fallback intelligente: Se non troviamo il nome, assumiamo sia un PDF
                # (Meglio provare a leggerlo come PDF che come testo spazzatura)
                if not filename: 
                    filename = "upload_senza_nome.pdf"

                print(f"📥 UPLOAD RILEVATO: {filename}")
                ui.notify(f'Analisi di {filename}...', type='info')

                try:
                    if not file_obj:
                        raise ValueError("Oggetto file vuoto.")

                    # 4. Lettura Dati (con fix async/await)
                    if hasattr(file_obj, 'read'):
                        data = file_obj.read()
                        if hasattr(data, '__await__'): 
                            data = await data
                    else:
                        data = file_obj
                    
                    # 5. Analisi Tipo File
                    text_content = ""
                    is_pdf = filename.lower().endswith(".pdf")
                    
                    if is_pdf:
                        # Salvataggio temporaneo necessario per LlamaIndex
                        temp_path = Path("temp_ghost_upload.pdf")
                        with open(temp_path, "wb") as f: f.write(data)
                        
                        # Thread separato per non bloccare la UI
                        def read_pdf_sync():
                            reader = SmartPDFReader()
                            docs = reader.load_data(temp_path)
                            return "\n".join([d.text for d in docs])

                        text_content = await nest_asyncio.asyncio.get_event_loop().run_in_executor(None, read_pdf_sync)
                        
                        # Pulizia
                        if temp_path.exists(): temp_path.unlink()
                    else:
                        # Testo semplice (txt, md, csv)
                        text_content = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
                    
                    if not text_content or not text_content.strip(): 
                        raise ValueError("Il file non contiene testo leggibile.")

                    # 6. Salvataggio Contesto
                    session.uploaded_context = f"FILE UTENTE ({filename}):\n{text_content[:25000]}\n"
                    print(f"✅ Testo estratto: {len(text_content)} caratteri.")
                    
                    # 7. Aggiornamento UI
                    context_label.text = f"📎 Allegato pronto: {filename}"
                    context_preview.set_visibility(True)
                    ui.notify('✅ Documento analizzato! Chiedi pure.', type='positive')
                    
                except Exception as err:
                    print(f"❌ Errore Upload: {traceback.format_exc()}")
                    ui.notify(f'Errore: {str(err)}', type='negative')
                    context_preview.set_visibility(False)

            # --- UPLOAD INVISIBILE ---
            # Lo posizioniamo fuori schermo così il browser non lo blocca, ma l'utente non lo vede.
            upload_element = ui.upload(
                auto_upload=True, 
                on_upload=handle_upload,
                max_file_size=15_000_000
            ).props('hide-upload-btn no-thumbnails accept=".pdf, .txt, .md"').style('position: absolute; top: -9999px; left: -9999px;')
            
            # Bottone Graffetta (Attiva l'upload nascosto)
            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')
            
            if session.is_cloud: upload_btn.set_visibility(False)

            async def send_message():
                text = input_field.value
                if not text: return
                
                full_query = text
                has_attachment = False
                
                # Inserimento Contesto
                if session.uploaded_context and not session.is_cloud:
                    full_query = f"ANALIZZA QUESTO DOCUMENTO:\n{session.uploaded_context}\n\nDOMANDA UTENTE: {text}"
                    session.uploaded_context = "" 
                    context_label.text = ""
                    context_preview.set_visibility(False)
                    has_attachment = True

                input_field.value = '' 
                
                with chat_container:
                    avatar_me = f'https://ui-avatars.com/api/?name={quote(user_data["full_name"])}&background=gray&color=fff'
                    ui.chat_message(text, name='Tu', sent=True, avatar=avatar_me)
                
                with chat_container:
                    is_cloud = session.is_cloud
                    bot_name = "Cloud" if is_cloud else "AI"
                    bot_bg = "E67E22" if is_cloud else "2ECC71"
                    bot_props = 'bg-color=orange-2 text-color=black' if is_cloud else 'bg-color=green-2 text-color=black'
                    
                    bot_msg = ui.chat_message(name='Arcum AI', avatar=f'https://ui-avatars.com/api/?name={bot_name}&background={bot_bg}&color=fff').props(bot_props)
                    
                    with bot_msg:
                        with ui.column().classes('w-full gap-2'): 
                            spinner = ui.spinner(size='sm')
                            response_area = ui.markdown()
                            sources_row = ui.row().classes('gap-2 mt-2 flex-wrap')

                try:
                    engine = None
                    used_mode = "SIMPLE"

                    if session.is_cloud:
                        engine = await session.get_cloud_engine()
                        used_mode = "CLOUD"
                    elif has_attachment:
                        # Se c'è un file allegato, usiamo il motore SIMPLE (Lettura diretta)
                        engine = await session.get_simple_engine()
                        used_mode = "FILE READER"
                    else:
                        decision = await session.decide_engine(text) 
                        if decision == "RAG":
                            engine = await session.get_rag_engine()
                            used_mode = "RAG (DB)"
                        else:
                            engine = await session.get_simple_engine()

                    clean_query = full_query.replace("@rag", "").replace("@cerca", "").replace("@simple", "").replace("@chat", "").strip()
                    if not clean_query: clean_query = full_query

                    print(f"⚙️ {used_mode} -> Processing...")
                    response = await engine.achat(clean_query)
                    
                    spinner.delete()
                    response_area.set_content(str(response) or "⚠️ Risposta vuota.")
                    
                    if not session.is_cloud and "RAG" in used_mode and hasattr(response, "source_nodes"):
                        seen = set()
                        with sources_row: 
                            ui.label("📚 Fonti:").classes('text-xs font-bold text-gray-700 mr-2 self-center opacity-70')
                            for node in response.source_nodes:
                                fname = node.metadata.get("filename", "Doc")
                                meta_path = node.metadata.get("file_path") 
                                if fname not in seen:
                                    seen.add(fname)
                                    icon = 'picture_as_pdf' if fname.lower().endswith('.pdf') else 'description'
                                    
                                    relative_path = None
                                    if meta_path and Path(meta_path).exists():
                                        try: relative_path = Path(meta_path).relative_to(ARCHIVE_DIR.absolute())
                                        except: pass
                                    if not relative_path:
                                        relative_path = find_relative_path(fname)
                                    
                                    url_link = f'/documents/{quote(str(relative_path).replace("\\\\", "/"), safe="/")}'
                                    
                                    with ui.link(target=url_link, new_tab=True).classes('no-underline decoration-none'):
                                        with ui.row().classes('items-center border border-gray-400/30 rounded px-2 py-1 bg-white/50 hover:bg-white/80 cursor-pointer gap-1'):
                                            ui.icon(icon).classes('text-gray-700 text-xs')
                                            ui.label(fname).classes('text-xs text-gray-800 max-w-[150px] truncate')

                except Exception as e:
                    spinner.delete()
                    print(f"❌ Error: {traceback.format_exc()}")
                    ui.notify(f"Errore: {e}", type='negative')
                    with bot_msg: ui.label(f"❌ Errore: {e}").classes('text-red-600 font-bold')

            input_field = ui.input(placeholder='Scrivi qui...').classes('w-full text-black').props('outlined rounded bg-color=white').on('keydown.enter', send_message)
            ui.button(icon='send', on_click=send_message).props('flat round color=primary')

if __name__ in {"__main__", "__mp_main__"}:
    print("🚀 Avvio Arcum AI (UI Pro)...")
    ui.run(title='Arcum AI', host='0.0.0.0', port=8080, favicon='🛡️', reload=False, storage_secret='CHIAVE_SEGRETA_ARCUM_AI_V2')