# Outlook Plugin Fixes - Summary

**Date**: February 11, 2026
**Status**: COMPLETE
**Scope**: 5 bugs + 3 improvements across C# plugin, Python bridge, and WebSocket endpoint

---

## Architecture Overview

```
[Outlook Plugin (C#)]  --WebSocket-->  [main_nice.py]  --bridge.py-->  [engine.py]
   ThisAddIn.cs                         /ws/outlook/{id}                 UserSession
   WebSocketTransport.cs                                                 tools/call
   PluginConfig.cs
```

---

## Bug Fixes

### BUG #1: COM Object Memory Leaks (CRITICAL)
**File**: `ThisAddIn.cs` - `GetEmails()` / `GetCalendar()`

**Problem**: Outlook COM objects (`Session`, `MAPIFolder`, `Items`, `MailItem`, `AppointmentItem`) were never released. In a VSTO add-in running all day, this causes progressive memory leaks and eventually crashes Outlook.

**Fix**:
- Added `using System.Runtime.InteropServices` for `Marshal.ReleaseComObject()`
- `Application.Session` captured in a variable (was leaking as intermediate)
- Each `item` in foreach loops released via `try/finally`
- Outer `finally` block releases `items` → `inbox`/`calendar` → `session` in reverse order

**Before**:
```csharp
Outlook.MAPIFolder inbox = Application.Session.GetDefaultFolder(...);
Outlook.Items items = inbox.Items;
foreach (object item in items) { /* use and forget */ }
```

**After**:
```csharp
Outlook.NameSpace session = null;
Outlook.MAPIFolder inbox = null;
Outlook.Items items = null;
try {
    session = Application.Session;
    inbox = session.GetDefaultFolder(...);
    items = inbox.Items;
    foreach (object item in items) {
        try { /* use */ }
        finally { Marshal.ReleaseComObject(item); }
    }
} finally {
    if (items != null) Marshal.ReleaseComObject(items);
    if (inbox != null) Marshal.ReleaseComObject(inbox);
    if (session != null) Marshal.ReleaseComObject(session);
}
```

---

### BUG #2: Calendar InvalidCastException (HIGH)
**File**: `ThisAddIn.cs` - `GetCalendar()`

**Problem**: `foreach (Outlook.AppointmentItem appt in restrictedItems)` throws `InvalidCastException` when the calendar contains `MeetingItem` objects (not just `AppointmentItem`).

**Fix**: Changed to safe cast pattern matching `foreach (object item in ...)` with `if (item is Outlook.AppointmentItem appt)`, consistent with `GetEmails()`.

---

### BUG #3: Fire-and-Forget SendAsync (MEDIUM)
**File**: `ThisAddIn.cs` - `OnMessageFromArcum()`

**Problem**: `_transport.SendAsync(responseJson)` was called without `await`. If sending failed, the exception was silently lost and the server-side Future hung for 30s until timeout.

**Fix**:
- Method signature changed from `private void` to `private async void` (valid for event handlers)
- `_transport.SendAsync(responseJson)` → `await _transport.SendAsync(responseJson)`
- Failures now propagate to the existing `catch` block and get logged

---

### BUG #4: Stale Pending Requests on Disconnect (MEDIUM)
**File**: `bridge.py` - `disconnect()`

**Problem**: When a plugin disconnected, `pending_requests` Futures for that user were never resolved. They hung until the 30s timeout, causing unnecessary delays.

**Fix**: `disconnect()` now iterates all pending requests, resolves them immediately with an error message, and logs how many were cancelled.

```python
def disconnect(self, user_id: str):
    if user_id in self.active_connections:
        del self.active_connections[user_id]

    failed = []
    for req_id, future in list(self.pending_requests.items()):
        if not future.done():
            future.set_result(f"Connessione Outlook persa per '{user_id}'.")
            failed.append(req_id)
    for req_id in failed:
        del self.pending_requests[req_id]

    log.info(f"Bridge: Utente '{user_id}' disconnesso. {len(failed)} richieste pendenti annullate.")
```

---

### BUG #5: No WebSocket Authentication (SECURITY)
**File**: `main_nice.py` - `outlook_endpoint()`

**Problem**: Anyone who knew the URL pattern `ws://server:8080/ws/outlook/{id}` could connect and impersonate any user to read their emails/calendar.

**Fix**: Added `_is_valid_outlook_id()` validation before accepting connections:
- Rejects empty or oversized `user_id` (>100 chars)
- Validates `user_id` exists as a registered `outlook_id` in `users.json`
- Rejects duplicate `outlook_id` (same ID assigned to multiple users) with `ERROR` log showing conflicting usernames
- Unauthorized connections closed with code `4001`

```python
def _is_valid_outlook_id(user_id: str) -> bool:
    if not user_id or len(user_id) > 100:
        return False
    users = load_users()
    matches = [name for name, data in users.items() if data.get("outlook_id") == user_id]
    if len(matches) == 0:
        return False
    if len(matches) > 1:
        slog.error(f"ERRORE CONFIG: outlook_id '{user_id}' duplicato per utenti: {matches}")
        return False
    return True
```

---

## Improvements

### IMP #6: Heartbeat Logging Too Verbose
**File**: `bridge.py` - `handle_incoming_message()`

**Problem**: Heartbeats fire every 30s per user and were logged at `INFO` level (~2880 lines/day per user).

**Fix**: Heartbeats now logged at `DEBUG` level. Other notifications (e.g. "closing") remain at `INFO`.

---

### IMP #7: Bridge Timeout Configurable
**File**: `bridge.py`

**Problem**: `asyncio.wait_for(future, timeout=30.0)` was hardcoded. The C# plugin has `RequestTimeoutMs = 60000` (60s) by default, causing a mismatch.

**Fix**: Added `BRIDGE_TIMEOUT` read from `.env` with default `60.0` (matching C# default).

```python
BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_TIMEOUT", "60.0"))
```

---

### IMP #8: C# Plugin Log Rotation
**File**: `ThisAddIn.cs` - `Log()`

**Problem**: `File.AppendAllText()` grew the plugin log file indefinitely with no rotation.

**Fix**: Size-based rotation at 5 MB. When `plugin.log` exceeds limit, it's renamed to `plugin.log.old` (previous `.old` is deleted). Max ~10 MB on disk.

```csharp
private const long MAX_LOG_SIZE = 5 * 1024 * 1024; // 5 MB

if (File.Exists(logPath))
{
    var info = new FileInfo(logPath);
    if (info.Length > MAX_LOG_SIZE)
    {
        string oldPath = logPath + ".old";
        if (File.Exists(oldPath)) File.Delete(oldPath);
        File.Move(logPath, oldPath);
    }
}
```

---

## Files Modified

| File | Changes |
|------|---------|
| `ThisAddIn.cs` | +`System.Runtime.InteropServices`, COM cleanup in GetEmails/GetCalendar, safe cast in GetCalendar, async OnMessageFromArcum, log rotation |
| `bridge.py` | +`BRIDGE_TIMEOUT` from .env, fail-fast disconnect, heartbeat at DEBUG |
| `main_nice.py` | +`_is_valid_outlook_id()` validation on WebSocket endpoint |

---

## Summary Table

| # | Type | Severity | Fix |
|---|------|----------|-----|
| 1 | Bug | CRITICAL | COM object leaks → `Marshal.ReleaseComObject()` cleanup |
| 2 | Bug | HIGH | Calendar `InvalidCastException` → safe `is` pattern |
| 3 | Bug | MEDIUM | Unawaited `SendAsync` → `async void` + `await` |
| 4 | Bug | MEDIUM | Stale Futures on disconnect → fail-fast resolution |
| 5 | Security | HIGH | No WS auth → validate `outlook_id` in `users.json` |
| 6 | Improvement | LOW | Heartbeat noise → `DEBUG` level |
| 7 | Improvement | LOW | Hardcoded 30s timeout → configurable `BRIDGE_TIMEOUT` |
| 8 | Improvement | LOW | No log rotation → 5 MB size-based rotation |
