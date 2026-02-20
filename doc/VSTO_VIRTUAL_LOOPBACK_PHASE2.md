# VSTO Plugin - Virtual Loopback (Phase 2: Attachments + Server-Driven Config)

**Date**: February 20, 2026
**Status**: ✅ FULLY IMPLEMENTED & TESTED - Production Ready
**Scope**: Attachment extraction, contact creation, server-driven config handshake, STA threading hardening

---

## What Was Added in Phase 2

Phase 2 extends Phase 1 (MVP email interception) with:

1. **Attachment extraction** — real files base64-encoded and sent to server; inline images skipped
2. **Outlook contact creation** — ArcumAI automatically added to address book on startup
3. **Server-driven config handshake** — server is single source of truth for behavioral config
4. **STA threading hardening** — all COM operations safely marshalled to Outlook's main thread
5. **All-skipped error path** — attachment size enforcement locally, no server round-trip for invalid payloads

---

## Architecture Changes

```
CONNECT TIME (new in Phase 2)
   Plugin                               Server
     |-- client/identify ------------->|
     |    client_type: "vsto_outlook"  |
     |    client_version: "2.0"        |
     |                                 |-- looks up VSTO_* config constants
     |<-- result.config ---------------|
     |    max_attachment_size_mb: 25   |
     |    arcumai_email: ...           |
     |    enable_virtual_loopback: ... |
     |    ...                          |
     |-- ApplyServerConfig() (C#)      |
     |-- EnsureContactExists() (COM)   |

SEND TIME (extended in Phase 2)
   Plugin                               Server
     |-- virtual_loopback/send_email ->|
     |    attachments: [               |
     |      { file_name, content_type, |
     |        size_bytes,              |
     |        content_base64 }         |
     |    ]                            |
     |    skipped_attachments: [...]   |
     |                                 |-- _process_attachment() per file
     |                                 |-- PDF/DOCX/XLSX/TXT/MSG/EML decode
     |<-- virtual_loopback/response ---|
```

---

## Feature 1: Attachment Extraction

### `ExtractAttachments()` — `VirtualLoopbackHandler.cs`

Called inside `ProcessInterceptedEmail()` before building the JSON-RPC payload.

**What it does:**

| Step | Detail |
|------|--------|
| Inline detection | Reads MAPI property `PR_ATTACH_CONTENT_ID` (`0x3712001F`). If set, the attachment is an embedded image (signature logo, etc.) and is silently skipped. |
| Per-file size limit | `att.Size > MaxAttachmentSizeMB × 1024²` → file added to `skippedAttachments` list |
| Total size limit | Running `totalBytes + fileSize > MaxTotalAttachmentsMB × 1024²` → file added to `skippedAttachments` |
| Extraction | `att.SaveAsFile(tempPath)` → `File.ReadAllBytes()` → `Convert.ToBase64String()` |
| MIME type | `GetMimeType(fileName)` maps extension to MIME string |
| Cleanup | Temp file deleted in `finally` block; COM object released via `Marshal.ReleaseComObject()` |

**MIME type mapping:**

| Extension | MIME Type |
|-----------|-----------|
| `.pdf` | `application/pdf` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.txt` | `text/plain` |
| `.csv` | `text/csv` |
| `.msg` | `application/vnd.ms-outlook` |
| `.eml` | `message/rfc822` |
| `.png/.jpg/.gif/.tiff` | `image/*` |
| other | `application/octet-stream` |

**Returns:** `(JArray extracted, List<string> skipped)` — both are included in the payload.

### All-Skipped Early Return

If the mail had attachments but **all** were rejected by size limits (i.e., `extracted.Count == 0` but `skipped.Count > 0`), the plugin **injects a local error reply without a server round-trip**:

```csharp
if (hasAttachments && attachmentsArray.Count == 0)
{
    // Build error JObject with bullet list of skipped files
    // Uses _config.MaxAttachmentSizeMB / MaxTotalAttachmentsMB (from server handshake)
    // Calls SimulateSentItem + CloseComposeWindow + DeleteFromOutbox + CreateResponseEmail
    // via _syncContext.Post() — no await, no server
    return;
}
```

The error reply lists each rejected file with its actual size and the applicable limit. Limits in the message are accurate because they come from the server config applied at connect time.

---

## Feature 2: Outlook Contact Creation

### `EnsureContactExists()` — `VirtualLoopbackHandler.cs`

Creates an Outlook contact for `ArcumAI` in the user's default Contacts folder so the address appears in the address book and Outlook resolves it without warnings.

**Called from two places:**

1. `ThisAddIn_Startup()` — immediately at plugin load (using `PluginConfig` defaults)
2. `ApplyServerConfig()` → `ApplyComChanges()` (via `_syncContext.Post()`) — after server handshake updates `ArcumAIEmailAddress` / `ArcumAIDisplayName`

**Logic:**
- Searches contacts by `[Email1Address]` filter first
- If found: logs `DEBUG` and returns (idempotent)
- If not found: creates `ContactItem` with `FullName`, `Email1Address`, `CompanyName = "ArcumAI"`, saves it

**Contact fields set:**

| Field | Value (from config) |
|-------|---------------------|
| `FullName` | `ArcumAIDisplayName` |
| `Email1Address` | `ArcumAIEmailAddress` |
| `Email1DisplayName` | `ArcumAIDisplayName` |
| `CompanyName` | `"ArcumAI"` |
| `Body` | Description string |

---

## Feature 3: Server-Driven Config Handshake

### Motivation

Prior to Phase 2, behavioral config (attachment limits, email address, timeouts) was duplicated: defined as C# defaults in `PluginConfig.cs` and again as constants in `src/config.py`. The Python side had no mechanism to enforce limits; both copies drifted independently.

**Design principle**: Server is the single source of truth. Clients identify themselves at connect time; server pushes the relevant config block for that client type.

### Protocol

#### Step 1 — Plugin → Server: `client/identify`

Sent immediately after WebSocket connect, before any other messages.

```json
{
  "jsonrpc": "2.0",
  "method": "client/identify",
  "id": "<uuid>",
  "params": {
    "client_type": "vsto_outlook",
    "client_version": "2.0"
  }
}
```

#### Step 2 — Server → Plugin: identify response

```json
{
  "jsonrpc": "2.0",
  "id": "<same-uuid>",
  "result": {
    "status": "ok",
    "config": {
      "max_attachment_size_mb": 25,
      "max_total_attachments_mb": 50,
      "arcumai_email": "arcumai@arcumai.swiss",
      "arcumai_display_name": "ArcumAI Assistant",
      "loopback_timeout_ms": 3600000,
      "enable_virtual_loopback": true,
      "show_processing_notification": true
    }
  }
}
```

The `config` block is scoped by `client_type`. A future mobile or web client would receive a different set of keys. Unknown keys are silently ignored by the client (forward compatible).

### What is Server-Driven vs Client-Local

| Property | Side | Reason |
|----------|------|--------|
| `ServerUrl`, `ReconnectDelayMs`, `MaxReconnectAttempts` | **Client-local** | Needed before server connection exists |
| `ConnectionTimeoutMs`, `RequestTimeoutMs`, `HeartbeatIntervalMs` | **Client-local** | Transport-level settings |
| `EnableLogging`, `LogLevel`, `LogFilePath` | **Client-local** | Must work before any server contact |
| `MaxAttachmentSizeMB`, `MaxTotalAttachmentsMB` | **Server-driven** | Server defines what it can process |
| `ArcumAIEmailAddress`, `ArcumAIDisplayName` | **Server-driven** | Plugin identity tied to server identity |
| `LoopbackTimeoutMs` | **Server-driven** | Server knows its own processing latency |
| `EnableVirtualLoopback`, `ShowProcessingNotification` | **Server-driven** | Feature flags controlled centrally |

### Server-Side Implementation

#### `src/config.py` — new `VSTO_*` constants

```python
# --- SERVER-PUSHED CLIENT CONFIG ---
VSTO_MAX_ATTACHMENT_MB        = int(os.getenv("VSTO_MAX_ATTACHMENT_MB", "25"))
VSTO_MAX_TOTAL_MB             = int(os.getenv("VSTO_MAX_TOTAL_MB", "50"))
VSTO_ARCUMAI_EMAIL            = os.getenv("VSTO_ARCUMAI_EMAIL", "arcumai@arcumai.swiss")
VSTO_ARCUMAI_DISPLAY_NAME     = os.getenv("VSTO_ARCUMAI_DISPLAY_NAME", "ArcumAI Assistant")
VSTO_LOOPBACK_TIMEOUT_MS      = int(os.getenv("VSTO_LOOPBACK_TIMEOUT_MS", "3600000"))
VSTO_ENABLE_VIRTUAL_LOOPBACK  = os.getenv("VSTO_ENABLE_VIRTUAL_LOOPBACK", "true").lower() == "true"
VSTO_SHOW_NOTIFICATION        = os.getenv("VSTO_SHOW_NOTIFICATION", "true").lower() == "true"
```

All values are overridable via environment variables or `.env` file.

#### `src/bridge.py` — `client/identify` handler + `_build_client_config()`

```python
# In handle_incoming_message():
if data.get("method") == "client/identify":
    client_type = data.get("params", {}).get("client_type", "unknown")
    self.client_types[user_id] = client_type
    config_block = self._build_client_config(client_type)
    response = {"jsonrpc": "2.0", "id": request_id,
                "result": {"status": "ok", "config": config_block}}
    await ws.send_text(json.dumps(response))
    # Logs WARNING if config_block is empty (unknown client type)
    return

def _build_client_config(self, client_type: str) -> dict:
    if client_type == "vsto_outlook":
        return {
            "max_attachment_size_mb":       VSTO_MAX_ATTACHMENT_MB,
            "max_total_attachments_mb":     VSTO_MAX_TOTAL_MB,
            "arcumai_email":                VSTO_ARCUMAI_EMAIL,
            "arcumai_display_name":         VSTO_ARCUMAI_DISPLAY_NAME,
            "loopback_timeout_ms":          VSTO_LOOPBACK_TIMEOUT_MS,
            "enable_virtual_loopback":      VSTO_ENABLE_VIRTUAL_LOOPBACK,
            "show_processing_notification": VSTO_SHOW_NOTIFICATION,
        }
    return {}   # unknown type: empty config, client uses defaults
```

`client_types: Dict[str, str]` tracks the type per connected user. Cleaned up in `disconnect()`.

### Client-Side Implementation

#### `ThisAddIn.cs` — new fields and methods

```csharp
private string _pendingIdentifyId;          // UUID of in-flight identify request
private SynchronizationContext _syncContext; // Outlook's STA thread, captured at startup
```

`_syncContext = SynchronizationContext.Current` is captured in `ThisAddIn_Startup()` which runs on Outlook's main STA thread.

**`SendIdentify()`** — called after successful WebSocket connect:
```csharp
_pendingIdentifyId = Guid.NewGuid().ToString();
// Sends client/identify JSON-RPC, logs "Sent client/identify to server"
```

**`OnMessageFromArcum()` — identify response dispatch** (before `tools/call` handling):
```csharp
JToken result = request["result"];
if (result != null && !string.IsNullOrEmpty(_pendingIdentifyId) && id == _pendingIdentifyId)
{
    _pendingIdentifyId = null;
    ApplyServerConfig(result["config"] as JObject);
    return;
}
```

**`ApplyServerConfig(JObject cfg)`**:

1. Dumps full received JSON to log: `"Config sync received from server: { ... }"`
2. Applies each present key to `_config` (thread-safe value writes)
3. Logs summary: `"Config sync applied: 7/7 keys — MaxAttachment=25MB, Email=..., Enabled=true"`
4. Schedules COM operations (ItemSend hook re-registration + `EnsureContactExists`) via `_syncContext.Post()` to run on STA thread

#### `PluginConfig.cs` — updated defaults

```csharp
// Virtual Loopback defaults (used until server handshake completes)
EnableVirtualLoopback = true;
ArcumAIEmailAddress   = "arcumai@arcumai.swiss";
ArcumAIDisplayName    = "ArcumAI Assistant";
MaxAttachmentSizeMB   = 25;
MaxTotalAttachmentsMB = 50;
LoopbackTimeoutMs     = 3600000;   // 1 hour
ShowProcessingNotification = true;
```

Defaults are safe and functional — plugin works fully even if the identify response never arrives.

#### `config.json` — server-driven properties removed

Only connection-level settings remain in `config.json` (all three copies: AppData, bin/Debug, source):

```json
{
  "ServerUrl": "ws://localhost:8080",
  "ReconnectDelayMs": 5000,
  "MaxReconnectAttempts": 720,
  "ConnectionTimeoutMs": 30000,
  "RequestTimeoutMs": 60000,
  "MaxEmailResults": 10,
  "EmailPreviewLength": 200,
  "EnableLogging": true,
  "LogLevel": "INFO",
  "UseSecureConnection": false,
  "AutoReconnect": true,
  "HeartbeatIntervalMs": 30000
}
```

---

## Feature 4: STA Threading Hardening

Outlook's COM model requires all COM operations to run on the main STA (Single-Threaded Apartment) thread. Two bugs were fixed in Phase 2.

### Bug 1: Outlook hang during ItemSend

**Root cause**: `SimulateSentItem()`, `CloseComposeWindow()`, and `DeleteFromOutbox()` were called synchronously inside `ProcessInterceptedEmail()` before the first `await`. These ran on Outlook's STA thread while the `ItemSend` handler was still active, causing a deadlock (`mail.Copy()` + `inspector.Close()` during an active send is unsafe).

**Fix**: All three calls moved into `_syncContext.Post()`:

```csharp
// Deferred to after ItemSend handler returns.
// With Cancel=true, compose window + mail COM object remain valid.
if (_syncContext != null)
    _syncContext.Post(_ => {
        SimulateSentItem(mail);
        CloseComposeWindow(mail);
        DeleteFromOutbox(mail);
    }, null);
```

### Bug 2: COM operations in `ApplyServerConfig` from thread-pool

**Root cause**: `OnMessageFromArcum` runs on a WebSocket thread-pool thread. `ApplyServerConfig` was directly calling `this.Application.ItemSend +=` and `EnsureContactExists()`, both of which are COM operations requiring the STA thread.

**Fix**: `_syncContext` captured at startup; COM work wrapped in `_syncContext.Post()`:

```csharp
void ApplyComChanges(object _)
{
    if (loopbackEnabled)
    {
        try { this.Application.ItemSend -= Application_ItemSend; } catch { }
        this.Application.ItemSend += Application_ItemSend;
        _loopbackHandler?.EnsureContactExists();
    }
    else
    {
        try { this.Application.ItemSend -= Application_ItemSend; } catch { }
    }
}
if (_syncContext != null) _syncContext.Post(ApplyComChanges, null);
```

The unsubscribe-then-subscribe pattern prevents duplicate event handler registrations on every reconnect.

---

## Updated WebSocket Protocol

### `virtual_loopback/send_email` — extended params

New fields added in Phase 2:

```json
{
  "jsonrpc": "2.0",
  "method": "virtual_loopback/send_email",
  "id": "<uuid>",
  "params": {
    "subject": "Analyze contract",
    "body": "Please review...",
    "conversation_id": "AAQkADM0...",
    "timestamp": "2026-02-20T10:00:00Z",
    "has_attachments": true,
    "cc_recipients": [],
    "attachments": [
      {
        "file_name": "contract.pdf",
        "content_type": "application/pdf",
        "size_bytes": 524288,
        "content_base64": "JVBERi0xLjQK..."
      }
    ],
    "skipped_attachments": [
      "large_video.mp4 (30 MB, limit is 25 MB)"
    ]
  }
}
```

Note: `max_attachment_size_mb` and `max_total_attachments_mb` were removed from the payload — limits are now enforced on both sides using the values pushed via the handshake.

---

## Edge Case Behavior

### Server: unrecognized `client_type`
- Accept the connection (do not terminate)
- Log `WARNING`: `"Bridge: 'user' identified as unknown client_type 'xyz' — no config pushed"`
- Respond with `{"status": "ok", "config": {}}` (empty config)
- Connection remains open; email search and calendar still function normally

### Client: server returns empty or partial config
- Apply whatever keys are present (partial override is fine)
- Missing keys: keep `PluginConfig.cs` hard-coded defaults silently
- Log `DEBUG`: `"Config sync applied: N/7 keys — MaxAttachment=..."`
- Plugin is always fully functional — no retry, no disable

### Client: server not reachable at startup
- `PluginConfig.cs` defaults apply for the entire session
- Defaults are safe and production-ready (25 MB limit, loopback enabled, etc.)
- On reconnect, `SendIdentify()` is called again and config is re-applied

### All attachments exceed size limits
- Error reply injected locally (no server round-trip)
- Bullet list of rejected files with actual sizes and applicable limits
- `SimulateSentItem` + `CloseComposeWindow` + `DeleteFromOutbox` still execute
- User sees a response email in Inbox explaining the issue

---

## Files Changed in Phase 2

| File | Changes |
|------|---------|
| `Core/VirtualLoopbackHandler.cs` | Added `ExtractAttachments()`, `GetMimeType()`, `EnsureContactExists()`, all-skipped early return, `_syncContext.Post()` for COM cleanup |
| `ThisAddIn.cs` | Added `_pendingIdentifyId`, `_syncContext`, `SendIdentify()`, `ApplyServerConfig()`, identify response dispatch in `OnMessageFromArcum` |
| `Core/PluginConfig.cs` | Updated defaults: `ArcumAIEmailAddress = "arcumai@arcumai.swiss"`, `LoopbackTimeoutMs = 3600000` |
| `src/config.py` | Added `VSTO_*` constants (7 keys, all env-var configurable); removed old `LOOPBACK_MAX_*` constants |
| `src/bridge.py` | Added `client_types` dict, `client/identify` handler, `_build_client_config()`, `disconnect()` cleanup; removed `_get_config_hint()` |
| `config.json` (3 copies) | Removed server-driven properties; kept only connection settings |

---

## Server Configuration (`.env`)

```bash
# VSTO plugin config pushed at connect time
VSTO_MAX_ATTACHMENT_MB=25          # Per-file size limit (MB)
VSTO_MAX_TOTAL_MB=50               # Total attachments size limit (MB)
VSTO_ARCUMAI_EMAIL=arcumai@arcumai.swiss
VSTO_ARCUMAI_DISPLAY_NAME=ArcumAI Assistant
VSTO_LOOPBACK_TIMEOUT_MS=3600000   # 1 hour — for large documents
VSTO_ENABLE_VIRTUAL_LOOPBACK=true
VSTO_SHOW_NOTIFICATION=true
```

Changes take effect on next plugin connect (no Outlook restart required if server is restarted and plugin reconnects).

---

## Startup Log Sequence (Phase 2)

```
[INFO]  ArcumAI Plugin started | Server: ws://localhost:8080 | User: nicol
[INFO]  Effective configuration: { "ServerUrl": "ws://...", ... }
[INFO]  VirtualLoopback: Created Outlook contact 'ArcumAI Assistant' <arcumai@arcumai.swiss>
[INFO]  Virtual Loopback enabled | Target: arcumai@arcumai.swiss
[INFO]  Connecting to ws://localhost:8080/ws/outlook/nicol (attempt 1)...
[INFO]  Connected successfully
[INFO]  Sent client/identify to server
[INFO]  Config sync received from server:
        {
          "max_attachment_size_mb": 25,
          "max_total_attachments_mb": 50,
          "arcumai_email": "arcumai@arcumai.swiss",
          "arcumai_display_name": "ArcumAI Assistant",
          "loopback_timeout_ms": 3600000,
          "enable_virtual_loopback": true,
          "show_processing_notification": true
        }
[INFO]  Config sync applied: 7/7 keys — MaxAttachment=25MB, Email=arcumai@arcumai.swiss, Enabled=True
```

---

## Testing Checklist

### T1: Basic email interception (no attachment)
```
1. Send email To: arcumai@arcumai.swiss, no attachments
2. Log: "VirtualLoopback: Processing email..."
3. Log: "Extracted 0/0 attachment(s)"
4. Server processes, response email appears in Inbox
5. Sent Items shows a copy
```

### T2: Email with valid attachment
```
1. Attach a file under 25 MB
2. Log: "VirtualLoopback: Found 1 attachment(s), extracting..."
3. Log: "VirtualLoopback: Extracted 'file.pdf' (512 KB, application/pdf)"
4. Log: "Extracted 1/1 attachment(s) (512 KB total)"
5. Server receives base64 content, processes it
6. Response appears in Inbox
```

### T3: Inline attachment (email signature image)
```
1. Send email with HTML signature containing an embedded logo
2. Log: "Skipping inline attachment 'logo.png' (Content-ID: <...>)"
3. Inline image NOT included in attachments array
4. Email processes normally
```

### T4: Attachment exceeds per-file limit
```
1. Set VSTO_MAX_ATTACHMENT_MB=1 in server .env, restart server
2. Attach a 2 MB file
3. Plugin receives config: MaxAttachmentSizeMB=1
4. Log: "Skipping 'file.txt' — exceeds per-file size limit"
5. All-skipped path: error reply injected locally (no server round-trip)
6. Error email in Inbox: "max 1 MB per file, 50 MB total"
7. Bullet list shows the rejected file with its actual size
```

### T5: Contact creation
```
1. First startup: log shows "Created Outlook contact 'ArcumAI Assistant'"
2. Second startup: log shows "ArcumAI contact already exists" (idempotent)
3. Contact appears in Outlook address book, resolves when typing "arcum"
```

### T6: Config handshake
```
1. Start server, restart Outlook
2. Log: "Sent client/identify to server"
3. Log: "Config sync received from server: { ... }" (full JSON)
4. Log: "Config sync applied: 7/7 keys"
5. Change VSTO_MAX_ATTACHMENT_MB=5 in .env, restart server
6. Plugin reconnects → log shows MaxAttachment=5MB
```

### T7: Disconnect then reconnect
```
1. Stop server
2. Log: "Connection lost from server"
3. Restart server
4. Plugin reconnects automatically
5. Log: "Sent client/identify to server" (identify sent again on reconnect)
6. Log: "Config sync applied: 7/7 keys"
```

### T8: Unknown client type (server side)
```
1. Add a new client that sends client_type: "unknown_future"
2. Server log: WARNING "identified as unknown client_type 'unknown_future' — no config pushed"
3. Server responds with {"status": "ok", "config": {}}
4. Connection stays open
```

---

## Summary

**Phase 2 Status**: ✅ **FULLY IMPLEMENTED, TESTED & PRODUCTION READY** (February 20, 2026)

### What Was Added
- ✅ Attachment extraction: base64-encoded, inline images filtered, size limits enforced
- ✅ MIME type detection for 15+ file types
- ✅ Outlook contact creation (ArcumAI in address book, idempotent)
- ✅ Server-driven config handshake (`client/identify` protocol)
- ✅ `VSTO_*` env-var-configurable constants on server (`src/config.py`)
- ✅ `_build_client_config()` on server — extensible to future client types
- ✅ `ApplyServerConfig()` on client — applies 7 keys, logs full received config
- ✅ All-skipped error path — local error reply, no wasted server round-trip
- ✅ STA thread safety — all COM operations via `_syncContext.Post()`
- ✅ `config.json` cleaned up — server-driven properties removed

### Key Architectural Decision
**Server is the single source of truth for behavioral config.** Attachment limits, email address, timeouts, and feature flags are all defined once in `src/config.py` (overridable via `.env`) and pushed to the plugin at connect time. Adding a new client type (mobile, web) requires only a new branch in `_build_client_config()`.
