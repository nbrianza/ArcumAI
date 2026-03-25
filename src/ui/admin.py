# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Admin page: Document Management UI (feature 6.2).

Shows all indexed documents in ChromaDB, allows deletion and re-ingestion.
Accessible only to users with role == 'ADMIN'.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nicegui import ui, app, run

from src.config import BASE_DIR, INBOX_DIR, COLLECTION_NAME, DB_PATH
from src.logger import server_log as slog


# ---------------------------------------------------------------------------
# ChromaDB helpers (run on thread to avoid blocking the event loop)
# ---------------------------------------------------------------------------

def _get_chroma_collection():
    import chromadb
    client = chromadb.PersistentClient(path=str(DB_PATH))
    return client.get_or_create_collection(COLLECTION_NAME)


def _list_documents() -> list[dict]:
    """Return unique documents from ChromaDB with metadata."""
    collection = _get_chroma_collection()
    data = collection.get(include=["metadatas"])
    ids = data.get("ids", [])
    metas = data.get("metadatas", [])

    # Group chunks by filename to show one row per document
    docs: dict[str, dict] = {}
    for i, meta in enumerate(metas):
        fname = meta.get("filename", "unknown")
        if fname not in docs:
            docs[fname] = {
                "filename": fname,
                "file_path": meta.get("file_path", ""),
                "file_hash": meta.get("file_hash", ""),
                "tipo": meta.get("tipo", ""),
                "chunk_count": 0,
                "chunk_ids": [],
            }
        docs[fname]["chunk_count"] += 1
        docs[fname]["chunk_ids"].append(ids[i])

    return sorted(docs.values(), key=lambda d: d["filename"].lower())


def _delete_document_chunks(chunk_ids: list[str]) -> int:
    """Delete chunks from ChromaDB. Returns count deleted."""
    collection = _get_chroma_collection()
    # ChromaDB delete accepts a list of ids
    collection.delete(ids=chunk_ids)
    return len(chunk_ids)


def _run_ingestion(target_path: str | None = None) -> str:
    """Run the ingestion pipeline as a subprocess.

    If target_path is given, copy that single file to INBOX_DIR first.
    Returns stdout+stderr output for display.
    """
    # If re-ingesting a single file, copy it back to inbox
    if target_path:
        src = Path(target_path)
        if src.exists():
            import shutil
            INBOX_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(INBOX_DIR / src.name))

    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "ingest.py")],
        capture_output=True, text=True, timeout=600,
        cwd=str(BASE_DIR),
    )
    return (result.stdout + "\n" + result.stderr).strip()


# ---------------------------------------------------------------------------
# NiceGUI page
# ---------------------------------------------------------------------------

def create_admin_page():
    """Register the /admin route."""

    @ui.page('/admin')
    async def admin_page():
        if not app.storage.user.get('authenticated', False):
            ui.navigate.to('/login')
            return

        if app.storage.user.get('role', '') != 'ADMIN':
            ui.label('Access denied. Admin role required.').classes('text-red-600 text-xl p-8')
            with ui.row().classes('p-8'):
                ui.button('Back to chat', on_click=lambda: ui.navigate.to('/')).props('color=primary')
            return

        username = app.storage.user.get('username', '?')
        slog.info(f"[{username}] Admin page accessed")

        # --- Header ---
        with ui.header().classes('text-white items-center gap-4 shadow-lg h-16 px-6').style('background-color: #2C6672'):
            ui.image('/assets/newArcumAILogo.PNG').style('max-width: 200px; max-height: 48px; object-fit: contain')
            ui.label('Document Management').classes('text-lg font-bold')
            ui.space()
            ui.button('Back to Chat', icon='chat', on_click=lambda: ui.navigate.to('/')).props('flat color=white')

            def logout():
                app.storage.user.clear()
                ui.navigate.to('/login')
            ui.button(icon='logout', on_click=logout).props('flat round color=white size=sm')

        # --- State ---
        doc_table_container = ui.column().classes('w-full')
        log_output = ui.textarea('Ingestion Log').classes('w-full font-mono text-xs hidden')

        async def refresh_table():
            doc_table_container.clear()
            docs = await run.io_bound(_list_documents)

            with doc_table_container:
                if not docs:
                    ui.label('No documents in the index.').classes('text-gray-500 italic p-4')
                    return

                ui.label(f'{len(docs)} documents indexed ({sum(d["chunk_count"] for d in docs)} total chunks)').classes(
                    'text-sm text-gray-600 mb-2'
                )

                columns = [
                    {'name': 'filename', 'label': 'Document', 'field': 'filename', 'align': 'left', 'sortable': True},
                    {'name': 'file_path', 'label': 'Path', 'field': 'file_path', 'align': 'left'},
                    {'name': 'tipo', 'label': 'Type', 'field': 'tipo', 'align': 'center'},
                    {'name': 'chunk_count', 'label': 'Chunks', 'field': 'chunk_count', 'align': 'center', 'sortable': True},
                    {'name': 'file_hash', 'label': 'Hash', 'field': 'file_hash', 'align': 'left'},
                ]

                rows = []
                for i, doc in enumerate(docs):
                    rows.append({
                        'id': i,
                        'filename': doc['filename'],
                        'file_path': doc['file_path'],
                        'tipo': doc['tipo'] or '-',
                        'chunk_count': doc['chunk_count'],
                        'file_hash': doc['file_hash'][:12] + '...' if doc['file_hash'] else '-',
                        '_chunk_ids': doc['chunk_ids'],
                        '_full_path': doc['file_path'],
                    })

                table = ui.table(
                    columns=columns,
                    rows=rows,
                    row_key='id',
                    selection='multiple',
                    pagination={'rowsPerPage': 20},
                ).classes('w-full').props('flat bordered dense')

                with ui.row().classes('gap-2 mt-2'):
                    async def delete_selected():
                        selected = table.selected
                        if not selected:
                            ui.notify('Select at least one document.', type='warning')
                            return
                        total = 0
                        for row in selected:
                            chunk_ids = row.get('_chunk_ids', [])
                            if chunk_ids:
                                n = await run.io_bound(_delete_document_chunks, chunk_ids)
                                total += n
                                slog.info(f"[{username}] Deleted {n} chunks for {row['filename']}")
                        ui.notify(f'Deleted {total} chunks from {len(selected)} document(s).', type='positive')
                        table.selected.clear()
                        await refresh_table()

                    ui.button('Delete selected', icon='delete', on_click=delete_selected).props(
                        'color=red outline'
                    )

                    async def reingest_selected():
                        selected = table.selected
                        if not selected:
                            ui.notify('Select at least one document.', type='warning')
                            return
                        # First delete from index, then re-ingest from archive
                        from src.config import ARCHIVE_DIR
                        for row in selected:
                            chunk_ids = row.get('_chunk_ids', [])
                            if chunk_ids:
                                await run.io_bound(_delete_document_chunks, chunk_ids)
                            # Copy file from archive back to inbox for re-ingestion
                            fpath = row.get('_full_path', '')
                            if fpath:
                                full = ARCHIVE_DIR / fpath
                                if full.exists():
                                    import shutil
                                    INBOX_DIR.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(str(full), str(INBOX_DIR / full.name))
                                    slog.info(f"[{username}] Queued re-ingest: {full.name}")

                        ui.notify('Running ingestion...', type='info')
                        output = await run.io_bound(_run_ingestion)
                        log_output.value = output
                        log_output.classes(remove='hidden')
                        ui.notify('Re-ingestion complete.', type='positive')
                        table.selected.clear()
                        await refresh_table()

                    ui.button('Re-ingest selected', icon='refresh', on_click=reingest_selected).props(
                        'color=orange outline'
                    )

        # --- Main layout ---
        with ui.column().classes('w-full max-w-6xl mx-auto p-6 gap-4'):
            ui.label('Indexed Documents').classes('text-2xl font-bold text-slate-800')

            with ui.row().classes('gap-2'):
                ui.button('Refresh', icon='refresh', on_click=refresh_table).props('color=primary outline')

                async def ingest_all():
                    ui.notify('Running full ingestion on data_nuovi/ ...', type='info')
                    output = await run.io_bound(_run_ingestion)
                    log_output.value = output
                    log_output.classes(remove='hidden')
                    ui.notify('Ingestion complete.', type='positive')
                    await refresh_table()

                ui.button('Ingest all data_nuovi/', icon='upload_file', on_click=ingest_all).props(
                    'color=green outline'
                )

            await refresh_table()

            # Log area (shown after ingestion runs)
            log_output
