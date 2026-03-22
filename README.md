# ArcumAI

**Privacy-first AI assistant for Swiss legal and fiduciary offices.**

ArcumAI combines a RAG (Retrieval-Augmented Generation) pipeline over legal documents with an Outlook integration, enabling professionals to query their document archive via a web chat interface or directly from their email client.

## Features

- **Hybrid RAG search** — ChromaDB vector search + BM25 keyword search over ingested documents
- **Multi-format document ingestion** — PDF (with OCR fallback), DOCX, MSG, EML, XLSX, TXT
- **Privacy-first design** — local LLM via Ollama by default; optional cloud (Gemini) with automatic PII masking (NER-based)
- **Outlook integration** — C# VSTO add-in intercepts emails to a designated address, sends them to the AI backend, and returns responses as reply emails
- **Web chat UI** — NiceGUI-based interface with authentication, conversation history, and file upload
- **Multi-language** — Italian, English, German, French
- **Hardware profiles** — configurable for high-resource servers or low-resource laptops

## Architecture

```
┌─────────────────┐     WebSocket/JSON-RPC     ┌──────────────────┐
│  Outlook VSTO   │ ◄──────────────────────────►│  Python Backend  │
│  Add-in (C#)    │                             │  (FastAPI/NiceGUI)│
└─────────────────┘                             └────────┬─────────┘
                                                         │
                                              ┌──────────┼──────────┐
                                              │          │          │
                                         ┌────▼───┐ ┌───▼────┐ ┌───▼───┐
                                         │ Ollama │ │ Gemini │ │ChromaDB│
                                         │ (local)│ │ (cloud)│ │+ BM25  │
                                         └────────┘ └────────┘ └────────┘
```

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENTS                                  │
│  ┌──────────────┐  ┌─────────────────────────────────────────┐  │
│  │   Web        │  │  Outlook VSTO Plugin (C#)               │  │
│  │  Browser     │  │  WebSocket → ws://server:8080/ws/...    │  │
│  └──────┬───────┘  └──────────────┬──────────────────────────┘  │
│         │                         │                             │
└─────────┼─────────────────────────┼─────────────────────────────┘
          │ HTTP                    │ WebSocket (JSON-RPC / MCP)
          ▼                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PYTHON BACKEND (main_nice.py)                 │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐     │
│  │  FastAPI │  │  NiceGUI UI  │  │  OutlookBridgeManager  │     │
│  │  /health │  │  /login, /   │  │  /ws/outlook/{user_id} │     │
│  └──────────┘  └──────┬───────┘  └───────────┬────────────┘     │
│                       │                      │                  │
│              ┌────────▼──────────────────────▼────────┐         │
│              │           UserSession                  │         │
│              │  ┌─────────┐ ┌───────┐ ┌──────┐ ┌────┐ │         │
│              │  │RAG Eng. │ │Simple │ │Cloud │ │Agent││         │
│              │  │(Hybrid) │ │(Local)│ │(Gem.)│ │(MCP)││         │
│              │  └────┬────┘ └───────┘ └──────┘ └────┘ |         │
│              └───────┼────────────────────────────────┘         │
│                      ▼                                          │
│  ┌─────────────────────────────────────────────────────┐        │
│  │  DATA LAYER                                         │        │
│  │  ChromaDB (vectors) + BM25 (keywords) + users.json  │        │
│  └─────────────────────────────────────────────────────┘        │
│                                                                 │
│  ┌──────────────────┐  ┌─────────────────────┐                  │
│  │  watcher.py       │  │  ingest.py         │                  │
│  │  (folder monitor) │──▶│  (batch processing)│                 │
│  └──────────────────┘  └─────────────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- **Python 3.10+**
- **Ollama** — running locally with a model pulled (e.g., `ollama pull llama3.2:3b`)
- **Tesseract OCR** — for PDF OCR fallback (optional)
- **Poppler** — for PDF processing (optional)
- **Visual Studio 2022** — for building the Outlook plugin (optional)
- **.NET Framework 4.8** — for the VSTO add-in (optional)

## Quick Start

### 1. Clone and set up Python environment

```bash
git clone https://github.com/nbrianza/ArcumAI.git
cd ArcumAI
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure environment

Copy the example and edit with your settings:

```bash
cp .env.example .env
```

Key variables (see [doc/ENV_VARIABLES.md](doc/ENV_VARIABLES.md) for the full reference):

| Variable | Description |
|----------|-------------|
| `PROFILE` | `HIGH_RESOURCE` or `LOW_RESOURCE` |
| `LLM_MODEL` | Ollama model name (e.g., `llama3.2:3b`) |
| `GOOGLE_API_KEY` | Required only for cloud/Gemini mode |
| `STORAGE_SECRET` | Session storage secret (change from default) |

### 3. Ingest documents

Place documents in the `data_nuovi/` folder, then run:

```bash
python ingest.py
```

### 4. Start the server

```bash
python main_nice.py
```

The web UI will be available at `http://localhost:8080`.

### 5. Outlook plugin (optional)

See the [Outlook plugin documentation](doc/VSTO_VIRTUAL_LOOPBACK_PHASE1.md) for build and installation instructions.

## Project Structure

```
ArcumAI/
├── main_nice.py              # Application entry point
├── ingest.py                 # Document ingestion pipeline
├── watcher.py                # Folder watcher for auto-ingestion
├── src/
│   ├── ai/                   # AI engines, sessions, prompt optimization, NER masking
│   ├── bridge/               # WebSocket bridge to Outlook (manager, loopback, queues)
│   ├── ui/                   # NiceGUI web interface components
│   ├── auth.py               # Authentication (bcrypt)
│   ├── config.py             # Configuration & hardware profiles
│   ├── database.py           # User/session database
│   ├── logger.py             # Logging setup
│   ├── readers.py            # Document readers (PDF, DOCX, MSG, etc.)
│   └── utils.py              # Utilities
├── outlook-plugin/           # C# VSTO Outlook add-in
│   └── ArcumAI.Outlook/
│       └── ArcumAI.OutlookAddIn/
│           ├── Core/         # Transport, Loopback, Config, Logger
│           └── ThisAddIn.cs  # Add-in entry point
├── tests/                    # Test suite
├── doc/                      # Documentation
└── requirements.txt          # Python dependencies
```

## Configuration

ArcumAI uses environment variables for all configuration. See [doc/ENV_VARIABLES.md](doc/ENV_VARIABLES.md) for the complete reference.

The system supports two hardware profiles:
- **HIGH_RESOURCE** — larger models, bigger context windows, more retrieval results
- **LOW_RESOURCE** — optimized for laptops and limited hardware

## Security Notes

- **Never commit `.env` files** — they contain API keys and secrets
- **Local-first** — by default, all AI processing uses Ollama (no data leaves your machine)
- **PII masking** — when using cloud APIs, NER-based masking automatically redacts personal data before sending
- **Authentication** — bcrypt password hashing, JWT session tokens

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 Nicolas Brianza
