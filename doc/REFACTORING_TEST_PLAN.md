# ArcumAI — Refactoring Test Plan

> Created: 2026-03-01
> Companion document: `REFACTORING_PLAN.md`
> Status: Phase 1 complete (2026-03-02) — Phase 2 pending
> Platform: Windows PowerShell

---

## 0. Before Anything — Establish a Baseline

Run the following **once before Phase 1 starts** and save the output.
Every phase uses this baseline as its reference.

```powershell
# 1. Server starts and responds (run in a separate terminal, keep it open)
python main_nice.py

# In a second PowerShell terminal:
(Invoke-WebRequest -UseBasicParsing http://localhost:8080).StatusCode
# expect: 200

# 2. Ingestion runs clean
python main.py 2>&1 | Select-Object -Last 5

# 3. RAG query returns results
python rag_query.py
```

In Visual Studio: **Build → Rebuild Solution** and save the Output window
contents to `baseline_build.txt`.

Also note the last lines of `logs/server.log` and `logs/ingestion.log` —
use these as "known good" log anchors.

---

## Regression Gate

Run after **every phase** before committing it as done:

```powershell
# 1. Run automated tests
python -m pytest tests/ -v --tb=short

# 2. Start server in background, wait, probe, then stop
$job = Start-Job { python c:\ArcumAI\main_nice.py }
Start-Sleep 5
(Invoke-WebRequest -UseBasicParsing http://localhost:8080).StatusCode
# expect: 200
Stop-Job $job ; Remove-Job $job
```

And in Outlook: send one plain-text virtual loopback email (no attachments)
to the ArcumAI address. A response must arrive within the configured timeout.

---

## Phase 1 — Structure Only (moves, no logic changes)

### What changed
New folders created, files moved to sub-packages, import paths updated.
No function bodies are modified.

### Automated check — import sweep

Create `tests/test_imports.py` (keep permanently — it pays ongoing dividends):

```python
# tests/test_imports.py
"""Verifies every public module can be imported without errors."""

def test_config():        from src import config
def test_auth():          from src import auth
def test_logger():        from src import logger
def test_readers():       from src import readers
def test_utils():         from src import utils
def test_bridge():        from src.bridge import bridge_manager
def test_engine():        from src.engine import UserSession
def test_ner():           from src.ner_masking import mask_pii

# Add as Phase 1 creates new packages:
def test_ai_package():    import src.ai
def test_bridge_pkg():    import src.bridge
```

Run with: `python -m pytest tests/test_imports.py -v`

### Manual smoke tests

| # | Action | Expected |
|---|---|---|
| S1 | `python main_nice.py` | Server starts, no `ImportError` or `ModuleNotFoundError` in console |
| S2 | `python main.py` | Ingestion runs (or exits cleanly if inbox empty) |
| S3 | `python watcher.py` | Watcher starts, no errors |
| S4 | Open browser `http://localhost:8080` | Login page renders |
| S5 | Log in and send one chat message | Response received (any mode) |

### C# plugin checks

| # | Action | Expected |
|---|---|---|
| C1 | Visual Studio → Rebuild Solution | Zero errors, zero new warnings |
| C2 | Run plugin in debug mode | Outlook opens, plugin loads without exception |
| C3 | Check `logs/arcumai_plugin.log` | `ArcumAI Plugin started` line present |

### Pass criteria
All S1–S5 pass. C1 builds clean. `test_imports.py` all green.
Log files show no new error categories compared to baseline.

### Rollback trigger
Any `ImportError` on server startup, or C1 fails to build.

---

## Phase 2 — Extract Clean, Self-Contained Units

Test each step individually before proceeding to the next.
Commit after each step passes.

---

### Step 2a — `src/ai/engines.py` (engine factory functions)

**New script** `tests/test_engines.py`:

```python
# tests/test_engines.py
from src.config import init_settings
init_settings()

def test_load_rag_engine_returns_engine():
    from src.ai.engines import load_rag_engine
    engine = load_rag_engine("DEFAULT")
    assert hasattr(engine, "achat"), "RAG engine must have achat method"

def test_load_simple_engine_returns_engine():
    from src.ai.engines import load_simple_local_engine
    engine = load_simple_local_engine()
    assert hasattr(engine, "achat") or hasattr(engine, "run")
```

**Manual:** Send a `@rag` query in the UI. Verify a response with source
documents appears.

**Log check:** `server.log` contains `RAG Engine Loaded`.

---

### Step 2b — `src/ai/prompt_optimizer.py`

**New script** `tests/test_prompt_optimizer.py`:

```python
# tests/test_prompt_optimizer.py
import asyncio
from src.config import init_settings
init_settings()

async def _test_off_mode():
    from src.ai.prompt_optimizer import optimize_prompt_for_rag
    result = await optimize_prompt_for_rag("Test subject", "Test body", mode="off")
    assert "Test subject" in result
    assert "Test body" in result

def test_off_mode():
    asyncio.run(_test_off_mode())

async def _test_local_mode():
    from src.ai.prompt_optimizer import optimize_prompt_for_rag
    result = await optimize_prompt_for_rag(
        "Contratto affitto", "Buongiorno, ...", mode="local"
    )
    assert isinstance(result, str) and len(result) > 10

def test_local_mode():
    asyncio.run(_test_local_mode())
```

**Manual:** Send a virtual loopback email. Check `server.log` for
`PromptOptimization:` lines — confirm the correct mode is shown.

**Pass:** `off` mode returns raw text unchanged. `local` mode returns a
shorter, optimized string.

---

### Step 2c — `src/bridge/pending_results.py`

**New script** `tests/test_pending_results.py`:

```python
# tests/test_pending_results.py
import asyncio
from pathlib import Path

TEMP_DIR = Path("test_pending_tmp")

async def _run():
    from src.bridge.pending_results import (
        save_pending_result, find_pending_result, delete_pending_result
    )
    TEMP_DIR.mkdir(exist_ok=True)

    response = {"subject": "Test", "response_text": "Hello"}
    await save_pending_result(
        "user1", "req-001", "conv-abc", response, base_dir=TEMP_DIR
    )

    found = find_pending_result("user1", "conv-abc", base_dir=TEMP_DIR)
    assert found is not None, "Should find saved result"
    assert found["response"]["subject"] == "Test"

    delete_pending_result("user1", "conv-abc", base_dir=TEMP_DIR)
    assert find_pending_result("user1", "conv-abc", base_dir=TEMP_DIR) is None

    print("pending_results: ALL PASS")
    import shutil; shutil.rmtree(TEMP_DIR)

asyncio.run(_run())
```

> Note: The `base_dir` parameter must be added during extraction (with the
> production default) to make this function testable without touching the
> filesystem in unexpected locations.

**Pass:** Script prints `ALL PASS`, temp directory cleaned up.

---

### Step 2d — `src/bridge/loopback_queue.py`

**New script** `tests/test_loopback_queue.py`:

```python
# tests/test_loopback_queue.py
import asyncio

async def _run():
    from src.bridge.loopback_queue import LoopbackQueue
    processed = []

    async def fake_processor(user_id, request_id, params):
        processed.append(params["subject"])

    q = LoopbackQueue(processor=fake_processor, max_concurrent=2)

    await q.enqueue("user1", "req-1", {"subject": "High"},   importance=2)
    await q.enqueue("user1", "req-2", {"subject": "Low"},    importance=0)
    await q.enqueue("user1", "req-3", {"subject": "Normal"}, importance=1)

    await asyncio.sleep(0.5)  # let worker drain

    assert processed[0] == "High", f"High priority should be first, got {processed}"
    print(f"Queue order: {processed}  — PASS")

asyncio.run(_run())
```

**Pass:** Output shows `High` processed first.

---

### Step 2e — `Core/Loopback/AttachmentExtractor.cs` (C#)

Manual tests in Outlook:

| # | Action | Expected |
|---|---|---|
| E1 | Send email to ArcumAI with a small PDF (< 1 MB) | `server.log` shows `Extracted 1/1 attachment` |
| E2 | Send email with a file exceeding `MaxAttachmentSizeMB` | Plugin log: `Skipping — exceeds per-file size limit` |
| E3 | Send email with zero attachments | `has_attachments: false` in WebSocket payload |
| E4 | Send email with an inline image in email signature | Inline image skipped (`Content-ID` present), not included as attachment |

**Log check:** `logs/arcumai_plugin.log` — look for `Extracted` and `Skipping`
lines. Confirm no `NullReferenceException`.

---

### Step 2f — `Core/Loopback/ContactManager.cs` (C#)

| # | Action | Expected |
|---|---|---|
| F1 | Delete ArcumAI from Outlook contacts. Restart Outlook. | Contact re-created at startup. Plugin log: `Created Outlook contact` |
| F2 | Contact already exists, restart Outlook | Plugin log: `ArcumAI contact already exists` — no duplicate created |

---

### Phase 2 Overall Pass Criteria
All unit scripts print PASS or pytest reports all green.
All Outlook manual tests pass.
`server.log` contains no new ERROR-level lines compared to baseline.
Plugin log contains no unhandled exceptions.

---

## Phase 3 — Heavier, Coupled Units

These require end-to-end flows to verify correctness.
Commit after each step passes.

---

### Step 3a — `src/bridge/loopback_processor.py`

**New script** `tests/test_attachment_decoding.py`:

```python
# tests/test_attachment_decoding.py
"""Tests attachment text extraction without Outlook or WebSocket."""
import base64, asyncio
from pathlib import Path

def _make_att(path: Path) -> dict:
    data = path.read_bytes()
    return {
        "file_name": path.name,
        "content_base64": base64.b64encode(data).decode()
    }

def test_pdf_extraction():
    from src.bridge.loopback_processor import process_attachment
    pdf = next(Path("data_archivio").rglob("*.pdf"), None)
    if not pdf:
        print("SKIP: no PDF in archive")
        return
    text = process_attachment(_make_att(pdf))
    assert len(text) > 50, "PDF should extract text"
    print(f"PDF extraction: {len(text)} chars — PASS")

def test_txt_extraction():
    from src.bridge.loopback_processor import process_attachment
    att = {
        "file_name": "test.txt",
        "content_base64": base64.b64encode(b"Hello world").decode()
    }
    result = process_attachment(att)
    assert "Hello world" in result
    print("TXT extraction: PASS")

def test_unsupported_type():
    from src.bridge.loopback_processor import process_attachment
    att = {
        "file_name": "test.xyz",
        "content_base64": base64.b64encode(b"data").decode()
    }
    result = process_attachment(att)
    assert "Unsupported" in result
    print("Unsupported type: PASS")
```

**End-to-end manual tests:**

| # | Action | Expected |
|---|---|---|
| L1 | Send email to ArcumAI with a PDF attachment | Response email appears in Inbox with document analysis |
| L2 | Send plain email (no attachments) | RAG-based response received |
| L3 | Send email with PDF exceeding size limit | Error email returned explaining the size limit |
| L4 | Disconnect plugin, send email, reconnect | Response delivered automatically on reconnect |

**Log check:** `server.log` — look for `VirtualLoopback [user]: Response delivered`
or `Client offline — result stored`.

---

### Step 3b — `src/ai/session.py` (UserSession)

**New script** `tests/test_session.py`:

```python
# tests/test_session.py
import asyncio
from src.config import init_settings
init_settings()

async def _run():
    from src.ai.session import UserSession

    session = UserSession(username="test_user", role="DEFAULT")
    assert session.username == "test_user"
    assert session.rag_engine is None       # lazy-loaded
    assert session.tools is not None        # created at init

    # Test engine routing
    decision = await session.decide_engine("@rag cosa dice il contratto?")
    assert decision == "RAG", f"Expected RAG, got {decision}"

    decision = await session.decide_engine("ciao come stai")
    assert decision in ("SIMPLE", "RAG"), f"Unexpected: {decision}"

    print("UserSession routing: PASS")

asyncio.run(_run())
```

**Manual tests — log in and exercise all three modes:**

| # | Action | Expected |
|---|---|---|
| U1 | Send `@rag [query]` | RAG mode indicator shown in UI, source documents listed |
| U2 | Send `@chat [text]` | Simple/Local mode indicator shown |
| U3 | Send `leggi le mie email` (Outlook connected) | Agent mode activated, email list returned |

---

### Step 3c — `Core/Loopback/OutlookMailFactory.cs` (C#)

| # | Action | Expected |
|---|---|---|
| M1 | Send full loopback email to ArcumAI | Response appears in Inbox **from** ArcumAI display name |
| M2 | Check Sent Items after interception | Original email present with correct timestamp and sender name (not X.500 DN format) |
| M3 | Check compose window after interception | No orphan compose window remains open in Outlook |
| M4 | Verify conversation threading | Request and response email grouped in the same conversation |
| M5 | Send from Exchange Online account | Sent Items copy shows SMTP address, not Exchange DN |

**Log check:** Plugin log — `Copied to Sent Items`, `Deleted intercepted item`,
no `COMException`.

---

### Step 3d — `Core/OutlookDataProvider.cs` (C#)

| # | Action | Expected |
|---|---|---|
| D1 | In chat UI: `leggi le mie email` | Email list returned from Outlook, displayed in chat |
| D2 | In chat UI: `cosa ho oggi in agenda?` | Calendar items for today returned |
| D3 | Email search with filter that matches nothing | Returns empty list, no exception |
| D4 | Calendar filter `week` | 7-day appointments returned |

**Log check:** Plugin log — `GetEmails: found N emails`,
`GetCalendar: found N appointments`.

---

### Phase 3 Overall Pass Criteria
All unit scripts pass. All L1–L4, M1–M5, D1–D4 manual tests pass.
`server.log` shows no new ERROR lines compared to baseline.
End-to-end latency for a simple RAG query is within 20% of baseline
(guards against accidentally blocking the event loop during refactoring).

---

## Phase 4 — Polish

Lower risk, but these changes touch entry points and interfaces.
One commit per item.

---

### Step 4a — `main.py` → `ingest.py` rename

```powershell
# After rename:
python ingest.py        # must run identically to old main.py
python watcher.py       # watcher invokes ingest.py — verify it still works
```

Check that `watcher.py` references the new filename correctly.

---

### Step 4b — `Core/PluginLogger.cs` + `IPluginLogger` interface (C#)

| # | Action | Expected |
|---|---|---|
| PL1 | Rebuild solution | Zero errors |
| PL2 | Run plugin, exercise all log paths | Log format unchanged: `yyyy-MM-dd HH:mm:ss.fff [LEVEL] message` |
| PL3 | Simulate log file > 5 MB | `.old` file created, new log file starts fresh |

---

### Step 4c — `Core/Config/PluginConfigLoader.cs` split (C#)

| # | Action | Expected |
|---|---|---|
| PC1 | Delete `arcumai_config.json`, restart plugin | Defaults loaded correctly, no exception |
| PC2 | Corrupt JSON config file, restart plugin | Falls back to defaults gracefully, warning in log |
| PC3 | Full config pushed from server after `client/identify` | All 8 keys applied, log shows `Config sync applied: 8/8` |

---

### Step 4d — `src/ui/rate_limiter.py` extraction

Add to `tests/test_imports.py`:

```python
def test_rate_limiter():
    from src.ui.rate_limiter import sanitize_input, check_rate_limit
```

**New tests** in `tests/test_rate_limiter.py`:

```python
# tests/test_rate_limiter.py

def test_sanitize_strips_control_chars():
    from src.ui.rate_limiter import sanitize_input
    result = sanitize_input("hello\x00world\x1f!")
    assert "\x00" not in result
    assert "\x1f" not in result

def test_sanitize_truncates_at_limit():
    from src.ui.rate_limiter import sanitize_input
    result = sanitize_input("a" * 5000)
    assert len(result) == 4000

def test_rate_limit_blocks_after_threshold():
    from src.ui.rate_limiter import check_rate_limit
    for _ in range(20):
        check_rate_limit("test_user_rl")
    assert check_rate_limit("test_user_rl") is False
```

---

## Summary Table

| Phase | Automated tests | Manual tests | Key log signals to verify |
|---|---|---|---|
| **1** | `test_imports.py` | S1–S5, C1–C3 | No new `ImportError` or build error |
| **2a–2b** | `test_engines.py`, `test_prompt_optimizer.py` | `@rag` query, loopback email | `RAG Engine Loaded`, `PromptOptimization:` |
| **2c–2d** | `test_pending_results.py`, `test_loopback_queue.py` | Offline delivery flow | `Pending result saved`, `delivered` |
| **2e–2f** | — | E1–E4, F1–F2 | `Extracted N/N`, `contact already exists` |
| **3a** | `test_attachment_decoding.py` | L1–L4 | `Response delivered`, `result stored` |
| **3b** | `test_session.py` | U1–U3 (all chat modes) | Mode indicator correct in UI |
| **3c** | — | M1–M5 | `Copied to Sent Items`, no `COMException` |
| **3d** | — | D1–D4 | `GetEmails: found N`, `GetCalendar: found N` |
| **4** | Add to `test_imports.py`, `test_rate_limiter.py` | PL1–PL3, PC1–PC3 | Log format unchanged |
