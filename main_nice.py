# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
import os
from pathlib import Path
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from nicegui import ui, app

# Environment Setup - MUST BE FIRST, before any src.* imports
from dotenv import load_dotenv
import nest_asyncio

# Force load .env with explicit path and override
env_file = Path(__file__).parent / '.env'
print(f"[DOTENV] Loading from: {env_file} (exists: {env_file.exists()})")
load_dotenv(env_file, override=True)
print(f"[DOTENV] PROMPT_OPTIMIZATION = {os.getenv('PROMPT_OPTIMIZATION')}")

nest_asyncio.apply()

# Import src modules AFTER .env is loaded
from src.bridge import bridge_manager

# --- CORS Configuration ---
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:8080').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Import Logic
from src.config import (
    ARCHIVE_DIR, init_settings, PROFILE, LLM_MODEL_NAME, EMBED_MODEL_NAME,
    CONTEXT_WINDOW, CHUNK_SIZE, CHUNK_OVERLAP,
    PROMPT_OPTIMIZATION, ENABLE_NER_MASKING,
    WS_RECEIVE_TIMEOUT,
    WS_API_KEY,
)
from src.auth import load_users, verify_password, ws_auth_is_rate_limited, ws_auth_record_failure
from src.engine import UserSession
from src.logger import server_log as slog
from src.conversations import ConversationStore

# Import UI Modules (Refactored)
from src.ui.header import create_header
from src.ui.sidebar import create_sidebar
from src.ui.chat_area import create_chat_area
from src.ui.footer import create_footer
from src.ui.conversation_panel import create_conversation_panel
from src.ui.admin import create_admin_page

# Global conversation store (single instance, like bridge_manager)
conversation_store = ConversationStore()

# Register the /admin page route
create_admin_page()

# Initialize Settings
init_settings()

# --- Cleanup empty conversations on server start/stop ---
conversation_store.cleanup_empty()

@app.on_shutdown
def _cleanup_on_shutdown():
    conversation_store.cleanup_empty()

# Setup Assets
if not ARCHIVE_DIR.exists(): ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files('/documents', str(ARCHIVE_DIR))

ASSETS_DIR = Path("assets")
if not ASSETS_DIR.exists(): ASSETS_DIR.mkdir()
app.add_static_files('/assets', str(ASSETS_DIR))


# --- HEALTH CHECK ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "ArcumAI"}


# --- WEBSOCKET ENDPOINT FOR OUTLOOK ---
def _is_valid_outlook_id(user_id: str) -> bool:
    """Checks that user_id is a registered and unique outlook_id in users.json."""
    if not user_id or len(user_id) > 100:
        return False
    users = load_users()
    matches = [name for name, data in users.items() if data.get("outlook_id") == user_id]
    if len(matches) == 0:
        return False
    if len(matches) > 1:
        slog.error(f"CONFIG ERROR: outlook_id '{user_id}' is duplicated for users: {matches}. Connection refused.")
        return False
    return True

@app.websocket("/ws/outlook/{user_id}")
async def outlook_endpoint(websocket: WebSocket, user_id: str):
    """
    Endpoint for the C# plugin to connect to.
    URL: ws://your-server:8080/ws/outlook/username
    """
    client_ip = websocket.client.host if websocket.client else "unknown"

    if ws_auth_is_rate_limited(client_ip):
        slog.warning(f"WS rate-limited: IP '{client_ip}' blocked after repeated failed attempts.")
        await websocket.close(code=4002, reason="Too many failed attempts")
        return

    if WS_API_KEY:
        api_key = websocket.headers.get("x-api-key", "")
        if api_key != WS_API_KEY:
            ws_auth_record_failure(client_ip)
            slog.warning(f"WS rejected: invalid API key (IP: {client_ip})")
            await websocket.close(code=4003, reason="Invalid API key")
            return

    if not _is_valid_outlook_id(user_id):
        ws_auth_record_failure(client_ip)
        slog.warning(f"WS rejected: outlook_id '{user_id}' not registered (IP: {client_ip}).")
        await websocket.close(code=4001, reason="Outlook ID not authorized")
        return

    await bridge_manager.connect(websocket, user_id)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=WS_RECEIVE_TIMEOUT)
            except asyncio.TimeoutError:
                slog.warning(f"WS inactivity timeout for '{user_id}' ({WS_RECEIVE_TIMEOUT}s) — closing")
                await websocket.close(code=1001, reason="Inactivity timeout")
                break
            await bridge_manager.handle_incoming_message(user_id, data)
    except WebSocketDisconnect:
        bridge_manager.disconnect(user_id)


# --- LOGIN PAGE ---
@ui.page('/login')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    users_db = load_users()

    with ui.card().classes('absolute-center w-96 p-8 shadow-2xl border').style('background-color: #2C6672; border-color: #2d5f64'):
        ui.image('/assets/newArcumAILogo.PNG').classes('w-48 mx-auto mb-4 object-contain')
        ui.label('Restricted Access').classes('text-xl font-bold text-center w-full mb-4 text-white')

        username = ui.input('Username').classes('w-full text-white').props('dark outlined input-class=text-white').on('keydown.enter', lambda: try_login())
        password = ui.input('Password', password=True).classes('w-full text-white').props('dark outlined input-class=text-white').on('keydown.enter', lambda: try_login())
        error_msg = ui.label('').classes('text-red-400 text-sm hidden mt-2 text-center w-full')

        def try_login():
            user = username.value
            pwd = password.value
            if user in users_db:
                stored_hash = users_db[user].get('pw_hash', '')
                if verify_password(pwd, stored_hash):
                    conversation_store.cleanup_empty(user)
                    app.storage.user.update({
                        'authenticated': True,
                        'username': user,
                        'role': users_db[user].get('role', 'DEFAULT'),
                        'full_name': users_db[user].get('name', user)
                    })
                    ui.notify(f'Welcome {app.storage.user["full_name"]}!', type='positive')
                    ui.navigate.to('/')
                    return
            error_msg.text = 'Invalid credentials'
            error_msg.classes(remove='hidden')
            ui.notify('Login Error', type='negative')

        ui.button('Login', on_click=try_login).props('color=orange-600 text-color=white').classes('w-full mt-6 font-bold')

# --- MAIN PAGE ---
@ui.page('/')
async def main_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/login')
        return

    user_data = app.storage.user
    username = user_data['username']
    session = UserSession(username=username, role=user_data['role'])

    # --- Conversation persistence ---
    # Start a new conversation by default; user can switch via the left panel.
    current_conv_id = conversation_store.create_conversation(username)
    session.set_conversation(conversation_store, current_conv_id)

    # 0. CONVERSATION PANEL (left drawer) — defined before layout so callbacks can reference chat_container
    chat_container_ref = [None]  # mutable ref, set after chat_area is created
    refresh_panel_ref = [None]

    def _reload_chat_for_conv(conv_id: str):
        """Switch to an existing conversation: reload history into chat area."""
        nonlocal current_conv_id
        current_conv_id = conv_id
        session.set_conversation(conversation_store, conv_id)

        # Re-render chat messages from stored history
        container = chat_container_ref[0]
        if container is None:
            return
        container.clear()
        conv = conversation_store.load_conversation(username, conv_id)
        if not conv:
            return
        from urllib.parse import quote
        avatar_me = f'https://ui-avatars.com/api/?name={quote(user_data["full_name"])}&background=gray&color=fff'
        avatar_bot = f'https://ui-avatars.com/api/?name=AI&background=2ECC71&color=fff'
        with container:
            for msg in conv.get("messages", []):
                if msg["role"] == "user":
                    ui.chat_message(msg["content"], name='You', sent=True, avatar=avatar_me)
                else:
                    ui.chat_message(msg["content"], name='Arcum AI', sent=False, avatar=avatar_bot).props('bg-color=green-2 text-color=black')

    def _start_new_conversation():
        nonlocal current_conv_id
        current_conv_id = conversation_store.create_conversation(username)
        session.set_conversation(conversation_store, current_conv_id)
        container = chat_container_ref[0]
        if container:
            container.clear()
        if refresh_panel_ref[0]:
            refresh_panel_ref[0]()

    refresh_panel = create_conversation_panel(
        username=username,
        store=conversation_store,
        on_select=_reload_chat_for_conv,
        on_new=_start_new_conversation,
    )
    refresh_panel_ref[0] = refresh_panel

    # 1. SIDEBAR (Returns the status label for future updates)
    mode_display = create_sidebar(user_data)

    # 2. CHAT AREA (Returns the column to write to)
    chat_container = create_chat_area()
    chat_container_ref[0] = chat_container

    # 3. FOOTER (Returns input and btn so we can modify them when switching cloud)
    input_field, upload_btn = create_footer(session, user_data, chat_container, mode_display,
                                            on_message_sent=lambda: refresh_panel_ref[0]() if refresh_panel_ref[0] else None)

    # 4. HEADER (Needs input_field and upload_btn to hide/modify them when switching Cloud)
    def update_ui_on_mode_change(is_cloud):
        if is_cloud:
            input_field.props('bg-color=orange-8 label="⚠️ CLOUD MODE" label-color=white text-color=white')
            upload_btn.set_visibility(False)
            mode_display.text = "☁️ Gemini Cloud"
            mode_display.classes(replace='text-orange-600')
        else:
            input_field.props('bg-color=white label="Type here..." label-color=black text-color=black')
            upload_btn.set_visibility(True)
            mode_display.text = "🟢 Local Chat"
            mode_display.classes(replace='text-green-600')

    create_header(user_data, session, update_ui_on_mode_change)

if __name__ in {"__main__", "__mp_main__"}:
    storage_secret = os.getenv('STORAGE_SECRET', 'CHIAVE_SEGRETA_ARCUM_AI_V2_DEV_DEFAULT')
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8080'))

    slog.info("Starting Arcum AI")
    slog.info(f"  Profile: {PROFILE} | LLM: {LLM_MODEL_NAME} | Embed: {EMBED_MODEL_NAME}")
    slog.info(f"  Context: {CONTEXT_WINDOW} | Chunk: {CHUNK_SIZE}/{CHUNK_OVERLAP}")
    slog.info(f"  Prompt Optimization: {PROMPT_OPTIMIZATION} | NER Masking: {ENABLE_NER_MASKING}")
    slog.info(f"  Host: {host}:{port}")

    ui.run(title='Arcum AI', host=host, port=port, favicon='🛡️', reload=False, storage_secret=storage_secret)
