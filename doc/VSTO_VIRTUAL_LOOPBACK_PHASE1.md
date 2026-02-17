# VSTO Plugin - Virtual Loopback (Phase 1: MVP)

**Date**: February 17, 2026
**Status**: IMPLEMENTED - Ready for testing
**Scope**: New feature - ArcumAI as a "virtual colleague" reachable via email

---

## Concept

Users can write an email to "ArcumAI" directly from Outlook (e.g. `arcumai@arcumai.ai` or selecting "ArcumAI" from the address book). The plugin intercepts the send, processes the content through the AI server, and injects the response back as a new unread email in the Inbox. No data leaves the local network.

This differentiates ArcumAI from sidebar-based solutions like Copilot by using the most natural interface for professionals: email itself.

---

## Architecture

```
USER                            PLUGIN (C#)                        SERVER (Python)
  |                                |                                   |
  |-- Writes email to "ArcumAI" ->|                                   |
  |-- Clicks "Send" ------------->|                                   |
  |                               |-- ItemSend: checks recipients     |
  |                               |-- Cancel = true (blocks real send)|
  |                               |-- Copies to Sent Items            |
  |                               |-- Extracts body + metadata        |
  |                               |-- WS: virtual_loopback/send ----->|
  |                               |                                   |-- ACK immediate
  |                               |                                   |-- Route to AI engine:
  |                               |                                   |     no attachments -> RAG
  |                               |                                   |     with attachments -> FILE_READER
  |                               |                                   |-- Generate response
  |                               |<---- WS: virtual_loopback/response|
  |                               |-- Creates MailItem in Inbox       |
  |                               |-- Subject: "Re: ..." (threading)  |
  |                               |-- Category: "ArcumAI"             |
  |                               |-- Status: Unread                  |
  |<-- Toast: "Response ready" ---|                                   |
```

---

## Files Created

### `outlook-plugin/.../Core/VirtualLoopbackHandler.cs` (NEW - ~350 lines)

Core class encapsulating all loopback logic, keeping `ThisAddIn.cs` clean.

**Key methods**:

| Method | Purpose |
|--------|---------|
| `AnalyzeRecipients()` | Scans To/CC/BCC for ArcumAI and real recipients |
| `ShouldIntercept()` | Returns true when ArcumAI is the ONLY recipient |
| `ShouldProcessInParallel()` | Returns true when ArcumAI + real recipients (CC scenario) |
| `RemoveArcumRecipient()` | Strips ArcumAI from recipients before real send (CC scenario) |
| `ProcessInterceptedEmail()` | Extracts content, builds JSON-RPC, sends via WebSocket, starts timeout timer |
| `HandleServerResponse()` | Receives AI response, marshals to STA thread |
| `CreateResponseEmail()` | Creates MailItem in Inbox with HTML body, threading, category |
| `SimulateSentItem()` | Copies intercepted email to Sent Items folder |
| `ShowToastNotification()` | Windows balloon notification for processing status |

**Thread safety**: Captures `SynchronizationContext` at construction, uses `Post()` for all COM operations. `ConcurrentDictionary` for pending requests.

**COM cleanup**: All COM objects released via `Marshal.ReleaseComObject()` in `finally` blocks.

---

## Files Modified

### `outlook-plugin/.../Core/PluginConfig.cs`

Added 7 new configuration properties:

| Property | Default | Description |
|----------|---------|-------------|
| `EnableVirtualLoopback` | `true` | Enable/disable the feature |
| `ArcumAIEmailAddress` | `"arcumai@arcumai.ai"` | Email address to intercept |
| `ArcumAIDisplayName` | `"ArcumAI Assistant"` | Display name in response emails |
| `MaxAttachmentSizeMB` | `25` | Max single attachment size |
| `MaxTotalAttachmentsMB` | `50` | Max total attachments size |
| `LoopbackTimeoutMs` | `300000` (5 min) | Processing timeout |
| `ShowProcessingNotification` | `true` | Show toast notifications |

Added: defaults in `SetDefaults()`, loading in `LoadFromAppConfig()`, validation in `Validate()`.

### `outlook-plugin/.../ThisAddIn.cs`

- Added `_loopbackHandler` field
- Added initialization in `ThisAddIn_Startup` (step 4): creates handler, hooks `ItemSend`
- Added `Application_ItemSend` handler with three paths:
  - **Intercept**: ArcumAI only -> `Cancel = true`, process via loopback
  - **Parallel**: ArcumAI + real people -> remove ArcumAI, send normally, also process
  - **Passthrough**: no ArcumAI -> do nothing
- Extended `OnMessageFromArcum` with `virtual_loopback/response` dispatch
- Added cleanup in `Application_Quit`: unhooks `ItemSend`
- Error handling: never cancels send on error (safety first)

### `outlook-plugin/.../ArcumAI.OutlookAddIn.csproj`

Added `<Compile Include="Core\VirtualLoopbackHandler.cs" />` to the source files ItemGroup.

### `src/bridge.py`

Extended `OutlookBridgeManager` with ~200 lines of new code:

**New methods**:

| Method | Purpose |
|--------|---------|
| `_process_loopback_email()` | Main async handler: decode attachments, route to AI, send response |
| `_process_attachment()` | Base64 decode + text extraction (PDF, DOCX, XLSX, TXT, MSG, EML) |
| `_route_to_ai_engine()` | Creates UserSession, routes to RAG (no attachments) or FILE_READER (with attachments) |
| `_build_cc_disclaimer()` | Generates disclaimer text for CC scenarios |
| `_markdown_to_html()` | Converts AI response to HTML for email body |

**Modified methods**:

- `handle_incoming_message()`: restructured with early returns, added `virtual_loopback/send_email` handler with immediate ACK and async task dispatch

---

## WebSocket Protocol Extension

Three new message types added to the existing JSON-RPC 2.0 protocol:

### 1. Plugin -> Server: `virtual_loopback/send_email`
```json
{
  "jsonrpc": "2.0",
  "method": "virtual_loopback/send_email",
  "id": "<uuid>",
  "params": {
    "subject": "Analyze contract",
    "body": "Please review the attached...",
    "conversation_id": "AAQkADM0...",
    "timestamp": "2026-02-17T14:30:00Z",
    "has_attachments": false,
    "cc_recipients": [],
    "attachments": []
  }
}
```

### 2. Server -> Plugin: ACK (immediate)
```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "result": { "status": "processing" }
}
```

### 3. Server -> Plugin: `virtual_loopback/response` (after AI processing)
```json
{
  "jsonrpc": "2.0",
  "method": "virtual_loopback/response",
  "params": {
    "request_id": "<uuid>",
    "subject": "Analyze contract",
    "conversation_id": "AAQkADM0...",
    "response_text": "# Analysis\n\n## Key Points\n...",
    "response_html": "<h1>Analysis</h1>..."
  }
}
```

---

## AI Engine Routing Logic

| Scenario | Engine | Rationale |
|----------|--------|-----------|
| Email without attachments | **RAG** | User asks a question -> search knowledge base (ChromaDB + BM25) |
| Email with attachments | **FILE_READER** | User sends documents -> analyze the attached content directly |

Implementation: `_route_to_ai_engine()` in `bridge.py` creates a `UserSession` and calls `run_chat_action()` with appropriate mode override.

---

## CC Disclaimer

When ArcumAI is in CC alongside real recipients, the email is sent normally (with ArcumAI removed from recipients) AND processed by ArcumAI. The response includes a disclaimer:

```
------------------------------------------------------------
NOTE: This response is for you only.
The CC recipients of the original email (Marco Rossi,
Anna Bianchi) did NOT receive this analysis.
If you find it useful, you can forward this email to them.
------------------------------------------------------------
```

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| ArcumAI only, no attachments | Intercept, Cancel=true, RAG engine |
| ArcumAI only, with attachments | Intercept, Cancel=true, FILE_READER engine |
| ArcumAI in CC + real recipients | Send normally (minus ArcumAI), process in parallel, response with CC disclaimer |
| Plugin not connected to server | No interception, email sends normally |
| Timeout (>5 min) | Timeout response email injected into Inbox |
| Disconnect during processing | Timeout timer fires, error email injected |
| Non-email item (meeting request) | Ignored (ItemSend only processes MailItem) |
| Error in ItemSend handler | Cancel=false, email sends normally (fail-safe) |

---

## Testing Checklist

### Basic Flow
```
1. Open Outlook with plugin loaded
2. Verify log: "Virtual Loopback enabled | Target: arcumai@arcumai.ai"
3. New Email -> To: "ArcumAI" (or arcumai@arcumai.ai)
4. Subject: "What contracts do we have from 2024?"
5. Click Send
6. Verify: toast notification "Processing..."
7. Verify: email appears in Sent Items
8. Verify: response email appears in Inbox as "Re: What contracts..."
9. Verify: response has category "ArcumAI"
10. Verify: response is marked as Unread
```

### CC Scenario
```
1. New Email -> To: colleague@real.com, CC: ArcumAI
2. Subject: "Contract review"
3. Click Send
4. Verify: email sent to colleague (without ArcumAI in recipients)
5. Verify: ArcumAI response in Inbox with CC disclaimer
```

### Disconnected
```
1. Stop the ArcumAI server
2. New Email -> To: ArcumAI
3. Click Send
4. Verify: email sends normally (no interception when disconnected)
```

---

## Dependencies

**No new NuGet packages required** - uses existing:
- `Newtonsoft.Json` (JSON serialization)
- `System.Windows.Forms` (toast notifications, already referenced)
- `System.Runtime.InteropServices` (COM cleanup, already referenced)

**Python side** - optional packages for attachment processing (Phase 2):
- `markdown` (for HTML conversion, fallback available)
- `python-docx` (for DOCX files, graceful degradation)
- `openpyxl` (for XLSX files, graceful degradation)

---

## Next Phases

### Phase 2: Attachments
- Add `ExtractAttachments()` with base64 encoding in `VirtualLoopbackHandler.cs`
- Filter inline attachments (email signatures) via `PR_ATTACH_CONTENT_ID`
- Size limit enforcement (25MB/file, 50MB total)
- Full attachment processing pipeline on server

### Phase 3: UX Polish
- Conversation threading (Message-ID, In-Reply-To headers)
- Outlook category color configuration
- HTML-styled response emails with ArcumAI branding
- Outlook contact card for "ArcumAI" in address book

### Phase 4: Robustness
- WebSocket message size monitoring
- Concurrent request limiting
- Temp file cleanup
- Exchange/O365 sync validation

---

**Status**: Phase 1 IMPLEMENTED - Ready for build and testing
