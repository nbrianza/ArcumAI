from fastapi import WebSocket, WebSocketDisconnect
from src.bridge import bridge_manager
from pathlib import Path
from nicegui import ui, app

# Setup Ambiente
from dotenv import load_dotenv
load_dotenv() 
import nest_asyncio
nest_asyncio.apply()

# Import Logic
from src.config import ARCHIVE_DIR, init_settings
from src.auth import load_users, verify_password
from src.engine import UserSession

# Import UI Modules (Refactored)
from src.ui.header import create_header
from src.ui.sidebar import create_sidebar
from src.ui.chat_area import create_chat_area
from src.ui.footer import create_footer

# Inizializza Settings
init_settings()

# Setup Assets
if not ARCHIVE_DIR.exists(): ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
app.add_static_files('/documents', str(ARCHIVE_DIR))

ASSETS_DIR = Path("assets")
if not ASSETS_DIR.exists(): ASSETS_DIR.mkdir()
app.add_static_files('/assets', str(ASSETS_DIR))


# --- ENDPOINT WEBSOCKET PER OUTLOOK ---
# L'app NiceGUI espone l'oggetto 'app' che è un'istanza FastAPI
@app.websocket("/ws/outlook/{user_id}")
async def outlook_endpoint(websocket: WebSocket, user_id: str):
    """
    Endpoint a cui si collega il plugin C#.
    URL: ws://tuo-server:8080/ws/outlook/nome_utente
    """
    await bridge_manager.connect(websocket, user_id)
    try:
        while True:
            # Loop infinito di ascolto
            data = await websocket.receive_text()
            await bridge_manager.handle_incoming_message(user_id, data)
    except WebSocketDisconnect:
        bridge_manager.disconnect(user_id)
        

# --- PAGINA LOGIN ---
@ui.page('/login')
def login_page():
    if app.storage.user.get('authenticated', False):
        ui.navigate.to('/')
        return

    users_db = load_users()

    with ui.card().classes('absolute-center w-96 p-8 shadow-2xl bg-slate-900 border border-slate-700'):
        ui.image('/assets/logow.png').classes('w-32 mx-auto mb-4 object-contain')
        ui.label('Accesso Riservato').classes('text-xl font-bold text-center w-full mb-4 text-white')
        
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

        ui.button('Accedi', on_click=try_login).props('color=orange-600 text-color=white').classes('w-full mt-6 font-bold')

# --- PAGINA PRINCIPALE ---
@ui.page('/')
async def main_page():
    if not app.storage.user.get('authenticated', False):
        ui.navigate.to('/login')
        return

    user_data = app.storage.user
    session = UserSession(username=user_data['username'], role=user_data['role']) 
    
    # 1. SIDEBAR (Restituisce l'etichetta di stato per aggiornamenti futuri)
    mode_display = create_sidebar(user_data)
    
    # 2. CHAT AREA (Restituisce la colonna dove scrivere)
    chat_container = create_chat_area()

    # 3. FOOTER (Restituisce input e btn per poterli modificare se switchiamo cloud)
    input_field, upload_btn = create_footer(session, user_data, chat_container, mode_display)

    # 4. HEADER (Ha bisogno di input_field e upload_btn per nasconderli/modificarli se switchiamo Cloud)
    def update_ui_on_mode_change(is_cloud):
        if is_cloud:
            input_field.props('bg-color=orange-8 label="⚠️ CLOUD MODE" label-color=white text-color=white')
            upload_btn.set_visibility(False)
            mode_display.text = "☁️ Gemini Cloud"
            mode_display.classes(replace='text-orange-600')
        else:
            input_field.props('bg-color=white label="Scrivi qui..." label-color=black text-color=black')
            upload_btn.set_visibility(True)
            mode_display.text = "🟢 Chat Locale"
            mode_display.classes(replace='text-green-600')

    create_header(user_data, session, update_ui_on_mode_change)

if __name__ in {"__main__", "__mp_main__"}:
    print("🚀 Avvio Arcum AI (Refactored)...")

    # Load storage secret from environment (fallback to default for dev)
    import os
    storage_secret = os.getenv('STORAGE_SECRET', 'CHIAVE_SEGRETA_ARCUM_AI_V2_DEV_DEFAULT')

    ui.run(title='Arcum AI', host='0.0.0.0', port=8080, favicon='🛡️', reload=False, storage_secret=storage_secret)