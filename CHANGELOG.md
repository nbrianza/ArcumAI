# Changelog

All notable changes to ArcumAI are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- GitHub Actions workflow (`.github/workflows/tests.yml`): pytest suite and Outlook add-in compile on every push and PR
- `requirements-dev.txt` (pytest); `INGEST_TIMEOUT_SEC` env var for admin-triggered ingestion
- Server answers plugin heartbeats with `heartbeat/ack`; the plugin uses it to detect a silent server (#20)

### Changed

- Numeric env vars are range-checked at startup and fail fast with a clear message (#27)
- Admin ingestion: one run at a time, below-normal priority, timeout reported instead of raised (#29)
- Plugin persists server-pushed config in `%APPDATA%\ArcumAI\Outlook\server-config.json` (#25)
- Plugin MAPI property URIs moved to `Core/MapiProperties.cs` (#31); `OutlookDataProvider` created per call (#30)

### Fixed

- **Outlook add-in did not compile on `main`** — unescaped quotes in the config README string (`PluginConfigLoader.cs`)
- `requirements.txt` was UTF-16 and missing Presidio/spaCy NER packages and models (fresh installs ran without PII masking)
- Corrupted conversation files no longer crash history loading (#26)
- Rate limiter cleanup guarded by a lock (#28)
- Plugin: large multi-frame WebSocket messages corrupted accented characters at 8 KB boundaries
- Plugin: every JSON-RPC request gets a response, with proper error codes for invalid params/unknown methods (#22)
- Plugin: attachment size re-checked on disk and capped by encoded payload size before loading (#21)
- Plugin: loopback timeouts re-armed after reconnect, so unanswered requests no longer stay pending forever (#24)
- Plugin: each COM object released independently (#23); removed no-op catch/rethrow (#32)

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
