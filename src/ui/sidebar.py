from nicegui import ui

def create_sidebar(user_data):
    """
    Crea la sidebar destra e restituisce l'etichetta di stato 
    così possiamo aggiornarla da fuori.
    """
    with ui.right_drawer(fixed=True, value=True).classes('bg-white border-l border-gray-200 p-4').props('width=280'):
        ui.label('💡 Guida Comandi').classes('text-lg font-bold text-slate-800 mb-4')

        # Card RAG Compatta
        with ui.card().classes('w-full mb-2 p-2 bg-blue-50 border-l-4 border-blue-600 shadow-sm'):
            with ui.row().classes('items-center gap-1'):
                ui.icon('search').props('size=xs').classes('text-blue-700')
                ui.label('@cerca / @rag').classes('font-bold text-blue-800 text-xs')
            ui.markdown('Cerca nei **documenti PDF**.').classes('text-xs text-gray-700 mt-0.5 leading-tight')

        # Card CHAT Compatta
        with ui.card().classes('w-full mb-2 p-2 bg-green-50 border-l-4 border-green-600 shadow-sm'):
            with ui.row().classes('items-center gap-1'):
                ui.icon('chat').props('size=xs').classes('text-green-700')
                ui.label('@chat / @simple').classes('font-bold text-green-800 text-xs')
            ui.markdown('Solo **Chat** (No documenti).').classes('text-xs text-gray-700 mt-0.5 leading-tight')

        ui.separator().classes('my-4')
        
        # Sessione Attiva
        ui.label('Sessione Attiva').classes('font-bold text-gray-600 text-sm')
        ui.label(f'Utente: {user_data["username"]}').classes('text-xs text-gray-500 mb-2')
        
        with ui.card().classes('w-full p-2 bg-slate-50 border border-gray-200'):
            ui.label('CONTESTO OPERATIVO:').classes('text-[10px] font-bold text-gray-400 mb-1')
            # Creiamo l'etichetta e la restituiamo
            mode_display = ui.label('🟢 Chat Locale').classes('text-sm font-bold text-green-600')
            
    return mode_display