# ADR 0004: RAG retrieval follows the document ownership rule

- Status: accepted (owner approval on issue #45, decisions D1 to D4)
- Date: 2026-10-25
- Issue: #45

## Context

ADR 0002 restricts `/documents` downloads by archive folder, but RAG retrieval returned chunks from every document to every user, on the web chat and in Outlook loopback replies. A non-admin user could therefore read the content of documents they do not own through chat answers.

Every chunk ingested by `ingest.py` already carries `file_path`, the path relative to the inbox, whose first folder is kept when the file is moved into the archive.

## Decision

- Non-admin retrieval is filtered at query time from the indexed `file_path`, using the same `can_read` rule as downloads (`src/documents.py`: `indexed_path_owner` plus `can_read`).
- `src/ai/engines.py:build_rag_retriever(username, role)` builds the retriever. ADMIN gets the unchanged hybrid retriever. Any other role gets: a Chroma `in` filter on the allowed `file_path` values (applied before ranking), a BM25 weight mask on the same values, and a post-filter on every sub-retriever result, which is the security guarantee. If nothing is readable, an empty retriever is used.
- `load_rag_engine(user_role, username=None)`: a missing username for a non-admin role means nothing is readable (deny by default).
- `UserSession.get_rag_engine` re-reads the role from the users file and passes the username; a user no longer in the file reads nothing.
- No log line names paths, files, owners or chunk text; the engine log only says whether the owner filter is on.

## Alternatives considered

- Owner metadata written at ingestion plus a one-time backfill: changes ingestion, needs a migration on every installation, and duplicates the owner. Worth revisiting if the per-session metadata scan becomes slow.
- Post-filter only: non-admin users would get only what of their own chunks happens to be in the global top-k.
- Per-user indexes: changes storage layout and requires full re-ingestion.
- `node_postprocessors` on the chat engine: runs after fusion truncation, same recall loss as post-filter only.

## Consequences

- Ownership for retrieval is the folder at ingestion time; a document moved inside the archive after ingestion must be re-ingested from `input_utente\<username>\`.
- Each non-admin engine build scans chunk metadata once (`collection.get(include=["metadatas"])`).
- On an archive not organised per user, non-admin users get no RAG context.
- Admin page re-ingestion drops the owner folder until issue #46 is fixed.
