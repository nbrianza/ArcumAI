# ArcumAI Environment Variables Reference

Complete list of all `.env` variables and C# configuration keys used by ArcumAI.

---

## Python `.env` Variables

The `.env` file is loaded in `main_nice.py` via `load_dotenv()` **before** any `src.*` imports.

Numeric variables are range-checked in `src/config.py` at import time: a non-numeric or out-of-range
value stops startup with `ValueError: Invalid config: NAME=value ...`. Ranges are listed below.

### Server & Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOST` | string | `0.0.0.0` | Server bind address |
| `PORT` | int | `8080` | Server port |
| `ALLOWED_ORIGINS` | string (csv) | `http://localhost:8080` | Comma-separated CORS allowed origins |
| `STORAGE_SECRET` | string | *(required; ephemeral random value when `ARCUMAI_ENV=dev`)* | NiceGUI session storage secret |
| `LOGIN_MAX_ATTEMPTS` | int | `6` | Failed web logins allowed per window, counted per client IP and per username (`1` - `1000`). The next attempt within the window is blocked without checking the password |
| `LOGIN_WINDOW` | int | `60` | Web login throttling window in seconds (`1` - `86400`). A block ends this many seconds after the last failed attempt |
| `SSL_CERTFILE` | string | *(empty)* | Path to the PEM server certificate (chain). Set together with `SSL_KEYFILE` to serve HTTPS and WSS; see [TLS_SETUP.md](TLS_SETUP.md) |
| `SSL_KEYFILE` | string | *(empty)* | Path to the PEM private key (not password protected; keep it outside the repository and never commit it). If only one of the two files is set, or a path does not exist, startup stops with a `ValueError` naming the variable |
| `TLS_TERMINATED_UPSTREAM` | bool | `false` | `true` declares that a reverse proxy in front of ArcumAI terminates TLS. Without TLS and without this flag, a non-loopback `HOST` logs one WARNING at startup (the server still starts) |

Note: web login throttling uses the client IP seen by the server. Behind a reverse proxy, or when many clients share one IP (NAT), all of them may count as one IP, so a few failed logins can block everyone at that address for `LOGIN_WINDOW` seconds.

### Web UI Chat Rate Limiting

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `RATE_LIMIT_MESSAGES` | int | `20` | `1` - `10000` | Web UI chat: max chat messages per logged-in user per window; further messages are refused until older ones leave the window |
| `RATE_LIMIT_WINDOW` | int | `60` | `1` - `86400` (seconds) | Web UI chat: length of the rate limit window |
| `RATE_LIMIT_STALE_TTL` | int | `3600` | `1` - `604800` (seconds) | Web UI chat limiter: users with no message for this long are removed from the in-memory state |
| `RATE_LIMIT_CLEANUP_INT` | int | `300` | `1` - `86400` (seconds) | Web UI chat limiter: interval between cleanups of idle users |

Note: the chat rate limit is counted per logged-in user, is held in memory and is reset when the server restarts.

### Hardware Profile & LLM

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `PROFILE` | string | `LOW_RESOURCE` | `HIGH_RESOURCE`, `LOW_RESOURCE` | Hardware profile selector; controls LLM defaults |
| `LLM_MODEL` | string | High: `llama3.3:70b`, Low: `llama3.2:3b` | Any Ollama model name | LLM model name |
| `EMBED_MODEL` | string | `BAAI/bge-m3` | Any HuggingFace embedding model | Embedding model name |
| `CONTEXT_WINDOW` | int | High: `16384`, Low: `4096` | `512` - `1048576` | Context window size |
| `REQUEST_TIMEOUT` | float | High: `120.0`, Low: `3600.0` | `1` - `86400` (seconds) | Request timeout in seconds |

### RAG / Text Chunking

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `CHUNK_SIZE` | int | High: `1024`, Low: `512` | `64` - `16384` | Text chunk size for embeddings |
| `CHUNK_OVERLAP` | int | High: `128`, Low: `64` | `0` - `8192`, and < `CHUNK_SIZE` | Chunk overlap for text splitting |
| `RETRIEVER_TOP_K` | int | High: `20`, Low: `10` | `1` - `500` | Number of top results from retriever |
| `FINAL_TOP_K` | int | High: `10`, Low: `5` | `1` - `500` | Final number of reranked results |

### AI Services & Prompt Optimization

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `GOOGLE_API_KEY` | string | *(required for cloud/gemini)* | Valid API key | Google Gemini API key; used by cloud engine and Gemini prompt optimization |
| `PROMPT_OPTIMIZATION` | string | `local` | `local`, `gemini`, `off` | Prompt optimization mode |
| `GEMINI_TIMEOUT` | float | `60.0` | `1` - `3600` (seconds) | Gemini API call timeout |

### Privacy / NER Masking

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `ENABLE_NER_MASKING` | string | `true` | `true`, `false` | Enable NER-based PII masking |
| `NER_SCORE_THRESHOLD` | float | `0.35` | `0.0` - `1.0` | NER confidence threshold; lower = more aggressive masking |

### Logging

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `LOG_LEVEL` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Python logging level |
| `LOG_CLIENT_CONTENT` | bool | `false` | `true`, `false` (any other value means `false`) | Development debug switch. `true` writes client content (email subjects and bodies, optimized prompts, chat and search queries, attachment, upload and archive file names, exception text) to `server.log`, `ingestion.log`, `watcher.log` and stdout. Requires `ARCUMAI_ENV=dev`: with any other `ARCUMAI_ENV` startup stops with a `ValueError`. Startup logs a WARNING while it is on. Lines that are DEBUG (MCP TX payload, masked email, Gemini output) also need `LOG_LEVEL=DEBUG`. Turn it off after use and purge `logs/`: `server.log` and `ingestion.log` (each with up to 30 rotated files) and `watcher.log` (up to 7 rotated files) |

By default the server logs never contain client content: they carry user and request ids, counts, sizes in characters or bytes, durations and a sanitized file extension. Support follows one email with its request id (`req=...` on the server, `ID: ...` in the add-in `plugin.log`). Note: `ARCUMAI_ENV=dev` also allows an ephemeral `STORAGE_SECRET`; if `ARCUMAI_ENV=dev` gains other effects, the `LOG_CLIENT_CONTENT` rule must be revisited.

The ingestion and watcher logs (`ingestion.log`, `watcher.log`, the console and the admin ingestion output panel) follow the same rule: a file is shown as `id=<first 12 hex characters of its content MD5> ext=<extension>` (the same 12 characters as the Hash column of the admin page), or `id=unavailable` when the file could not be read; successful watcher transfers show only the extension, and folder names are never shown. Files that fail to ingest are still moved to the error folder with their names. Files that were in use or that the watcher could not move stay where they were, in the drop zone or the inbox. To find the file behind an id, for example `id=3f2a9c0b1d4e`, search all three folders in PowerShell (`Get-FileHash` returns upper-case hex; `-like` is case-insensitive):

```powershell
Get-ChildItem -Path "<error folder>", "<drop zone>", "<inbox>" -Recurse -File | Get-FileHash -Algorithm MD5 | Where-Object Hash -like "3f2a9c0b1d4e*"
```

### Outlook Bridge Connection Security

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `WS_API_KEY` | string (secret) | *(empty)* | Any string; empty disables the check | Outlook WebSocket bridge: shared secret the add-in must send as `X-API-Key`; must match the add-in `ApiKey` setting. A wrong key is refused and counts as a failed attempt for `WS_AUTH_*` |
| `WS_AUTH_MAX_ATTEMPTS` | int | `5` | `1` - `1000` | Outlook WebSocket bridge (`/ws/outlook/{user_id}`): failed connection attempts (wrong API key or unregistered Outlook ID) allowed per client IP per window; further connections from that IP are refused. Not related to web login (see `LOGIN_*`) |
| `WS_AUTH_WINDOW` | int | `60` | `1` - `86400` (seconds) | Outlook WebSocket bridge: length of the failed-attempt window per client IP |
| `WS_RECEIVE_TIMEOUT` | int | `120` | `10` - `3600` (seconds) | Outlook WebSocket bridge: a connection that sends nothing for this many seconds is closed. Keep it above the add-in `HeartbeatIntervalMs` (default 30000 ms = 30 s); the default is 4 times the heartbeat |

Note: `WS_API_KEY` is a secret. Set it only in the local `.env` file, which is never committed; never log it or paste it into issues, pull requests or chats. Leaving it empty removes API key protection on the bridge, so set a long random value in production (for example `WS_API_KEY=<long-random-secret>`, replacing the placeholder) and the same value as `ApiKey` in the add-in. Raising `WS_AUTH_MAX_ATTEMPTS` or `WS_RECEIVE_TIMEOUT` weakens the bridge protection. Failed attempts are counted per client IP seen by the server: behind a reverse proxy, or when many clients share one IP (NAT), they all count as one IP, as for `LOGIN_*`.

### VSTO / Outlook Bridge

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BRIDGE_TIMEOUT` | float | `60.0` | Outlook WebSocket bridge timeout (seconds) |
| `LOOPBACK_MAX_CONCURRENT` | int | `3` | Max concurrent loopback emails (1-64) |
| `VSTO_MAX_ATTACHMENT_MB` | int | `25` | Max single attachment size (1-500 MB) |
| `VSTO_MAX_TOTAL_MB` | int | `50` | Max total attachment size (1-1000 MB) |
| `VSTO_MAX_PAYLOAD_MB` | int | `30` | Max payload size per request (1-1000 MB) |
| `VSTO_ARCUMAI_EMAIL` | string | `assistant@arcumai.ch` | Email address for loopback replies |
| `VSTO_ARCUMAI_DISPLAY_NAME` | string | `ArcumAI Assistant` | Display name for loopback sender |
| `VSTO_LOOPBACK_TIMEOUT_MS` | int | `3600000` | Timeout applied to one loopback email: how long the Outlook add-in waits for the answer before it puts a timeout reply in the inbox (10000-86400000 ms, 1 hour by default). Pushed to the add-in at `client/identify`. The server keeps processing the email after it expires. |
| `VSTO_ENABLE_VIRTUAL_LOOPBACK` | string | `true` | Enable virtual loopback (`true`/`false`) |
| `VSTO_SHOW_NOTIFICATION` | string | `true` | Show processing notification (`true`/`false`) |

### Pending Results (Offline Cache)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PENDING_RESULT_TTL_HOURS` | int | `48` | Time-to-live for cached pending results (1-8760 hours) |
| `PENDING_RESULTS_DIR` | string | `temp/pending_results` | Directory path for pending result storage |

### Admin Ingestion

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `INGEST_TIMEOUT_SEC` | int | `600` | Max runtime of an ingestion started from `/admin` (60-86400 s). The process runs at below-normal priority and only one run is allowed at a time. |

### OCR (Document Readers)

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `TESSERACT_CMD` | string (path) | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Path to `tesseract.exe` | Document OCR: Tesseract executable used for scanned PDFs (ingestion, chat uploads, Outlook loopback attachments) |
| `POPPLER_PATH` | string (path) | `C:\Program Files\Poppler\Library\bin` | Path to the Poppler `bin` folder | Document OCR: Poppler tools that turn PDF pages into images for Tesseract |

Note: these paths are not range-checked. If either path does not exist, OCR is disabled with the warning `OCR disabled: ...` and startup continues (no startup failure); only scanned or low-quality PDFs need OCR.

### Implicit / Set by Code

| Variable | Type | Value | Description |
|----------|------|-------|-------------|
| `HF_HUB_DOWNLOAD_TIMEOUT` | string | `60` | HuggingFace Hub download timeout; set automatically in `src/config.py` |

---

## C# Outlook Plugin Configuration

The C# plugin does **not** read `.env` files. Configuration is loaded from (in priority order):

1. **JSON file** — `%APPDATA%\ArcumAI\Outlook\config.json`
2. **app.config** — XML application configuration
3. **Hardcoded defaults** — `PluginConfigLoader.SetDefaults()`

### Connection

| Key | Type | Default | Validation | Description |
|-----|------|---------|------------|-------------|
| `ServerUrl` | string | `ws://localhost:8080` | Must start with `ws://` or `wss://` | WebSocket server URL |
| `UseSecureConnection` | bool | `false` | Must be `true` unless the `ServerUrl` starts with `wss://` or its host is exactly `localhost`, `127.x.x.x` or `[::1]`; otherwise the add-in refuses to connect and logs `[ERROR] Connection refused: ...` (fix the setting, restart Outlook) | Use secure WebSocket (wss://) |
| `UserId` | string | *(Windows username)* | Auto-detected if empty | User identifier |
| `AutoReconnect` | bool | `true` | — | Auto-reconnect on disconnect |
| `ReconnectDelayMs` | int | `5000` | >= 1000 | Delay between reconnection attempts (ms) |
| `MaxReconnectAttempts` | int | `720` | >= -1 (-1 = infinite) | Max reconnect attempts |
| `ConnectionTimeoutMs` | int | `30000` | > 0 | Initial connection timeout (ms) |
| `HeartbeatIntervalMs` | int | `30000` | >= 0 (0 = disabled) | Heartbeat ping interval (ms) |
| `RequestTimeoutMs` | int | `60000` | > 0 | MCP request timeout (ms) |

### Email Query

| Key | Type | Default | Validation | Description |
|-----|------|---------|------------|-------------|
| `MaxEmailResults` | int | `10` | 1 - 100 | Max emails returned per query |
| `EmailPreviewLength` | int | `200` | 50 - 1000 | Characters shown in email preview |

### Logging

| Key | Type | Default | Validation | Description |
|-----|------|---------|------------|-------------|
| `EnableLogging` | bool | `true` | — | Enable file logging |
| `LogLevel` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Minimum log level |
| `LogFilePath` | string | `%APPDATA%\ArcumAI\Outlook\logs\plugin.log` | Valid path or empty | Log file location |
| `LogClientContent` | bool | `false` | — | Development debug switch, local only (`App.config` or `config.json`, never pushed by the server). `true` writes email subjects, attachment file names and search queries to `plugin.log`; startup logs a WARNING while it is on. Turn it off after use and delete `plugin.log` and `plugin.log.old` |

### Virtual Loopback

| Key | Type | Default | Validation | Description |
|-----|------|---------|------------|-------------|
| `EnableVirtualLoopback` | bool | `true` | — | Enable virtual loopback email processing |
| `ArcumAIEmailAddress` | string | `arcumai@arcumai.swiss` | Valid email format | Email address for loopback replies |
| `ArcumAIDisplayName` | string | `ArcumAI Assistant` | — | Display name for loopback sender |
| `MaxAttachmentSizeMB` | int | `25` | 1 - 100 | Max single attachment size (MB) |
| `MaxTotalAttachmentsMB` | int | `50` | 1 - 200 | Max total attachments size (MB) |
| `MaxPayloadSizeMB` | int | `30` | > 0 | Max payload size per request (MB) |
| `LoopbackTimeoutMs` | int | `3600000` | >= 10000 | Loopback processing timeout (ms) |
| `ShowProcessingNotification` | bool | `true` | — | Show notification during processing |

---

## Example `.env` File

```env
# Server
HOST=0.0.0.0
PORT=8080
ALLOWED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
STORAGE_SECRET=your_secret_here
CHAINLIT_AUTH_SECRET=your_auth_secret_here
# TLS (see doc/TLS_SETUP.md); leave empty to run without TLS
SSL_CERTFILE=
SSL_KEYFILE=
TLS_TERMINATED_UPSTREAM=false

# Hardware profile
PROFILE=LOW_RESOURCE

# AI / API keys
GOOGLE_API_KEY=your_google_api_key
PROMPT_OPTIMIZATION=local
GEMINI_TIMEOUT=60.0

# Privacy
ENABLE_NER_MASKING=true
NER_SCORE_THRESHOLD=0.35

# Logging
LOG_LEVEL=INFO

# VSTO / Outlook bridge
VSTO_MAX_ATTACHMENT_MB=10
VSTO_ENABLE_VIRTUAL_LOOPBACK=true
VSTO_SHOW_NOTIFICATION=true
```
