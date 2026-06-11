# ArcumAI — Full Project Analysis & Reference Documentation

**Date:** 2026-03-08
**Branch:** `dev-features`
**Scope:** Complete codebase inspection — Python backend + C# VSTO plugin

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Component Inventory](#3-component-inventory)
4. [Issues Found](#4-issues-found)
   - 4.1 Critical Security Issues
   - 4.2 Bugs & Logic Errors
   - 4.3 Code Quality & Anti-Patterns
   - 4.4 Performance Concerns
5. [Suggested Improvements](#5-suggested-improvements)
6. [Proposed New Capabilities](#6-proposed-new-capabilities)
7. [Architectural Reference (Target State)](#7-architectural-reference-target-state)
8. [Roadmap](#8-roadmap)
9. [File-by-File Reference](#9-file-by-file-reference)

---

## 1. Executive Summary

ArcumAI is a **privacy-first AI assistant** for Swiss legal/fiduciary offices. It combines:

- **Python backend** (FastAPI + NiceGUI): RAG pipeline over legal documents (ChromaDB + BM25 hybrid search), AI chat (Ollama local + Gemini cloud), WebSocket bridge to Outlook
- **C# VSTO Outlook add-in**: Intercepts emails to `assistant@arcumai.ch`, sends them to the backend for AI processing, returns responses as reply emails
- **Document ingestion pipeline**: Watches a folder, reads PDF/DOCX/MSG/EML/XLSX/TXT, creates embeddings, stores in ChromaDB

**Current State:** Functional MVP with solid refactoring (4 phases completed). The code works but has several security vulnerabilities, missing production hardening, and opportunities for significant capability improvements.

**Key Strengths:**
- Clean modular architecture after refactoring
- Privacy-first design (local LLM default, NER masking for cloud)
- Robust file processing pipeline with OCR fallback
- Well-designed WebSocket bridge with priority queues, offline result storage, and deduplication
- Multi-language support (IT/EN/DE/FR)

**Key Risks:**
- Hardcoded API keys committed to repository
- No HTTPS/WSS enforcement
- No session timeout or CSRF protection
- Missing input validation in several places
- No automated testing beyond import checks

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENTS                                   │
│  ┌──────────────┐  ┌─────────────────────────────────────────┐  │
│  │  NiceGUI Web  │  │  Outlook VSTO Plugin (C#)              │  │
│  │  (Browser)    │  │  WebSocket → ws://server:8080/ws/...   │  │
│  └──────┬───────┘  └──────────────┬──────────────────────────┘  │
│         │                         │                              │
└─────────┼─────────────────────────┼──────────────────────────────┘
          │ HTTP                    │ WebSocket (JSON-RPC)
          ▼                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PYTHON BACKEND (main_nice.py)                  │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐    │
│  │  FastAPI  │  │  NiceGUI UI  │  │  OutlookBridgeManager  │    │
│  │  /health  │  │  /login, /   │  │  /ws/outlook/{user_id} │    │
│  └──────────┘  └──────┬───────┘  └───────────┬────────────┘    │
│                       │                       │                  │
│              ┌────────▼───────────────────────▼────────┐        │
│              │           UserSession                    │        │
│              │  ┌─────────┐ ┌───────┐ ┌──────┐ ┌────┐│        │
│              │  │RAG Eng. │ │Simple │ │Cloud │ │Agent││        │
│              │  │(Hybrid) │ │(Local)│ │(Gem.)│ │(MCP)││        │
│              │  └────┬────┘ └───────┘ └──────┘ └────┘│        │
│              └───────┼────────────────────────────────┘        │
│                      ▼                                          │
│  ┌─────────────────────────────────────────────────────┐       │
│  │  DATA LAYER                                          │       │
│  │  ChromaDB (vectors) + BM25 (keywords) + users.json  │       │
│  └─────────────────────────────────────────────────────┘       │
│                                                                  │
│  ┌──────────────────┐  ┌─────────────────────┐                 │
│  │  watcher.py       │  │  ingest.py           │                 │
│  │  (folder monitor) │──▶│  (batch processing) │                 │
│  └──────────────────┘  └─────────────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow — Virtual Loopback (Email → AI → Reply)

```
1. User sends email to assistant@arcumai.ch from Outlook
2. VSTO plugin intercepts → extracts body + attachments
3. Plugin sends JSON-RPC "virtual_loopback/send_email" via WebSocket
4. Bridge ACKs → enqueues in priority queue
5. Worker acquires AI semaphore → LoopbackProcessor processes:
   a. Decode base64 attachments → extract text
   b. Optimize prompt (local LLM or Gemini with NER masking)
   c. Route to RAG (no attachments) or FILE_READER (with attachments)
   d. Generate AI response
6. Response sent back via WebSocket (or stored to disk if client offline)
7. Plugin creates reply email in Outlook Inbox
```

---

## 3. Component Inventory

### Python Source Files (src/)

| File | LOC | Purpose |
|------|-----|---------|
| `main_nice.py` | 183 | App entry point: FastAPI + NiceGUI + WS endpoint |
| `ingest.py` | 231 | Batch document ingestion pipeline |
| `watcher.py` | 178 | Folder watcher → triggers ingestion |
| `src/config.py` | 178 | All configuration, paths, prompts |
| `src/auth.py` | 92 | User CRUD + bcrypt password hashing |
| `src/database.py` | 14 | ChromaDB index loader |
| `src/logger.py` | 63 | Dual-logger (ingestion + server) |
| `src/readers.py` | 191 | PDF (smart OCR), MSG, EML readers |
| `src/utils.py` | 211 | File ops, triggers loading, ChromaDB utils |
| `src/engine.py` | 10 | Re-exports for backward compat |
| `src/ai/engines.py` | 81 | RAG, Simple, Cloud engine factories |
| `src/ai/session.py` | 292 | UserSession: routing, tools, chat execution |
| `src/ai/prompt_optimizer.py` | 177 | Email→query optimization (local/Gemini) |
| `src/ai/ner_masking.py` | 285 | PII detection/masking via Presidio |
| `src/bridge/manager.py` | 311 | WebSocket bridge + queue management |
| `src/bridge/loopback_processor.py` | 334 | Email processing + AI routing |
| `src/bridge/loopback_queue.py` | 22 | Priority queue data structures |
| `src/bridge/pending_results.py` | 136 | Offline result persistence |
| `src/ui/footer.py` | 207 | Chat input, upload, send logic |
| `src/ui/header.py` | 51 | Top bar with cloud toggle |
| `src/ui/sidebar.py` | 37 | Command guide + status |
| `src/ui/chat_area.py` | 9 | Chat container |
| `src/ui/rate_limiter.py` | 34 | Rate limiting + input sanitization |

### C# Source Files (outlook-plugin/)

| File | Purpose |
|------|---------|
| `ThisAddIn.cs` | VSTO entry point, lifecycle management |
| `Core/VirtualLoopbackHandler.cs` | Email interception + response handling |
| `Core/OutlookDataProvider.cs` | Email search + calendar data extraction |
| `Core/PluginConfig.cs` | Configuration properties + validation |
| `Core/PluginConfigLoader.cs` | Config loading from JSON + AppConfig |
| `Core/PluginLogger.cs` | IPluginLogger interface + implementation |
| `Core/Transport/IMcpTransport.cs` | Transport abstraction interface |
| `Core/Transport/WebSocketTransport.cs` | WebSocket client with reconnection |
| `Core/Loopback/AttachmentExtractor.cs` | Attachment reading + base64 encoding |
| `Core/Loopback/ContactManager.cs` | Contact resolution from address book |
| `Core/Loopback/OutlookMailFactory.cs` | Reply email construction |

### Supporting Files

| File | Purpose |
|------|---------|
| `admin_tool.py` | CLI user management |
| `rag_query.py` | CLI RAG testing tool |
| `scripts/debug_search.py` | Debug search retrieval |
| `scripts/diagnose_file.py` | Inspect ChromaDB entries |
| `scripts/diagnose_pdf.py` | PDF text quality analysis |
| `scripts/scarica_leggi_ti.py` | Swiss law scraper (Ticino) |
| `scripts/test_gemini.py` | Gemini API connection test |
| `triggers/*.txt` | Keyword files for intent routing |
| `requirements.txt` | Python dependencies (254 packages) |
| `users.json` | User database (gitignored) |

---

## 4. Issues Found

### 4.1 Critical Security Issues

#### SEC-1: Hardcoded API Key in Source Code (CRITICAL) — RESOLVED 2026-03-15
**File:** `scripts/test_gemini.py:6`
**Issue:** Google API key was hardcoded in source and committed to git history.
**Resolution:**
- Script rewritten to load key from `.env` via `dotenv` (no more hardcoded secret)
- Compromised key (`AIzaSyCCXYI...`) revoked in Google Cloud Console
- New key generated and stored only in `.env` (gitignored)
- **Note:** The old key remains visible in git history. If this is a public or shared repo, consider using `git filter-branch` or BFG Repo-Cleaner to purge it.

#### SEC-2: Default Storage Secret in Source Code (HIGH)
**File:** `main_nice.py:173`
**Issue:** `CHIAVE_SEGRETA_ARCUM_AI_V2_DEV_DEFAULT` is a hardcoded fallback for the NiceGUI session encryption secret.
**Impact:** If `STORAGE_SECRET` env var is missing, all sessions use this predictable key → session hijacking.
**Fix:** Fail-fast if `STORAGE_SECRET` is not set. Never provide a default.

#### SEC-3: No HTTPS/WSS Enforcement (HIGH)
**File:** `main_nice.py`
**Issue:** Server binds on `0.0.0.0:8080` with plain HTTP. WebSocket connections are unencrypted.
**Impact:** Credentials, API keys, and email content transmitted in cleartext.
**Fix:** Add TLS termination (reverse proxy like nginx/Caddy) or enable uvicorn SSL.

#### SEC-4: Static Files Serve the Entire Archive Directory (HIGH)
**File:** `main_nice.py:53`
**Issue:** `app.add_static_files('/documents', str(ARCHIVE_DIR))` exposes ALL archived documents to any authenticated user.
**Impact:** No per-user access control on documents. Any user can enumerate and download any document.
**Fix:** Implement access-controlled document serving (check user role/ownership before serving).

#### SEC-5: No CSRF Protection (MEDIUM)
**File:** `main_nice.py`
**Issue:** No CSRF tokens on form submissions or state-changing operations.
**Impact:** Cross-site request forgery attacks possible.

#### SEC-6: WebSocket Connection No Authentication Token (MEDIUM)
**File:** `main_nice.py:80-97`
**Issue:** WebSocket endpoint only validates `outlook_id` from the URL path. No authentication token, no connection secret.
**Impact:** Anyone who knows a valid `outlook_id` can connect and send/receive emails.
**Fix:** Require a connection token (e.g., HMAC signature) or pre-shared secret.

#### SEC-7: .env File Contains Production Secrets (MEDIUM)
**File:** `.env`
**Issue:** Contains `CHAINLIT_AUTH_SECRET`, `GOOGLE_API_KEY`, `STORAGE_SECRET` — all real production values.
**Note:** `.gitignore` correctly excludes `.env`, but there's still risk if the file was ever committed or shared.

#### SEC-8: MD5 Hash for Deduplication (LOW)
**File:** `src/utils.py:14-24`
**Issue:** Uses MD5 for file deduplication. MD5 is cryptographically broken.
**Impact:** Low in this context (deduplication, not authentication), but collisions are possible.
**Fix:** Use SHA-256 instead.

#### SEC-9: No Login Brute-Force Protection (HIGH)
**File:** `main_nice.py:117-134`
**Issue:** No rate limiting on login attempts. Attacker can brute-force credentials at unlimited speed.
**Fix:** Implement exponential backoff or account lockout after N failed attempts.

#### SEC-10: PII Logged at DEBUG Level (MEDIUM)
**File:** `src/ai/prompt_optimizer.py:116-117`
**Issue:** `slog.debug(f"Masked email text:\n{masked_email}")` — logs masked email content. Combined with `LOG_LEVEL=DEBUG` in production `.env`, this writes PII to logs even after masking.
**Fix:** Never log email content at any level, or use a separate privacy-safe logger.

#### SEC-11: No WebSocket Origin Validation (MEDIUM)
**File:** `main_nice.py:80-97`
**Issue:** No check that WebSocket connections originate from the expected Outlook plugin. Any origin can connect.
**Fix:** Validate `Origin` header or require a pre-shared connection token.

---

### 4.2 Bugs & Logic Errors

#### BUG-1: `requirements.txt` Has Corrupted Encoding
**File:** `requirements.txt`
**Issue:** File contains spaces between every character (UTF-16 BOM or encoding issue). Example: `a i o f i l e s = = 2 4 . 1 . 0` instead of `aiofiles==24.1.0`.
**Impact:** `pip install -r requirements.txt` will fail. Dependencies cannot be installed from this file.
**Fix:** Regenerate with `pip freeze > requirements.txt` using UTF-8 encoding.

#### BUG-2: `diagnose_file.py` Uses Hardcoded Paths Instead of Config
**File:** `scripts/diagnose_file.py:6-8`
**Issue:** `BASE_DIR = Path(__file__).parent.resolve()` — resolves to `scripts/` not project root. `DB_PATH` will point to `scripts/chroma_db/` which doesn't exist.
**Fix:** Use `Path(__file__).parent.parent.resolve()` or import from `src.config`.

#### BUG-3: Lock File Race Condition in `ingest.py`
**File:** `ingest.py:36-41`
**Issue:** `open(LOCK_FILE, 'x')` is not atomic on Windows (NFS/SMB). If two watcher instances trigger simultaneously, both could acquire the lock.
**Impact:** Concurrent ingestion could corrupt the ChromaDB index.
**Fix:** Use `msvcrt.locking()` on Windows or `fcntl.flock()` on Linux for proper file locking.

#### BUG-4: Temp File Left Behind on Upload Error
**File:** `src/ui/footer.py:58-74`
**Issue:** `temp_ghost_upload.pdf` is created but only deleted in the success path. If `read_pdf_sync()` raises, the file persists.
**Fix:** Use `tempfile.NamedTemporaryFile` with a `finally` block, similar to `loopback_processor.py`.

#### BUG-5: `WATCH_EXTENSIONS` Uses Case-Sensitive Matching
**File:** `src/config.py:86-93`
**Issue:** Extensions are listed as both `.pdf` and `.PDF`, but the check in `watcher.py:55` does `Path(event.src_path).suffix not in WATCH_EXTENSIONS`. Python's `Path.suffix` preserves the original case, so `.Pdf` or `.pDf` would be missed.
**Fix:** Normalize to lowercase: `if Path(event.src_path).suffix.lower() not in {'.pdf', '.msg', ...}`.

#### BUG-6: User Database File Concurrent Write Risk
**File:** `src/auth.py:35-38`
**Issue:** `save_users()` does a plain `open(..., 'w')` + `json.dump()`. No locking, no atomic write.
**Impact:** If two requests modify users simultaneously, data can be lost or corrupted.
**Fix:** Write to a temp file first, then `os.replace()` atomically.

#### BUG-7: `_user_timestamps` Memory Leak in Rate Limiter
**File:** `src/ui/rate_limiter.py:12`
**Issue:** `_user_timestamps` dictionary grows indefinitely. Old usernames are never removed.
**Impact:** Minor memory leak over long server uptime.
**Fix:** Add periodic cleanup or use `TTLCache`.

#### BUG-8: Unreachable Code in `src/utils.py`
**File:** `src/utils.py:130-132`
**Issue:** Code after `return []` at line 128 is unreachable. The `from src.config import ARCHIVE_DIR` and functions below it are placed after a `return` statement inside `get_all_nodes_from_chroma`.
**Reality:** This code is actually at module level (not inside the function), but the comment `# --- ADDITIONS FOR ARCUM AI HYBRID UI ---` on line 130 and the missing blank line after the function make it look like it's inside the function. It works, but it's confusing.
**Fix:** Add a clear separation (blank lines + section comment) between the function and the module-level code.

#### BUG-9: `analyze_text_quality` Return Type Inconsistency
**File:** `scripts/diagnose_pdf.py:22-43`
**Issue:** Returns a tuple `("EMPTY (0 chars)", 0.0, 0.0)` for empty text but a dict for non-empty text. Line 91 checks `isinstance(stats, tuple)` — fragile and confusing.
**Fix:** Always return a dict; use a flag field for the empty case.

#### BUG-10: Double Hash Calculation in Ingestion
**File:** `ingest.py:92,143`
**Issue:** `calcola_hash_file(file_path)` is called at line 143 for deduplication, then again at line 92 inside `read_and_chunk_file()`. Wastes I/O on large files.
**Fix:** Pass the already-computed hash into `read_and_chunk_file()`.

#### BUG-11: Silent File Truncation on Upload
**File:** `src/ui/footer.py:82`
**Issue:** `session.uploaded_context = text_content[:10000]` silently truncates uploaded files at 10,000 characters without warning the user. Critical information may be cut off.
**Fix:** Warn the user if truncation occurred and make the limit configurable.

#### BUG-12: No Scheduled Cleanup of Pending Results
**File:** `src/bridge/pending_results.py`
**Issue:** TTL-based expiry only happens when a client reconnects. If a client never reconnects, temp files accumulate indefinitely on disk.
**Fix:** Add a periodic cleanup task (e.g., hourly) to purge expired results.

---

### 4.3 Code Quality & Anti-Patterns

#### QA-1: Bare `except` Clauses
**Files:** Multiple (e.g., `footer.py:159`, `footer.py:192`)
**Issue:** `except: pass` or `except Exception:` used extensively without logging.
**Fix:** At minimum log the exception.

#### QA-2: Global State in `bridge_manager`
**File:** `src/bridge/manager.py:311`
**Issue:** `bridge_manager = OutlookBridgeManager()` is a module-level singleton. Hard to test, hard to reset.
**Fix:** Use dependency injection or a factory function.

#### QA-3: Duplicate Import of `Path`
**File:** `src/utils.py:154`
**Issue:** `from pathlib import Path` appears twice in the same file (line 7 and line 154).

#### QA-4: `nest_asyncio` Usage
**File:** `main_nice.py:17`
**Issue:** `nest_asyncio.apply()` patches the event loop to allow re-entrant async calls. This is a workaround, not a solution — it can mask real concurrency bugs.
**Fix:** Investigate why nested event loops are needed and restructure if possible.

#### QA-5: No Type Hints on Most Functions
**Files:** Most Python files
**Issue:** Limited use of type annotations. Makes IDE support and static analysis less effective.

#### QA-6: Mixed Languages in Comments and Strings
**Files:** Throughout
**Issue:** Mix of Italian and English in comments, variable names, log messages, and system prompts. Example: `pulisci_cartelle_vuote`, `calcola_hash_file`, `sposta_file_con_struttura`.
**Impact:** Reduces readability for non-Italian speakers.

#### QA-7: Hardcoded LLM Model Names
**File:** `src/ai/engines.py:70`, `src/ai/prompt_optimizer.py:23`
**Issue:** `"models/gemini-2.5-flash"` is hardcoded. Should be configurable.

#### QA-8: No Dependency Pinning Strategy
**File:** `requirements.txt`
**Issue:** 254 packages with exact versions but no separation between direct and transitive dependencies. No `pyproject.toml` or `setup.cfg`.
**Fix:** Use `pyproject.toml` with direct dependencies; generate lock file for reproducible builds.

---

### 4.4 Performance Concerns

#### PERF-1: BM25 Index Full Rebuild on Every Ingestion
**File:** `ingest.py:203-213`
**Issue:** After every batch, ALL nodes are fetched from ChromaDB to rebuild the entire BM25 index.
**Impact:** O(n) operation that gets slower as the corpus grows. With thousands of documents, this becomes a bottleneck.
**Fix:** Implement incremental BM25 updates or rebuild on a schedule.

#### PERF-2: ChromaDB `get()` Without Pagination
**File:** `src/utils.py:105-128`
**Issue:** `chroma_collection.get(include=["documents", "metadatas"])` loads ALL documents into memory.
**Impact:** Will OOM with large collections.
**Fix:** Use pagination (`limit`/`offset`) or streaming.

#### PERF-3: New UserSession Created Per Loopback Request
**File:** `src/bridge/loopback_processor.py:250-266`
**Issue:** Every loopback email creates a new `UserSession`, which loads `users.json` from disk, initializes tools, and creates new engine instances.
**Impact:** Redundant I/O and initialization overhead.
**Fix:** Cache sessions per user or use a session pool.

#### PERF-4: `torch` in Dependencies (2+ GB)
**File:** `requirements.txt`
**Issue:** Full PyTorch is installed (`torch==2.9.1`) for sentence-transformers. On CPU-only servers, this wastes significant disk space.
**Fix:** Use `torch-cpu` or `onnxruntime` backend for sentence-transformers.

#### PERF-5: Synchronous OCR Blocking
**File:** `src/readers.py:122-126`
**Issue:** OCR (Tesseract) is synchronous and CPU-intensive. When called from the watcher pipeline, it blocks the entire process.
**Fix:** Run OCR in a thread pool or subprocess.

---

## 5. Suggested Improvements

### 5.1 Security Hardening (Priority: CRITICAL)

1. **Rotate all exposed API keys** — The Google API key in `scripts/test_gemini.py` is compromised
2. **Enforce STORAGE_SECRET** — Remove default fallback, fail on startup if not set
3. **Add WebSocket authentication** — Require a token/secret for Outlook connections
4. **Implement per-user document access control** — Don't serve all documents to all users
5. **Add session timeout** — Currently sessions last forever
6. **Add HTTPS/WSS** — Use reverse proxy (nginx/Caddy) or configure uvicorn SSL
7. **Add brute-force protection** — Currently no login attempt throttling

### 5.2 Testing (Priority: HIGH)

1. **Unit tests for AI routing logic** — `UserSession.decide_engine()` is complex and untested
2. **Integration tests for the bridge** — WebSocket connection/disconnection, message handling
3. **Test the loopback processor** — Attachment decoding, AI routing, response formatting
4. **Test auth module** — Password validation, user CRUD
5. **Test rate limiter** — Edge cases, window expiration
6. **Add CI/CD pipeline** — Run tests on every push

### 5.3 Code Quality (Priority: MEDIUM)

1. **Fix `requirements.txt` encoding** — Regenerate with proper UTF-8
2. **Create `pyproject.toml`** — Modern Python packaging with direct dependency listing
3. **Standardize language** — Choose English for all code, comments, and variable names
4. **Add type hints** — At least for public interfaces
5. **Remove dead scripts** — `scripts/test_gemini.py` with hardcoded key, etc.
6. **Add proper error types** — Custom exceptions instead of generic `Exception`

### 5.4 Operational (Priority: MEDIUM)

1. **Health check enhancement** — Include DB connection status, Ollama reachability
2. **Structured logging (JSON)** — For log aggregation tools (ELK, Grafana Loki)
3. **Metrics endpoint** — Prometheus-compatible metrics (request count, latency, queue depth)
4. **Graceful shutdown** — Drain queue, close WebSocket connections cleanly
5. **Configuration validation** — Validate all config values on startup, fail-fast on errors
6. **Environment-based config** — Separate dev/staging/prod configurations

---

## 6. Proposed New Capabilities

### 6.1 Multi-User Conversation History (Priority: HIGH)
**Why:** Currently, chat history is ephemeral (lost on page refresh). Users lose context.
**What:** Persist conversation history per user in SQLite/PostgreSQL. Allow users to resume previous conversations.
**How:** Add a `conversations` table with `user_id`, `timestamp`, `messages JSONB`. Load on session start.

### 6.2 Document Management UI (Priority: HIGH)
**Why:** Users cannot see what documents are in the knowledge base, delete outdated ones, or manually trigger re-ingestion.
**What:** Admin panel showing all indexed documents with metadata, search, delete, and re-index actions.
**How:** Add a `/admin` page in NiceGUI with ChromaDB CRUD operations.

### 6.3 Multi-Tenant Support (Priority: HIGH)
**Why:** Currently all users share the same document collection. A legal firm's documents should be isolated per client/matter.
**What:** Namespace documents by tenant/matter. Filter retrieval by tenant context.
**How:** Add a `tenant_id` metadata field to all ChromaDB documents. Filter during retrieval.

### 6.4 Streaming Responses in Web UI (Priority: MEDIUM)
**Why:** The web UI waits for the full AI response before displaying. With large/complex queries, users see a spinner for 30+ seconds.
**What:** Stream tokens as they're generated.
**How:** Use `astream_chat()` and update the UI incrementally via NiceGUI's reactivity.

### 6.5 Email Thread Context (Priority: MEDIUM)
**Why:** The loopback processor treats each email independently. It doesn't understand reply chains.
**What:** When processing a reply, include the previous messages in the conversation for context.
**How:** Use `conversation_id` to look up previous exchanges. Maintain a conversation cache.

### 6.6 Feedback & Learning Loop (Priority: MEDIUM)
**Why:** No mechanism for users to signal "this answer was helpful" or "this was wrong."
**What:** Add thumbs up/down buttons on AI responses. Collect feedback for fine-tuning and prompt improvement.
**How:** Store feedback in a `feedback` table. Use it to adjust system prompts or retrieval parameters.

### 6.7 Calendar/Task Integration (Priority: MEDIUM)
**Why:** The agent has `tool_get_calendar` but it's read-only and limited.
**What:** Allow the AI to create calendar events, set reminders, and manage tasks.
**How:** Add MCP tools for `create_event`, `set_reminder`. Implement in C# plugin.

### 6.8 Multi-Model Support (Priority: LOW)
**Why:** Currently hardcoded to Ollama (local) and Gemini (cloud). Users might want Claude, GPT-4, or Mistral.
**What:** Pluggable LLM provider system.
**How:** Abstract the LLM interface. Add provider configs to `.env`.

### 6.9 Audit Log (Priority: HIGH for compliance)
**Why:** Swiss fiduciary firms have regulatory requirements for data access logging.
**What:** Log every document access, AI query, and user action with timestamps.
**How:** Structured audit log to a separate, append-only log file or database table.

### 6.10 Automated Document Classification (Priority: MEDIUM)
**Why:** Currently all documents are treated equally. Legal documents, invoices, contracts, and correspondence have different significance.
**What:** Auto-classify ingested documents by type. Add classification metadata for better retrieval filtering.
**How:** Use a lightweight classifier (or LLM prompt) during ingestion to tag document type.

---

## 7. Architectural Reference (Target State)

### 7.1 Layered Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  PRESENTATION LAYER                                           │
│  ┌─────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ NiceGUI Web  │  │ Admin Panel   │  │ REST API (future)│  │
│  └─────────────┘  └───────────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  API LAYER (FastAPI)                                          │
│  ┌──────────┐  ┌────────────┐  ┌────────────────────────┐  │
│  │ Auth MW   │  │ Rate Limit │  │ WebSocket Gateway      │  │
│  │ CSRF MW   │  │ Middleware │  │ (Outlook Bridge)       │  │
│  └──────────┘  └────────────┘  └────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  BUSINESS LOGIC LAYER                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ AI Pipeline  │  │ Doc Pipeline │  │ User Management  │  │
│  │ (Session,    │  │ (Ingest,     │  │ (Auth, Roles,    │  │
│  │  Routing,    │  │  OCR, Index) │  │  Audit)          │  │
│  │  Engines)    │  │              │  │                  │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────────┘  │
├─────────┼────────────────┼──────────────────┼────────────────┤
│  DATA ACCESS LAYER                                            │
│  ┌──────▼──────┐  ┌──────▼───────┐  ┌──────▼───────────┐  │
│  │ VectorStore │  │ DocStore     │  │ UserStore        │  │
│  │ (ChromaDB)  │  │ (FileSystem) │  │ (SQLite/Postgres)│  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Ollama   │  │ Gemini   │  │ Tesseract│  │ Poppler    │ │
│  │ (Local)  │  │ (Cloud)  │  │ (OCR)    │  │ (PDF)      │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Target File Structure

```
arcumai/
├── pyproject.toml              ← Modern packaging, direct deps only
├── .env.example                ← Template (no secrets!)
├── docker-compose.yml          ← Container orchestration
├── Dockerfile                  ← Multi-stage build
│
├── src/
│   ├── __init__.py
│   ├── app.py                  ← FastAPI app factory
│   ├── settings.py             ← Pydantic Settings with validation
│   │
│   ├── api/                    ← API endpoints
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── websocket.py
│   │   └── admin.py
│   │
│   ├── ai/                     ← AI pipeline (unchanged, well-structured)
│   │   ├── engines.py
│   │   ├── session.py
│   │   ├── prompt_optimizer.py
│   │   └── ner_masking.py
│   │
│   ├── bridge/                 ← Outlook bridge (unchanged, well-structured)
│   │   ├── manager.py
│   │   ├── loopback_processor.py
│   │   ├── loopback_queue.py
│   │   └── pending_results.py
│   │
│   ├── ingestion/              ← Document pipeline
│   │   ├── ingest.py
│   │   ├── watcher.py
│   │   └── readers.py
│   │
│   ├── data/                   ← Data access layer
│   │   ├── vector_store.py
│   │   ├── user_store.py
│   │   └── audit_store.py
│   │
│   ├── ui/                     ← NiceGUI pages (unchanged)
│   │   ├── header.py
│   │   ├── sidebar.py
│   │   ├── chat_area.py
│   │   ├── footer.py
│   │   └── admin_panel.py
│   │
│   └── core/                   ← Shared utilities
│       ├── logging.py
│       ├── security.py
│       └── file_utils.py
│
├── tests/
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_session.py
│   │   ├── test_rate_limiter.py
│   │   └── test_readers.py
│   ├── integration/
│   │   ├── test_bridge.py
│   │   └── test_ingestion.py
│   └── conftest.py
│
├── outlook-plugin/             ← C# (unchanged, well-structured)
│
└── scripts/                    ← Dev/debug tools
    ├── debug_search.py
    └── diagnose_pdf.py
```

### 7.3 Configuration Management (Target)

Replace the current `config.py` with Pydantic Settings:

```python
# src/settings.py (target)
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Required - no defaults, fail on startup if missing
    storage_secret: str
    google_api_key: str

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    allowed_origins: list[str] = ["http://localhost:8080"]

    # AI
    profile: str = "LOW_RESOURCE"
    llm_model: str = "llama3.2:3b"
    embed_model: str = "BAAI/bge-m3"
    prompt_optimization: str = "local"
    enable_ner_masking: bool = True
    gemini_model: str = "models/gemini-2.5-flash"

    # Paths
    chroma_path: str = "chroma_db"
    bm25_path: str = "storage_bm25"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
```

---

## 8. Roadmap

### Phase 5: Security Hardening (Immediate — 1-2 weeks)
- [x] Rotate compromised API keys (done 2026-03-15)
- [x] Remove hardcoded secrets from source (done 2026-03-15)
- [ ] Enforce STORAGE_SECRET (no default)
- [ ] Add WebSocket authentication token
- [ ] Fix `requirements.txt` encoding
- [ ] Add `.env.example` template
- [ ] Add login attempt throttling
- [ ] **C#:** Sanitize HTML in server responses before injecting into Outlook emails (CS-SEC-1)
- [ ] **C#:** Add message size limits to WebSocket receive loop (CS-BUG-2)
- [ ] **C#:** Add thread-safe locking to PluginLogger (CS-BUG-3)
- [ ] **C#:** Reject executable file attachments (.exe, .bat, .msi) (CS-SEC-4)

### Phase 6: Testing & CI (2-3 weeks)
- [ ] Write unit tests for auth, session routing, rate limiter, readers
- [ ] Write integration tests for bridge WebSocket flow
- [ ] Set up CI pipeline (GitHub Actions)
- [ ] Add code linting (ruff/flake8)
- [ ] Add type checking (mypy)

### Phase 7: Data Layer Improvements (3-4 weeks)
- [ ] Migrate user store from JSON to SQLite/PostgreSQL
- [ ] Add conversation history persistence
- [ ] Add audit logging for compliance
- [ ] Implement per-user document access control
- [ ] Add document management admin panel

### Phase 8: Production Readiness (4-6 weeks)
- [ ] Create `pyproject.toml` with proper dependency management
- [ ] Add Docker containerization
- [ ] Add health check with dependency verification
- [ ] Add Prometheus metrics
- [ ] Add structured logging (JSON)
- [ ] Implement graceful shutdown
- [ ] Add HTTPS/WSS via reverse proxy
- [ ] Session timeout and proper session management

### Phase 9: Feature Enhancements (6-10 weeks)
- [ ] Streaming responses in web UI
- [ ] Email thread context in loopback
- [ ] User feedback collection
- [ ] Document auto-classification
- [ ] Multi-tenant document isolation
- [ ] Calendar write operations

---

## 9. File-by-File Reference

### `main_nice.py` — Application Entry Point
- **Purpose:** Bootstraps FastAPI + NiceGUI, defines login/main pages, WebSocket endpoint
- **Key Dependencies:** `src.bridge`, `src.config`, `src.auth`, `src.engine`, `src.ui.*`
- **Issues:** SEC-2 (default secret), SEC-3 (no HTTPS), SEC-4 (static files), SEC-5 (no CSRF), SEC-6 (WS auth)
- **Notes:** Well-structured entry point. The `_is_valid_outlook_id` validation is good. CORS middleware is properly configured.

### `ingest.py` — Batch Ingestion Pipeline
- **Purpose:** Reads files from inbox, creates embeddings, stores in ChromaDB + BM25, moves to archive
- **Key Flow:** Lock → scan files → deduplicate (hash) → read/chunk → batch insert → BM25 rebuild → cleanup
- **Issues:** BUG-3 (lock race), PERF-1 (BM25 full rebuild), PERF-2 (full collection load)
- **Notes:** Solid batch processing with good error handling. The lock file mechanism works for single-server deployment.

### `watcher.py` — Folder Monitor
- **Purpose:** Watches `input_utente/` for new files, moves to inbox, triggers ingestion
- **Key Flow:** watchdog observer → debounce → move files → spawn `ingest.py` subprocess
- **Issues:** BUG-5 (case-sensitive extensions)
- **Notes:** Good resilience design with health checks and retry logic.

### `src/config.py` — Configuration
- **Purpose:** All paths, AI parameters, prompts, VSTO config, and role definitions
- **Issues:** QA-7 (hardcoded model names), duplicate section numbering (two "10" sections)
- **Notes:** Well-organized with hardware profiles. The system prompts are thoughtfully designed for the Swiss legal domain.

### `src/auth.py` — Authentication
- **Purpose:** User CRUD with bcrypt password hashing
- **Issues:** BUG-6 (concurrent write risk)
- **Notes:** Good password policy enforcement. The bcrypt rounds (12) are appropriate.

### `src/readers.py` — Document Readers
- **Purpose:** Smart PDF reader (native + OCR fallback), MSG reader, EML reader
- **Notes:** The SmartPDFReader is sophisticated — metadata-based scanner detection, linguistic quality scoring, configurable OCR threshold. Well-designed for the Swiss multilingual context.

### `src/ai/session.py` — User Session
- **Purpose:** Core routing engine — decides which AI engine to use based on triggers, keywords, and LLM classification
- **Key Logic:** `decide_engine()` → trigger matching → keyword detection → LLM fallback classification
- **Issues:** PERF-3 (new session per loopback)
- **Notes:** The ReActAgent dual-version support (Workflow + Legacy) is a good forward-compatibility strategy.

### `src/ai/prompt_optimizer.py` — Prompt Optimization
- **Purpose:** Converts raw emails into optimized RAG search queries
- **Key Flow:** local LLM or Gemini (with NER masking) → strip noise → extract intent → reformulate
- **Notes:** Privacy-first design is excellent. The NER mask/unmask pipeline is robust.

### `src/ai/ner_masking.py` — PII Masking
- **Purpose:** Presidio-based PII detection with Swiss/Italian custom recognizers
- **Key Entities:** SWISS_LEGAL_ENTITY, IT_FISCAL_CODE, CH_IBAN, NOTARIAL_REFERENCE, CH_VAT_NUMBER
- **Notes:** Well-designed for the domain. The numbered placeholder system allows reliable de-anonymization.

### `src/bridge/manager.py` — WebSocket Bridge
- **Purpose:** Manages Outlook WebSocket connections, MCP tool calls, and loopback queue
- **Key Features:** Priority queue, per-user workers, global AI semaphore, client config push, deduplication
- **Notes:** Sophisticated and well-designed. The queue worker survives client disconnects. Race conditions in delivery are handled.

### `src/bridge/loopback_processor.py` — Email Processing
- **Purpose:** Processes loopback emails: attachment extraction, AI routing, response dispatch
- **Key Features:** Base64 attachment decoding, size guards, CC disclaimer, markdown→HTML conversion
- **Notes:** Good separation of concerns. The attachment processing supports PDF, DOCX, XLSX, MSG, EML, TXT, CSV.

### `src/bridge/pending_results.py` — Offline Result Storage
- **Purpose:** Stores AI results to disk when the Outlook client is offline
- **Key Features:** TTL-based expiry, atomic delivery (rename-based locking), race condition safety
- **Notes:** Well-designed for reliability. The `.delivering` suffix trick is clever.

### `src/ui/footer.py` — Chat Footer
- **Purpose:** File upload handling, message sending, response rendering with sources
- **Issues:** BUG-4 (temp file leak), QA-1 (bare excepts)
- **Notes:** Complex but functional. The source linking (PDF click-through) is a nice feature.

### `src/ui/rate_limiter.py` — Rate Limiting
- **Purpose:** Per-user message rate limiting + input sanitization
- **Issues:** BUG-7 (memory leak)
- **Notes:** Simple and effective. Control character stripping is a good security measure.

### C# Plugin — Detailed Analysis

**Architecture:** Clean separation (Transport/Loopback/Core) with proper namespacing.

**Strengths:**
- Proper COM object lifecycle management (`Marshal.ReleaseComObject`)
- Async/await used correctly in most places
- Server-pushed configuration with validation
- Heartbeat mechanism for dead connection detection
- Good error isolation in email interception

**C# Security Issues Found:**

| ID | Severity | File | Issue |
|----|----------|------|-------|
| CS-SEC-1 | **CRITICAL** | `OutlookMailFactory.cs:313` | HTML response from server injected directly into Outlook email body — XSS risk. `<script>` tags execute in Outlook's HTML engine |
| CS-SEC-2 | **HIGH** | `AttachmentExtractor.cs:98` | Temp file path partially predictable (`arcumai_{Guid}_{filename}`) — pre-attack possible |
| CS-SEC-3 | **HIGH** | `ThisAddIn.cs:214` | Server-pushed config applied without cryptographic verification — MITM can disable loopback or change server URL |
| CS-SEC-4 | **MEDIUM** | `AttachmentExtractor.cs` | Executable files (.exe, .bat, .msi) accepted without warning or rejection |
| CS-SEC-5 | **MEDIUM** | `OutlookMailFactory.cs:326-331` | MAPI sender spoofing — `PR_SENT_REPRESENTING_NAME` set to arbitrary display name |
| CS-SEC-6 | **MEDIUM** | `OutlookMailFactory.cs:494` | `ArcumAIDisplayName` not HTML-encoded in email template — if name contains `<script>`, it executes |
| CS-SEC-7 | **LOW** | `ContactManager.cs:41` | DASL filter string injection — single quotes in email address break filter |

**C# Bugs Found:**

| ID | Severity | File | Issue |
|----|----------|------|-------|
| CS-BUG-1 | **HIGH** | `ThisAddIn.cs:156` | Race condition in `HeartbeatTickAsync` — `_transport` can be nullified between null check and `.IsConnected` access |
| CS-BUG-2 | **HIGH** | `WebSocketTransport.cs:127` | No size limit on multi-frame messages — malicious server can send infinite frames → OOM |
| CS-BUG-3 | **HIGH** | `PluginLogger.cs:42-50` | Race condition in log rotation — two threads can rotate simultaneously → file corruption |
| CS-BUG-4 | **MEDIUM** | `VirtualLoopbackHandler.cs:31` | `_pendingRequests` dictionary grows unbounded — failed responses create ghost entries |
| CS-BUG-5 | **MEDIUM** | `OutlookMailFactory.cs:148` | Subject matching for inspector closure not unique — two compose windows with same subject close wrong one |
| CS-BUG-6 | **MEDIUM** | `WebSocketTransport.cs:73` | No timeout on `SendAsync` — network hang blocks thread forever |
| CS-BUG-7 | **LOW** | `WebSocketTransport.cs:25` | `_ws.State` can throw `ObjectDisposedException` if WebSocket already disposed |

**C# Performance Issues:**

| ID | File | Issue |
|----|------|-------|
| CS-PERF-1 | `OutlookDataProvider.cs:46` | O(n) email search — iterates all inbox items with substring matching instead of DASL filters |
| CS-PERF-2 | `AttachmentExtractor.cs:101` | 50 MB attachment → ~130 MB memory (file bytes + base64 + JSON). No streaming |
| CS-PERF-3 | `PluginLogger.cs:54` | `File.AppendAllText` opens/writes/closes file on every log call — should use buffered `StreamWriter` |
| CS-PERF-4 | `WebSocketTransport.cs:104` | Fixed 8 KB receive buffer — inefficient for large messages, multiple copies |

**C# Missing Features:**
1. No offline response caching if server is down
2. No UI for manual reconnection (Outlook ribbon button)
3. No progress indicator for large attachments
4. No automatic add-in updates (VSTO auto-update)
5. No unit tests for any C# code
6. No support for shared mailboxes/calendars
7. No reply/forward support on AI responses (read-only)
8. No request cancellation mechanism

---

## Appendix A: Dependency Analysis

### Heavy Dependencies (consider alternatives)
| Package | Size | Used For | Alternative |
|---------|------|----------|-------------|
| `torch` | ~2 GB | sentence-transformers embeddings | `onnxruntime` backend |
| `playwright` | ~200 MB | Law scraper script only | Move to separate project |
| `kubernetes` | ~50 MB | Not used in any source file | Remove |
| `traceloop-sdk` + 30 OpenTelemetry packages | ~100 MB | Instrumentation (unused?) | Remove if not active |

### Key Direct Dependencies
- **AI:** llama-index, chromadb, ollama, google-generativeai, sentence-transformers
- **Web:** fastapi, nicegui, uvicorn, websockets
- **Doc Processing:** pypdf, extract-msg, docx2txt, openpyxl, pytesseract, pdf2image
- **Auth:** bcrypt, PyJWT
- **Privacy:** presidio-analyzer, presidio-anonymizer (optional)

---

## Appendix B: Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PROFILE` | `LOW_RESOURCE` | Hardware profile (`LOW_RESOURCE` / `HIGH_RESOURCE`) |
| `LLM_MODEL` | Profile-based | Ollama model name |
| `EMBED_MODEL` | `BAAI/bge-m3` | HuggingFace embedding model |
| `CONTEXT_WINDOW` | Profile-based | LLM context window size |
| `REQUEST_TIMEOUT` | Profile-based | LLM request timeout (seconds) |
| `STORAGE_SECRET` | ⚠️ hardcoded | NiceGUI session encryption key |
| `GOOGLE_API_KEY` | — | Gemini API key |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8080` | Server port |
| `ALLOWED_ORIGINS` | `http://localhost:8080` | CORS origins (comma-separated) |
| `PROMPT_OPTIMIZATION` | `local` | Optimization mode: `local` / `gemini` / `off` |
| `ENABLE_NER_MASKING` | `true` | PII masking before cloud API calls |
| `NER_SCORE_THRESHOLD` | `0.35` | Presidio confidence threshold |
| `LOG_LEVEL` | `INFO` | Logging level |
| `BRIDGE_TIMEOUT` | `60.0` | MCP tool call timeout (seconds) |
| `LOOPBACK_TIMEOUT` | `3600.0` | Loopback processing timeout |
| `LOOPBACK_MAX_CONCURRENT` | `3` | Max parallel AI requests |
| `PENDING_RESULT_TTL_HOURS` | `48` | How long to keep offline results |
| `VSTO_MAX_ATTACHMENT_MB` | `25` | Max single attachment size |
| `VSTO_MAX_TOTAL_MB` | `50` | Max total attachment size |
| `VSTO_ARCUMAI_EMAIL` | `assistant@arcumai.ch` | AI assistant email address |
| `VSTO_ENABLE_VIRTUAL_LOOPBACK` | `true` | Enable email interception |

---

