# ArcumAI — Refactoring Plan

> Created: 2026-03-01
> Status: Phase 3 complete (2026-03-02) — Phase 4 pending

---

## 1. Overview

This document captures the refactoring proposal for the ArcumAI codebase.
The primary driver is **separation of duties**: several source files have grown
to accumulate multiple unrelated responsibilities, making them hard to navigate,
test, and extend independently.

---

## 2. File Size Inventory

| File | Lines | Verdict |
|---|---|---|
| `Core/VirtualLoopbackHandler.cs` | **1135** | Critical — 5+ responsibilities |
| `src/bridge.py` | **743** | Critical — 5+ responsibilities |
| `ThisAddIn.cs` | **578** | High — 4 responsibilities |
| `src/engine.py` | **539** | High — 3 responsibilities |
| `Core/PluginConfig.cs` | **476** | Medium — bloated loader |
| `src/ui/footer.py` | **229** | Low — business logic mixed into UI |

---

## 3. Files That Stay As-Is

These are well-scoped and do not need splitting.

| File | Lines | Reason |
|---|---|---|
| `src/config.py` | 184 | Single concern: environment → typed constants |
| `src/readers.py` | 190 | Single concern: file format readers |
| `src/ner_masking.py` | 284 | Single concern: Presidio PII masking |
| `src/auth.py` | 91 | Single concern: user auth |
| `src/utils.py` | 210 | Utility grab-bag, acceptable size |
| `Core/WebSocketTransport.cs` | 147 | Single concern: WebSocket I/O |
| `watcher.py` | 177 | Single concern: file watcher |

---

## 4. Priority 1 — Critical

### 4.1 Split `Core/VirtualLoopbackHandler.cs` (1135 lines → 4 files)

This class handles five completely different things. Each is large enough to
stand alone.

**Target structure:**

```
Core/
  Loopback/
    VirtualLoopbackHandler.cs   (~180 lines)  ← orchestrator only
    AttachmentExtractor.cs      (~160 lines)  ← new
    OutlookMailFactory.cs       (~380 lines)  ← new
    ContactManager.cs           ( ~80 lines)  ← new
```

| New file | Extracted responsibilities |
|---|---|
| `VirtualLoopbackHandler.cs` (trimmed) | `ShouldIntercept`, `ShouldProcessInParallel`, `RemoveArcumRecipient`, `ProcessInterceptedEmail` (orchestration only), `HandleServerResponse`, `NotifyPendingOnDisconnect` |
| `AttachmentExtractor.cs` | `ExtractAttachments`, `GetMimeType`, per-file and total size guards |
| `OutlookMailFactory.cs` | `SimulateSentItem`, `DeleteInterceptedItem`, `CloseComposeWindow`, `DeleteFromOutbox`, `CreateResponseEmail`, `WrapInEmailHtml`, `InjectResponseOnMainThread` |
| `ContactManager.cs` | `EnsureContactExists` and future contact-related helpers |

**Why:** `SimulateSentItem` alone is ~90 lines of MAPI property manipulation —
a distinct domain from attachment extraction or recipient analysis. The file is
hard to navigate and will only grow.

---

### 4.2 Split `src/bridge.py` (743 lines → 4 files)

`OutlookBridgeManager` currently owns WebSocket connection management,
JSON-RPC protocol dispatch, email processing business logic, attachment
decoding, priority queue management, offline result persistence, and response
formatting.

**Target structure:**

```
src/
  bridge/
    __init__.py            ← re-exports bridge_manager for backward compat
    manager.py             (~180 lines)  ← connection + protocol + config push
    loopback_processor.py  (~220 lines)  ← new
    loopback_queue.py      (~110 lines)  ← new
    pending_results.py     (~140 lines)  ← new
```

| New file | Extracted responsibilities |
|---|---|
| `manager.py` | `OutlookBridgeManager`: WebSocket `connect`/`disconnect`, `send_mcp_request`, `handle_incoming_message`, `_build_client_config` |
| `loopback_processor.py` | `_process_loopback_email`, `_process_attachment` (all 6 format branches), `_route_to_ai_engine`, `_markdown_to_html`, `_build_cc_disclaimer` |
| `loopback_queue.py` | `_EmailTask`, `_UserQueue`, `_enqueue_email`, `_queue_worker` |
| `pending_results.py` | `_save_pending_result`, `_find_pending_result`, `_delete_pending_result`, `_deliver_pending_results` |

**Why:** The attachment decoder imports `SmartPDFReader`, `docx`, `openpyxl` —
it is a mini file-reader service. The pending-result store is an independent
persistence layer with its own file-naming convention, TTL logic, and
race-condition handling. Neither belongs with WebSocket protocol code.

> **Backward compatibility:** `src/bridge/__init__.py` should re-export
> `bridge_manager` so all existing call sites (`from src.bridge import bridge_manager`)
> continue to work without changes.

---

## 5. Priority 2 — High

### 5.1 Split `src/engine.py` (539 lines → 3 files)

`engine.py` conflates three distinct concerns: building engine instances,
optimizing prompts (including a full NER masking pipeline), and managing user
sessions.

**Target structure:**

```
src/
  ai/
    __init__.py
    session.py            (~180 lines)  ← UserSession (renamed from engine.py)
    engines.py            ( ~90 lines)  ← new
    prompt_optimizer.py   (~200 lines)  ← new
    ner_masking.py                      ← moved from src/
```

| New file | Extracted responsibilities |
|---|---|
| `session.py` | `UserSession`: `__init__`, `_get_outlook_id`, `_create_user_tools`, `get_*_engine`, `decide_engine`, `run_chat_action`, `_format_history_as_text` |
| `engines.py` | `load_rag_engine`, `load_simple_local_engine`, `load_cloud_engine` |
| `prompt_optimizer.py` | `optimize_prompt_for_rag`, `_optimize_with_local_llm`, `_optimize_with_gemini`, `_get_gemini_optimizer` |

**Why:** The Gemini prompt optimizer with NER masking/unmasking is a
self-contained pipeline (~180 lines) that should be testable independently.
`load_rag_engine` also has its own Chroma/BM25 concerns — keeping it separate
allows it to evolve without touching the session class.

---

### 5.2 Split `ThisAddIn.cs` (578 lines → 3 files)

The VSTO entry point currently owns connection management, JSON-RPC dispatch,
Outlook data queries, and logging.

**Target structure:**

```
Core/
  OutlookDataProvider.cs   (~130 lines)  ← new
  PluginLogger.cs          ( ~60 lines)  ← new
ThisAddIn.cs               (~300 lines)  ← trimmed
```

| New file | Extracted responsibilities |
|---|---|
| `ThisAddIn.cs` (trimmed) | VSTO lifecycle (`Startup`, `Shutdown`, `Quit`), connection management (`ConnectToArcum`, `ScheduleReconnect`, `SendIdentify`, `ApplyServerConfig`, heartbeat), message dispatch (`OnMessageFromArcum`) |
| `OutlookDataProvider.cs` | `GetEmails`, `GetCalendar` — Outlook COM query logic with correct COM object release |
| `PluginLogger.cs` | `Log`, rotation logic, log level filtering — behind an `IPluginLogger` interface |

**Why:** `GetEmails` and `GetCalendar` are pure Outlook COM data-access
functions with no business being in the add-in lifecycle class. Extracting
`PluginLogger` also enables injecting a mock logger in future tests.

---

## 6. Priority 3 — Low

### 6.1 `src/ui/footer.py` — extract rate-limiting and validation

The footer owns input rate-limiting state and sanitization logic — reusable
business rules that don't belong in a UI component.

```
src/ui/
  rate_limiter.py   (~40 lines)  ← new: _check_rate_limit, sanitize_input, constants
  footer.py         (~190 lines) ← purely UI rendering
```

### 6.2 `Core/PluginConfig.cs` — split loader from properties

```
Core/
  Config/
    PluginConfig.cs        (~150 lines)  ← properties + singleton accessor + Validate()
    PluginConfigLoader.cs  (~200 lines)  ← LoadConfiguration(), SetDefaults(), JSON parsing
```

---

## 7. Recommended Directory Structure Changes

### Python backend — introduce sub-packages in `src/`

**Current `src/` (flat, ~8 files)** will become crowded after splits.
Grouping by domain package makes the structure self-documenting.

```
src/
├── ai/                        ← new sub-package
│   ├── __init__.py
│   ├── session.py             ← UserSession (from engine.py)
│   ├── engines.py             ← load_rag/simple/cloud engine
│   ├── prompt_optimizer.py    ← optimize_prompt_for_rag pipeline
│   └── ner_masking.py         ← moved from src/
│
├── bridge/                    ← new sub-package
│   ├── __init__.py            ← re-exports bridge_manager
│   ├── manager.py             ← OutlookBridgeManager
│   ├── loopback_processor.py
│   ├── loopback_queue.py
│   └── pending_results.py
│
├── ui/                        ← already exists
│   ├── __init__.py
│   ├── rate_limiter.py        ← new
│   ├── chat_area.py
│   ├── footer.py
│   ├── header.py
│   └── sidebar.py
│
├── __init__.py
├── auth.py
├── config.py
├── database.py
├── logger.py
├── readers.py
└── utils.py
```

### C# plugin — sub-folders under `Core/`

```
Core/
├── Config/
│   ├── PluginConfig.cs
│   └── PluginConfigLoader.cs
│
├── Loopback/
│   ├── VirtualLoopbackHandler.cs
│   ├── AttachmentExtractor.cs
│   ├── OutlookMailFactory.cs
│   └── ContactManager.cs
│
├── Transport/
│   ├── IMcpTransport.cs
│   └── WebSocketTransport.cs
│
├── OutlookDataProvider.cs
└── PluginLogger.cs
```

Namespaces follow folders: `ArcumAI.OutlookAddIn.Core.Loopback`,
`ArcumAI.OutlookAddIn.Core.Transport`, etc.

### Root level cleanup

```
/
├── ingest.py       ← renamed from main.py (ingestion pipeline, not the app)
├── main_nice.py    ← consider renaming to app.py (server entry point)
├── watcher.py
├── rag_query.py
├── admin_tool.py
│
└── scripts/        ← new: consolidates non-production one-offs
    ├── debug_search.py
    ├── diagnose_file.py
    ├── diagnose_pdf.py
    ├── scarica_leggi_ti.py
    └── test_gemini.py
```

---

## 8. Recommended Execution Order

Perform refactoring in **4 phases** (see `REFACTORING_TEST_PLAN.md` for the
test plan for each phase). Never perform all changes at once.

| Phase | Scope | Risk |
|---|---|---|
| **1** | Structure only — create folders, move files, fix import paths | Minimal |
| **2** | Extract clean, self-contained units (no inter-module dependencies) | Low |
| **3** | Extract heavier, coupled units | Medium |
| **4** | Polish — renames, interfaces, minor splits | Low |

### Phase 1 — Structure only
- Create `src/ai/`, `src/bridge/`, `scripts/`
- Move `src/ner_masking.py` → `src/ai/ner_masking.py`
- Move debug/prove scripts → `scripts/`
- Create `Core/Transport/`, `Core/Loopback/`, `Core/Config/`
- Move `IMcpTransport.cs` + `WebSocketTransport.cs` → `Core/Transport/`

### Phase 2 — Clean, self-contained extractions
| Step | What |
|---|---|
| 2a | `src/engine.py` → `src/ai/engines.py` (factory functions only) |
| 2b | `src/engine.py` → `src/ai/prompt_optimizer.py` |
| 2c | `src/bridge.py` → `src/bridge/pending_results.py` |
| 2d | `src/bridge.py` → `src/bridge/loopback_queue.py` |
| 2e | `VirtualLoopbackHandler.cs` → `AttachmentExtractor.cs` |
| 2f | `VirtualLoopbackHandler.cs` → `ContactManager.cs` |

### Phase 3 — Heavier, coupled extractions
| Step | What |
|---|---|
| 3a | `src/bridge.py` → `src/bridge/loopback_processor.py` |
| 3b | `src/engine.py` → `src/ai/session.py` (UserSession) |
| 3c | `VirtualLoopbackHandler.cs` → `OutlookMailFactory.cs` |
| 3d | `ThisAddIn.cs` → `OutlookDataProvider.cs` |

### Phase 4 — Polish
- Rename `main.py` → `ingest.py`
- Extract `PluginLogger.cs` with `IPluginLogger` interface
- Split `PluginConfig.cs` → `PluginConfigLoader.cs`
- Extract `src/ui/rate_limiter.py`

---

## 9. One Rule for Every Step

> **One commit per extracted unit, with a message that states exactly what
> moved and what logic did not change.**

Example: `Extract AttachmentExtractor from VirtualLoopbackHandler — no logic changes`

This makes any future `git bisect` trivially easy and keeps code reviews
manageable.
