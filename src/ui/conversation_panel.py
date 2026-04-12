# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Left-drawer panel that lists past conversations and allows creating new ones."""
from __future__ import annotations

from nicegui import ui

from src.conversations import ConversationStore


def create_conversation_panel(
    username: str,
    store: ConversationStore,
    on_select: callable,
    on_new: callable,
):
    """Render a left drawer with the conversation list.

    Args:
        username: current logged-in user.
        store: ConversationStore instance.
        on_select: callback(conv_id) when user clicks an existing conversation.
        on_new: callback() when user clicks "New conversation".

    Returns:
        A refresh function that re-renders the list (call after create/delete).
    """

    drawer = ui.left_drawer(fixed=True, value=True).classes(
        'border-r border-slate-700 p-0'
    ).style('background-color: #2C6672').props('width=260')

    conv_list_container = None  # will be set inside drawer

    def _render_list():
        nonlocal conv_list_container
        if conv_list_container is not None:
            conv_list_container.clear()

        conversations = store.list_conversations(username)

        with conv_list_container:
            if not conversations:
                ui.label('No conversations yet.').classes(
                    'text-xs italic px-3 mt-2'
                ).style('color: rgba(255,255,255,0.5)')
                return

            for conv in conversations:
                _conv_item(conv)

    def _conv_item(conv: dict):
        title = conv["title"] or "(empty)"
        count = conv["message_count"]
        conv_id = conv["id"]
        created = conv["created_at"][:10] if conv["created_at"] else ""

        with ui.row().classes(
            'w-full items-center px-3 py-2 cursor-pointer rounded gap-2 group'
        ).style('border-radius: 6px').on('click', lambda cid=conv_id: on_select(cid)).on(
            'mouseenter', lambda e: e.sender.style('background-color: rgba(0,0,0,0.15)')
        ).on('mouseout', lambda e: e.sender.style('background-color: transparent')):
            with ui.column().classes('flex-1 min-w-0 gap-0'):
                ui.label(title).classes(
                    'text-white text-xs font-medium truncate w-full'
                )
                ui.label(f'{created}  ·  {count} msg').classes(
                    'text-[10px]'
                ).style('color: rgba(255,255,255,0.6)')
            ui.button(
                icon='delete',
                on_click=lambda e, cid=conv_id: _delete(e, cid),
            ).props('flat round size=xs color=red-4').classes(
                'opacity-0 group-hover:opacity-100'
            )

    def _delete(event, conv_id: str):
        event.sender.parent_slot.parent.set_visibility(False)  # hide row immediately
        store.delete_conversation(username, conv_id)
        refresh()

    with drawer:
        # Header
        with ui.row().classes('w-full items-center px-3 pt-4 pb-2 gap-2'):
            ui.label('Conversations').classes(
                'text-white text-sm font-bold flex-1'
            )
            ui.button(icon='add', on_click=lambda: on_new()).props(
                'flat round size=sm color=orange-4'
            ).tooltip('New conversation')

        ui.separator().style('background-color: rgba(255,255,255,0.2)')

        conv_list_container = ui.column().classes('w-full gap-0 overflow-y-auto px-1 py-1')

    # Initial render
    _render_list()

    def refresh():
        _render_list()

    return refresh
