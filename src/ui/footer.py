# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import traceback
import nest_asyncio
from pathlib import Path
from urllib.parse import quote
from nicegui import ui, run  # <--- ADDED 'run'

from src.readers import SmartPDFReader
from src.utils import find_relative_path
from src.config import ARCHIVE_DIR
from src.logger import server_log as slog
from src.ui.rate_limiter import _check_rate_limit, sanitize_input

def create_footer(session, user_data, chat_container, mode_display, on_message_sent=None):

    with ui.footer().classes('bg-slate-50 p-4 border-t border-gray-200 pr-[300px]'):

        with ui.row().classes('w-full max-w-4xl mx-auto mb-2 text-sm text-gray-600 items-center gap-2') as context_preview:
            context_label = ui.label('').classes('italic')
        context_preview.set_visibility(False)

        with ui.row().classes('w-full max-w-4xl mx-auto items-center gap-2 no-wrap'):

            # --- 1. UPLOAD HANDLER ---
            async def handle_upload(e):
                file_obj = getattr(e, 'content', None) or getattr(e, 'file', None)
                filename = getattr(e, 'name', None) or getattr(e, 'filename', None)
                if not filename and hasattr(e, 'file'):
                     filename = getattr(e.file, 'name', None) or getattr(e.file, 'filename', None)

                if not filename: filename = "unnamed_upload.pdf"

                # Server-side file extension validation
                allowed_ext = {'.pdf', '.txt', '.md'}
                file_ext = Path(filename).suffix.lower()
                if file_ext not in allowed_ext:
                    ui.notify(f'Unsupported file type: {file_ext}', type='negative')
                    return

                slog.info(f"[{user_data.get('username', '?')}] UPLOAD START: {filename}")
                ui.notify(f'Analyzing {filename}...', type='info')
                mode_display.text = "⏳ Analyzing File..."
                mode_display.classes(replace='text-orange-500')

                try:
                    if not file_obj: raise ValueError("File object is empty.")

                    # Read bytes
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

                        # --- FIX: RUNNING IN THREAD TO PREVENT TIMEOUTS ---
                        def read_pdf_sync():
                            """Blocking function to be run in a separate thread"""
                            try:
                                reader = SmartPDFReader()
                                docs = reader.load_data(temp_path)
                                return "\n".join([d.text for d in docs])
                            except Exception as e:
                                raise e

                        # Execute in a separate thread so the UI doesn't freeze
                        text_content = await run.io_bound(read_pdf_sync)

                        if temp_path.exists(): temp_path.unlink()
                    else:
                        text_content = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)

                    if not text_content or not text_content.strip():
                        raise ValueError("File is empty or unreadable.")

                    # Save to Session (Limit 10k chars for Llama 3.2 safety)
                    session.uploaded_context = text_content[:10000]

                    # Update UI
                    context_label.text = f"📎 Attachment ready: {filename}"
                    context_preview.set_visibility(True)
                    mode_display.text = f"📄 File: {filename[:15]}..."
                    mode_display.classes(replace='text-blue-600')

                    slog.info(f"[{user_data.get('username', '?')}] UPLOAD COMPLETE. Context size: {len(session.uploaded_context)}")
                    ui.notify('✅ Document analyzed! Ask away.', type='positive')

                except Exception as err:
                    slog.error(f"[{user_data.get('username', '?')}] Error Upload", exc_info=True)
                    ui.notify(f'Error: {str(err)}', type='negative')
                    mode_display.text = "❌ Upload Error"
                    mode_display.classes(replace='text-red-600')

            upload_element = ui.upload(
                auto_upload=True,
                on_upload=handle_upload,
                max_file_size=15_000_000
            ).props('hide-upload-btn no-thumbnails accept=".pdf, .txt, .md"').style('position: absolute; top: -9999px; left: -9999px;')

            upload_btn = ui.button(icon='attach_file', on_click=lambda: upload_element.run_method('pickFiles')).props('flat round color=grey-7')
            if session.is_cloud: upload_btn.set_visibility(False)

            # --- 2. SEND MESSAGE HANDLER ---
            async def send_message():
                text = sanitize_input(input_field.value or "")
                if not text: return
                if not _check_rate_limit(user_data.get('username', 'anon')):
                    ui.notify('Too many messages. Please wait a moment.', type='warning')
                    return
                input_field.value = ''

                # Render User Message
                with chat_container:
                    avatar_me = f'https://ui-avatars.com/api/?name={quote(user_data["full_name"])}&background=gray&color=fff'
                    ui.chat_message(text, name='You', sent=True, avatar=avatar_me)

                # Render AI Message Placeholder
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
                    # Execute Logic - Now returns (response_obj, response_text, used_mode)
                    response_obj, response_text, used_mode = await session.run_chat_action(text)

                    # All UI updates are guarded: if the browser tab was closed/reset while
                    # the AI was running, NiceGUI deletes the client and raises RuntimeError.
                    # That is a normal lifecycle event, not an application error.
                    try:
                        # Update Sidebar State
                        if used_mode == "CLOUD":
                            mode_display.text = "☁️ Gemini Cloud"
                            mode_display.classes(replace='text-orange-600')
                        elif used_mode == "FILE READER":
                            mode_display.text = "📄 Attachment Analysis"
                            mode_display.classes(replace='text-blue-600')
                        elif used_mode == "RAG":
                            mode_display.text = "📚 RAG (Database)"
                            mode_display.classes(replace='text-purple-600')
                        else:
                            mode_display.text = "🟢 Local Chat"
                            mode_display.classes(replace='text-green-600')

                        try: spinner.delete()
                        except Exception: pass

                        response_area.set_content(response_text or "⚠️ Empty response.")

                        # Show Sources (RAG Only) - Now checking response_obj instead of response
                        if not session.is_cloud and used_mode == "RAG" and response_obj and hasattr(response_obj, "source_nodes"):
                            seen = set()
                            with sources_row:
                                ui.label("📚 Sources:").classes('text-xs font-bold text-gray-700 mr-2 self-center opacity-70')
                                for node in response_obj.source_nodes:
                                    fname = node.metadata.get("filename", "Doc")
                                    meta_path = node.metadata.get("file_path")
                                    if fname not in seen:
                                        seen.add(fname)
                                        icon = 'picture_as_pdf' if fname.lower().endswith('.pdf') else 'description'

                                        relative_path = None
                                        if meta_path and Path(meta_path).exists():
                                            try: relative_path = Path(meta_path).relative_to(ARCHIVE_DIR.absolute())
                                            except Exception: pass
                                        if not relative_path: relative_path = find_relative_path(fname)

                                        url_link = f'/documents/{quote(str(relative_path).replace("\\\\", "/"), safe="/")}'
                                        with ui.link(target=url_link, new_tab=True).classes('no-underline decoration-none'):
                                            with ui.row().classes('items-center border border-gray-400/30 rounded px-2 py-1 bg-white/50 hover:bg-white/80 cursor-pointer gap-1'):
                                                ui.icon(icon).classes('text-gray-700 text-xs')
                                                ui.label(fname).classes('text-xs text-gray-800 max-w-[150px] truncate')

                        # Notify conversation panel to refresh (title/count update)
                        if on_message_sent:
                            try:
                                on_message_sent()
                            except Exception as e:
                                slog.error(f"on_message_sent callback failed: {e}", exc_info=True)

                    except RuntimeError:
                        slog.debug(f"[{user_data.get('username', '?')}] Client disconnected before response could be rendered")

                except Exception as e:
                    try: spinner.delete()
                    except Exception: pass
                    slog.error(f"[{user_data.get('username', '?')}] Error Chat", exc_info=True)
                    err_msg = str(e)
                    if "ReadTimeout" in err_msg:
                        err_msg = "⏳ The document is too complex. Please try again."
                    try:
                        ui.notify(f"Error: {err_msg}", type='negative')
                        with bot_msg: ui.label(f"❌ {err_msg}").classes('text-red-600 font-bold')
                    except RuntimeError:
                        slog.debug(f"[{user_data.get('username', '?')}] Client disconnected during error handling")

            input_field = ui.input(placeholder='Type here...').classes('w-full text-black').props('outlined rounded bg-color=white').on('keydown.enter', send_message)
            ui.button(icon='send', on_click=send_message).props('flat round color=primary')

    return input_field, upload_btn
