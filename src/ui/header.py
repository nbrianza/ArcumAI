from nicegui import ui

def create_header(user_data, session, update_callback):
    """
    Crea l'header superiore.
    
    Args:
        user_data: Dizionario dati utente (nome, ruolo).
        session: Oggetto UserSession.
        update_callback: Funzione da chiamare quando cambia la modalità (Cloud/Local) 
                         per aggiornare Sidebar e Footer.
    """
    with ui.header().classes('bg-slate-900 text-white items-center gap-4 shadow-lg h-20 px-4 border-b border-slate-800'):
        # LOGO
        ui.image('/assets/logow.png').classes('h-16 w-16 object-contain')
        
        # Badge Utente
        with ui.row().classes('items-center gap-2 bg-slate-800 rounded-full px-3 py-1 ml-4 hidden md:flex border border-slate-700'):
            ui.icon('person').classes('text-orange-400')
            ui.label(f"{user_data['full_name']}").classes('text-sm font-bold text-slate-200')
            ui.label(f"[{user_data['role']}]").classes('text-[10px] text-slate-400 uppercase tracking-wider')

        ui.space()
        
        # Etichetta Stato
        status_label = ui.label('MODE: SAFE LOCAL').classes('font-bold text-green-400 text-sm md:text-base')

        # Funzione Logout
        def logout():
            from nicegui import app
            app.storage.user.clear()
            ui.navigate.to('/login')
        
        ui.button(icon='logout', on_click=logout).props('flat round color=white size=sm')

        # Switch Cloud/Local
        async def toggle_mode(e):
            session.is_cloud = e.value
            if session.is_cloud:
                status_label.text = 'MODE: CLOUD WARNING'
                status_label.classes(replace='text-orange-400')
            else:
                status_label.text = 'MODE: SAFE LOCAL'
                status_label.classes(replace='text-green-400')
            
            # Notifica gli altri componenti (Sidebar e Footer) che la modalità è cambiata
            if update_callback:
                update_callback(is_cloud=session.is_cloud)

        ui.switch('Cloud', on_change=toggle_mode).props('color=orange')