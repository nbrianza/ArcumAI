import traceback
import nest_asyncio
from pathlib import Path
from urllib.parse import quote
from nicegui import ui

# Import Logic
from src.readers import SmartPDFReader
from src.utils import find_relative_path
from src.config import ARCHIVE_DIR

def create_footer(session, user_data, chat_container, mode_display):
    """
    Crea il footer con l'input e gestisce la logica di invio messaggi e upload.
    
    Args:
        session: La sessione utente (UserSession).
        user_data: Dati utente (nome, avatar, ecc.).
        chat_container: La colonna UI dove stampare i messaggi.
        mode_display: L'etichetta della sidebar da aggiornare (es. "Analisi in corso").
    """
    
    # --- UI FOOTER ---
    with ui.footer().classes('bg-slate-50 p-4 border-t border-gray-200 pr-[300px]'): 
        
        # Anteprima File Caricato
        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):
            
            # --- LOGICA UPLOAD ---
            async def handle_upload(e):
                file_obj = getattr(e, 'content', None) or getattr(e, 'file', None)
                filename = getattr(e, 'name', None) or getattr(e, 'filename', None)
                if not filename and hasattr(e, 'file'):
                     filename = getattr(e.file, 'name', None) or getattr(e.file, 'filename', None)
                
                if not filename: filename = "upload_senza_nome.pdf"

                print(f"📥 UPLOAD AVVIATO: {filename}")
                ui.notify(f'Analisi {filename}...', type='info')
                
                # Update Sidebar
                mode_display.text = "⏳ Analisi File..."
                mode_display.classes(replace='text-orange-500')

                try:
                    if not file_obj: raise ValueError("Oggetto file vuoto.")
                    
                    # Async Read Fix
                    if hasattr(file_obj, 'read'):
                        data = file_obj.read()
                        if hasattr(data, '__await__'): data = await data
                    else:
                        data = file_obj
                    
                    text_content = ""
                    is_pdf = filename.lower().endswith(".pdf")
                    
                    if is_pdf:
                        temp_path = Path("temp_ghost_upload.pdf")
                        with open(temp_path, "wb") as f: f.write(data)
                        
                        def read_pdf_sync():
                            reader = SmartPDFReader()
                            docs = reader.load_data(temp_path)
                            return "\n".join([d.text for d in docs])

                        text_content = await nest_asyncio.asyncio.get_event_loop().run_in_executor(None, read_pdf_sync)
                        if temp_path.exists(): temp_path.unlink()
                    else:
                        text_content = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
                    
                    if not text_content.strip(): raise ValueError("File vuoto o illeggibile.")

                    # Salva in Sessione
                    session.uploaded_context = f"FILE UTENTE ({filename}):\n{text_content[:25000]}\n"
                    
                    # Update UI
                    context_label.text = f"📎 Allegato pronto: {filename}"
                    context_preview.set_visibility(True)
                    mode_display.text = f"📄 File: {filename[:15]}..."
                    mode_display.classes(replace='text-blue-600')
                    
                    ui.notify('✅ Documento analizzato! Chiedi pure.', type='positive')
                    
                except Exception as err:
                    print(f"❌ Error: {traceback.format_exc()}")
                    ui.notify(f'Errore: {str(err)}', type='negative')
                    mode_display.text = "❌ Errore Caricamento"
                    mode_display.classes(replace='text-red-600')

            # Elemento Upload Invisibile (Ghost)
            upload_element = ui.upload(
                auto_upload=True, 
                on_upload=handle_upload,
                max_file_size=15_000_000
            ).props('hide-upload-btn no-thumbnails accept=".pdf, .txt, .md"').style('position: absolute; top: -9999px; left: -9999px;')
            
            # Bottone Graffetta
            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')
            
            if session.is_cloud: upload_btn.set_visibility(False)

            # --- LOGICA INVIO MESSAGGIO ---
            async def send_message():
                text = input_field.value
                if not text: return
                
                full_query = text
                has_attachment = False
                
                # Iniezione Contesto File
                if session.uploaded_context and not session.is_cloud:
                    full_query = f"ANALIZZA:\n{session.uploaded_context}\n\nDOMANDA: {text}"
                    session.uploaded_context = "" 
                    context_label.text = ""
                    context_preview.set_visibility(False)
                    has_attachment = True

                input_field.value = '' 
                
                # Rendering Messaggio Utente
                with chat_container:
                    avatar_me = f'https://ui-avatars.com/api/?name={quote(user_data["full_name"])}&background=gray&color=fff'
                    ui.chat_message(text, name='Tu', sent=True, avatar=avatar_me)
                
                # Rendering Messaggio AI (Placeholder)
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

                # Logica Selezione Engine
                try:
                    engine = None
                    used_mode = "SIMPLE"

                    if session.is_cloud:
                        engine = await session.get_cloud_engine()
                        used_mode = "CLOUD"
                        mode_display.text = "☁️ Gemini Cloud"
                        mode_display.classes(replace='text-orange-600')
                    elif has_attachment:
                        engine = await session.get_simple_engine()
                        used_mode = "FILE READER"
                        mode_display.text = "📄 Analisi Allegato"
                        mode_display.classes(replace='text-blue-600')
                    else:
                        decision = await session.decide_engine(text) 
                        if decision == "RAG":
                            engine = await session.get_rag_engine()
                            used_mode = "RAG (DB)"
                            mode_display.text = "📚 RAG (Database)"
                            mode_display.classes(replace='text-purple-600')
                        else:
                            engine = await session.get_simple_engine()
                            used_mode = "SIMPLE"
                            mode_display.text = "🟢 Chat Locale"
                            mode_display.classes(replace='text-green-600')

                    clean_query = full_query.replace("@rag", "").replace("@cerca", "").replace("@simple", "").replace("@chat", "").strip()
                    if not clean_query: clean_query = full_query

                    print(f"⚙️ {used_mode} -> Processing...")
                    
                    # ESECUZIONE REALE
                    response = await engine.achat(clean_query)
                    
                    try: spinner.delete()
                    except: pass
                    
                    response_area.set_content(str(response) or "⚠️ Risposta vuota.")
                    
                    # Visualizzazione Fonti (Solo RAG Locale)
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
                    try: spinner.delete()
                    except: pass
                    
                    print(f"❌ Error: {traceback.format_exc()}")
                    
                    err_msg = str(e)
                    if "ReadTimeout" in err_msg:
                        err_msg = "⏳ Il documento è molto complesso e l'AI ha impiegato troppo tempo. Riprova."
                    
                    ui.notify(f"Errore: {err_msg}", type='negative')
                    with bot_msg: ui.label(f"❌ {err_msg}").classes('text-red-600 font-bold')

            # Input Field
            input_field = ui.input(placeholder='Scrivi qui...').classes('w-full text-black').props('outlined rounded bg-color=white').on('keydown.enter', send_message)
            ui.button(icon='send', on_click=send_message).props('flat round color=primary')
            
    # Restituiamo input_field e upload_btn nel caso servano fuori (es. per header toggle)
    return input_field, upload_btn