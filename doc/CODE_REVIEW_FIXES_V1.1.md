# Code Review Fixes — v1.1

**Date**: March 3, 2026
**Status**: COMPLETE
**Scope**: Full code review of Python backend + C# VSTO plugin — 38 issues found, all fixed.
**Tests**: 16/16 pytest pass. Smoke test (server + virtual loopback + web UI) pass.

---

## Summary Table

| ID | Severity | Component | File | Description |
|----|----------|-----------|------|-------------|
| C#-1 | CRITICAL | C# | `WebSocketTransport.cs` | `Disconnected` event could fire twice per connection lifecycle |
| C#-2 | CRITICAL | C# | `ThisAddIn.cs` | `Receive` timeout used `RequestTimeoutMs` (60s) — false-disconnects during long AI jobs |
| C#-3 | HIGH | C# | `ThisAddIn.cs` | Heartbeat timer race: `StartHeartbeat` disposed timer another thread was Elapsed-executing |
| C#-4 | HIGH | C# | `ThisAddIn.cs` | Heartbeat `async void` lambda swallowed exceptions silently |
| C#-5 | HIGH | C# | `VirtualLoopbackHandler.cs` | `else` branches called COM directly without STA guard — unsafe on non-STA threads |
| C#-6 | HIGH | C# | `VirtualLoopbackHandler.cs` | Timeout Task.Run had no exception handler — unobserved task exceptions |
| C#-7 | HIGH | C# | `ThisAddIn.cs` | Transport events not unsubscribed on shutdown — duplicate handlers on plugin reload |
| C#-8 | MEDIUM | C# | `ThisAddIn.cs` | `Thread.MemoryBarrier()` missing after config-sync writes |
| C#-9 | MEDIUM | C# | `ThisAddIn.cs` | Config-sync result never validated — invalid values silently accepted |
| C#-10 | MEDIUM | C# | `ThisAddIn.cs` | `SynchronizationContext.Current` is `null` on VSTO STA thread — `_syncContext.Post()` never ran |
| C#-11 | MEDIUM | C# | `PluginConfig.cs` | `LogLevel.ToUpper()` NullReferenceException if `LogLevel` is null |
| C#-12 | LOW | C# | `ThisAddIn.cs` | Empty / whitespace JSON string passed to `JObject.Parse()` |
| PY-1 | CRITICAL | Python | `loopback_processor.py` | Base64 attachment decoded into memory before size-check — DoS / OOM vector |
| PY-2 | CRITICAL | Python | `loopback_processor.py` | `tmp_path` assigned after `NamedTemporaryFile.__exit__` — file reference could be lost |
| PY-3 | HIGH | Python | `session.py` | Intent-classifier LLM call had no timeout — could hang `run_chat_action` indefinitely |
| PY-S1 | SECURITY | Python | `bridge/manager.py` | Full JSON payload (incl. email bodies) logged at INFO level |
| PY-S2 | SECURITY | Python | `bridge/loopback_processor.py` | Raw `str(e)` sent to plugin — exposes internal paths / keys |
| PY-S3 | SECURITY | Python | `bridge/pending_results.py` | `ws.send_text()` failure left `.delivering` temp file permanently stuck |
| L1 | LOW | Python | `watcher.py` | `subprocess.run("ingest.py")` used relative path — breaks if CWD changes |
| L2 | LOW | Python | `ai/prompt_optimizer.py` | Gemini API call had no timeout — could hang indefinitely |
| L3 | LOW | Python | `bridge/pending_results.py` | `except Exception: pass` in `find()`/`delete()` swallowed unexpected errors silently |
| L4 | LOW | Python | `src/utils.py` | `except Exception: pass` in folder cleanup swallowed errors silently |
| L5 | LOW | Python | `ai/engines.py` | `'CHROMA_PATH' in globals()` always `True` — dead else branch |
| L6 | LOW | Python | `ai/engines.py` | `except: pass` on BM25 load — failure reason invisible |
| L7 | LOW | Python | `src/config.py` | Debug `print()` statements left in production code |
| L8 | LOW | Python | `src/readers.py` | Bare `except:` clauses — catches `BaseException` including `SystemExit` |
| L9 | LOW | Python | `ingest.py` | Bare `except:` on path relativize — too broad |
| L10 | LOW | Python | `src/ui/footer.py` | NiceGUI `RuntimeError` on stale client (tab closed mid-query) logged as ERROR |

---

## C# Fixes

### C#-1 — `WebSocketTransport.cs` — Double Disconnected event (CRITICAL)

**Problem:** `Disconnected?.Invoke(...)` was called from both `SendAsync` (on send failure) and the end of `ReceiveLoop`, with no deduplication. A network failure during receive would fire the event twice, triggering two parallel reconnect sequences.

**Fix:** Added `_disconnectedFired` int field guarded by `Interlocked.CompareExchange`. `FireDisconnected()` fires exactly once per connection lifetime and is reset to 0 in `ConnectAsync`.

```csharp
private void FireDisconnected()
{
    if (Interlocked.CompareExchange(ref _disconnectedFired, 1, 0) == 0)
        Disconnected?.Invoke(this, EventArgs.Empty);
}
```

Also: `ReceiveLoop` now skips `FireDisconnected()` when `_cts.IsCancellationRequested` (voluntary cancellation from `ConnectAsync` during reconnect — not a real disconnect).

---

### C#-2 — `WebSocketTransport.cs` — False-disconnect from receive timeout (CRITICAL)

**Problem:** A previous fix added a per-receive `CancellationTokenSource(RequestTimeoutMs)` timeout (60s). The server never ACKs heartbeats, so 60s silence (normal during long AI processing) triggered disconnect → reconnect loop.

**Fix:** Removed the receive timeout entirely. `ReceiveAsync` now uses only `_cts.Token`. Dead connections are detected by `SendAsync` failure on the heartbeat, not by receive silence.

```csharp
// No per-receive timeout: the heartbeat (SendAsync failure) detects dead connections.
// A timeout here would false-fire during long AI processing (up to 1 hour).
var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
```

---

### C#-3 / C#-4 — `ThisAddIn.cs` — Heartbeat timer race + unhandled exceptions (HIGH)

**Problem 1 (Race):** `StartHeartbeat` called `StopHeartbeat` then created a new timer. Between Stop and the new assignment, the old `Elapsed` handler could fire on a threadpool thread, reference a disposed timer or `_heartbeatTimer = null`, and crash.

**Problem 2 (Silent exceptions):** The `async void` lambda assigned to `Elapsed` meant any unhandled exception was silently lost via `UnobservedTaskException`.

**Fix:**
- Added `_heartbeatLock` object; `StartHeartbeat` and `StopHeartbeat` now swap the timer inside the lock and dispose outside
- Extracted `HeartbeatTickAsync()` as an `async Task` method; `Elapsed` handler uses `.ContinueWith(...OnlyOnFaulted)` to log exceptions

---

### C#-5 — `VirtualLoopbackHandler.cs` — Unsafe COM calls in `else` branches (HIGH)

**Problem:** Three `else` branches (for `_syncContext == null`) called Outlook COM methods (`SimulateSentItem`, `DeleteInterceptedItem`, `CreateResponseEmail`) directly on whatever thread was running — potentially a threadpool thread. COM calls from non-STA threads in VSTO are undefined behaviour.

**Fix:** `else` branches now log an ERROR and skip the COM operations. The root cause (`_syncContext == null`) is fixed separately in C#-10.

---

### C#-6 — `VirtualLoopbackHandler.cs` — Unhandled exception in timeout `Task.Run` (HIGH)

**Problem:** The timeout fire-and-forget `Task.Run(async () => { await Task.Delay(...); ... })` had no exception handler. Any exception in `InjectResponseOnMainThread` would become an unobserved task exception.

**Fix:** Wrapped the Task.Run body in `try/catch(Exception ex)` with `_logAction("ERROR", ...)`.

---

### C#-7 — `ThisAddIn.cs` — Transport event leak on shutdown/reload (HIGH)

**Problem:** `ThisAddIn_Shutdown` did not unsubscribe `MessageReceived` and `Disconnected` from `_transport`. On plugin reload (Outlook without full restart), the new instance would subscribe again, resulting in duplicate handlers.

**Fix:** Added explicit unsubscribe before teardown:
```csharp
_transport.MessageReceived -= OnMessageFromArcum;
_transport.Disconnected -= OnDisconnected;
```

---

### C#-8 / C#-9 — `ThisAddIn.cs` — Config-sync memory visibility + validation (MEDIUM)

**Problem 1:** Server-pushed config values were written on the WebSocket receive thread and read by the STA Outlook thread without a memory barrier — theoretically stale reads on multi-core CPUs.

**Problem 2:** The patched config was never validated after sync. An invalid server-pushed value (e.g. negative timeout) would be accepted silently.

**Fix:**
```csharp
Thread.MemoryBarrier();
if (!_config.Validate(out string syncValidationError))
    _logger.Log("WARNING", $"Config sync produced invalid configuration: {syncValidationError}");
```

---

### C#-10 — `ThisAddIn.cs` — Null `SynchronizationContext` on VSTO STA thread (MEDIUM)

**Problem:** `SynchronizationContext.Current` is `null` on the Outlook STA thread. `_syncContext` was always null → all `_syncContext.Post(...)` calls in `VirtualLoopbackHandler` silently did nothing → compose window never closed, email never moved to Sent Items.

**Fix:** At startup, if `Current` is null, install `WindowsFormsSynchronizationContext` (which uses a hidden WinForms `Control` to marshal calls to the STA message loop):
```csharp
_syncContext = SynchronizationContext.Current;
if (_syncContext == null)
{
    var ctx = new System.Windows.Forms.WindowsFormsSynchronizationContext();
    SynchronizationContext.SetSynchronizationContext(ctx);
    _syncContext = ctx;
}
```

---

### C#-11 — `PluginConfig.cs` — NullReferenceException in Validate() (MEDIUM)

**Problem:** `LogLevel.ToUpper()` throws `NullReferenceException` if `LogLevel` is null (e.g. deserializing a config file that omits the field).

**Fix:** `(LogLevel ?? "").ToUpper()`

---

### C#-12 — `ThisAddIn.cs` — Empty JSON passed to `JObject.Parse()` (LOW)

**Problem:** A zero-byte or whitespace WebSocket frame would reach `JObject.Parse("")` and throw `JsonReaderException`, logged as an unhandled error.

**Fix:** `if (string.IsNullOrWhiteSpace(json)) return;` before parsing.

---

## Python Fixes

### PY-1 — `loopback_processor.py` — Base64 decoded before size check (CRITICAL)

**Problem:** `base64.b64decode(content_b64)` was called before checking the attachment size. A malicious or corrupt payload could force the server to allocate hundreds of MB in memory.

**Fix:** Added a pre-decode length guard using the known base64 expansion factor (~1.34×):
```python
max_encoded_len = int(VSTO_MAX_ATTACHMENT_MB * 1024 * 1024 * 1.34)
if len(content_b64) > max_encoded_len:
    log.warning(f"VirtualLoopback: attachment '{file_name}' exceeds size guard, skipping decode")
    return f"[Attachment too large to process server-side: {file_name}]"
```

---

### PY-2 — `loopback_processor.py` — `tmp_path` assigned after file context exit (CRITICAL)

**Problem:** `tmp_path = Path(tmp.name)` appeared after the `with NamedTemporaryFile(...) as tmp:` block body, meaning on Windows (where the file is still open during `with`) the assignment was in the right place, but the reference could be lost if an exception occurred before assignment.

**Fix:** `tmp_path` now assigned as the first statement inside the `with` block, before `tmp.write(file_bytes)`.

---

### PY-3 — `session.py` — No timeout on intent-classifier LLM call (HIGH)

**Problem:** `await Settings.llm.acomplete(prompt)` for intent classification had no timeout. A stalled Ollama process would hang `run_chat_action` indefinitely (up to `REQUEST_TIMEOUT` = 3600s in LOW_RESOURCE profile).

**Fix:** Wrapped with a 10-second timeout; existing `except Exception: return "RAG"` fallback handles `asyncio.TimeoutError`:
```python
resp = await asyncio.wait_for(Settings.llm.acomplete(prompt), timeout=10.0)
```

---

### PY-S1 — `bridge/manager.py` — Full payload logged at INFO (SECURITY)

**Problem:** `log.info(f"📤 MCP TX [{user_id}]: {json_str}")` logged the complete request JSON (including email bodies, calendar details, query content) at INFO level. Server logs are world-readable on disk and may be forwarded to a SIEM.

**Fix:** Full payload demoted to DEBUG; INFO now logs only method name + byte count:
```python
log.debug(f"📤 MCP TX [{user_id}]: {json_str}")
log.info(f"📤 MCP TX [{user_id}]: method={payload.get('method')} ({len(json_str)} bytes)")
```

---

### PY-S2 — `loopback_processor.py` — Raw exception text in response (SECURITY)

**Problem:** `f"An error occurred during processing: {str(e)}"` sent Python exception text (including filesystem paths, internal variable names, API endpoints) directly to the Outlook plugin.

**Fix:** Generic messages with no internal details:
```python
"response_text": "An error occurred while processing your request. Please try again.",
"response_html": "<p style='color:red'>Error: unable to process your request. Please try again.</p>",
```
Full exception still logged server-side via `log.error(..., exc_info=True)`.

---

### PY-S3 — `pending_results.py` — Stuck `.delivering` temp file (SECURITY / RELIABILITY)

**Problem:** If `ws.send_text()` raised (WebSocket closed between the `.get()` and the actual send), the outer `except Exception` caught it but the `.delivering` file was never renamed back to `.json`. The result was permanently undeliverable until server restart.

**Fix:** Wrapped `ws.send_text()` in its own try/except that renames back on failure:
```python
try:
    await ws.send_text(json.dumps(push))
    delivering_path.unlink(missing_ok=True)
    delivered += 1
except Exception as send_err:
    log.warning(f"Pending delivery send failed for '{user_id}': {send_err} — will retry on next reconnect")
    try:
        delivering_path.rename(Path(p))
    except Exception:
        pass
    break
```

---

### L1 — `watcher.py` — Relative path for `ingest.py` subprocess (LOW)

**Problem:** `subprocess.run([sys.executable, "ingest.py"])` used a relative path. If the process CWD was changed (e.g., by a systemd unit with `WorkingDirectory`), the script would not be found.

**Fix:** Absolute path computed once at module level:
```python
_INGEST_SCRIPT = Path(__file__).parent / "ingest.py"
subprocess.run([sys.executable, str(_INGEST_SCRIPT)], check=True)
```

---

### L2 — `ai/prompt_optimizer.py` + `config.py` — No timeout on Gemini API call (LOW)

**Problem:** `await llm.acomplete(meta_prompt)` had no timeout. A Gemini API hang would block prompt optimization indefinitely.

**Fix:** `GEMINI_TIMEOUT` added to `config.py` (env-configurable, default 60s). Wrapped with `asyncio.wait_for`; existing `except Exception` fallback handles `asyncio.TimeoutError` and returns raw email.

```python
# config.py
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "60.0"))

# prompt_optimizer.py
response = await asyncio.wait_for(llm.acomplete(meta_prompt), timeout=GEMINI_TIMEOUT)
```

---

### L3 — `pending_results.py` — Silent `except Exception: pass` in `find()`/`delete()` (LOW)

**Problem:** Unexpected errors (e.g. disk full, corrupt JSON, permission denied) in `find()` and `delete()` were silently swallowed, making failures invisible.

**Fix:** Split into `FileNotFoundError` (benign, silent) and unexpected (log warning):
```python
except FileNotFoundError:
    pass
except Exception as e:
    log.warning(f"Unexpected error reading/deleting pending result {p}: {e}")
```

---

### L4 — `src/utils.py` — Silent exception in folder cleanup (LOW)

**Problem:** `except Exception: pass` in `pulisci_cartelle_vuote` hid errors like permission denied or disk full during `shutil.rmtree`.

**Fix:** `except Exception as e: log.warning(f"Could not remove empty folder '{folder}': {e}")`

---

### L5 / L6 — `ai/engines.py` — Dead conditional + silent BM25 failure (LOW)

**Problem 1:** `'CHROMA_PATH' in globals()` was always `True` (unconditionally imported). The `else str(DB_PATH)` branch was dead code.

**Problem 2:** `except: pass` on BM25 load hid the failure reason.

**Fix:**
```python
path_to_use = str(CHROMA_PATH)  # always defined; dead conditional removed
...
except Exception as e:
    slog.warning(f"BM25 load failed, using vector-only retriever: {e}")
```

---

### L7 — `config.py` — Debug print statements in production (LOW)

**Problem:** `print(f"[DEBUG] PROMPT_OPTIMIZATION from env: ...")` left from development were stdout-polluting in production.

**Fix:** Removed entirely.

---

### L8 / L9 — `readers.py`, `ingest.py` — Bare `except:` clauses (LOW)

**Problem:** Bare `except:` catches `BaseException` including `KeyboardInterrupt` and `SystemExit`.

**Fix:** Changed to `except Exception:` or narrower (`except ValueError:` in `ingest.py`).

---

### L10 — `src/ui/footer.py` — NiceGUI stale client crash (LOW)

**Problem:** If the browser tab closed or disconnected while the AI was processing, NiceGUI deleted the client object. When `send_message` resumed and tried to update the UI (spinner, response area, sources), it raised `RuntimeError: The client this element belongs to has been deleted` — logged as ERROR with full traceback.

**Fix:** All post-`await` UI operations wrapped in `try/except RuntimeError`, logged at DEBUG:
```python
try:
    # ... all UI updates ...
except RuntimeError:
    slog.debug(f"[{username}] Client disconnected before response could be rendered")
```
The error handler's `ui.notify()` / `ui.label()` calls are similarly guarded.

---

## Files Modified

| File | Fixes |
|------|-------|
| `outlook-plugin/.../Core/Transport/WebSocketTransport.cs` | C#-1, C#-2 |
| `outlook-plugin/.../Core/VirtualLoopbackHandler.cs` | C#-5, C#-6 |
| `outlook-plugin/.../ThisAddIn.cs` | C#-3, C#-4, C#-7, C#-8, C#-9, C#-10, C#-12 |
| `outlook-plugin/.../Core/PluginConfig.cs` | C#-11 |
| `src/bridge/loopback_processor.py` | PY-1, PY-2, PY-S2 |
| `src/bridge/manager.py` | PY-S1 |
| `src/bridge/pending_results.py` | PY-S3, L3 |
| `src/ai/session.py` | PY-3 |
| `src/ai/prompt_optimizer.py` | L2 |
| `src/ai/engines.py` | L5, L6 |
| `src/config.py` | L2 (GEMINI_TIMEOUT), L7 |
| `src/ui/footer.py` | L10 |
| `src/utils.py` | L4 |
| `src/readers.py` | L8 |
| `ingest.py` | L9 |
| `watcher.py` | L1 |
