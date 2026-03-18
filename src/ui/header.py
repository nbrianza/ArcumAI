# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
from nicegui import ui

def create_header(user_data, session, update_callback):
    """
    Creates the top header.

    Args:
        user_data: User data dictionary (name, role).
        session: UserSession object.
        update_callback: Function to call when mode changes (Cloud/Local)
                         to update Sidebar and Footer.
    """
    with ui.header().classes('bg-slate-900 text-white items-center gap-4 shadow-lg h-20 px-4 border-b border-slate-800'):
        # LOGO
        ui.image('/assets/logow.png').classes('h-16 w-16 object-contain')

        # User Badge
        with ui.row().classes('items-center gap-2 bg-slate-800 rounded-full px-3 py-1 ml-4 hidden md:flex border border-slate-700'):
            ui.icon('person').classes('text-orange-400')
            ui.label(f"{user_data['full_name']}").classes('text-sm font-bold text-slate-200')
            ui.label(f"[{user_data['role']}]").classes('text-[10px] text-slate-400 uppercase tracking-wider')

        ui.space()

        # Status Label
        status_label = ui.label('MODE: SAFE LOCAL').classes('font-bold text-green-400 text-sm md:text-base')

        # Logout Function
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

            # Notify other components (Sidebar and Footer) that the mode has changed
            if update_callback:
                update_callback(is_cloud=session.is_cloud)

        ui.switch('Cloud', on_change=toggle_mode).props('color=orange')
