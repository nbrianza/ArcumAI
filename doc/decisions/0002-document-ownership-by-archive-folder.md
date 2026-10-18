# ADR 0002: Document ownership by archive folder

- Status: accepted (owner approval on issue #36, decisions D1 to D8)
- Date: 2026-10-18
- Issue: #36

## Context

The document archive (`data_archivio/`, `ARCHIVE_DIR`) was published as a static file mount at `/documents`. Static mounts bypass the NiceGUI login check, so any client that could reach the server could download any archived file without logging in.

The owner requires that a user with the ADMIN role can read every document, any other logged-in user can read only the documents they own, and unauthenticated requests are refused.

No ownership data exists today: `users.json` holds only password hash, role, name and Outlook id; ChromaDB chunk metadata holds file hash, name and path; ingestion records no user. The only signal that already flows through the pipeline is the folder structure: `watcher.py` and `ingest.py` move files from `input_utente/` through `data_nuovi/` into `data_archivio/` preserving their relative folders.

## Decision

- A document is owned by the user whose username equals the first folder of its path under `data_archivio/`, compared case-insensitively (`casefold()`), matching Windows file system semantics.
- Documents without such a folder (files at the archive root, or under a first folder that matches no user) have no owner and are readable by ADMIN only (deny by default).
- `/documents/{path}` is served by an authenticated FastAPI route (`src/documents.py`) instead of a static mount. For each request it checks, in this order: a valid session (401), path containment after resolving `..` and symlinks (404), ownership (403, checked before existence so that names in other folders cannot be probed), existence (404). Responses carry `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-store`; error bodies are fixed strings.
- The role is re-read from `users.json` on every download, so a demoted or deleted user does not keep access through an old session.
- Each denial logs one WARNING with the reason and the client IP only, never the requested name or path.
- Chat Sources links are built from the indexed `file_path`; documents the user cannot open are shown as a plain label.

## Alternatives considered

- Ownership map file (path or hash to usernames): new private data file and admin UI, and every existing document starts unowned anyway.
- Owner metadata written into ChromaDB at ingestion: changes ingestion, requires re-ingesting the whole archive, and adds a lookup per download. It remains the natural mechanism for filtering RAG retrieval and can derive the owner from this same folder convention.
- Owner inferred from email sender or Outlook id: only emails have a sender, and it is usually the client, not the staff member.
- Middleware in front of the static mount: two code paths (containment in StaticFiles, ownership in the middleware) to keep consistent.

## Consequences

- Offices organise the drop zone per user: `input_utente/<username>/...`. Usernames must be unique ignoring case.
- On an archive not organised per user, non-admin users lose access to every document outside their own folder until an administrator moves files into `<username>/` folders. Moved files keep their old indexed `file_path`; Sources links fall back to the name search until they are re-indexed.
- Shared documents and multiple owners per document are not supported.
- RAG retrieval is not filtered by owner yet, so chat answers can still contain text from documents the user does not own; tracked as a separate follow-up issue (D7).
- Admin re-ingestion currently copies files to the inbox root, dropping the owner folder; tracked as a separate issue (D8).
- Changing the convention later requires a data migration.
