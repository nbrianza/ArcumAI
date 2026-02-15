from nicegui import ui

def create_chat_area():
    """
    Creates the main container for chat messages.
    Returns the column object so we can write to it.
    """
    # pr-[300px] prevents content from going under the right sidebar
    return ui.column().classes('w-full max-w-4xl mx-auto p-4 flex-grow gap-4 pr-[300px]')
