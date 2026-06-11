# Changelog

All notable changes to ArcumAI are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.2.0] — 2026-06-11

### Added

**Conversation history**
- `src/conversations.py` — `ConversationStore` class persists per-user chat sessions as JSON files
- Sidebar conversation panel with session list, load, and delete
- Auto-title from first user message; newest-first ordering

**Admin panel** (`src/ui/admin.py`)
- User management: create, update password, delete users
- Trigger manual document ingestion from the UI
- System status display

**Automated test suite** — 134 tests across 3 tiers
- Tier 1 (fast, no deps): auth, rate limiter, NER masking, loopback queue, pending results
- Tier 2 (mocked): prompt optimizer, file readers, config constants, conversation store
- Tier 3 (integration): WebSocket bridge manager, ingest pipeline
- `tests/TEST_CASES.md` — full catalogue of all test cases

**Outlook plugin — client config push**
- Server pushes VSTO configuration (attachment limits, loopback email address, timeout) to the plugin during the `client/identify` handshake, eliminating hardcoded values in the C# client

**Optional WebSocket shared-secret authentication** (`WS_API_KEY` env var)
- Plugins send a `X-Api-Key` header; server rejects connections with an invalid key

### Changed

- `src/ui/footer.py` — file upload now validates content via magic bytes, not just extension; rejects files whose bytes don't match the declared type
- `src/ai/ner_masking.py` — PII placeholders now use UUID-based tokens (`__PII_xxxxxxxx__`) to prevent collisions with naturally occurring `<TYPE_N>` patterns in text
- `src/ai/prompt_optimizer.py` — rejects emails exceeding 100 000 characters before sending to Gemini; raises `ValueError` with a clear message
- `src/config.py` — Tesseract and Poppler paths read from `TESSERACT_CMD` / `POPPLER_PATH` env vars; emits a startup warning when OCR is disabled instead of silently failing
- `src/bridge/manager.py` — pending MCP futures cancelled immediately on disconnect (no more hung tool calls after reconnect); orphaned futures cancelled on WebSocket request timeout
- `src/bridge/pending_results.py` — temp file cleanup failures now logged instead of silently swallowed
- `src/ui/rate_limiter.py` — stale user entries evicted periodically to prevent unbounded memory growth
- `src/auth.py` — per-IP brute-force protection added to the WebSocket auth endpoint

### Fixed (Security)

- **STORAGE_SECRET fail-fast** — server refuses to start in production without `STORAGE_SECRET`; predictable default removed. Dev mode (`ARCUMAI_ENV=dev`) generates an ephemeral random secret and logs a warning.
- **Path traversal in admin ingestion** — `find_relative_path` and the admin file picker now reject filenames containing `..` or glob metacharacters
- **Path traversal in `find_relative_path`** — results validated to be inside `ARCHIVE_DIR` before serving
- **Log injection** — `user_id` sanitized at all bridge entry points (`\n`, `\r`, `\x1b` escaped)
- **C# payload logging** — attachment content redacted from debug logs in `VirtualLoopbackHandler`
- **C# `UseSecureConnection` flag** — enforced in WebSocket URL construction; `wss://` used when flag is set
- **Bare `except` clauses** — replaced with typed exceptions throughout; `on_message_sent` callback failures now logged
- **WebSocket inactivity timeout** — receive loop now times out after `WS_RECEIVE_TIMEOUT` seconds (default 120 s) to free zombie connections
- **Disconnect race condition** — timeout tasks cancelled on clean disconnect to prevent false timeout emails after reconnect
- **Payload size limit** — C# plugin enforces max payload size before sending to backend
- **Email address matching** — loose `StartsWith` fallback removed from loopback address check; exact match only

---

## [1.1.0] — 2026-03-09

### Added
- Token-based context management in `UserSession`
- Web UI refresh with improved layout and mode indicators

### Fixed
- 28 bugs and security issues identified in internal code review (Python + C#)
- `watcher.py` subprocess call updated from `main.py` to `ingest.py` after rename

---

## [1.0.0] — 2026-02-xx

Initial open-source release.

- Hybrid RAG pipeline (ChromaDB + BM25)
- Ollama local LLM with Gemini cloud fallback
- NER-based PII masking for cloud calls
- Outlook VSTO add-in with virtual loopback
- NiceGUI web interface with bcrypt authentication
- Multi-format document ingestion (PDF, DOCX, MSG, EML, XLSX, TXT)
- Hardware profiles (HIGH_RESOURCE / LOW_RESOURCE)
