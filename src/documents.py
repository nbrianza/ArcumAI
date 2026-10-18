# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Authenticated, owner-scoped download route for archived documents (#36).

Replaces the former unauthenticated static mount of ARCHIVE_DIR at /documents.

Rule (owner decision on issue #36):
- a user with role ADMIN can read every document;
- any other logged-in user can read only documents they own, that is documents
  whose first folder under the archive equals their username (case-insensitive);
- documents without an owner (archive root, unknown first folder) are ADMIN-only;
- unauthenticated requests get 401.

Denied requests log one WARNING with the reason and the client IP only, never
the requested name or path (archive file names often contain client names).
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse
from nicegui import app

from src import auth
from src.config import ARCHIVE_DIR
from src.logger import server_log as slog

router = APIRouter()

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}


def resolve_archive_path(rel_path: str, root: Path) -> Path | None:
    """Resolves rel_path inside root, or returns None if it is unsafe.

    None for empty paths, NUL bytes, colons (drive letters, alternate data
    streams), absolute paths, and anything that resolves outside root or to
    root itself. resolve() collapses '..' and follows symlinks, so containment
    is checked on the final target.
    """
    if not rel_path or "\x00" in rel_path or ":" in rel_path:
        return None
    if rel_path[0] in ("/", "\\") or Path(rel_path).is_absolute():
        return None
    try:
        root_resolved = root.resolve()
        resolved = (root_resolved / rel_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if resolved == root_resolved or not resolved.is_relative_to(root_resolved):
        return None
    return resolved


def document_owner(resolved: Path, root: Path) -> str | None:
    """Returns the first folder of the document under root, or None at the root."""
    try:
        parts = resolved.relative_to(root.resolve()).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    return parts[0]


def can_read(username: str, role: str, owner: str | None) -> bool:
    """ADMIN reads everything; other users read only documents they own."""
    if role == "ADMIN":
        return True
    if not owner or not username:
        return False
    return owner.casefold() == username.casefold()


async def current_user() -> dict | None:
    """Returns {'username', 'role'} for a valid session, else None.

    The role is re-read from the users file on every call, so a demoted or
    deleted user does not keep access from an old session. Async so it runs in
    the request task, where NiceGUI has set the request context for app.storage.
    """
    try:
        session = app.storage.user
        if not session.get("authenticated"):
            return None
        username = session.get("username")
    except Exception:
        return None
    if not username:
        return None
    entry = auth.load_users().get(username)
    if not isinstance(entry, dict):
        return None
    return {"username": username, "role": entry.get("role", "")}


def source_link(file_path: str | None, filename: str, username: str, role: str,
                root: Path | None = None) -> str | None:
    """Builds the /documents URL for a RAG source, or None if the user cannot open it.

    Prefers the indexed file_path (relative to the archive) and falls back to the
    name search of find_relative_path when it does not point to an existing file.
    """
    from src.utils import find_relative_path

    if root is None:
        root = ARCHIVE_DIR
    resolved = resolve_archive_path(file_path, root) if file_path else None
    if resolved is None or not resolved.is_file():
        resolved = resolve_archive_path(find_relative_path(filename), root)
    if resolved is None or not can_read(username, role, document_owner(resolved, root)):
        return None
    rel = resolved.relative_to(root.resolve()).as_posix()
    return "/documents/" + quote(rel, safe="/")


def _deny(request: Request, status_code: int, body: str, reason: str) -> PlainTextResponse:
    client_ip = request.client.host if request.client else "unknown"
    slog.warning(f"Document access denied: {reason} (IP: {client_ip})")
    return PlainTextResponse(body, status_code=status_code, headers=_SECURITY_HEADERS)


@router.api_route("/documents/{rel_path:path}", methods=["GET", "HEAD"])
async def get_document(rel_path: str, request: Request, user: dict | None = Depends(current_user)):
    if user is None:
        return _deny(request, 401, "Not authenticated", "unauthenticated")
    root = ARCHIVE_DIR
    resolved = resolve_archive_path(rel_path, root)
    if resolved is None:
        return _deny(request, 404, "Not found", "outside archive")
    # Ownership before existence: a non-owner cannot probe names in other folders.
    if not can_read(user["username"], user["role"], document_owner(resolved, root)):
        return _deny(request, 403, "Forbidden", "forbidden")
    if not resolved.is_file():
        return _deny(request, 404, "Not found", "not found")
    return FileResponse(resolved, headers=_SECURITY_HEADERS)
