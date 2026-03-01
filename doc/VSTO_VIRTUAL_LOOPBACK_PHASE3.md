# VSTO Plugin - Virtual Loopback (Phase 3: Robustness & UX Polish)

**Date**: February 23, 2026
**Status**: ✅ FULLY IMPLEMENTED & CODE REVIEWED - Pending Production Testing
**Scope**: Email threading, payload size guard, per-user priority queue, disconnect resilience, Exchange/O365 MAPI hardening

---

## What Was Added in Phase 3

Phase 3 extends Phase 2 (attachment extraction + server-driven config) with production-grade robustness:

1. **Email conversation threading** — responses appear in the same Outlook conversation as the original via MAPI In-Reply-To/References headers
2. **Payload size guard** — server-driven hard limit prevents oversized WebSocket messages before they are sent; 70% threshold triggers a warning
3. **Per-user priority queue** — FIFO queue per connected user; Outlook Importance flag (Low/Normal/High) sets processing priority; global AI semaphore caps concurrency across all users
4. **Disconnect resilience** — queue survives client disconnect, results stored to temp files, delivered automatically on reconnect; duplicate resends served from cache without reprocessing
5. **Exchange/O365 MAPI hardening** — all silent `catch {}` blocks replaced with named DEBUG logs for diagnostics on Exchange Online

---

## Architecture Changes

```
SEND TIME (extended in Phase 3)
   Plugin                               Server
     |-- virtual_loopback/send_email ->|
     |    original_message_id: ...     |
     |    importance: 0|1|2            |   importance → queue priority
     |                                 |-- _enqueue_email(user_id, priority)
     |                                 |     ↓ dedup check (conversation_id)
     |                                 |     ↓ _UserQueue.PriorityQueue
     |                                 |-- _queue_worker() per user
     |                                 |     ↓ asyncio.Semaphore(N) global
     |                                 |-- _process_loopback_email()
     |                                 |
     |                                 |-- client online?
     |                                 |     YES → ws.send_text()
     |                                 |     NO  → _save_pending_result() (temp file)
     |<-- virtual_loopback/response ---|
          original_message_id: ...

RECONNECT TIME (new in Phase 3)
   Plugin                               Server
     |-- client/identify ------------->|
     |<-- result.config (8 keys) ------|
     |                                 |-- _deliver_pending_results(user_id)
     |<-- virtual_loopback/response ---|   (one message per stored temp file)
          (pending results delivered)

DISCONNECT (new in Phase 3)
   Plugin                               Server
     |                                 |-- queue worker KEEPS RUNNING
     |-- [toast: N requests pending]   |   (queue not emptied on disconnect)
     |-- ScheduleReconnect()           |-- results saved to temp files
```

---

## Feature 1: Email Conversation Threading

Responses now appear grouped with the original email in Outlook's **Show as Conversations** view.

### How it works

**On original email** (`ProcessInterceptedEmail()` — `VirtualLoopbackHandler.cs`):

A stable Message-ID is generated for the original email and set via MAPI:

```csharp
string originalMessageId = $"<arcumai-orig-{requestId}@local>";
try
{
    mail.PropertyAccessor.SetProperty(
        "http://schemas.microsoft.com/mapi/proptag/0x1035001F", // PR_INTERNET_MESSAGE_ID
        originalMessageId);
}
catch (Exception ex)
{
    _logAction("DEBUG", $"MAPI [PR_INTERNET_MESSAGE_ID on original]: {ex.Message}");
}
```

The `original_message_id` value is then included in the WebSocket payload so the server can pass it through to the response unchanged.

**On response email** (`CreateResponseEmail()` — `VirtualLoopbackHandler.cs`):

```csharp
string originalMsgId = responseData["original_message_id"]?.ToString();
if (!string.IsNullOrEmpty(originalMsgId))
{
    try
    {
        responseItem.PropertyAccessor.SetProperty(
            "http://schemas.microsoft.com/mapi/proptag/0x1042001F", // PR_IN_REPLY_TO_ID
            originalMsgId);
        responseItem.PropertyAccessor.SetProperty(
            "http://schemas.microsoft.com/mapi/proptag/0x1039001F", // PR_INTERNET_REFERENCES
            originalMsgId);
    }
    catch (Exception ex)
    {
        _logAction("DEBUG", $"MAPI [PR_IN_REPLY_TO_ID/PR_INTERNET_REFERENCES]: {ex.Message}");
    }
}
```

**Server pass-through** (`_process_loopback_email()` — `bridge.py`):

```python
original_message_id = params.get("original_message_id", "")
# included unchanged in every response_params dict
"original_message_id": original_message_id
```

No server-side processing: the value is extracted from the request and mirrored into the response.

### Verification

1. Send email to ArcumAI, wait for response
2. Outlook → View → Show as Conversations: original + "Re: ..." appear grouped
3. Plugin log (happy path): no `MAPI [PR_INTERNET_MESSAGE_ID on original]` DEBUG entry

---

## Feature 2: Payload Size Guard

Prevents oversized JSON-RPC messages from reaching the WebSocket layer.

### Server-driven configuration

The limit is pushed via the `client/identify` handshake as the **8th config key**:

```json
{
  "max_attachment_size_mb": 25,
  "max_total_attachments_mb": 50,
  "max_payload_size_mb": 30,
  "arcumai_email": "arcumai@arcumai.swiss",
  "arcumai_display_name": "ArcumAI Assistant",
  "loopback_timeout_ms": 3600000,
  "enable_virtual_loopback": true,
  "show_processing_notification": true
}
```

**Server side** (`src/config.py`):
```python
VSTO_MAX_PAYLOAD_MB = int(os.getenv("VSTO_MAX_PAYLOAD_MB", "30"))
```

**Client side** (`PluginConfig.cs`):
```csharp
public int MaxPayloadSizeMB { get; set; }   // default: 30
```

Applied in `ApplyServerConfig()` (`ThisAddIn.cs`):
```csharp
if (cfg["max_payload_size_mb"] != null)
    { _config.MaxPayloadSizeMB = cfg.Value<int>("max_payload_size_mb"); applied++; }
// total = 8
```

### Guard logic (`ProcessInterceptedEmail()` — `VirtualLoopbackHandler.cs`)

The guard runs **immediately after `jsonStr = payload.ToString(Formatting.None)`** — critically, this is **before** `_pendingRequests` registration and before the Block 1 COM-cleanup post. This ordering is intentional:

- If the guard fires on the hard limit path, it posts a **single combined action** (COM cleanup + error response in one `_syncContext.Post`).
- `_pendingRequests[requestId]` is never set, so no ghost entry accumulates on disconnect.
- Normal-path Block 1 (COM cleanup only) is posted only after the guard passes.

```
payload size < 70% of limit  → proceed normally
70% ≤ payload < 100% of limit → WARNING log, proceed
payload ≥ limit               → combined post (cleanup + error reply), return — no _pendingRequests entry, no server round-trip
```

The hard-limit path:
1. Logs `WARNING: "VirtualLoopback: Payload X MB >= limit Y MB — aborted"`
2. Builds an error `JObject` with `subject`, `conversation_id`, `original_message_id`, and a human-readable message
3. Posts a single combined action: `SimulateSentItem` + `CloseComposeWindow` + `DeleteFromOutbox` + `CreateResponseEmail` (exactly once — no double execution possible)
4. Returns before `_pendingRequests[requestId] = ...` — no pending entry, no timeout timer started

### Verification

```
1. Set VSTO_MAX_PAYLOAD_MB=1 in server .env, restart server
2. Attach a 2 MB file, send to ArcumAI
3. Plugin log: "Payload X MB >= limit 1 MB — aborted"
4. Error email in Inbox, no server round-trip in server log
5. Restore VSTO_MAX_PAYLOAD_MB=30
```

---

## Feature 3: Per-User Priority Queue + Global AI Semaphore

### Design

Each connected user gets their own FIFO queue. Outlook's **Importance** flag maps to processing priority:

| Outlook Importance | `OlImportance` value | Queue priority |
|-------------------|---------------------|----------------|
| High | 2 | 0 (processed first) |
| Normal | 1 | 1 |
| Low | 0 | 2 (processed last) |

Priority mapping: `queue_priority = 2 - max(0, min(2, importance))`

A global `asyncio.Semaphore(LOOPBACK_MAX_CONCURRENT)` limits simultaneous AI jobs across **all** users.

### Data structures (`bridge.py`)

```python
@dataclass(order=True)
class _EmailTask:
    priority:   int          # 0=High, 1=Normal, 2=Low  (PriorityQueue ordering)
    sequence:   int          # FIFO tiebreaker within same priority
    user_id:    str  = field(compare=False)
    request_id: str  = field(compare=False)
    params:     dict = field(compare=False)

class _UserQueue:
    def __init__(self, user_id: str):
        self.user_id      = user_id
        self.queue        = asyncio.PriorityQueue()
        self.sequence     = 0
        self.worker_task: asyncio.Task | None = None
```

### Worker lifecycle

- Worker task is created on first email from a user, never cancelled
- Worker keeps running even when the client disconnects (results stored to temp files)
- `asyncio.CancelledError` is the only exit path (application shutdown)

```python
async def _queue_worker(self, user_id: str):
    uq = self._user_queues[user_id]
    while True:
        try:
            task = await uq.queue.get()
            async with self._ai_semaphore:          # global concurrency cap
                await self._process_loopback_email(task.user_id, task.request_id, task.params)
            uq.queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(...)   # never kill the worker
```

### Importance field in payload (`VirtualLoopbackHandler.cs`)

```csharp
["importance"] = (int)mail.Importance   // 0=Low, 1=Normal, 2=High
```

### New server `.env` variables

```bash
LOOPBACK_MAX_CONCURRENT=3    # Max simultaneous AI jobs across all users (default: 3)
```

### Verification

```
1. Send 3 emails: Low importance, Normal, High (set via Outlook Importance flag)
2. Server log: enqueued with priority 2, 1, 0 respectively
3. High-priority email processes first regardless of arrival order
4. Server log: AI semaphore acquired — max 3 concurrent across all users
```

---

## Feature 4: Disconnect Resilience

### Problem

If the VSTO client disconnects while the AI is processing an email, the result was lost. Additionally, if a user resends the same email thinking it was lost, it would be reprocessed unnecessarily.

### Solution

- **Queue survives disconnect**: worker task keeps running; `disconnect()` never cancels the worker
- **Temp file storage**: when processing completes and the client is offline, the result is saved to disk
- **Delivery on reconnect**: after the `client/identify` handshake, pending results are delivered automatically
- **Deduplication**: if the same `conversation_id` already has a stored result, it is delivered immediately without reprocessing

### Temp file format

Files are stored in `PENDING_RESULTS_DIR` (default: `temp/pending_results/`):

```
arcumai_pending_{user_id}_{request_id}.json
```

```json
{
  "user_id": "nicol",
  "request_id": "abc-123",
  "conversation_id": "AAQkADM0...",
  "created_at": "2026-02-23T14:30:00+00:00",
  "response": {
    "request_id": "abc-123",
    "subject": "Re: Analyze contract",
    "response_text": "...",
    "original_message_id": "<arcumai-orig-abc-123@local>"
  }
}
```

### TTL

Results older than `PENDING_RESULT_TTL_HOURS` (default: 48h) are automatically deleted when the client reconnects. This prevents stale responses from appearing days later.

### `_enqueue_email()` — deduplication check

```python
async def _enqueue_email(self, user_id, request_id, params, importance):
    conv_id = params.get("conversation_id", "")

    # Guard: only check dedup when conv_id is non-empty.
    # An empty conv_id would match every other empty-conv_id result (false positive).
    cached = self._find_pending_result(user_id, conv_id) if conv_id else None
    if cached:
        # Deliver immediately — no reprocessing
        ws = self.active_connections.get(user_id)
        if ws:
            push = {"jsonrpc": "2.0", "method": "virtual_loopback/response",
                    "params": cached["response"]}
            await ws.send_text(json.dumps(push))
            self._delete_pending_result(user_id, conv_id)
        return

    priority = 2 - max(0, min(2, importance))   # High(2)→0, Normal(1)→1, Low(0)→2

    if user_id not in self._user_queues:
        uq = _UserQueue(user_id)
        self._user_queues[user_id] = uq
        uq.worker_task = asyncio.create_task(self._queue_worker(user_id))
    else:
        uq = self._user_queues[user_id]
        # Restart dead worker (e.g. cancelled by external asyncio shutdown signal)
        if uq.worker_task is None or uq.worker_task.done():
            log.warning(f"Queue[{user_id}]: worker not running — restarting")
            uq.worker_task = asyncio.create_task(self._queue_worker(user_id))

    uq.sequence += 1
    await uq.queue.put(_EmailTask(priority=priority, sequence=uq.sequence,
                                   user_id=user_id, request_id=request_id, params=params))
```

### `disconnect()` change

```python
def disconnect(self, user_id: str):
    self.active_connections.pop(user_id, None)
    self.client_types.pop(user_id, None)
    # MCP pending requests cancelled (unchanged)

    # Queue: worker keeps running — results stored to temp files
    if user_id in self._user_queues:
        remaining = self._user_queues[user_id].queue.qsize()
        if remaining > 0:
            log.info(f"Queue[{user_id}]: client disconnected with {remaining} item(s) still "
                     f"queued — continuing processing, results stored to temp files")
    # NOTE: do NOT pop from _user_queues or cancel the worker
```

### `NotifyPendingOnDisconnect()` (`VirtualLoopbackHandler.cs`)

Called from `ThisAddIn.OnDisconnected()` — shows a Windows toast if there are pending requests, but **does not inject error emails**:

```csharp
public void NotifyPendingOnDisconnect()
{
    int count = _pendingRequests.Count;
    if (count == 0) return;
    _logAction("WARNING",
        $"VirtualLoopback: {count} request(s) still processing — results will be delivered on reconnect");
    ShowToastNotification("ArcumAI",
        $"{count} request(s) still processing.\nResults will be delivered when the connection is restored.");
}
```

`_pendingRequests` is the existing `ConcurrentDictionary<string, PendingRequest>` tracking in-flight requests by `request_id`. Its 1-hour timeout (`LoopbackTimeoutMs`) remains the safety net if the server also goes down permanently.

### `OnDisconnected()` (`ThisAddIn.cs`)

```csharp
private void OnDisconnected(object sender, EventArgs e)
{
    if (_isShuttingDown) return;
    Log("WARNING", "Connection lost from server");
    StopHeartbeat();
    _loopbackHandler?.NotifyPendingOnDisconnect();   // toast only, no error emails
    ScheduleReconnect();
}
```

### `_deliver_pending_results()` flow

Called via `asyncio.create_task()` after the `client/identify` response is sent:

```
0. Recover any abandoned .delivering files (from a server crash between rename and delete)
      → rename arcumai_pending_{user_id}_*.delivering back to .json
1. Scan temp/pending_results/ for arcumai_pending_{user_id}_*.json (sorted)
2. For each file:
   a. Parse created_at — if age > PENDING_RESULT_TTL_HOURS → delete, skip
   b. Atomically rename .json → .delivering
      • If rename fails (FileNotFoundError/OSError) → another coroutine claimed it → skip
      • The dedup scan in _enqueue_email() only globs *.json, so it cannot see .delivering files
   c. Re-fetch ws from active_connections (NOT cached at function start — avoids stale-ws errors
      if client reconnects/disconnects again mid-delivery loop)
   d. Send virtual_loopback/response push notification via WebSocket
   e. Delete .delivering file
   f. If client disconnected mid-loop: rename .delivering back to .json, stop
3. Log: "Pending results for 'nicol': 2 delivered, 1 expired"
```

The atomic rename in step (b) prevents a race condition where `_enqueue_email()` calls `_find_pending_result()` (dedup check) at the same moment `_deliver_pending_results()` is mid-send — without the rename the dedup would see the file, deliver it, then the concurrent delivery would also send it (duplicate response email).

### New server `.env` variables

```bash
PENDING_RESULT_TTL_HOURS=48           # Discard temp results older than this (default: 48)
PENDING_RESULTS_DIR=temp/pending_results  # Temp file directory (default)
```

### Verification

```
1. Send email to ArcumAI → kill server mid-processing
2. Plugin: toast "1 request still processing — results will be delivered on reconnect"
3. No error email injected in Inbox
4. Restart server → plugin reconnects → identify handshake
5. Server log: "Pending results for 'nicol': 1 delivered, 0 expired"
6. Response email appears in Inbox

Deduplication test:
7. Before step 4, resend the same original email
8. Server: finds cached result → delivers immediately → no reprocessing
9. Response appears again (fast, no AI round-trip)

TTL test:
10. Manually set created_at to >48h ago in a temp file
11. Reconnect → server deletes it → "0 delivered, 1 expired"
```

---

## Feature 5: Exchange/O365 MAPI Hardening

All `catch { }` (empty / silent) blocks in `VirtualLoopbackHandler.cs` were replaced with named DEBUG-level log entries. This is critical for Exchange Online where MAPI property setters often throw `COMException` silently on managed profiles.

### Hardened locations

| Method | MAPI Property | Log label |
|--------|--------------|-----------|
| `CreateResponseEmail()` | `PR_SENT_REPRESENTING_NAME` / `PR_SENT_REPRESENTING_EMAIL_ADDRESS` | `MAPI [PR_SENT_REPRESENTING]` |
| `CreateResponseEmail()` | `PR_MESSAGE_DELIVERY_TIME` | `MAPI [PR_MESSAGE_DELIVERY_TIME]` |
| `CreateResponseEmail()` | `PR_INTERNET_MESSAGE_ID` (on response) | `MAPI [PR_INTERNET_MESSAGE_ID]` |
| `CreateResponseEmail()` (new) | `PR_IN_REPLY_TO_ID` / `PR_INTERNET_REFERENCES` | `MAPI [PR_IN_REPLY_TO_ID/PR_INTERNET_REFERENCES]` |
| `ProcessInterceptedEmail()` (new) | `PR_INTERNET_MESSAGE_ID` (on original) | `MAPI [PR_INTERNET_MESSAGE_ID on original]` |
| `SimulateSentItem()` | `PR_CLIENT_SUBMIT_TIME` | `MAPI [PR_CLIENT_SUBMIT_TIME]` |

**Pattern applied uniformly:**

```csharp
catch (Exception ex) { _logAction("DEBUG", $"MAPI [PR_SENT_REPRESENTING]: {ex.Message}"); }
```

All MAPI property failures are non-fatal: the email is still sent/received without the optional metadata. The DEBUG label makes it trivial to filter plugin logs for Exchange-specific issues.

### Verification

```
1. Run plugin against Exchange Online (O365) account
2. Filter plugin.log for "MAPI ["
3. Each entry identifies exactly which property failed and with which error
4. No empty swallowed exceptions; no silent behavioral differences
```

---

## Updated WebSocket Protocol

### `virtual_loopback/send_email` — Phase 3 additions

New fields added in Phase 3:

```json
{
  "jsonrpc": "2.0",
  "method": "virtual_loopback/send_email",
  "id": "<uuid>",
  "params": {
    "subject": "Analyze contract",
    "body": "Please review...",
    "conversation_id": "AAQkADM0...",
    "timestamp": "2026-02-23T10:00:00Z",
    "has_attachments": true,
    "cc_recipients": [],
    "attachments": [...],
    "skipped_attachments": [...],
    "original_message_id": "<arcumai-orig-abc-123@local>",
    "importance": 2
  }
}
```

### `virtual_loopback/response` — Phase 3 additions

```json
{
  "jsonrpc": "2.0",
  "method": "virtual_loopback/response",
  "params": {
    "request_id": "abc-123",
    "subject": "Re: Analyze contract",
    "conversation_id": "AAQkADM0...",
    "response_text": "...",
    "original_message_id": "<arcumai-orig-abc-123@local>"
  }
}
```

`original_message_id` is echoed back from the request params — the server does not process it, only passes it through.

---

## Files Changed in Phase 3

| File | Changes |
|------|---------|
| `Core/VirtualLoopbackHandler.cs` | `original_message_id` generation + MAPI set on original; `importance` in payload; payload size guard; In-Reply-To/References on response; `NotifyPendingOnDisconnect()`; all `catch {}` → named DEBUG logs |
| `ThisAddIn.cs` | `ApplyServerConfig()` handles 8th key `max_payload_size_mb` (total=8); `OnDisconnected()` calls `NotifyPendingOnDisconnect()` |
| `Core/PluginConfig.cs` | Added `MaxPayloadSizeMB` property (default: 30) with `SetDefaults()` and `LoadFromAppConfig()` entries |
| `src/bridge.py` | `_EmailTask` + `_UserQueue` dataclasses; `_enqueue_email()` with dedup; `_queue_worker()` with `_ai_semaphore`; queue survives `disconnect()`; `_save/find/delete/deliver_pending_results()`; `original_message_id` pass-through; 8-key `_build_client_config()`; `_deliver_pending_results()` called after identify |
| `src/config.py` | Added `VSTO_MAX_PAYLOAD_MB`; new section `LOOPBACK QUEUE & RESILIENCE` with `LOOPBACK_MAX_CONCURRENT`, `PENDING_RESULT_TTL_HOURS`, `PENDING_RESULTS_DIR` |

**Code review fixes (applied after initial implementation):**

| File | Fix | Finding |
|------|-----|---------|
| `Core/VirtualLoopbackHandler.cs` | Moved payload guard before `_pendingRequests` registration and Block 1 post | Bug 1 (Critical) + Bug 2 (High) |
| `Core/VirtualLoopbackHandler.cs` | Added `original_message_id` to all-skipped error response | Issue 6 |
| `Core/VirtualLoopbackHandler.cs` | Added `OriginalMessageId` to `LoopbackRequest`; pass-through in `CreateTimeoutResponse()` | Issue 7 |
| `ThisAddIn.cs` | `"WARNING"` log level when `applied < total` in `ApplyServerConfig()` | Issue 10 |
| `src/bridge.py` | Added `from __future__ import annotations` | Bug 4 |
| `src/bridge.py` | Guard dedup check with `if conv_id else None` | Bug 3 |
| `src/bridge.py` | Dead worker restart check (`worker_task.done()`) in `_enqueue_email()` | Issue 9 |
| `src/bridge.py` | Rewrote `_deliver_pending_results()` with atomic rename, per-iteration ws re-fetch, `.delivering` recovery | Issue 5 + Issue 8 |

---

## Code Review & Fixes (Post-Implementation)

A code review was performed after the initial Phase 3 implementation. Ten findings were identified and fixed. Severity: **Critical** (1), **High** (1), **Medium** (2), **Low** (4), **Info** (2).

---

### Bug 1 — Critical: Double COM operations on payload guard path

**File**: `VirtualLoopbackHandler.cs` — `ProcessInterceptedEmail()`

**Root cause**: Block 1 (`SimulateSentItem + CloseComposeWindow + DeleteFromOutbox`) was posted via `_syncContext.Post()` before the payload guard check. When the guard fired on the hard-limit path, it posted a second combined block containing the same 3 operations + `CreateResponseEmail`. Net result: two Sent Items copies, two compose-window close attempts, Outbox deleted twice.

**Fix**: Moved the payload guard **before** `_pendingRequests` registration and before Block 1. The guard path posts a single combined action (cleanup + error response). Block 1 is posted only after the guard passes (normal path). The guard's early `return` ensures no overlap.

---

### Bug 2 — High: `_pendingRequests` ghost entry on payload guard abort

**File**: `VirtualLoopbackHandler.cs` — `ProcessInterceptedEmail()`

**Root cause**: `_pendingRequests[requestId] = new LoopbackRequest { ... }` was set before the payload guard check. If the guard fired, the `return` left the entry in the dictionary permanently — the timeout timer was never started, so the entry was never cleaned up. On disconnect, `NotifyPendingOnDisconnect()` would show a ghost toast for a request that was already rejected locally.

**Fix**: Same structural fix as Bug 1. `_pendingRequests[requestId] = ...` is now set after the guard passes — the guard's early `return` never touches the dictionary. No explicit `TryRemove` needed.

---

### Bug 3 — Medium: Empty `conversation_id` causes false-positive deduplication

**File**: `src/bridge.py` — `_enqueue_email()`

**Root cause**: `_find_pending_result(user_id, "")` matches any stored result that also has `conversation_id = ""`. Two unrelated emails both lacking a ConversationID would be mistakenly treated as the same email — the second one would get the first email's cached response.

**Fix**:
```python
# Before
cached = self._find_pending_result(user_id, conv_id)

# After
cached = self._find_pending_result(user_id, conv_id) if conv_id else None
```

---

### Bug 4 — Low: Python 3.9 incompatibility (`X | None` type syntax)

**File**: `src/bridge.py` — module-level and class-level type annotations

**Root cause**: `dict | None` and `asyncio.Task | None` are runtime syntax errors on Python < 3.10 unless `from __future__ import annotations` is present (which defers annotation evaluation).

**Fix**: Added `from __future__ import annotations` as the first line of `bridge.py`. This is a zero-behavior-change addition that makes all annotations strings at runtime, enabling Python 3.9 compatibility.

---

### Issue 5 — Medium: Race condition in `_deliver_pending_results()` vs `_enqueue_email()`

**File**: `src/bridge.py`

**Root cause**: If `_enqueue_email()` ran its dedup check (`_find_pending_result`) at the same moment `_deliver_pending_results()` was between reading the file and sending the WebSocket message, both would see the `.json` file, both would attempt to deliver the result — producing a duplicate response email in the user's Inbox.

**Fix**: Atomic OS-level rename `.json` → `.delivering` before each `await ws.send_text()`. Since `_find_pending_result` only globs `*.json`, a file mid-delivery is invisible to the dedup check. On server crash between rename and unlink, the `.delivering` files are recovered (renamed back to `.json`) at the start of the next `_deliver_pending_results()` call.

---

### Issue 6 — Low: All-skipped error response missing `original_message_id`

**File**: `VirtualLoopbackHandler.cs` — `ProcessInterceptedEmail()`

**Root cause**: When all attachments were skipped and an error response was injected locally, the `errorResponse` JObject did not include `original_message_id`. `CreateResponseEmail()` therefore could not set PR_IN_REPLY_TO_ID on the response — the error email was not threaded in the Outlook conversation.

**Fix**: Added `["original_message_id"] = originalMessageId` to the all-skipped `errorResponse` JObject.

---

### Issue 7 — Low: Timeout response missing `original_message_id`

**File**: `VirtualLoopbackHandler.cs` — `CreateTimeoutResponse()`

**Root cause**: The timeout error email (generated 1 hour after send if no server response arrives) did not include `original_message_id`. Like Issue 6, it appeared in the Inbox unthreaded.

**Fix**:
1. Added `public string OriginalMessageId { get; set; }` to the `LoopbackRequest` class.
2. Set it during `_pendingRequests[requestId] = new LoopbackRequest { ..., OriginalMessageId = originalMessageId }`.
3. Updated `CreateTimeoutResponse()` to include `["original_message_id"] = req.OriginalMessageId ?? ""`.

---

### Issue 8 — Low: Stale `ws` reference in `_deliver_pending_results()`

**File**: `src/bridge.py`

**Root cause**: The original implementation fetched `ws = self.active_connections.get(user_id)` once before the delivery loop. If the client disconnected between two deliveries (mid-loop), the stale `ws` reference would throw a WebSocket error on subsequent sends.

**Fix**: `ws` is now re-fetched on **every iteration** of the loop:
```python
ws = self.active_connections.get(user_id)   # inside the for-loop
```
If `ws` is `None` at any point, delivery pauses: the `.delivering` file is renamed back to `.json` and the loop breaks, leaving remaining files for the next reconnect.

---

### Issue 9 — Info: Dead queue worker not restarted

**File**: `src/bridge.py` — `_enqueue_email()`

**Root cause**: The worker task could be killed by an external `asyncio.CancelledError` (e.g. during application shutdown signal handling). A new email for the same user would find the `_user_queues` entry already existing but with a finished `worker_task` — the email would be enqueued but never processed.

**Fix**: Before enqueuing, check `worker_task.done()` in the `else` branch and restart if needed:
```python
else:
    uq = self._user_queues[user_id]
    if uq.worker_task is None or uq.worker_task.done():
        log.warning(f"Queue[{user_id}]: worker not running — restarting")
        uq.worker_task = asyncio.create_task(self._queue_worker(user_id))
```

---

### Issue 10 — Info: Partial config logs at wrong level

**File**: `ThisAddIn.cs` — `ApplyServerConfig()`

**Root cause**: When fewer than `total` config keys are received (e.g. server is running an older version), the log message was written at `"DEBUG"` level. A misconfigured handshake (wrong `max_payload_size_mb`, wrong email address, etc.) could go unnoticed in production logs.

**Fix**: Changed to `"WARNING"` when `applied < total`:
```csharp
Log(applied == total ? "INFO" : "WARNING",
    $"Config sync applied: {applied}/{total} keys — ...");
```

---

## Server Configuration (`.env`)

Full set of VSTO-related server variables after Phase 3:

```bash
# Pushed to VSTO client at connect time (client/identify handshake)
VSTO_MAX_ATTACHMENT_MB=25          # Per-file attachment size limit (MB)
VSTO_MAX_TOTAL_MB=50               # Total attachments size limit (MB)
VSTO_MAX_PAYLOAD_MB=30             # WebSocket payload hard limit (MB)
VSTO_ARCUMAI_EMAIL=arcumai@arcumai.swiss
VSTO_ARCUMAI_DISPLAY_NAME=ArcumAI Assistant
VSTO_LOOPBACK_TIMEOUT_MS=3600000   # 1 hour — for large documents
VSTO_ENABLE_VIRTUAL_LOOPBACK=true
VSTO_SHOW_NOTIFICATION=true

# Server-side queue & resilience (not pushed to client)
LOOPBACK_MAX_CONCURRENT=3          # Max simultaneous AI jobs across all users
PENDING_RESULT_TTL_HOURS=48        # Discard temp results older than this
PENDING_RESULTS_DIR=temp/pending_results
```

---

## Startup / Reconnect Log Sequence (Phase 3)

```
[INFO]  Connecting to ws://localhost:8080/ws/outlook/nicol (attempt 1)...
[INFO]  Connected successfully
[INFO]  Sent client/identify to server
[INFO]  Config sync received from server:
        {
          "max_attachment_size_mb": 25,
          "max_total_attachments_mb": 50,
          "max_payload_size_mb": 30,
          "arcumai_email": "arcumai@arcumai.swiss",
          "arcumai_display_name": "ArcumAI Assistant",
          "loopback_timeout_ms": 3600000,
          "enable_virtual_loopback": true,
          "show_processing_notification": true
        }
[INFO]  Config sync applied: 8/8 keys — MaxAttachment=25MB, Email=arcumai@arcumai.swiss, Enabled=True

# Reconnect with pending results:
[INFO]  Pending results for 'nicol': 2 delivered, 0 expired
```

Disconnect with active requests:
```
[WARNING] Connection lost from server
[WARNING] VirtualLoopback: 1 request(s) still processing — results will be delivered on reconnect
# → Windows toast shown to user
# → queue worker continues server-side
# → result saved to temp/pending_results/arcumai_pending_nicol_abc-123.json
```

---

## Testing Checklist

### T1: Email conversation threading
```
1. Send email To: arcumai@arcumai.swiss
2. Wait for AI response in Inbox
3. Outlook → View menu → Show as Conversations (toggle on)
4. Original + "Re: ..." appear grouped in same conversation thread
5. Plugin log (happy path): no "MAPI [PR_INTERNET_MESSAGE_ID on original]" entry
```

### T2: Payload size guard — hard limit
```
1. Set VSTO_MAX_PAYLOAD_MB=1 in server .env, restart server
2. Plugin reconnects → config sync: "max_payload_size_mb": 1
3. Attach a 2 MB file, send to ArcumAI
4. Plugin log: "VirtualLoopback: Payload X MB >= limit 1 MB — aborted"
5. Error email in Inbox explaining the limit
6. Server log: no "virtual_loopback/send_email" received (no round-trip)
7. Restore VSTO_MAX_PAYLOAD_MB=30, restart server
```

### T3: Payload size guard — warning only (70% threshold)
```
1. Set VSTO_MAX_PAYLOAD_MB=10 in server .env
2. Attach a 7.5 MB file (>70% of 10 MB)
3. Plugin log: "VirtualLoopback: Large payload warning: X MB (limit: 10 MB)"
4. Email still sent to server and processed normally
```

### T4: Priority queue — processing order
```
1. Send 3 emails in quick succession:
   - Email A: Importance = Low (set via Outlook Options)
   - Email B: Importance = Normal
   - Email C: Importance = High
2. Server log shows enqueue order (A then B then C by arrival)
3. Processing order: C first (priority 0), then B (priority 1), then A (priority 2)
4. Server log: "Queue[nicol]: processing seq=3 priority=0" first
```

### T5: Global concurrency cap
```
1. Set LOOPBACK_MAX_CONCURRENT=1 in server .env
2. Send 3 emails simultaneously from 2 different users
3. Server log: only 1 "_ai_semaphore acquired" at a time
4. Other workers wait until semaphore is released
```

### T6: Disconnect resilience — basic
```
1. Send email to ArcumAI
2. Immediately kill server (Ctrl+C)
3. Plugin: Windows toast "1 request(s) still processing — results will be delivered..."
4. No error email in Inbox
5. Restart server
6. Plugin reconnects, identify handshake completes
7. Server log: "Pending results for 'nicol': 1 delivered, 0 expired"
8. AI response email appears in Inbox
```

### T7: Disconnect resilience — deduplication
```
1. Send email to ArcumAI
2. Kill server before response arrives
3. Resend the same email (thinking it was lost)
4. Restart server
5. Server log: "Queue[nicol]: duplicate request conv_id=... — delivering cached result"
6. Response appears once (not twice), without reprocessing the AI
```

### T8: Disconnect resilience — TTL expiry
```
1. Manually edit a temp file: set "created_at" to 3 days ago (>48h)
2. Plugin reconnects, identify handshake
3. Server log: "Pending results for 'nicol': 0 delivered, 1 expired"
4. Expired file deleted, no response delivered
```

### T9: Exchange/O365 MAPI hardening
```
1. Configure plugin against an Exchange Online (O365) mailbox
2. Send email to ArcumAI, wait for response
3. Filter plugin.log for "MAPI ["
4. Each MAPI failure shows property name and error message (not silently swallowed)
5. Response email still arrives despite any MAPI property failures
```

### T10: Config sync — 8 keys
```
1. Start server, restart Outlook
2. Log: "Config sync applied: 8/8 keys — MaxAttachment=25MB, ..."
3. Change VSTO_MAX_PAYLOAD_MB=5 in .env, restart server
4. Plugin reconnects → "max_payload_size_mb": 5 received
5. Test T2 with 6 MB payload → blocked correctly
```

### T11: Payload guard — no double Sent Items (Bug 1 regression check)
```
1. Set VSTO_MAX_PAYLOAD_MB=1, restart server, reconnect
2. Attach 2 MB file, send to ArcumAI
3. Check Sent Items folder: exactly ONE copy of the sent email
4. Error reply appears in Inbox once (not twice)
5. Server log: no "virtual_loopback/send_email" entry (no round-trip)
```

### T12: Payload guard — no ghost pending request (Bug 2 regression check)
```
1. Set VSTO_MAX_PAYLOAD_MB=1, restart server, reconnect
2. Attach 2 MB file, send to ArcumAI
3. Immediately kill server (Ctrl+C)
4. Plugin: NO toast about pending requests (guard aborted before _pendingRequests was set)
5. No timeout error email after 1 hour
```

### T13: Empty conversation_id — no false dedup (Bug 3 regression check)
```
1. Send two different emails from a mail client that does not set ConversationID
   (both arrive with empty conversation_id)
2. First email processed normally
3. Second email processed normally — NOT served the first email's cached response
4. Two distinct AI responses appear in Inbox
```

### T14: Deliver pending results — no duplicate delivery (Issue 5 regression check)
```
1. Send email to ArcumAI, kill server before response
2. Restart server → plugin reconnects
3. Immediately send the same email again (triggers _enqueue_email dedup check)
4. Only ONE response email in Inbox (not two)
5. Server log: either "delivering cached result" (dedup path) or normal processing —
   but not both, and not two responses
```

---

## Summary

**Phase 3 Status**: ✅ **FULLY IMPLEMENTED** — February 23, 2026

### What Was Added

**Phase 3 features:**
- ✅ Email conversation threading (MAPI PR_IN_REPLY_TO_ID + PR_INTERNET_REFERENCES)
- ✅ Payload size guard: server-driven limit, hard block + 70% warning, local error reply
- ✅ `MaxPayloadSizeMB` as 8th server-pushed config key (via `client/identify`)
- ✅ Per-user `asyncio.PriorityQueue` — FIFO with Outlook Importance as priority source
- ✅ Global `asyncio.Semaphore` capping concurrent AI jobs across all users
- ✅ Queue survives client disconnect — worker never cancelled
- ✅ Temp file storage for results when client is offline (`temp/pending_results/`)
- ✅ Automatic result delivery on reconnect (after identify handshake)
- ✅ Deduplication: resent email served from cache, no reprocessing
- ✅ TTL-based cleanup of stale temp results (default: 48h)
- ✅ `NotifyPendingOnDisconnect()` — Windows toast, no error email injection
- ✅ Exchange/O365 MAPI hardening: all silent catches → named DEBUG logs

**Code review fixes (10 findings, all resolved):**
- ✅ Bug 1 (Critical): Payload guard moved before Block 1 post — eliminates double COM operations and double Sent Items on hard-limit path
- ✅ Bug 2 (High): Payload guard moved before `_pendingRequests` registration — eliminates ghost entries and spurious disconnect toasts
- ✅ Bug 3 (Medium): Dedup skipped when `conv_id` is empty — prevents false-positive cache hits between unrelated emails
- ✅ Bug 4 (Low): `from __future__ import annotations` added — Python 3.9 compatibility for `X | None` type syntax
- ✅ Issue 5 (Medium): Atomic `.json` → `.delivering` rename before each `await` — prevents race condition causing duplicate response emails
- ✅ Issue 6 (Low): `original_message_id` added to all-skipped error response — attachment rejection emails are now threaded
- ✅ Issue 7 (Low): `OriginalMessageId` stored in `LoopbackRequest`; timeout response now includes it — timeout emails are now threaded
- ✅ Issue 8 (Low): `ws` re-fetched per iteration in `_deliver_pending_results()` — prevents stale-ws errors on mid-loop disconnect
- ✅ Issue 9 (Info): Dead worker restart check (`worker_task.done()`) in `_enqueue_email()` — queue recovers from external cancellation
- ✅ Issue 10 (Info): Partial config now logs at WARNING level — misconfigured handshake visible in production logs

### Key Architectural Decisions

**Queue never dies on disconnect.** The server keeps processing even if the VSTO client is offline. This decouples AI processing time from network stability and lets users send emails then close Outlook — responses arrive when they next connect.

**Deduplication by `conversation_id`.** Outlook assigns a stable `ConversationID` to emails in the same thread. Using it as the dedup key means a resent email (same subject, same thread) is recognized and served from cache without any AI round-trip.

**Server is still the single source of truth.** `MaxPayloadSizeMB` follows the same pattern established in Phase 2: defined in `src/config.py`, pushed via `client/identify`, applied in `ApplyServerConfig()`. The C# default (30 MB) is only used if the handshake never arrives.
