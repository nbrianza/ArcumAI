# ArcumAI System - Complete Build Specification

Build a comprehensive RAG (Retrieval-Augmented Generation) document management system for legal/notarial offices in Switzerland (Canton Ticino) with the following exact specifications:

---

## 1. PROJECT OVERVIEW

**Name**: ArcumAI
**Purpose**: Intelligent document ingestion, storage, and conversational retrieval system with multi-mode AI assistance
**Target Users**: Legal professionals, notaries, fiduciaries, accountants
**Platform**: Windows (primary), with web interface
**Language**: Italian (UI and prompts)

---

## 2. TECHNOLOGY STACK

### Backend (Python 3.10+)
- **Web Framework**: FastAPI + NiceGUI 3.6.1 (for reactive UI)
- **LLM Framework**: LlamaIndex 0.14.12
- **Local LLM**: Ollama (Llama 3.2:3b or 3.3:70b)
- **Cloud LLM**: Google Gemini 2.5 Flash
- **Vector Database**: ChromaDB 1.4.1
- **Embeddings**: HuggingFace BAAI/bge-m3
- **Hybrid Search**: BM25 (rank-bm25 0.2.2) + Vector similarity
- **OCR**: Tesseract + Poppler (optional, Windows paths)
- **Authentication**: bcrypt 5.0.0
- **File Watching**: watchdog 6.0.0
- **Document Parsing**:
  - PDF: pypdf 6.6.0 + pdf2image 1.17.0
  - MSG: extract-msg 0.55.0
  - EML: email (built-in) + BeautifulSoup4
  - DOCX: docx2txt 0.9
  - XLSX: openpyxl 3.1.5

### Frontend
- **UI Framework**: NiceGUI (Python-based reactive web framework)
- **Styling**: TailwindCSS classes
- **Icons**: Material Icons
- **Avatar Generation**: ui-avatars.com API
- **Color Scheme**: Dark slate theme with orange/green accents

### Outlook Plugin (C# .NET Framework 4.8)
- **Type**: VSTO (Visual Studio Tools for Office)
- **Target**: Outlook 2016+
- **Transport**: WebSocket (System.Net.WebSockets)
- **Protocol**: JSON-RPC 2.0
- **Serialization**: Newtonsoft.Json 13.0.4
- **Configuration**: JSON + App.config support

---

## 3. ARCHITECTURE OVERVIEW

```
┌─────────────────┐
│  User Files     │  (input_utente/)
└────────┬────────┘
         │ watcher.py monitors
         ▼
┌─────────────────┐
│  Inbox Queue    │  (data_nuovi/)
└────────┬────────┘
         │ main.py processes
         ▼
┌─────────────────┐     ┌──────────────┐
│  ChromaDB       │◄────│  BM25 Index  │
│  (Vectors)      │     │  (Keywords)  │
└────────┬────────┘     └──────────────┘
         │
         │ ┌────────────────┐
         └─│  Archived Docs │ (data_archivio/)
           └────────────────┘
                  ▲
                  │
         ┌────────┴────────┐
         │  NiceGUI Web UI │ (main_nice.py)
         │  Port 8080      │
         └────────┬────────┘
                  │
         ┌────────┼────────────────┐
         │        │                │
    ┌────▼───┐ ┌─▼─────┐ ┌───────▼──────┐
    │ RAG    │ │Simple │ │ Agent/Outlook│
    │ Engine │ │Chat   │ │ (WebSocket)  │
    └────────┘ └───────┘ └───────┬──────┘
                                  │
                         ┌────────▼────────┐
                         │ Outlook Plugin  │
                         │ (C# VSTO)       │
                         └─────────────────┘
```

---

## 4. DIRECTORY STRUCTURE

```
ArcumAI/
├── main.py                    # Batch ingestion script
├── main_nice.py               # Web UI entry point
├── watcher.py                 # File watcher service
├── admin_tool.py              # CLI user management
├── rag_query.py              # CLI query tool
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables
├── users.json                 # User database (bcrypt hashes)
│
├── src/
│   ├── __init__.py
│   ├── engine.py             # Multi-mode AI engine logic
│   ├── config.py             # Centralized configuration
│   ├── database.py           # ChromaDB access layer
│   ├── readers.py            # Smart document readers (PDF OCR, MSG, EML)
│   ├── auth.py               # User authentication (bcrypt)
│   ├── bridge.py             # WebSocket bridge for Outlook
│   ├── logger.py             # Logging configuration
│   ├── utils.py              # File operations, triggers, hashing
│   └── ui/
│       ├── header.py         # Top navigation bar
│       ├── sidebar.py        # Mode selector + user info
│       ├── chat_area.py      # Message display container
│       └── footer.py         # Input field + file upload
│
├── input_utente/             # Drop zone (watched by watcher.py)
├── data_nuovi/               # Inbox queue
├── data_archivio/            # Successfully processed files
├── data_error/               # Failed processing
├── data_duplicati/           # Duplicate files (MD5 check)
├── chroma_db/                # ChromaDB persistence
├── storage_bm25/             # BM25 index
├── logs/                     # Application logs
├── triggers/                 # Text files with RAG keywords
│   ├── global.txt
│   └── chat.txt
│
└── outlook-plugin/
    └── ArcumAI.Outlook/
        └── ArcumAI.OutlookAddIn/
            ├── ThisAddIn.cs           # Main plugin logic
            ├── Core/
            │   ├── IMcpTransport.cs   # Transport interface
            │   ├── WebSocketTransport.cs
            │   └── PluginConfig.cs    # Configuration manager
            ├── config.json            # Plugin settings
            ├── App.config             # XML alternative config
            └── *.csproj               # Visual Studio project
```

---

## 5. CORE MODULES SPECIFICATION

### 5.1 `src/config.py`

**Purpose**: Centralized configuration with hardware profiles

**Key Settings**:
```python
PROFILE = "LOW_RESOURCE" | "HIGH_RESOURCE"

LOW_RESOURCE:
  - LLM: llama3.2:3b
  - Context: 4096 tokens
  - Chunk: 512 tokens, overlap 64
  - Top-K: 10

HIGH_RESOURCE:
  - LLM: llama3.3:70b
  - Context: 16384 tokens
  - Chunk: 1024 tokens, overlap 128
  - Top-K: 20

PATHS:
  - INBOX_DIR: data_nuovi/
  - ARCHIVE_DIR: data_archivio/
  - CHROMA_PATH: chroma_db/
  - BM25_PATH: storage_bm25/
  - DROP_DIR: input_utente/

WATCH_EXTENSIONS: .pdf, .msg, .eml, .docx, .xlsx, .txt

ROLE_PROMPTS:
  - ADMIN: Administrative assistant (email drafts, deadlines)
  - LEGAL: Legal expert (Swiss CO/CC, contract analysis)
  - EXECUTIVE: Strategic advisor (bullet points, money focus)
  - COMMERCIALISTA: Accountant (commercial focus)
  - DEFAULT: General legal assistant
```

**OCR Configuration**:
```python
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\Program Files\Poppler\Library\bin"
OCR_ENABLED = check if both exist
```

---

### 5.2 `src/readers.py`

**Classes**:

#### `SmartPDFReader`
- **Hybrid OCR Detection**:
  1. Extract native text with `pypdf.PdfReader`
  2. Detect scanner signatures: "ScannerPro", "CamScanner", "Adobe Scan", etc.
  3. Linguistic analysis: Check if 10%+ of text is valid Italian words
  4. If scanned → Use Tesseract OCR on images from `pdf2image`
  5. If native → Use extracted text directly

#### `MyOutlookReader` (for .msg)
- Extract: subject, sender, recipients, body (HTML→plain), attachments list
- Format: "Soggetto: X\nDa: Y\nA: Z\n\nCorpo: ..."
- Use `extract_msg` library

#### `MyEmlReader` (for .eml)
- Parse with `email` module
- Extract headers + body (handle multipart)
- Convert HTML to plain text with BeautifulSoup

---

### 5.3 `src/engine.py`

**Class**: `UserSession`

**Attributes**:
```python
username: str
role: str (ADMIN|LEGAL|EXECUTIVE|COMMERCIALISTA|DEFAULT)
is_cloud: bool (toggle for Gemini)
uploaded_context: str (file content)
global_history: List[ChatMessage] (persistent chat memory)
outlook_id: str (from users.json)
tools: List[FunctionTool] (for agent mode)
```

**Engines** (lazy-loaded):
1. **RAG Engine** (`ContextChatEngine`):
   - Retriever: QueryFusionRetriever (vector + BM25, reciprocal rerank)
   - Memory: ChatMemoryBuffer (8192 tokens)
   - System prompt: Role-specific from ROLE_PROMPTS
   - Context template: Custom with "ignore if general knowledge" clause

2. **Simple Local Engine** (`SimpleChatEngine`):
   - For file uploads or general chat
   - Memory: 16384 tokens
   - System prompt: "Analyze provided text only"

3. **Cloud Engine** (`SimpleChatEngine` with Gemini):
   - Gemini 2.5 Flash (8192 token context)
   - System prompt: "Use global knowledge, not chat"

4. **Agent Engine** (`ReActAgent` - Workflow or Legacy):
   - Tools: `tool_read_email`, `tool_get_calendar`
   - Connects to Outlook via WebSocket bridge
   - Tools have docstrings with "CRITICAL INSTRUCTIONS" to prevent tool confusion

**Engine Selection Logic** (`decide_engine`):
1. Check explicit selectors: `@rag`, `@cerca`, `@simple`, `@chat`, `@outlook`, `@agent`
2. Check global RAG triggers (from `triggers/global.txt`)
3. Check Outlook keywords: "email", "posta", "calendario", "agenda"
4. Fallback: LLM classifier (prompt: "Classify intent: RAG, AGENT, or SIMPLE")
5. Default: `"RAG"`

**Query Flow** (`run_chat_action`):
```python
1. Determine engine (cloud override OR file mode OR decide_engine)
2. Sync global_history → engine.memory.chat_history
3. Build final_input:
   - Format history as text (last 6 messages)
   - Inject uploaded file if present
   - Add clean query
4. Call engine:
   - Workflow: .run(user_msg=...)
   - ChatEngine: .achat(...)
   - Sync: .chat(...) via run.io_bound
5. Append to global_history (user + assistant messages)
6. Return (response_str, used_mode)
```

**CRITICAL BUGS TO AVOID**:
- ❌ **DO NOT** convert response to string before returning if you want source_nodes
- ❌ **DO NOT** call `engine.memory.reset()` AFTER setting `chat_history`
- ✅ **DO** return the raw response object for RAG source extraction

---

### 5.4 `src/bridge.py`

**Class**: `OutlookBridgeManager`

**Attributes**:
```python
active_connections: Dict[str, WebSocket]  # user_id → WebSocket
pending_requests: Dict[str, asyncio.Future]  # request_id → Future
```

**Methods**:

#### `connect(websocket, user_id)`
- Accept WebSocket connection
- Store in `active_connections`

#### `send_mcp_request(user_id, tool_name, args)` → `Any`
- Generate unique request_id (UUID)
- Build JSON-RPC 2.0 payload:
  ```json
  {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "search_emails", "arguments": {"query": "..."}},
    "id": "abc-123-uuid"
  }
  ```
- Create asyncio.Future for response
- Send to WebSocket
- Await response with 30s timeout
- Return result or error message

#### `handle_incoming_message(user_id, message)`
- Parse JSON response
- Match `id` to pending request
- Resolve Future with result
- Delete from pending_requests

**WebSocket Endpoint** (in `main_nice.py`):
```python
@app.websocket("/ws/outlook/{user_id}")
async def outlook_endpoint(websocket: WebSocket, user_id: str):
    await bridge_manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            await bridge_manager.handle_incoming_message(user_id, data)
    except WebSocketDisconnect:
        bridge_manager.disconnect(user_id)
```

---

### 5.5 `main.py` (Batch Ingestion)

**Process Flow**:
```python
1. Acquire lock file (prevent concurrent runs)
2. Scan INBOX_DIR for files matching WATCH_EXTENSIONS
3. For each file:
   a. Calculate MD5 hash
   b. Check ChromaDB for duplicates
   c. If duplicate → move to data_duplicati/
   d. Read + chunk file (read_and_chunk_file)
   e. Add to batch accumulator
4. Every BATCH_SIZE files (default 10):
   a. Call index.insert_nodes(accumulated_nodes)
   b. Move files to data_archivio/
   c. Clear accumulator + gc.collect()
5. After all files:
   a. Rebuild BM25 index from all ChromaDB nodes
   b. Persist BM25 to storage_bm25/
6. Clean empty folders in INBOX_DIR
7. Release lock file
```

**Key Function** (`read_and_chunk_file`):
```python
def read_and_chunk_file(file_path):
    ext = file_path.suffix.lower()

    if ext == ".pdf": docs = SmartPDFReader().load_data(file_path)
    elif ext == ".docx": docs = DocxReader().load_data(file_path)
    elif ext == ".xlsx": docs = PandasExcelReader().load_data(file_path)
    elif ext == ".msg": docs = MyOutlookReader().load_data(file_path)
    elif ext == ".eml": docs = MyEmlReader().load_data(file_path)
    else: return None, "SKIP_EXT"

    current_hash = calcola_hash_file(file_path)

    for doc in docs:
        doc.metadata["file_hash"] = current_hash
        doc.metadata["filename"] = file_path.name
        doc.metadata["file_path"] = str(relative_path)

    nodes = Settings.text_splitter.get_nodes_from_documents(docs)
    return nodes, current_hash
```

**NOTE**: Add handler for `.txt` files:
```python
elif ext in [".txt", ".md"]:
    text = file_path.read_text(encoding='utf-8', errors='ignore')
    docs = [Document(text=text)]
```

---

### 5.6 `watcher.py`

**Class**: `StagingHandler(FileSystemEventHandler)`

**Behavior**:
```python
1. Monitor DROP_DIR recursively
2. On file event (created/modified/moved):
   - Skip if directory or temp file (~$, .)
   - Skip if extension not in WATCH_EXTENSIONS
   - Set needs_processing = True
   - Record last_event_time
3. Main loop (1 second tick):
   - If needs_processing and (time_since_last > WATCH_DEBOUNCE):
     a. Scan DROP_DIR for valid files
     b. Move files to INBOX_DIR (preserve folder structure)
     c. Run subprocess: python main.py
     d. Clean empty folders in DROP_DIR
```

**Robustness**:
- Health check for DROP_DIR (exists, readable, writable)
- Retry on network share disconnection (5 second intervals)
- Time-based log rotation (TimedRotatingFileHandler)

---

### 5.7 `src/ui/` Modules

#### `header.py`
```python
def create_header(user_data, session, update_ui_callback):
    # Top bar: Logo + User Avatar + Cloud Toggle + Logout
    # Cloud toggle calls update_ui_callback(is_cloud)
```

#### `sidebar.py`
```python
def create_sidebar(user_data):
    # Left panel: Current mode display + User info
    # Returns mode_display label for updates
```

#### `chat_area.py`
```python
def create_chat_area():
    # Scrollable column for chat messages
    # Returns container for appending messages
```

#### `footer.py`
```python
def create_footer(session, user_data, chat_container, mode_display):
    # Input field + Upload button + Send button
    # File upload handler:
      - Save to temp_ghost_upload.pdf
      - Use SmartPDFReader for PDFs
      - Run in separate thread (run.io_bound) to prevent UI freeze
      - Limit to 10k chars for session.uploaded_context
    # Send message handler:
      - Call session.run_chat_action(text)
      - Render user message + bot response
      - Show sources if RAG mode and response has source_nodes
    # Returns (input_field, upload_btn) for header modifications
```

**Source Rendering**:
```python
if not session.is_cloud and used_mode == "RAG" and hasattr(response, "source_nodes"):
    for node in response.source_nodes:
        fname = node.metadata.get("filename")
        file_path = node.metadata.get("file_path")
        # Create clickable link: /documents/{relative_path}
        # Display with PDF icon or description icon
```

---

### 5.8 `main_nice.py`

**FastAPI + NiceGUI Integration**:
```python
from fastapi import WebSocket
from nicegui import ui, app

# Add static file routes
app.add_static_files('/documents', str(ARCHIVE_DIR))
app.add_static_files('/assets', str(ASSETS_DIR))

# WebSocket endpoint for Outlook
@app.websocket("/ws/outlook/{user_id}")
async def outlook_endpoint(...): ...

# Login page
@ui.page('/login')
def login_page():
    # Load users.json
    # Verify password with bcrypt
    # Set app.storage.user (authenticated, username, role, full_name)

# Main page
@ui.page('/')
async def main_page():
    # Check authentication
    # Create UserSession(username, role)
    # Build UI: sidebar, chat_area, footer, header
    # Wire mode toggle callback

if __name__ == "__main__":
    ui.run(
        title='Arcum AI',
        host='0.0.0.0',
        port=8080,
        favicon='🛡️',
        reload=False,
        storage_secret=os.getenv('STORAGE_SECRET', 'default-secret')
    )
```

**UI Color Scheme**:
- Background: `bg-slate-900`
- Cards: `bg-slate-800`, `border-slate-700`
- User messages: Gray avatar, right-aligned
- Bot messages:
  - Local: Green avatar (`bg-green-2`)
  - Cloud: Orange avatar (`bg-orange-2`)
  - RAG: Purple mode indicator
  - File: Blue mode indicator

---

## 6. OUTLOOK PLUGIN SPECIFICATION

### 6.1 `ThisAddIn.cs`

**Lifecycle**:
```csharp
ThisAddIn_Startup:
  1. Load PluginConfig.Instance
  2. Validate configuration
  3. Log startup with full config dump
  4. Create WebSocketTransport
  5. Wire MessageReceived + Disconnected events
  6. ConnectToArcum()
  7. Hook Outlook.Quit event

ConnectToArcum:
  1. Call _transport.ConnectAsync(config.ServerUrl, config.UserId)
  2. If success: Reset reconnectAttempt, StartHeartbeat()
  3. If fail: Log error, ScheduleReconnect()

ScheduleReconnect:
  1. Check AutoReconnect enabled
  2. Check MaxReconnectAttempts (-1 = infinite)
  3. Wait ReconnectDelayMs
  4. Call ConnectToArcum()

OnMessageFromArcum:
  1. Parse JSON-RPC request
  2. Extract method, id, params
  3. If "tools/call":
     - tool_name = "search_emails" → GetEmails(query)
     - tool_name = "get_calendar" → GetCalendar(filter)
  4. Build JSON-RPC response
  5. Send back via WebSocket

GetEmails(query):
  1. Get default Inbox folder
  2. Sort by ReceivedTime descending
  3. Filter by query (search in Subject + SenderName)
  4. Limit to config.MaxEmailResults
  5. Return: "[ReceivedTime] DA: Sender | OGGETTO: Subject | ANTEPRIMA: Body..."

GetCalendar(filter):
  1. Get default Calendar folder
  2. Include recurring events
  3. Filter: "today", "tomorrow", "week"
  4. Return: "[Start - End] Subject @ Location"
```

**Heartbeat**:
```csharp
System.Timers.Timer _heartbeatTimer;
Interval = config.HeartbeatIntervalMs (default 30s)
Elapsed: Send {"method":"heartbeat"}
If send fails → StopHeartbeat, ScheduleReconnect
```

---

### 6.2 `Core/WebSocketTransport.cs`

```csharp
public class WebSocketTransport : IMcpTransport
{
    private ClientWebSocket _ws;
    private CancellationTokenSource _cts;

    public event EventHandler<string> MessageReceived;
    public event EventHandler Disconnected;

    public async Task ConnectAsync(string baseUri, string userId)
    {
        var uri = $"{baseUri}/ws/outlook/{userId}";
        var timeout = PluginConfig.Instance.ConnectionTimeoutMs;

        await _ws.ConnectAsync(new Uri(uri), timeout_cts.Token);
        _ = ReceiveLoop(); // Fire and forget background listener
    }

    private async Task ReceiveLoop()
    {
        var buffer = new byte[8192];

        while (IsConnected)
        {
            var result = await _ws.ReceiveAsync(buffer, _cts.Token);

            if (result.MessageType == WebSocketMessageType.Close)
                break;

            // Handle multi-frame messages (>8KB)
            if (!result.EndOfMessage)
            {
                // Accumulate full message
            }

            string message = Encoding.UTF8.GetString(buffer, 0, result.Count);
            MessageReceived?.Invoke(this, message);
        }

        Disconnected?.Invoke(this, EventArgs.Empty);
    }
}
```

---

### 6.3 `Core/PluginConfig.cs`

**Singleton Pattern**:
```csharp
public class PluginConfig
{
    public static PluginConfig Instance { get; }

    // Properties
    public string ServerUrl { get; set; } = "ws://localhost:8080";
    public int MaxReconnectAttempts { get; set; } = 10;
    public int ReconnectDelayMs { get; set; } = 5000;
    public int HeartbeatIntervalMs { get; set; } = 30000;
    public bool AutoReconnect { get; set; } = true;
    public string LogLevel { get; set; } = "INFO";
    // ... 10+ more properties

    private static PluginConfig LoadConfiguration()
    {
        // Priority: config.json → App.config → Defaults
        var configPath = "%APPDATA%\\ArcumAI\\Outlook\\config.json";
        if (File.Exists(configPath))
            return JsonConvert.DeserializeObject<PluginConfig>(json);

        var config = new PluginConfig();
        config.LoadFromAppConfig();
        return config;
    }

    public bool Validate(out string error)
    {
        // Validate URL format, value ranges, etc.
    }

    public string GetWebSocketUrl()
    {
        return $"{ServerUrl}/ws/outlook/{UserId}";
    }
}
```

---

## 7. AUTHENTICATION SYSTEM

**File**: `users.json`
```json
{
  "admin": {
    "name": "Administrator",
    "role": "ADMIN",
    "pw_hash": "$2b$12$...",
    "outlook_id": "admin"
  },
  "notaio": {
    "name": "Marco Rossi",
    "role": "LEGAL",
    "pw_hash": "$2b$12$...",
    "outlook_id": "marco.rossi"
  }
}
```

**Functions** (in `src/auth.py`):
```python
def load_users() -> dict:
    return json.loads(USERS_FILE.read_text())

def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed.encode())

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()
```

**Admin Tool** (`admin_tool.py`):
```bash
python admin_tool.py
# Menu:
# 1. List users
# 2. Add user (username, name, role, password, outlook_id)
# 3. Delete user
# 4. Change password
# 5. Change role
```

---

## 8. CONFIGURATION FILES

### `.env`
```bash
GOOGLE_API_KEY=your-gemini-api-key
STORAGE_SECRET=your-nicegui-session-secret
HF_HUB_DOWNLOAD_TIMEOUT=60
```

### `triggers/global.txt`
```
contratto
regolamento
articolo
legge
normativa
atto notarile
clausola
scadenza
mandato
procura
```

### `triggers/chat.txt`
```
ciao
buongiorno
come stai
aiutami
```

---

## 9. KEY ALGORITHMS

### Duplicate Detection
```python
def calcola_hash_file(file_path: Path) -> str:
    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()

# In main.py:
existing = collection.get(where={"file_hash": current_hash}, limit=1)
if existing and existing["ids"]:
    move_to(DUPLICATES_DIR)
```

### File Movement with Retry
```python
def sposta_file_con_struttura(src, base_src, base_dest, max_retries=3):
    relative = src.relative_to(base_src)
    dest = base_dest / relative
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            shutil.move(str(src), str(dest))
            return
        except PermissionError:
            time.sleep(2 ** attempt)  # Exponential backoff
    raise
```

### Hybrid Retrieval (RAG)
```python
vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
bm25_retriever = BM25Retriever.from_persist_dir(BM25_PATH)

retriever = QueryFusionRetriever(
    [vector_retriever, bm25_retriever],
    similarity_top_k=RETRIEVER_TOP_K,
    num_queries=1,
    mode="reciprocal_rerank",
    use_async=False
)
```

---

## 10. DEPLOYMENT

### Python Backend
```bash
# Install Ollama
curl https://ollama.ai/install.sh | sh
ollama pull llama3.2:3b

# Install Python dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Set GOOGLE_API_KEY

# Create first user
python admin_tool.py

# Start watcher (background)
nohup python watcher.py > /dev/null 2>&1 &

# Start web UI
python main_nice.py
```

### Outlook Plugin
```bash
# Build in Visual Studio 2019+
msbuild ArcumAI.OutlookAddIn.csproj /p:Configuration=Release

# Install
Double-click: bin\Release\ArcumAI.OutlookAddIn.vsto

# Configure
Copy config.json to: %APPDATA%\ArcumAI\Outlook\
Edit ServerUrl if needed

# Restart Outlook
```

### Windows Service (Optional)
```bash
# Use NSSM (Non-Sucking Service Manager)
nssm install ArcumWatcher "C:\Python310\python.exe" "C:\ArcumAI\watcher.py"
nssm install ArcumUI "C:\Python310\python.exe" "C:\ArcumAI\main_nice.py"
nssm start ArcumWatcher
nssm start ArcumUI
```

---

## 11. TESTING SCENARIOS

### 1. Basic RAG Query
```python
User: "Trova tutti i contratti del 2024"
Expected:
  - Engine: RAG
  - Retriever: Hybrid (vector + BM25)
  - Sources: List of PDF files with "contratto" + "2024"
  - UI: Clickable source links displayed
```

### 2. File Upload Analysis
```python
User uploads: contract.pdf
User: "Quali sono le clausole critiche?"
Expected:
  - Engine: SIMPLE (File Reader mode)
  - Context: First 10k chars of contract.pdf
  - Response: Analysis based on file content only
```

### 3. Outlook Integration
```python
User: "Mostrami le email di oggi"
Expected:
  - Engine: AGENT
  - Tool call: tool_read_email(query="")
  - WebSocket: JSON-RPC to Outlook plugin
  - Response: Last 10 emails from inbox
```

### 4. Cloud Mode
```python
User toggles Cloud Mode
User: "Chi è il presidente della Svizzera?"
Expected:
  - Engine: CLOUD (Gemini)
  - No retrieval
  - Response: Use Gemini's knowledge
```

### 5. Duplicate Detection
```python
Drop same file twice in input_utente/
Expected:
  - First: Ingested → data_archivio/
  - Second: Detected → data_duplicati/
  - ChromaDB: Only 1 set of nodes
```

---

## 12. KNOWN ISSUES TO FIX

### Critical
1. **RAG Sources Lost**: `engine.py` returns `str(response)` instead of raw object
   - Fix: Return `response_obj` and convert to string only in UI

2. **Chat History Ignored**: `memory.reset()` called AFTER setting history
   - Fix: Remove line 340 or move before line 316

3. **.txt Files Orphaned**: Watched but not ingested
   - Fix: Add `.txt` handler in `read_and_chunk_file()`

### Minor
4. **Hardcoded Secret**: `storage_secret` in `main_nice.py`
   - Fix: Load from `.env`

5. **Temp File Collision**: Fixed filename `temp_ghost_upload.pdf`
   - Fix: Use `tempfile.NamedTemporaryFile()` or UUID

---

## 13. SUCCESS CRITERIA

The system is complete when:

✅ Drop a PDF → Auto-ingested within 10 seconds
✅ Ask "Trova X" → Returns relevant documents with clickable sources
✅ Upload file + ask → Analyzes file content
✅ Toggle cloud → Uses Gemini for general knowledge
✅ Connect Outlook → Can read emails and calendar via chat
✅ Login as different roles → Get role-specific prompts
✅ Duplicates detected → Moved to separate folder
✅ BM25 index rebuilt after ingestion
✅ Plugin config via JSON → All settings respected
✅ Auto-reconnect on disconnect → Logs show retry attempts

---

## 14. ADDITIONAL NOTES

- **Encoding**: All files UTF-8, especially `requirements.txt`
- **Logging**: Structured logs with levels (DEBUG/INFO/WARNING/ERROR)
- **Error Handling**: Graceful degradation, never crash UI
- **Performance**: Batch processing (10 files at a time), gc.collect() between batches
- **Security**: bcrypt (cost 12), no raw passwords, WebSocket localhost-only by default
- **Extensibility**: Easy to add new roles, triggers, or document types

---

**Build this system exactly as specified. The result should be a production-ready, enterprise-grade RAG assistant for legal/notarial workflows.**
