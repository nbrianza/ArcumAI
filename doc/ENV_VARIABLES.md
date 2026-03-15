# ArcumAI Environment Variables Reference

Complete list of all `.env` variables and C# configuration keys used by ArcumAI.

---

## Python `.env` Variables

The `.env` file is loaded in `main_nice.py` via `load_dotenv()` **before** any `src.*` imports.

### Server & Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOST` | string | `0.0.0.0` | Server bind address |
| `PORT` | int | `8080` | Server port |
| `ALLOWED_ORIGINS` | string (csv) | `http://localhost:8080` | Comma-separated CORS allowed origins |
| `STORAGE_SECRET` | string | `CHIAVE_SEGRETA_ARCUM_AI_V2_DEV_DEFAULT` | NiceGUI session storage secret |
| `CHAINLIT_AUTH_SECRET` | string | *(required)* | Chainlit authentication secret |

### Hardware Profile & LLM

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `PROFILE` | string | `LOW_RESOURCE` | `HIGH_RESOURCE`, `LOW_RESOURCE` | Hardware profile selector; controls LLM defaults |
| `LLM_MODEL` | string | High: `llama3.3:70b`, Low: `llama3.2:3b` | Any Ollama model name | LLM model name |
| `EMBED_MODEL` | string | `BAAI/bge-m3` | Any HuggingFace embedding model | Embedding model name |
| `CONTEXT_WINDOW` | int | High: `16384`, Low: `4096` | Any positive int | Context window size |
| `REQUEST_TIMEOUT` | float | High: `120.0`, Low: `3600.0` | Any positive float (seconds) | Request timeout in seconds |

### RAG / Text Chunking

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CHUNK_SIZE` | int | High: `1024`, Low: `512` | Text chunk size for embeddings |
| `CHUNK_OVERLAP` | int | High: `128`, Low: `64` | Chunk overlap for text splitting |
| `RETRIEVER_TOP_K` | int | High: `20`, Low: `10` | Number of top results from retriever |
| `FINAL_TOP_K` | int | High: `10`, Low: `5` | Final number of reranked results |

### AI Services & Prompt Optimization

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `GOOGLE_API_KEY` | string | *(required for cloud/gemini)* | Valid API key | Google Gemini API key; used by cloud engine and Gemini prompt optimization |
| `PROMPT_OPTIMIZATION` | string | `local` | `local`, `gemini`, `off` | Prompt optimization mode |
| `GEMINI_TIMEOUT` | float | `60.0` | Any positive float (seconds) | Gemini API call timeout |

### Privacy / NER Masking

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `ENABLE_NER_MASKING` | string | `true` | `true`, `false` | Enable NER-based PII masking |
| `NER_SCORE_THRESHOLD` | float | `0.35` | `0.0` - `1.0` | NER confidence threshold; lower = more aggressive masking |

### Logging

| Variable | Type | Default | Allowed Values | Description |
|----------|------|---------|----------------|-------------|
| `LOG_LEVEL` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Python logging level |

### VSTO / Outlook Bridge

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BRIDGE_TIMEOUT` | float | `60.0` | Outlook WebSocket bridge timeout (seconds) |
| `LOOPBACK_TIMEOUT` | float | `3600.0` | Loopback email processing timeout (seconds) |
| `LOOPBACK_MAX_CONCURRENT` | int | `3` | Max concurrent loopback emails |
| `VSTO_MAX_ATTACHMENT_MB` | int | `25` | Max single attachment size (1-100 MB) |
| `VSTO_MAX_TOTAL_MB` | int | `50` | Max total attachment size (1-200 MB) |
| `VSTO_MAX_PAYLOAD_MB` | int | `30` | Max payload size per request (MB) |
| `VSTO_ARCUMAI_EMAIL` | string | `assistant@arcumai.ch` | Email address for loopback replies |
| `VSTO_ARCUMAI_DISPLAY_NAME` | string | `ArcumAI Assistant` | Display name for loopback sender |
| `VSTO_LOOPBACK_TIMEOUT_MS` | int | `3600000` | Loopback timeout in milliseconds |
| `VSTO_ENABLE_VIRTUAL_LOOPBACK` | string | `true` | Enable virtual loopback (`true`/`false`) |
| `VSTO_SHOW_NOTIFICATION` | string | `true` | Show processing notification (`true`/`false`) |

### Pending Results (Offline Cache)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PENDING_RESULT_TTL_HOURS` | int | `48` | Time-to-live for cached pending results (hours) |
| `PENDING_RESULTS_DIR` | string | `temp/pending_results` | Directory path for pending result storage |

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
| `UseSecureConnection` | bool | `false` | — | Use secure WebSocket (wss://) |
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
