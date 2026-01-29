import traceback
import nest_asyncio
from pathlib import Path
from urllib.parse import quote
from nicegui import ui

from src.readers import SmartPDFReader
from src.utils import find_relative_path
from src.config import ARCHIVE_DIR

def create_footer(session, user_data, chat_container, mode_display):
    
    with ui.footer().classes('bg-slate-50 p-4 border-t border-gray-200 pr-[300px]'): 
        
        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):
            
            async def handle_upload(e):
                file_obj = getattr(e, 'content', None) or getattr(e, 'file', None)
                filename = getattr(e, 'name', None) or getattr(e, 'filename', None)
                if not filename and hasattr(e, 'file'):
                     filename = getattr(e.file, 'name', None) or getattr(e.file, 'filename', None)
                
                if not filename: filename = "upload_senza_nome.pdf"

                print(f"📥 UPLOAD AVVIATO: {filename}")
                ui.notify(f'Analisi {filename}...', type='info')
                mode_display.text = "⏳ Analisi File..."
                mode_display.classes(replace='text-orange-500')

                try:
                    if not file_obj: raise ValueError("Oggetto file vuoto.")
                    
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
                        
                        # Esecuzione Diretta (Stabile su Windows)
                        try:
                            reader = SmartPDFReader()
                            docs = reader.load_data(temp_path)
                            text_content = "\n".join([d.text for d in docs])
                        finally:
                            if temp_path.exists(): temp_path.unlink()
                    else:
                        text_content = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
                    
                    if not text_content or not text_content.strip(): 
                        raise ValueError("File vuoto o illeggibile.")

                    # LIMITE 10k per Llama 3.2
                    session.uploaded_context = text_content[:10000]
                    
                    context_label.text = f"📎 Allegato pronto: {filename}"
                    context_preview.set_visibility(True)
                    mode_display.text = f"📄 File: {filename[:15]}..."
                    mode_display.classes(replace='text-blue-600')
                    
                    ui.notify('✅ Documento analizzato! Chiedi pure.', type='positive')
                    
                except Exception as err:
                    print(f"❌ Errore Upload: {traceback.format_exc()}")
                    ui.notify(f'Errore: {str(err)}', type='negative')
                    mode_display.text = "❌ Errore Caricamento"
                    mode_display.classes(replace='text-red-600')

            upload_element = ui.upload(auto_upload=True, on_upload=handle_upload, max_file_size=15_000_000).props('hide-upload-btn no-thumbnails accept=".pdf, .txt, .md"').style('position: absolute; top: -9999px; left: -9999px;')
            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')
            if session.is_cloud: upload_btn.set_visibility(False)

            async def send_message():
                text = input_field.value
                if not text: return
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
                    # Passiamo il controllo all'engine
                    response, used_mode = await session.run_chat_action(text)
                    
                    if used_mode == "CLOUD":
                        mode_display.text = "☁️ Gemini Cloud"
                        mode_display.classes(replace='text-orange-600')
                    elif used_mode == "FILE READER":
                        mode_display.text = "📄 Analisi Allegato"
                        mode_display.classes(replace='text-blue-600')
                    elif used_mode == "RAG":
                        mode_display.text = "📚 RAG (Database)"
                        mode_display.classes(replace='text-purple-600')
                    else:
                        mode_display.text = "🟢 Chat Locale"
                        mode_display.classes(replace='text-green-600')
                    
                    try: spinner.delete()
                    except: pass
                    
                    response_area.set_content(str(response) or "⚠️ Risposta vuota.")
                    
                    if not session.is_cloud and used_mode == "RAG" and hasattr(response, "source_nodes"):
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
                                    if not relative_path: relative_path = find_relative_path(fname)
                                    
                                    url_link = f'/documents/{quote(str(relative_path).replace("\\\\", "/"), safe="/")}'
                                    with ui.link(target=url_link, new_tab=True).classes('no-underline decoration-none'):
                                        with ui.row().classes('items-center border border-gray-400/30 rounded px-2 py-1 bg-white/50 hover:bg-white/80 cursor-pointer gap-1'):
                                            ui.icon(icon).classes('text-gray-700 text-xs')
                                            ui.label(fname).classes('text-xs text-gray-800 max-w-[150px] truncate')

                except Exception as e:
                    try: spinner.delete()
                    except: pass
                    print(f"❌ Error Chat: {traceback.format_exc()}")
                    err_msg = str(e)
                    if "ReadTimeout" in err_msg:
                        err_msg = "⏳ Il documento è troppo complesso. Riprova."
                    ui.notify(f"Errore: {err_msg}", type='negative')
                    with bot_msg: ui.label(f"❌ {err_msg}").classes('text-red-600 font-bold')

            input_field = ui.input(placeholder='Scrivi qui...').classes('w-full text-black').props('outlined rounded bg-color=white').on('keydown.enter', send_message)
            ui.button(icon='send', on_click=send_message).props('flat round color=primary')
            
    return input_field, upload_btn