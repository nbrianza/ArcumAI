from nicegui import ui

def create_chat_area():
    """
    Crea il contenitore principale per i messaggi della chat.
    Restituisce l'oggetto colonna in modo da poterci scrivere dentro.
    """
    # pr-[300px] serve per non finire sotto la sidebar di destra
    return ui.column().classes('w-full max-w-4xl mx-auto p-4 flex-grow gap-4 pr-[300px]')