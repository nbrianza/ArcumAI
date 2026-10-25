# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Admin page: Document Management UI (feature 6.2).

Shows all indexed documents in ChromaDB, allows deletion and re-ingestion.
Accessible only to users with role == 'ADMIN'.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from nicegui import ui, app, run

from src.config import (
    BASE_DIR, INBOX_DIR, ARCHIVE_DIR, ERROR_DIR, DUPLICATES_DIR,
    COLLECTION_NAME, DB_PATH, INGEST_TIMEOUT_SEC,
)
from src.documents import resolve_archive_path
from src.logger import server_log as slog, content, safe_ext

_ingestion_lock = threading.Lock()


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

    # Group chunks by file_path to show one row per document: two files with the
    # same name in different folders are different documents (#46).
    docs: dict[str, dict] = {}
    for i, meta in enumerate(metas):
        fname = meta.get("filename", "unknown")
        key = meta.get("file_path") or fname
        if key not in docs:
            docs[key] = {
                "filename": fname,
                "file_path": meta.get("file_path", ""),
                "file_hash": meta.get("file_hash", ""),
                "tipo": meta.get("tipo", ""),
                "chunk_count": 0,
                "chunk_ids": [],
            }
        docs[key]["chunk_count"] += 1
        docs[key]["chunk_ids"].append(ids[i])

    return sorted(docs.values(), key=lambda d: d["filename"].lower())


def _delete_document_chunks(chunk_ids: list[str]) -> int:
    """Delete chunks from ChromaDB. Returns count deleted."""
    collection = _get_chroma_collection()
    # ChromaDB delete accepts a list of ids
    collection.delete(ids=chunk_ids)
    return len(chunk_ids)


def _stage_for_reingest(file_path: str) -> Path:
    """Move an archived document into INBOX_DIR at the same relative path.

    file_path is relative to ARCHIVE_DIR and comes from client-synchronised
    table data, so it is validated here. Moving instead of copying lets
    ingest.py archive the file back into its own folder with no _<timestamp>
    duplicate, and makes the indexed file_path equal to the archive-relative
    path (#46). Returns the staged archive-relative path. Error messages never
    contain the requested path.
    """
    resolved = resolve_archive_path(file_path, ARCHIVE_DIR)
    if resolved is None:
        raise ValueError("Path traversal rejected")
    if not resolved.is_file():
        raise FileNotFoundError("Document not found in the archive")
    rel = resolved.relative_to(ARCHIVE_DIR.resolve())
    dest = INBOX_DIR / rel
    if not dest.resolve().is_relative_to(INBOX_DIR.resolve()):
        raise ValueError("Path traversal rejected")
    if dest.exists():
        raise FileExistsError("A file with the same path is already waiting in the inbox")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved), str(dest))
    return rel


def _unstage_after_failed_delete(rel: Path) -> bool:
    """Move a staged file from INBOX_DIR back to its archive folder.

    Used when deleting the chunks failed after staging: left in the inbox, the
    file would be found still indexed and sent to data_duplicati. Returns True
    when the file is back in the archive, False when it could not be moved
    (source gone, target occupied, or an OS error such as a file lock).
    Never overwrites an archived file.
    """
    staged = INBOX_DIR / rel
    archived = ARCHIVE_DIR / rel
    if not staged.is_file() or archived.exists():
        return False
    try:
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(archived))
    except OSError:
        return False
    return True


def _settle_after_reingest(rel: Path) -> str:
    """Report where a staged document ended up after the ingestion run.

    "ok": archived again in its folder. "queued": still in the inbox (lock held
    by another run, or timeout); the next run archives it in the right folder.
    "restored": ingestion sent it to data_error or data_duplicati and it was
    moved back to its archive folder, not indexed. "missing": none of these.
    """
    archived = ARCHIVE_DIR / rel
    if archived.is_file():
        return "ok"
    if (INBOX_DIR / rel).is_file():
        return "queued"
    for folder in (ERROR_DIR, DUPLICATES_DIR):
        failed = folder / rel
        if failed.is_file() and not archived.exists():
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(failed), str(archived))
            return "restored"
    return "missing"


def _run_ingestion(target_path: str | None = None) -> str:
    """Run the ingestion pipeline as a subprocess.

    If target_path is given, move that single archived file to INBOX_DIR first,
    keeping its folder (see _stage_for_reingest).
    Returns stdout+stderr output for display. Only one run at a time: concurrent
    ingest.py processes would write to the same ChromaDB/BM25 stores.
    """
    if not _ingestion_lock.acquire(blocking=False):
        return "An ingestion is already running. Try again when it has finished."
    try:
        # If re-ingesting a single file, move it back to the inbox in its folder
        if target_path:
            src = Path(target_path).resolve()
            if not src.is_relative_to(ARCHIVE_DIR.resolve()):
                raise ValueError("Path traversal rejected")
            if src.exists():
                _stage_for_reingest(str(src.relative_to(ARCHIVE_DIR.resolve())))

        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "ingest.py")],
                capture_output=True, text=True, timeout=INGEST_TIMEOUT_SEC,
                cwd=str(BASE_DIR), **_low_priority_kwargs(),
            )
        except subprocess.TimeoutExpired as e:
            slog.warning(f"Admin ingestion killed after {INGEST_TIMEOUT_SEC}s timeout")
            partial = "\n".join(_as_text(s) for s in (e.stdout, e.stderr) if s).strip()
            return (f"Ingestion stopped: exceeded the {INGEST_TIMEOUT_SEC}s timeout "
                    f"(INGEST_TIMEOUT_SEC). Files not yet processed remain in data_nuovi/.\n\n"
                    f"{partial}").strip()
        except Exception as e:
            # Report instead of raising, so the re-ingest summary still settles every
            # staged file (#46). Only the exception type is logged: its text can
            # contain paths.
            slog.error(f"Admin ingestion failed to run: {type(e).__name__}")
            return (f"Ingestion could not be started or failed unexpectedly ({type(e).__name__}). "
                    f"Files not yet processed remain in data_nuovi/.")
        return (result.stdout + "\n" + result.stderr).strip()
    finally:
        _ingestion_lock.release()


def _low_priority_kwargs() -> dict:
    # Keep the web server responsive while ingestion (OCR, embeddings) saturates the CPU.
    if sys.platform == "win32":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {"preexec_fn": lambda: os.nice(10)}


def _as_text(data: str | bytes) -> str:
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


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
                                doc_ext = safe_ext(row['filename'])
                                slog.info(f"[{username}] Deleted {n} chunks for 1 document "
                                          f"({content(row['filename'], f'ext={doc_ext}')})")
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
                        # Move each file from the archive into the inbox at the same
                        # relative path first; delete its chunks only after the move
                        # succeeded, so a failed move leaves the document indexed (#46).
                        staged: list[tuple[str, Path]] = []
                        for row in selected:
                            name = row.get('filename', '?')
                            try:
                                rel = await run.io_bound(_stage_for_reingest, row.get('_full_path', ''))
                            except ValueError:
                                slog.warning(f"[{username}] Re-ingest rejected an invalid document path")
                                ui.notify(f'{name}: invalid document path, skipped.', type='negative')
                                continue
                            except FileNotFoundError:
                                ui.notify(f'{name}: file not found in the archive, skipped.', type='negative')
                                continue
                            except FileExistsError:
                                ui.notify(f'{name}: a file with the same path is already waiting in '
                                          f'data_nuovi/, skipped.', type='negative')
                                continue
                            except OSError:
                                ui.notify(f'{name}: the file could not be moved (it may be open in '
                                          f'another program), skipped.', type='negative')
                                continue

                            chunk_ids = row.get('_chunk_ids', [])
                            if chunk_ids:
                                try:
                                    await run.io_bound(_delete_document_chunks, chunk_ids)
                                except Exception:
                                    # Put the file back, otherwise ingestion would find its
                                    # hash still indexed and send it to data_duplicati.
                                    slog.warning(f"[{username}] Re-ingest: chunk deletion failed, "
                                                 f"moving the staged file back to the archive")
                                    if await run.io_bound(_unstage_after_failed_delete, rel):
                                        ui.notify(f'{name}: the index could not be updated, skipped.',
                                                  type='negative')
                                    else:
                                        slog.error(f"[{username}] Re-ingest rollback failed: a staged "
                                                   f"file could not be moved back to the archive")
                                        ui.notify(f'{name}: the index could not be updated and the file '
                                                  f'could not be moved back. It may still be in data_nuovi/ '
                                                  f'or already in data_duplicati/; move it back to its '
                                                  f'archive folder manually.',
                                                  type='negative', timeout=0, close_button=True)
                                    continue
                            staged.append((name, rel))
                            slog.info(f"[{username}] Queued re-ingest: 1 file "
                                      f"({content(rel.name, f'ext={safe_ext(rel.name)}')})")

                        if not staged:
                            table.selected.clear()
                            await refresh_table()
                            return

                        ui.notify('Running ingestion...', type='info')
                        try:
                            output = await run.io_bound(_run_ingestion)
                        except Exception as e:
                            # Files are staged and their chunks deleted: still settle
                            # and report each one below.
                            slog.error(f"[{username}] Re-ingest run failed: {type(e).__name__}")
                            output = (f"Ingestion failed unexpectedly ({type(e).__name__}). "
                                      f"Files not yet processed remain in data_nuovi/.")
                        log_output.value = output
                        log_output.classes(remove='hidden')

                        reindexed = 0
                        for name, rel in staged:
                            try:
                                status = await run.io_bound(_settle_after_reingest, rel)
                            except OSError:
                                status = 'missing'
                            if status == 'ok':
                                reindexed += 1
                            elif status == 'queued':
                                ui.notify(f'{name}: still waiting in data_nuovi/, it will be indexed '
                                          f'by the next ingestion run.', type='warning')
                            elif status == 'restored':
                                ui.notify(f'{name}: ingestion failed; the file is back in its archive '
                                          f'folder but is not indexed.', type='warning')
                            else:
                                ui.notify(f'{name}: not found in the archive after ingestion; check '
                                          f'data_error/ and data_duplicati/.', type='warning')
                        ui.notify(f'{reindexed} of {len(staged)} document(s) re-indexed.',
                                  type='positive' if reindexed == len(staged) else 'warning')
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
