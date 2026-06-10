# ArcumAI — Full Project Analysis \& Reference Documentation

**Date:** 2026-05-28
**Branch:** `dev-features`
**Scope:** Complete codebase inspection — Python backend + C# VSTO plugin
**Previous analysis:** `ARCUMAI\\\_FULL\\\_ANALYSIS\\\_backup\\\_2026-03-08.md`

\---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Component Inventory](#3-component-inventory)
4. [Issues Found](#4-issues-found)

   * 4.1 Open Security Issues
   * 4.2 Resolved Security Issues (since v1.0)
   * 4.3 Open Bugs \& Logic Errors
   * 4.4 Resolved Bugs (since v1.0)
   * 4.5 Code Quality \& Anti-Patterns
   * 4.6 Performance Concerns
5. [Suggested Improvements](#5-suggested-improvements)
6. [Proposed New Capabilities](#6-proposed-new-capabilities)
7. [Architectural Reference (Target State)](#7-architectural-reference-target-state)
8. [Roadmap](#8-roadmap)
9. [File-by-File Reference](#9-file-by-file-reference)
10. [Appendices](#10-appendices)

\---

## 1\. Executive Summary

ArcumAI is a **privacy-first AI assistant** for Swiss legal/fiduciary offices. It combines:

* **Python backend** (FastAPI + NiceGUI): RAG pipeline over legal documents (ChromaDB + BM25 hybrid search), AI chat (Ollama local + Gemini cloud), WebSocket bridge to Outlook, conversation history persistence
* **C# VSTO Outlook add-in**: Intercepts emails to `assistant@arcumai.ch`, sends them to the backend for AI processing, returns responses as reply emails injected into the Inbox
* **Document ingestion pipeline**: Watches a folder, reads PDF/DOCX/MSG/EML/XLSX/TXT with OCR fallback, creates embeddings, stores in ChromaDB + BM25

**Current State (2026-05-28):** Post-refactoring (4 phases complete), post-security-hardening (v1.1 code review + hotfix series). The system is a functional, production-quality MVP for a small deployment. A significant wave of security and stability fixes has been applied since the previous analysis. Several open issues remain, particularly around missing HTTPS and incomplete C# hardening.

**Key Strengths:**

* Clean modular architecture after 4 refactoring phases
* Privacy-first design: local LLM default, reversible NER/Presidio masking for cloud API calls
* Robust WebSocket bridge with priority queues, offline result persistence, and deduplication
* Conversation history persisted per user (JSON, atomic writes)
* Multi-language support for IT/EN/DE/FR documents
* Sophisticated SmartPDFReader with scanner detection and OCR quality scoring
* Role-based system prompts tuned for Swiss legal/fiduciary domain
* Defensive coding: input sanitization, path traversal prevention, log injection prevention, rate limiting, brute-force protection

**Remaining Risks:**

* No HTTPS/WSS enforcement (cleartext transport)
* No session timeout or CSRF protection
* Default hardcoded NiceGUI session secret (no fail-fast if `STORAGE\\\_SECRET` unset)
* Static file endpoint serves all archived documents without per-user access control
* Several C# issues unaddressed (HTML injection in email bodies, unbounded WebSocket buffer, logger race condition)
* No automated functional tests (only import smoke tests)

\---

## 2\. Architecture Overview

```
┌───────────────────────────────────────────────────────────────────┐
│                            CLIENTS                                 │
│  ┌──────────────┐   ┌──────────────────────────────────────────┐  │
│  │  NiceGUI Web  │   │  Outlook VSTO Plugin (C# / .NET 4.8)    │  │
│  │  (Browser)    │   │  WS → ws\\\[s]://server:8080/ws/outlook/…  │  │
│  └──────┬───────┘   └───────────────────┬──────────────────────┘  │
└─────────┼───────────────────────────────┼──────────────────────────┘
          │ HTTP/NiceGUI                  │ WebSocket (JSON-RPC 2.0)
          ▼                               ▼
┌───────────────────────────────────────────────────────────────────┐
│              PYTHON BACKEND  (main\\\_nice.py + FastAPI)             │
│                                                                   │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │  FastAPI    │  │  NiceGUI UI  │  │  OutlookBridgeManager  │   │
│  │  /health    │  │  /login  /   │  │  /ws/outlook/{user\\\_id} │   │
│  └────────────┘  └──────┬───────┘  └──────────┬─────────────┘   │
│                         │                      │                  │
│                ┌────────▼──────────────────────▼──────────┐      │
│                │                UserSession                 │      │
│                │  ┌──────────┐ ┌────────┐ ┌──────┐ ┌───┐ │      │
│                │  │ RAG Eng. │ │ Simple │ │Cloud │ │MCP│ │      │
│                │  │ (Hybrid) │ │(Local) │ │(Gem.)│ │Agt│ │      │
│                │  └──────────┘ └────────┘ └──────┘ └───┘ │      │
│                └──────────────────────────────────────────┘      │
│                                                                   │
│  ┌──────────────────┐  ┌─────────────────────────────────────┐  │
│  │  watcher.py       │  │  ingest.py (batch)                  │  │
│  │  (folder monitor) │→ │  → ChromaDB + BM25                  │  │
│  └──────────────────┘  └─────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  DATA LAYER                                               │   │
│  │  ChromaDB (vectors) ● BM25 (keywords) ● users.json       │   │
│  │  conversations/<user>/<id>.json ● temp/pending\\\_results/  │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
```

### Data Flow — Virtual Loopback (Email → AI → Reply)

```
1. User sends email to assistant@arcumai.ch from Outlook
2. VSTO ItemSend hook fires → VirtualLoopbackHandler.OnEmailSent()
3. Plugin extracts body, attachments (base64), cc\\\_recipients
4. Plugin sends JSON-RPC "virtual\\\_loopback/send\\\_email" via WebSocket
5. Backend ACKs → enqueues in \\\_UserQueue (priority queue per user)
6. Worker acquires \\\_ai\\\_semaphore (cap = LOOPBACK\\\_MAX\\\_CONCURRENT = 3)
7. LoopbackProcessor.\\\_process\\\_loopback\\\_email():
   a. Decode base64 attachments → extract text
   b. optimize\\\_prompt\\\_for\\\_rag() → local LLM or Gemini+NER masking
   c. Route: attachments present? → simple\\\_local\\\_engine; else → RAG engine
   d. CC recipients present? → prepend disclaimer
   e. Convert markdown → HTML
8. Response via WebSocket "virtual\\\_loopback/response" OR stored to disk
9. Plugin injects response as email in Outlook Inbox (MAPI properties)
```

### Data Flow — Web UI Chat

```
1. User logs in → NiceGUI session created with UserSession
2. User types message → sanitize\\\_input() → \\\_check\\\_rate\\\_limit()
3. Session.decide\\\_engine() → trigger/keyword/LLM classification
4. Engine.chat() or agent\\\_engine.arun() → response
5. Response rendered in chat\\\_area (markdown)
6. Message + response persisted to storage/conversations/<user>/<id>.json
```

\---

## 3\. Component Inventory

### Python Source Files

|File|LOC|Purpose|
|-|-|-|
|`main\\\_nice.py`|\~183|App entry point: FastAPI + NiceGUI + WS endpoint|
|`ingest.py`|\~231|Batch document ingestion pipeline|
|`watcher.py`|\~178|Folder watcher → triggers ingestion|
|`rag\\\_query.py`|\~120|CLI diagnostic RAG testing tool|
|`admin\\\_tool.py`|\~120|CLI user management (add/delete/update)|
|`src/config.py`|\~200|All configuration, paths, hardware profiles, system prompts|
|`src/auth.py`|\~92|User CRUD + bcrypt password hashing + WS brute-force protection|
|`src/database.py`|\~14|ChromaDB index initialization|
|`src/logger.py`|\~63|Dual logger (ingestion + server), rotating files|
|`src/readers.py`|\~191|SmartPDFReader, MyOutlookReader, MyEmlReader|
|`src/utils.py`|\~211|File ops, hash, move with retry, empty folder cleanup|
|`src/conversations.py`|\~120|ConversationStore: per-user JSON persistence|
|`src/engine.py`|\~13|Re-exports (backward compat)|
|`src/ai/engines.py`|\~81|RAG, Simple, Cloud LLM engine factories|
|`src/ai/session.py`|\~292|UserSession: routing, tools, engine management|
|`src/ai/prompt\\\_optimizer.py`|\~177|Email → RAG query optimization (local/Gemini/off)|
|`src/ai/ner\\\_masking.py`|\~285|PII masking/unmasking via Presidio + Swiss custom recognizers|
|`src/bridge/manager.py`|\~311|OutlookBridgeManager: WS connections, MCP routing, queues|
|`src/bridge/loopback\\\_processor.py`|\~334|Email processing: attachments, AI routing, response dispatch|
|`src/bridge/loopback\\\_queue.py`|\~22|\_EmailTask + \_UserQueue priority queue structures|
|`src/bridge/pending\\\_results.py`|\~136|Offline result persistence (disk, TTL-based, atomic delivery)|
|`src/ui/footer.py`|\~207|Chat input, file upload (magic byte validation), send logic|
|`src/ui/header.py`|\~51|Top bar with user info + logout|
|`src/ui/sidebar.py`|\~37|Conversation history sidebar|
|`src/ui/chat\\\_area.py`|\~9|Chat message container|
|`src/ui/rate\\\_limiter.py`|\~34|Per-user rate limiting + control char sanitization|
|`src/ui/admin.py`|\~80|Admin panel for ingestion management|
|`src/ui/conversation\\\_panel.py`|\~30|Conversation metadata display|

### C# Source Files (outlook-plugin/)

|File|Purpose|
|-|-|
|`ThisAddIn.cs`|VSTO entry point, lifecycle, heartbeat, reconnect|
|`Core/VirtualLoopbackHandler.cs`|Email interception, server dispatch, response injection|
|`Core/OutlookDataProvider.cs`|Email search + calendar data (MCP tool backend)|
|`Core/PluginConfig.cs`|Configuration singleton: \~30 properties, validate, save|
|`Core/PluginConfigLoader.cs`|Config loading: JSON → app.config → hardcoded defaults|
|`Core/PluginLogger.cs`|IPluginLogger interface + file/debug implementation|
|`Core/Transport/IMcpTransport.cs`|Transport abstraction interface (JSON-RPC 2.0)|
|`Core/Transport/WebSocketTransport.cs`|WebSocket client with background receive loop|
|`Core/Loopback/AttachmentExtractor.cs`|Attachment reading, size limits, base64 encoding|
|`Core/Loopback/ContactManager.cs`|ArcumAI contact existence in Outlook address book|
|`Core/Loopback/OutlookMailFactory.cs`|Reply email construction via MAPI properties|

### Supporting Files

|File|Purpose|
|-|-|
|`scripts/debug\\\_search.py`|Debug ChromaDB retrieval quality|
|`scripts/diagnose\\\_file.py`|Inspect ChromaDB entries for a specific file|
|`scripts/diagnose\\\_pdf.py`|Analyze PDF text extraction quality|
|`scripts/scarica\\\_leggi\\\_ti.py`|Swiss Ticino law scraper (Playwright)|
|`scripts/test\\\_gemini.py`|Gemini API connection test|
|`triggers/\\\*.txt`|Keyword trigger files for intent routing|
|`requirements.txt`|Python dependencies (\~254 packages)|
|`users.json`|User database (gitignored)|
|`.env.example`|Config template (no secrets)|

\---

## 4\. Issues Found

> \\\*\\\*Key:\\\*\\\* 🔴 OPEN — 🟢 RESOLVED — 🟡 PARTIALLY MITIGATED

### 4.1 Open Security Issues

#### SEC-2: Default Storage Secret (HIGH) 🔴

**File:** `main\\\_nice.py` (session storage setup)
**Issue:** A hardcoded fallback string `CHIAVE\\\_SEGRETA\\\_ARCUM\\\_AI\\\_V2\\\_DEV\\\_DEFAULT` is used if `STORAGE\\\_SECRET` env var is absent.
**Impact:** Sessions use a predictable key → session hijacking possible.
**Fix:** Fail fast (`raise RuntimeError`) if `STORAGE\\\_SECRET` is not set.

#### SEC-3: No HTTPS/WSS Enforcement (HIGH) 🔴

**File:** `main\\\_nice.py`, server startup
**Issue:** Server binds on `0.0.0.0:8080` with plain HTTP. WebSocket connections are unencrypted.
**Impact:** Credentials, API keys, and email content in cleartext over the network.
**Fix:** Add TLS termination via nginx/Caddy reverse proxy, or configure uvicorn SSL certificates.

#### SEC-4: Static Files Expose Entire Archive (HIGH) 🔴

**File:** `main\\\_nice.py`
**Issue:** `app.add\\\_static\\\_files('/documents', str(ARCHIVE\\\_DIR))` exposes all archived documents to any authenticated user.
**Impact:** No per-user document access control; any user can enumerate and download any document.
**Fix:** Implement a controlled FastAPI endpoint that checks user role/ownership before streaming the file.

#### SEC-5: No CSRF Protection (MEDIUM) 🔴

**File:** `main\\\_nice.py`, UI forms
**Issue:** No CSRF tokens on state-changing operations.
**Impact:** Cross-site request forgery possible from malicious pages.

#### SEC-8: MD5 for File Deduplication (LOW) 🔴

**File:** `src/utils.py` (`calcola\\\_hash\\\_file`)
**Issue:** MD5 used for deduplication. While not a cryptographic attack surface here, collisions are theoretically possible.
**Fix:** Use SHA-256 instead for future-proofing.

#### SEC-9: No Login Brute-Force Protection (HIGH) 🔴

**File:** `main\\\_nice.py` (login handler)
**Issue:** No rate limiting on HTTP login attempts. Only WebSocket auth has brute-force protection.
**Fix:** Track failed login attempts per IP/username; implement exponential backoff or CAPTCHA after N failures.

#### SEC-10: PII Potentially Logged at DEBUG Level (MEDIUM) 🔴

**File:** `src/ai/prompt\\\_optimizer.py`
**Issue:** `slog.debug(f"Masked email text:\\\\n{masked\\\_email}")` logs the post-masking email body. With `LOG\\\_LEVEL=DEBUG` in production, this still writes email content to disk.
**Fix:** Remove the log statement or use a separate privacy-safe logger that never emits email content.

#### SEC-11: No WebSocket Origin Validation (MEDIUM) 🔴

**File:** `main\\\_nice.py` (WS endpoint)
**Issue:** Any host that knows a valid `outlook\\\_id` and API key can connect to the WebSocket endpoint. No `Origin` header check.
**Fix:** Optionally validate `Origin` header to restrict connections to expected hosts.

**C# Open Security Issues:**

|ID|Sev|File|Issue|
|-|-|-|-|
|CS-SEC-1|**CRITICAL**|`OutlookMailFactory.cs`|HTML from server response injected directly into Outlook email body — XSS risk if server is compromised or MITM'd|
|CS-SEC-2|**HIGH**|`AttachmentExtractor.cs`|Temp file path partially predictable (`arcumai\\\_{Guid}\\\_{filename}`)|
|CS-SEC-3|**HIGH**|`ThisAddIn.cs`|Server-pushed config applied without cryptographic verification — MITM can modify server URL or disable loopback|
|CS-SEC-4|**MEDIUM**|`AttachmentExtractor.cs`|Executable files (.exe, .bat, .msi) accepted without warning or rejection|
|CS-SEC-5|**MEDIUM**|`OutlookMailFactory.cs`|MAPI sender spoofing — `PR\\\_SENT\\\_REPRESENTING\\\_NAME` set to arbitrary display name|
|CS-SEC-6|**MEDIUM**|`OutlookMailFactory.cs`|`ArcumAIDisplayName` not HTML-encoded in email template — if name contains `<script>` it executes|
|CS-SEC-7|**LOW**|`ContactManager.cs`|DASL filter string injection — single quote in email address breaks filter|

\---

### 4.2 Resolved Security Issues (since v1.0)

|ID|Fixed in|What was fixed|
|-|-|-|
|SEC-1|`cf3ba69`|Google API key removed from `scripts/test\\\_gemini.py` source; key revoked|
|WS-AUTH|`43bd5a2`|Optional shared-secret header (`X-API-Key`) auth on WebSocket endpoint|
|PATH-TRAV|`2b2c302`|`find\\\_relative\\\_path()` validates and normalizes paths to prevent traversal|
|ADMIN-PATH|`65af259`|Admin ingestion endpoint rejects path traversal attempts|
|LOG-INJ|`cd23e38`|`user\\\_id` sanitized (control chars → hex) at bridge entry points|
|BARE-EXC|`290a1ad`|Bare `except:` clauses replaced; exceptions now logged|
|WS-BRUTE|`5c11200`|Per-IP brute-force protection on WebSocket auth|
|WS-INACTIVE|`c539ff1`|Inactivity timeout on WebSocket receive loop|
|PII-UUID|`85d0a55`|PII placeholders use UUID-based keys to prevent de-anonymization collisions|
|EMAIL-SIZE|`960cda0`|Emails exceeding 100k chars rejected before sending to Gemini|
|MAGIC-BYTES|`45eedfa`|File uploads validated via magic bytes, not just extension|
|OCR-ENV|`3fcc305`|Tesseract/Poppler paths configurable via env vars; warning logged if OCR disabled|
|WS-TIMEOUT|`2b5c866`|Orphaned futures cancelled on WebSocket request timeout|
|CS-SECURE|`e2ad404`|C#: `UseSecureConnection` flag properly enforced in URL and header|
|CS-PAYLOAD|`92524bd`|C#: Sensitive payload content redacted from debug logs|

\---

### 4.3 Open Bugs \& Logic Errors

#### BUG-1: `requirements.txt` Corrupted Encoding (HIGH) 🔴

**File:** `requirements.txt`
**Issue:** File appears to have UTF-16 encoding (spaces between every character), making `pip install -r requirements.txt` fail on a clean environment.
**Fix:** Regenerate with `pip freeze > requirements.txt` (UTF-8) or use `pip list --format=freeze > requirements.txt`.

#### BUG-2: `diagnose\\\_file.py` Resolves to Wrong Base Directory (MEDIUM) 🔴

**File:** `scripts/diagnose\\\_file.py:6`
**Issue:** `BASE\\\_DIR = Path(\\\_\\\_file\\\_\\\_).parent.resolve()` resolves to `scripts/` not the project root. `DB\\\_PATH` points to `scripts/chroma\\\_db/` which doesn't exist.
**Fix:** Use `Path(\\\_\\\_file\\\_\\\_).parent.parent.resolve()` or import from `src.config`.

#### BUG-3: Lock File Race Condition in `ingest.py` (MEDIUM) 🔴

**File:** `ingest.py` (`acquire\\\_lock`)
**Issue:** `open(LOCK\\\_FILE, 'x')` is not atomic on Windows SMB/NFS. Two simultaneous watcher triggers could both acquire the lock.
**Fix:** Use `msvcrt.locking()` on Windows for proper exclusive file locking.

#### BUG-4: Temp File Left on Upload Error (MEDIUM) 🔴

**File:** `src/ui/footer.py`
**Issue:** A temp PDF file is created but only deleted in the success path. If `SmartPDFReader` raises, the file persists.
**Fix:** Use `tempfile.NamedTemporaryFile` with a `finally` block.

#### BUG-5: Case-Sensitive Extension Matching in Watcher (LOW) 🔴

**File:** `src/config.py` (`WATCH\\\_EXTENSIONS`), `watcher.py`
**Issue:** Extensions listed as both `.pdf` and `.PDF`, but `Path.suffix` preserves original case. `.Pdf` or `.pDf` files are ignored.
**Fix:** Normalize: `Path(event.src\\\_path).suffix.lower() not in {'.pdf', '.msg', ...}`.

#### BUG-6: Concurrent Write Risk on `users.json` (MEDIUM) 🔴

**File:** `src/auth.py` (`save\\\_users`)
**Issue:** Direct `open(..., 'w')` + `json.dump()` with no locking and no atomic write. Two concurrent requests could corrupt the file.
**Fix:** Write to a temp file first, then `os.replace()` atomically. (Conversation persistence already uses this pattern correctly.)

#### BUG-7: Memory Growth in Rate Limiter (LOW) 🔴

**File:** `src/ui/rate\\\_limiter.py`
**Issue:** `\\\_user\\\_timestamps` dict grows indefinitely. Stale users (long idle) are evicted by the cleanup mechanism, but the cleanup interval is 300s and TTL is 3600s — a large number of users could accumulate before eviction.
**Status:** Partially mitigated by stale-user eviction added in `5c11200`. Monitor in production.

#### BUG-8: Double Hash Calculation in Ingestion (LOW) 🔴

**File:** `ingest.py`
**Issue:** `calcola\\\_hash\\\_file(file\\\_path)` is called twice — once for deduplication check, once inside `read\\\_and\\\_chunk\\\_file()`. Wastes I/O on large files.
**Fix:** Pass the precomputed hash into `read\\\_and\\\_chunk\\\_file()`.

#### BUG-9: Silent File Truncation on Upload (MEDIUM) 🔴

**File:** `src/ui/footer.py`
**Issue:** `session.uploaded\\\_context = text\\\_content\\\[:10000]` silently truncates at 10,000 chars. Users with large files get partial results with no warning.
**Fix:** Warn the user if truncation occurred; make the limit configurable.

#### BUG-10: No Scheduled Cleanup of Pending Results (LOW) 🔴

**File:** `src/bridge/pending\\\_results.py`
**Issue:** TTL-based expiry only triggers when a client reconnects. If a client never reconnects, expired temp files accumulate.
**Fix:** Add a periodic cleanup task (e.g., hourly) on server startup.

**C# Open Bugs:**

|ID|Sev|File|Issue|
|-|-|-|-|
|CS-BUG-1|**HIGH**|`ThisAddIn.cs`|Race in `HeartbeatTickAsync` — `\\\_transport` can be nullified between null check and `.IsConnected` access|
|CS-BUG-2|**HIGH**|`WebSocketTransport.cs`|No size limit on multi-frame messages → malicious server can send infinite frames → OOM|
|CS-BUG-3|**HIGH**|`PluginLogger.cs`|Race condition in log rotation — two threads can simultaneously rotate → file corruption|
|CS-BUG-4|**MEDIUM**|`VirtualLoopbackHandler.cs`|`\\\_pendingRequests` dict grows unbounded — timed-out requests create ghost entries|
|CS-BUG-5|**MEDIUM**|`OutlookMailFactory.cs`|Subject matching for inspector closure not unique — two compose windows with same subject close the wrong one|
|CS-BUG-6|**MEDIUM**|`WebSocketTransport.cs`|No timeout on `SendAsync` — network hang blocks thread indefinitely|
|CS-BUG-7|**LOW**|`WebSocketTransport.cs`|`\\\_ws.State` can throw `ObjectDisposedException` if WebSocket already disposed|

\---

### 4.4 Resolved Bugs (since v1.0)

|ID|Fixed in|What was fixed|
|-|-|-|
|TIMEOUT-ORPHAN|`2b5c866`|Bridge cancels orphaned asyncio.Future on WS request timeout|
|TIMEOUT-STALE|`c16d463`|Stale loopback timeout tasks cancelled on client disconnect (prevented false timeout emails after reconnect)|
|NULL-PARAMS|`7bda04e`|Null-safe params access in `handle\\\_incoming\\\_message`|
|EMAIL-MATCH|`3ea1834`|Loose `StartsWith` fallback removed from email address matching|
|TEMP-LOG|`274df9a`|Temp file cleanup failures logged instead of silenced|
|RATE-MEM|`5c11200`|Rate limiter `\\\_user\\\_timestamps` stale-entry eviction added|

\---

### 4.5 Code Quality \& Anti-Patterns

#### QA-1: Mixed Italian/English Identifiers

**Files:** Throughout (e.g., `pulisci\\\_cartelle\\\_vuote`, `calcola\\\_hash\\\_file`, `sposta\\\_file\\\_con\\\_struttura`)
**Issue:** Function names and variable names mix Italian and English. System prompts are Italian; log messages are English; code comments are both.
**Impact:** Reduces readability for non-Italian contributors.
**Recommendation:** Choose one language for identifiers; document in a style guide.

#### QA-2: Global Module-Level Singleton (`bridge\\\_manager`)

**File:** `src/bridge/\\\_\\\_init\\\_\\\_.py`
**Issue:** `bridge\\\_manager = OutlookBridgeManager()` instantiated at import time. Hard to test in isolation.
**Recommendation:** Use a factory function or dependency injection for testability.

#### QA-3: `nest\\\_asyncio` Usage

**File:** `main\\\_nice.py`
**Issue:** `nest\\\_asyncio.apply()` patches the event loop to permit re-entrant calls. This is a workaround, not a fix; can mask real concurrency bugs.
**Recommendation:** Investigate root cause; restructure coroutine calls if possible.

#### QA-4: No Type Hints on Most Functions

**Files:** Most Python modules.
**Issue:** Limited use of type annotations reduces IDE support and makes static analysis ineffective.
**Recommendation:** Add type hints to public function signatures at minimum.

#### QA-5: Hardcoded Gemini Model Name

**Files:** `src/ai/engines.py`, `src/ai/prompt\\\_optimizer.py`
**Issue:** `"models/gemini-2.5-flash"` hardcoded. Breaking changes in Gemini API (model deprecation) would require code edits.
**Fix:** Add `GEMINI\\\_MODEL` to `src/config.py` / `.env`.

#### QA-6: No Dependency Management Strategy

**File:** `requirements.txt`
**Issue:** 254 packages (direct + transitive) with exact pins. No `pyproject.toml` or lockfile strategy. Dependency graph is opaque.
**Recommendation:** Move to `pyproject.toml` with direct dependencies; use `pip-tools` or `uv` for lockfile generation.

#### QA-7: `diagnose\\\_pdf.py` Return Type Inconsistency

**File:** `scripts/diagnose\\\_pdf.py`
**Issue:** Returns a tuple for the empty-text case but a dict for normal results. The caller checks `isinstance(stats, tuple)` — fragile.
**Fix:** Always return a dict with an `empty` flag.

\---

### 4.6 Performance Concerns

#### PERF-1: BM25 Full Rebuild on Every Ingestion (HIGH)

**File:** `ingest.py`
**Issue:** After every batch, all ChromaDB nodes are fetched to rebuild the entire BM25 index. O(n) operation that degrades with corpus size.
**Fix:** Incremental BM25 updates, or rebuild on a schedule (nightly) rather than per-batch.

#### PERF-2: ChromaDB `get()` Without Pagination (HIGH)

**File:** `src/utils.py`
**Issue:** `chroma\\\_collection.get(include=\\\["documents", "metadatas"])` loads the entire collection into memory. Will OOM with large corpora.
**Fix:** Use `limit`/`offset` pagination or streaming queries.

#### PERF-3: New `UserSession` Per Loopback Request (MEDIUM)

**File:** `src/bridge/loopback\\\_processor.py`
**Issue:** Every loopback email creates a new `UserSession`, triggering `users.json` I/O, tool initialization, and engine construction.
**Fix:** Cache sessions per user\_id in `LoopbackProcessor`, or use a session pool.

#### PERF-4: PyTorch CPU Install (\~2 GB) (MEDIUM)

**File:** `requirements.txt`
**Issue:** Full `torch` installed for sentence-transformers embeddings. On CPU servers this wastes \~2 GB disk.
**Fix:** Use `torch` CPU-only wheel (`--index-url https://download.pytorch.org/whl/cpu`) or `onnxruntime` backend.

#### PERF-5: Synchronous OCR Blocking (MEDIUM)

**File:** `src/readers.py` (`SmartPDFReader`)
**Issue:** Tesseract OCR is synchronous and CPU-intensive. Called from the ingestion pipeline, it blocks the process.
**Fix:** Run OCR in a `ThreadPoolExecutor` or subprocess.

**C# Performance Issues:**

|ID|File|Issue|
|-|-|-|
|CS-PERF-1|`OutlookDataProvider.cs`|O(n) email search — iterates all inbox items instead of DASL filters|
|CS-PERF-2|`AttachmentExtractor.cs`|50 MB attachment → \~130 MB memory (file bytes + base64 + JSON). No streaming|
|CS-PERF-3|`PluginLogger.cs`|`File.AppendAllText` opens/writes/closes on every log call — should use buffered `StreamWriter`|
|CS-PERF-4|`WebSocketTransport.cs`|Fixed 8 KB receive buffer — multiple copies needed for large messages|

\---

## 5\. Suggested Improvements

### 5.1 Security Hardening (Priority: HIGH — remaining items)

1. **Enforce `STORAGE\\\_SECRET`** — Remove default fallback; raise on startup if unset
2. **Fix C# HTML injection** (CS-SEC-1) — Strip or sanitize HTML received from server before inserting into email body
3. **Add HTTPS/WSS** — Use nginx/Caddy reverse proxy or configure uvicorn with SSL certs
4. **Session timeout** — Add idle-session expiry to NiceGUI sessions
5. **Login rate limiting** — Mirror the WS brute-force protection for HTTP login
6. **Reject executable attachments** (CS-SEC-4) — Block .exe, .bat, .msi, .ps1 in C# plugin
7. **Sign server-pushed config** (CS-SEC-3) — HMAC-sign config payloads to prevent MITM modification

### 5.2 Testing (Priority: HIGH)

1. **Unit tests for AI routing** — `UserSession.decide\\\_engine()` logic
2. **Unit tests for auth** — Password validation, user CRUD edge cases
3. **Integration tests for bridge** — WebSocket connect/disconnect, message routing
4. **Test loopback processor** — Attachment decoding, CC handling, response dispatch
5. **Test rate limiter** — Boundary conditions, window expiration
6. **CI/CD pipeline** — GitHub Actions: lint + test on every push

### 5.3 Code Quality (Priority: MEDIUM)

1. **Fix `requirements.txt` encoding** — Regenerate properly
2. **Create `pyproject.toml`** — Direct deps only; lockfile via pip-tools or uv
3. **Add `GEMINI\\\_MODEL` env var** — Remove hardcoded model name
4. **Add type hints** — At least for public interfaces in `src/`
5. **Unify identifier language** — English throughout, or add glossary
6. **Custom exception types** — Instead of bare `Exception` raises

### 5.4 Operational (Priority: MEDIUM)

1. **Richer health check** — Include ChromaDB connectivity, Ollama reachability, queue depth
2. **Structured logging** — JSON logs for ELK/Loki aggregation
3. **Prometheus metrics** — Request counts, queue depth, latency percentiles
4. **Graceful shutdown** — Drain queue, flush logs, close WS connections cleanly
5. **Per-user document access control** — Replace static file serving with a controlled endpoint
6. **Scheduled pending result cleanup** — Periodic task to purge expired `temp/pending\\\_results/` files

\---

## 6\. Proposed New Capabilities

### 6.1 Multi-Tenant Document Isolation (Priority: HIGH)

**Why:** All users share one ChromaDB collection. Legal firm documents should be isolated per client/matter.
**What:** Namespace documents by `tenant\\\_id`. Filter retrieval by tenant.
**How:** Add `tenant\\\_id` to ChromaDB document metadata. Filter in `QueryFusionRetriever`.

### 6.2 Streaming Responses in Web UI (Priority: MEDIUM)

**Why:** Web UI blocks until full response arrives. Users see a spinner for 30+ seconds on complex queries.
**What:** Stream tokens as generated.
**How:** Use `astream\\\_chat()` and update NiceGUI chat area incrementally via reactive bindings.

### 6.3 Email Thread Context in Loopback (Priority: MEDIUM)

**Why:** Loopback processor treats each email independently. Reply chains have no memory.
**What:** When processing a reply, include previous exchanges as context.
**How:** Use `conversation\\\_id` from VirtualLoopbackHandler to look up stored exchanges. Maintain loopback conversation cache.

### 6.4 User Feedback \& Learning Loop (Priority: MEDIUM)

**Why:** No mechanism for users to signal answer quality.
**What:** Thumbs up/down on AI responses. Collect feedback for prompt tuning.
**How:** Store feedback in `storage/feedback.jsonl`. Use to adjust system prompts or retrieval parameters.

### 6.5 Calendar Write Operations (Priority: MEDIUM)

**Why:** Agent has a read-only `tool\\\_get\\\_calendar`. Users expect the AI to create events.
**What:** MCP tools for `create\\\_event`, `set\\\_reminder`, `delete\\\_event`.
**How:** Add methods to `OutlookDataProvider.cs`. Expose as MCP tools in `UserSession.\\\_create\\\_user\\\_tools()`.

### 6.6 Audit Log for Compliance (Priority: HIGH)

**Why:** Swiss fiduciary offices have data access logging requirements.
**What:** Append-only log of every document access, AI query, user login, and admin action.
**How:** Write structured JSON lines to `logs/audit.jsonl` from all critical paths.

### 6.7 Document Management Admin Panel (Priority: HIGH)

**Why:** Users cannot see, search, or remove documents from the knowledge base.
**What:** Admin page listing all indexed docs with metadata, search, delete, re-index actions.
**How:** Add ChromaDB CRUD API endpoints; bind to `src/ui/admin.py` (panel exists but is limited).

### 6.8 Incremental BM25 Updates (Priority: HIGH for scaling)

**Why:** Full BM25 rebuild on every ingestion will become the scaling bottleneck.
**What:** Add/remove individual documents from the BM25 index without full rebuild.
**How:** Maintain BM25 incrementally or schedule nightly full rebuilds instead of per-batch.

### 6.9 Automated Document Classification (Priority: LOW)

**Why:** All ingested documents treated equally. Legal memos, invoices, contracts have different retrieval significance.
**What:** Auto-classify document type during ingestion. Add `doc\\\_type` metadata for filtered retrieval.
**How:** LLM prompt during ingestion to tag type, or a lightweight classifier.

### 6.10 Multi-Model Support (Priority: LOW)

**Why:** Hardcoded to Ollama + Gemini. Users might prefer Claude, GPT-4, or Mistral.
**What:** Pluggable LLM provider abstraction.
**How:** Abstract `load\\\_cloud\\\_engine()` into a provider factory; add `CLOUD\\\_PROVIDER` env var.

\---

## 7\. Architectural Reference (Target State)

### 7.1 Layered Architecture (Target)

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
├─────────┼────────────────┼──────────────────┼──────────────┤
│  DATA ACCESS LAYER                                            │
│  ┌──────▼──────┐  ┌──────▼───────┐  ┌──────▼───────────┐  │
│  │ VectorStore │  │ DocStore     │  │ UserStore        │  │
│  │ (ChromaDB)  │  │ (FileSystem) │  │ (SQLite future)  │  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Ollama   │  │ Gemini   │  │ Tesseract│  │ Poppler    │ │
│  │ (Local)  │  │ (Cloud)  │  │ (OCR)    │  │ (PDF)      │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 7.2 Target Configuration Management

Replace `src/config.py` globals with Pydantic Settings:

```python
# src/settings.py (target)
from pydantic\\\_settings import BaseSettings

class Settings(BaseSettings):
    storage\\\_secret: str           # Required — no default, fail if unset
    google\\\_api\\\_key: str = ""      # Optional — Gemini disabled if empty

    host: str = "0.0.0.0"
    port: int = 8080
    profile: str = "LOW\\\_RESOURCE"
    llm\\\_model: str = "llama3.2:3b"
    embed\\\_model: str = "BAAI/bge-m3"
    gemini\\\_model: str = "models/gemini-2.5-flash"
    prompt\\\_optimization: str = "local"
    enable\\\_ner\\\_masking: bool = True
    ner\\\_score\\\_threshold: float = 0.35

    class Config:
        env\\\_file = ".env"
        env\\\_file\\\_encoding = "utf-8"
```

### 7.3 Target File Structure

```
arcumai/
├── pyproject.toml              ← Direct deps + dev deps + tooling config
├── .env.example                ← Template (no secrets)
│
├── src/
│   ├── app.py                  ← FastAPI app factory
│   ├── settings.py             ← Pydantic Settings (replaces config.py)
│   ├── ai/                     ← (unchanged — well-structured)
│   ├── bridge/                 ← (unchanged — well-structured)
│   ├── ingestion/              ← Move ingest.py + watcher.py + readers.py here
│   ├── data/                   ← vector\\\_store.py, user\\\_store.py, audit\\\_store.py
│   ├── ui/                     ← (unchanged)
│   └── core/                   ← logging.py, security.py, file\\\_utils.py
│
├── tests/
│   ├── unit/                   ← test\\\_auth.py, test\\\_session.py, test\\\_rate\\\_limiter.py
│   ├── integration/            ← test\\\_bridge.py, test\\\_ingestion.py
│   └── conftest.py
│
└── outlook-plugin/             ← (unchanged — well-structured)
```

\---

## 8\. Roadmap

### Phase 5: Security Hardening

* \[x] Rotate compromised API keys (`cf3ba69` 2026-03)
* \[x] Remove hardcoded secrets from source (`cf3ba69`)
* \[x] Add WebSocket API key authentication (`43bd5a2`)
* \[x] Add WebSocket brute-force protection (`5c11200`)
* \[x] Add path traversal prevention (`2b2c302`, `65af259`)
* \[x] Add log injection prevention (`cd23e38`)
* \[x] PII UUID-based masking (`85d0a55`)
* \[x] C#: `UseSecureConnection` enforced (`e2ad404`)
* \[x] C#: Sensitive payload redaction (`92524bd`)
* \[ ] Enforce `STORAGE\\\_SECRET` (no default fallback) — **OPEN**
* \[ ] Add HTTPS/WSS (reverse proxy) — **OPEN**
* \[ ] Add login attempt throttling — **OPEN**
* \[ ] C#: HTML sanitize server response before email injection (CS-SEC-1) — **OPEN**
* \[ ] C#: Reject executable attachments (CS-SEC-4) — **OPEN**
* \[ ] C#: WebSocket receive buffer size limit (CS-BUG-2) — **OPEN**

### Phase 6: Testing \& CI

* \[ ] Unit tests: auth, session routing, rate limiter, readers
* \[ ] Integration tests: bridge WebSocket flow, ingestion pipeline
* \[ ] CI pipeline (GitHub Actions): lint + test on every push
* \[ ] Linting: ruff
* \[ ] Type checking: mypy

### Phase 7: Data Layer \& Operational Improvements

* \[ ] Fix `requirements.txt` encoding
* \[ ] Migrate user store from JSON to SQLite
* \[ ] Add audit logging (append-only `logs/audit.jsonl`)
* \[ ] Per-user document access control
* \[ ] Scheduled pending result cleanup task
* \[ ] Incremental BM25 updates

### Phase 8: Production Readiness

* \[ ] `pyproject.toml` with proper dependency management
* \[ ] Docker containerization
* \[ ] Health check with ChromaDB + Ollama status
* \[ ] Prometheus metrics endpoint
* \[ ] Structured JSON logging
* \[ ] Graceful shutdown hook
* \[ ] Session timeout configuration

### Phase 9: Feature Enhancements

* \[ ] Streaming responses in web UI
* \[ ] Email thread context in loopback
* \[ ] User feedback collection
* \[ ] Calendar write operations
* \[ ] Multi-tenant document isolation
* \[ ] Document management admin panel
* \[ ] Automated document classification

\---

## 9\. File-by-File Reference

### `main\\\_nice.py` — Application Entry Point

* **Purpose:** Bootstraps FastAPI + NiceGUI, defines login/main pages, WebSocket endpoint for Outlook plugin
* **Key Functions:**

  * `health\\\_check()` — GET /health
  * `\\\_is\\\_valid\\\_outlook\\\_id(user\\\_id)` — validates against users.json; rejects duplicates
  * `outlook\\\_endpoint(websocket, user\\\_id)` — WS endpoint with rate limiting, API key auth, ID validation
  * `login\\\_page()` — GET /login
* **Active Issues:** SEC-2 (default session secret), SEC-3 (no HTTPS), SEC-4 (static files), SEC-5 (no CSRF), SEC-9 (no login brute-force)
* **Strengths:** CORS configured, duplicate `outlook\\\_id` detection, IP-based WS auth rate limiting

### `ingest.py` — Batch Ingestion Pipeline

* **Purpose:** Reads files from `data\\\_nuovi/`, deduplicates by hash, chunks via LlamaIndex, stores in ChromaDB + BM25, moves to `data\\\_archivio/`
* **Key Flow:** lock → scan → deduplicate → read/chunk → batch insert → BM25 rebuild → cleanup
* **Active Issues:** BUG-3 (lock race), BUG-8 (double hash), PERF-1 (BM25 rebuild), PERF-2 (full collection load)

### `watcher.py` — Folder Monitor

* **Purpose:** Watches `input\\\_utente/` for new files via watchdog; debounces 5s; moves to inbox; spawns ingestion subprocess
* **Active Issues:** BUG-5 (case-sensitive extensions)
* **Strengths:** Folder health checks, retry on unavailability, file structure preservation during move

### `src/config.py` — Configuration

* **Purpose:** All paths, hardware profiles (HIGH/LOW\_RESOURCE), LLM settings, VSTO constants, system prompts, role definitions
* **Hardware profiles:**

  * `LOW\\\_RESOURCE` (default): llama3.2:3b, 4k context, 512-chunk, top\_k=10
  * `HIGH\\\_RESOURCE`: llama3.3:70b, 16k context, 1024-chunk, top\_k=20
* **Role prompts:** ADMIN, LEGAL, EXECUTIVE, COMMERCIALISTA, DEFAULT — each with domain-specific Italian-language prompt
* **Active Issues:** QA-5 (hardcoded Gemini model)

### `src/auth.py` — Authentication

* **Purpose:** User CRUD, bcrypt hashing (12 rounds), password policy, WS brute-force protection
* **Active Issues:** BUG-6 (non-atomic users.json write)
* **Strengths:** Solid password policy (min 8, uppercase, lowercase, digit), per-IP WS failure tracking

### `src/conversations.py` — Conversation Persistence

* **Purpose:** Persists chat sessions per user to `storage/conversations/<username>/<id>.json`
* **ConversationStore methods:** `list\\\_conversations`, `load\\\_conversation`, `save\\\_message`, `delete\\\_conversation`, `cleanup\\\_empty`
* **Strengths:** Atomic writes (temp + rename), UTF-8 JSON, username path sanitization

### `src/readers.py` — Document Readers

* **Purpose:** SmartPDFReader (native + OCR), MyOutlookReader (MSG), MyEmlReader (EML)
* **SmartPDFReader logic:** Scanner metadata detection → native extraction → linguistic quality scoring (% common words in 4 languages) → OCR if score < threshold
* **Languages supported:** Italian, English, German, French (Tesseract + Poppler)
* **Strengths:** Sophisticated scanner detection; graceful OCR fallback; multilingual common-word scoring

### `src/utils.py` — File Utilities

* **Purpose:** MD5 hash, file move with retry (3x), empty folder cleanup (ignores junk files), ChromaDB utilities
* **`sposta\\\_file\\\_con\\\_struttura`:** Preserves directory structure; handles name collisions with timestamp; Windows PermissionError retry
* **Active Issues:** SEC-8 (MD5 vs SHA-256), PERF-2 (full ChromaDB load)

### `src/ai/engines.py` — LLM Engine Factories

* **Purpose:** Creates three engine types on demand

  * `load\\\_rag\\\_engine(role)` — ChromaDB vectors + BM25 hybrid via QueryFusionRetriever (reciprocal\_rerank); role-specific system prompt; 8k memory buffer
  * `load\\\_simple\\\_local\\\_engine()` — Direct Ollama, no RAG; 16k memory buffer
  * `load\\\_cloud\\\_engine()` — Gemini 2.5 Flash; requires GOOGLE\_API\_KEY; 8k memory buffer
* **Strengths:** BM25 graceful degradation (vector-only if BM25 unavailable)

### `src/ai/session.py` — UserSession

* **Purpose:** Central routing engine; manages all AI engines and tools per user
* **Engine selection:** Trigger matching → keyword detection → LLM classification fallback
* **Tools available to agent:** `read\\\_email\\\_wrapper(query)`, `calendar\\\_wrapper(filter)` — both with guardrails
* **Active Issues:** PERF-3 (new session per loopback request)
* **Strengths:** Dual ReActAgent support (Workflow + Legacy), lazy engine init

### `src/ai/prompt\\\_optimizer.py` — Prompt Optimization

* **Purpose:** Converts raw email body into optimized RAG search query
* **Modes:** `local` (Ollama), `gemini` (cloud + NER masking/unmasking), `off` (raw)
* **Active Issues:** SEC-10 (PII logged at DEBUG)
* **Strengths:** 100k char rejection guard, lazy Gemini init, fallback to raw on errors

### `src/ai/ner\\\_masking.py` — PII Masking

* **Purpose:** Presidio-based PII detection with Swiss/Italian domain extensions
* **Custom recognizers:** SWISS\_LEGAL\_ENTITY (SA/AG/GmbH/Sagl), IT\_FISCAL\_CODE, CH\_IBAN, CH\_VAT\_NUMBER
* **Strategy:** UUID-keyed placeholders (reversible); multiple language models (it\_core\_news\_lg, en\_core\_web\_lg)
* **Strengths:** Fully reversible via `unmask\\\_pii()`; graceful if Presidio not installed

### `src/bridge/manager.py` — OutlookBridgeManager

* **Purpose:** Manages all Outlook WebSocket connections; routes MCP tool calls; manages per-user priority queues
* **MCP protocol:** JSON-RPC 2.0 over WebSocket; UUID request/response matching; 60s timeout
* **Offline resilience:** Queued emails survive client disconnect; `PendingResultStore` delivers on reconnect
* **Strengths:** `\\\_safe\\\_uid()` log injection prevention; dedup via `active\\\_connections`; AI semaphore (cap 3)

### `src/bridge/loopback\\\_processor.py` — Email Processing

* **Purpose:** Full pipeline: decode attachments → optimize prompt → route AI → format response → deliver
* **Attachment formats:** PDF, DOCX, XLSX, MSG, EML, TXT, CSV (base64 decoded in Python)
* **Routing logic:** Attachments present → simple\_local (FILE\_READER); no attachments → RAG engine
* **Active Issues:** PERF-3 (new UserSession per request)
* **Strengths:** CC disclaimer logic; markdown→HTML conversion; offline delivery fallback

### `src/bridge/pending\\\_results.py` — Offline Result Storage

* **Purpose:** Stores AI responses to disk when Outlook client is offline; delivers on reconnect
* **Key design:** 48h TTL; atomic `.delivering` rename to prevent double-delivery; startup recovery of interrupted deliveries
* **Active Issues:** BUG-10 (no scheduled cleanup)

### `src/ui/footer.py` — Chat Footer

* **Purpose:** File upload (PDF/TXT/MD with magic byte validation) + message send + response rendering
* **Active Issues:** BUG-4 (temp file leak on error), BUG-9 (silent truncation at 10k chars)
* **Strengths:** Magic byte validation for PDFs; OCR-aware upload in thread; rate limit + sanitization before send

### `src/ui/rate\\\_limiter.py` — Rate Limiting

* **Purpose:** Per-user sliding-window rate limit (20 msg/60s) + control char sanitization
* **Active Issues:** BUG-7 (memory growth partially mitigated)
* **Strengths:** Periodic cleanup; stale-user eviction; control char stripping (0x00-0x08, 0x0e-0x1f, 0x7f)

### C# Plugin — Architecture Summary

**Design pattern:** Transport abstraction (IMcpTransport) + Plugin lifecycle (ThisAddIn) + Virtual loopback (VirtualLoopbackHandler + Core/Loopback/\*) + Data access (OutlookDataProvider) + Configuration (PluginConfig + PluginConfigLoader) + Logging (IPluginLogger)

**Key strengths:**

* `IMcpTransport` interface allows swapping WebSocket for other transports
* Server-pushed configuration (backend can update plugin settings)
* COM object lifecycle management (`Marshal.ReleaseComObject`)
* SynchronizationContext marshaling for Outlook STA thread compliance
* Heartbeat with dead-connection detection
* Exponential backoff reconnection

**Key open issues:**

* CS-SEC-1 (HTML injection in email body) — most critical
* CS-BUG-2 (unbounded WebSocket buffer) — memory safety risk
* CS-BUG-3 (logger race condition) — low-probability but corrupts logs
* CS-PERF-3 (per-call file I/O in logger) — easy fix with buffered writer

\---

## 10\. Appendices

### Appendix A: Dependency Analysis

**Heavy dependencies (consider alternatives):**

|Package|Size|Used For|Alternative|
|-|-|-|-|
|`torch`|\~2 GB|sentence-transformers embeddings|`torch` CPU-only wheel|
|`playwright`|\~200 MB|Law scraper in `scripts/` only|Isolate to separate project|
|`kubernetes`|\~50 MB|Not used in any source file|Remove|
|`traceloop-sdk` + OpenTelemetry (30 packages)|\~100 MB|Unused instrumentation|Remove if not active|

**Key direct dependencies:**

* **AI:** llama-index, chromadb, bm25s, ollama, google-generativeai, sentence-transformers
* **Web:** fastapi, nicegui, uvicorn, websockets
* **Doc processing:** pypdf, extract-msg, docx2txt, openpyxl, pytesseract, pdf2image
* **Auth:** bcrypt, python-jose
* **Privacy:** presidio-analyzer, presidio-anonymizer (optional)

### Appendix B: Environment Variables Reference

|Variable|Default|Description|
|-|-|-|
|`PROFILE`|`LOW\\\_RESOURCE`|Hardware profile|
|`LLM\\\_MODEL`|Profile-based|Ollama model name|
|`EMBED\\\_MODEL`|`BAAI/bge-m3`|HuggingFace embedding model|
|`CONTEXT\\\_WINDOW`|Profile-based|LLM context window (tokens)|
|`REQUEST\\\_TIMEOUT`|Profile-based|LLM request timeout (seconds)|
|`STORAGE\\\_SECRET`|⚠️ has hardcoded fallback|NiceGUI session encryption key|
|`GOOGLE\\\_API\\\_KEY`|—|Gemini API key|
|`HOST`|`0.0.0.0`|Server bind address|
|`PORT`|`8080`|Server port|
|`ALLOWED\\\_ORIGINS`|`http://localhost:8080`|CORS origins (comma-separated)|
|`WS\\\_API\\\_KEY`|—|Optional WebSocket shared secret|
|`PROMPT\\\_OPTIMIZATION`|`local`|`local` / `gemini` / `off`|
|`ENABLE\\\_NER\\\_MASKING`|`true`|PII masking before cloud calls|
|`NER\\\_SCORE\\\_THRESHOLD`|`0.35`|Presidio confidence threshold|
|`LOG\\\_LEVEL`|`INFO`|Logging level|
|`BRIDGE\\\_TIMEOUT`|`60.0`|MCP tool call timeout (seconds)|
|`LOOPBACK\\\_TIMEOUT`|`3600.0`|Loopback processing timeout|
|`LOOPBACK\\\_MAX\\\_CONCURRENT`|`3`|Max parallel AI requests|
|`PENDING\\\_RESULT\\\_TTL\\\_HOURS`|`48`|How long to keep offline results|
|`VSTO\\\_MAX\\\_ATTACHMENT\\\_MB`|`25`|Max single attachment size|
|`VSTO\\\_MAX\\\_TOTAL\\\_MB`|`50`|Max total attachment size|
|`VSTO\\\_ARCUMAI\\\_EMAIL`|`assistant@arcumai.ch`|AI assistant email address|
|`VSTO\\\_ENABLE\\\_VIRTUAL\\\_LOOPBACK`|`true`|Enable email interception|
|`TESSERACT\\\_PATH`|`C:\\\\Program Files\\\\Tesseract-OCR\\\\tesseract.exe`|Tesseract binary path|
|`POPPLER\\\_PATH`|`C:\\\\Program Files\\\\Poppler\\\\Library\\\\bin`|Poppler bin directory|

### Appendix C: Data Directory Layout

```
ArcumAI/
├── chroma\\\_db/                  ← ChromaDB vector store (LlamaIndex-managed)
├── storage\\\_bm25/               ← BM25 keyword index (LlamaIndex-managed)
├── storage/
│   └── conversations/
│       └── <username>/
│           └── <conv\\\_id>.json  ← Conversation history
├── temp/
│   └── pending\\\_results/
│       └── arcumai\\\_pending\\\_<uid>\\\_<rid>.json  ← Offline loopback results
├── logs/
│   ├── server.log              ← Rotating daily (30-day retention)
│   └── ingestion.log           ← Rotating daily
├── data\\\_nuovi/                 ← Drop zone for new documents
├── data\\\_archivio/              ← Successfully ingested documents
├── data\\\_error/                 ← Failed ingestion files
├── data\\\_duplicati/             ← Duplicate files
└── input\\\_utente/               ← User file drop zone (watched by watcher.py)
```

### Appendix D: C# Plugin Configuration Loading Priority

1. `%APPDATA%\\\\ArcumAI\\\\Outlook\\\\config.json` (JSON file — highest priority)
2. `app.config` (via `System.Configuration.ConfigurationManager.AppSettings`)
3. Hardcoded defaults in `PluginConfigLoader.SetDefaults()`

Default values: `ServerUrl=ws://localhost:8080`, `UserId=<Windows username>`, `ReconnectDelayMs=5000`, `MaxReconnectAttempts=720`, `HeartbeatIntervalMs=30000`, `RequestTimeoutMs=60000`, `LogFilePath=%APPDATA%\\\\ArcumAI\\\\Outlook\\\\logs\\\\plugin.log`

\---

*Analysis generated 2026-05-28 from full codebase inspection.
Previous analysis (2026-03-08): `doc/ARCUMAI\\\_FULL\\\_ANALYSIS\\\_backup\\\_2026-03-08.md`*

