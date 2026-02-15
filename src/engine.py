import sys
import os
import chromadb
from nicegui import run
from llama_index.core.llms import ChatMessage, MessageRole

# LlamaIndex Imports
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.llms.gemini import Gemini
from llama_index.core.tools import FunctionTool

# --- FIX IMPORT: ADAPTATION TO WORKFLOW VERSION (v0.12+) ---
try:
    # Attempt 1: New Workflow API (Required by your error log)
    from llama_index.core.agent.workflow import ReActAgent as WorkflowReActAgent
except ImportError:
    WorkflowReActAgent = None

try:
    # Attempt 2: Old API (Fallback for older versions)
    from llama_index.core.agent import ReActAgent as LegacyReActAgent
except ImportError:
    LegacyReActAgent = None

# Config and Utils
from src.utils import load_global_triggers, load_chat_triggers
GLOBAL_TRIGGERS_LIST = load_global_triggers()
GLOBAL_CHAT_TRIGGERS = load_chat_triggers()

from src.config import (
    DB_PATH, CHROMA_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, init_settings,
    DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, ROLE_PROMPTS
)

# Auth & Bridge
from src.auth import load_users
from src.bridge import bridge_manager
from src.logger import server_log as slog


# --- 1. ENGINE LOADING ---

def load_rag_engine(user_role="DEFAULT"):
    """RAG Engine: Searches the Vector Database."""
    path_to_use = str(CHROMA_PATH) if 'CHROMA_PATH' in globals() else str(DB_PATH)

    db = chromadb.PersistentClient(path=path_to_use)
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)
    retriever = vector_retriever
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            retriever = QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False, verbose=True
            )
        except: pass

    selected_prompt = ROLE_PROMPTS.get(user_role, DEFAULT_SYSTEM_PROMPT)
    slog.info(f"RAG Engine Loaded | Profile: {user_role}")

    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE,
        llm=Settings.llm
    )

def load_simple_local_engine():
    """Local Engine: Llama. Sees uploaded files."""
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un analista di dati. Rispondi basandoti ESCLUSIVAMENTE sul testo fornito, se presente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=16384)
    )

def load_cloud_engine():
    """Cloud Engine: Gemini. Sees chat + Global Knowledge."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: raise ValueError("Missing GOOGLE_API_KEY in .env file")

    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=api_key)

    return SimpleChatEngine.from_defaults(
        system_prompt=(
            "Sei Gemini, un'IA avanzata di Google. "
            "Usa la tua vasta conoscenza per rispondere a domande su aziende, concetti o dati esterni. "
            "Non limitarti a riassumere la chat."
        ),
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192)
    )


# --- PURE TOOL IMPLEMENTATION ---
async def _impl_read_email(target_outlook_id: str, query: str):
    if not target_outlook_id:
        return "ERROR: No Outlook account linked to this ArcumAI user."
    return await bridge_manager.send_mcp_request(target_outlook_id, "search_emails", {"query": query})

async def _impl_get_calendar(target_outlook_id: str, date_filter: str = "today"):
    if not target_outlook_id:
        return "ERROR: No Outlook account linked."
    return await bridge_manager.send_mcp_request(target_outlook_id, "get_calendar", {"filter": date_filter})


# --- 2. SESSION CLASS ---

class UserSession:
    def __init__(self, username, role="DEFAULT"):
        self.username = username
        self.role = role
        self.is_cloud = False
        self.uploaded_context = ""
        self.global_history = []
        self.rag_engine = None
        self.simple_engine = None
        self.cloud_engine = None
        self.agent_engine = None

        # 1. RETRIEVE OUTLOOK ID
        self.outlook_id = self._get_outlook_id()

        # 2. CREATE USER-SPECIFIC TOOLS
        self.tools = self._create_user_tools()

    def _get_outlook_id(self):
        users = load_users()
        user_info = users.get(self.username, {})
        return user_info.get("outlook_id", None)

    def _create_user_tools(self):
        """
        Creates tools with STRONG DISAMBIGUATION INSTRUCTIONS.
        """

        # 1. EXPLICIT ASYNC WRAPPERS (Replace 'partial' for stability)
        async def read_email_wrapper(query: str = ""):
            """
            EXCLUSIVE TOOL FOR: EMAIL, MAIL, MESSAGES, INBOX.

            CRITICAL INSTRUCTIONS FOR THE AI:
            1. USE THIS TOOL ONLY IF THE USER ASKS TO "READ", "SEARCH" OR "VIEW" EMAIL/MAIL.
            2. NEVER USE THIS TOOL IF THE QUESTION IS ABOUT THE CALENDAR.
            3. AFTER using this tool, STOP. Do not call other tools unless explicitly requested.
            4. Parameter 'query':
               - Leave EMPTY ("") for "latest emails", "today's mail", "what did I receive".
               - Use "from:Name" or "subject:Subject" only if specified.
            """
            # Guardrail: If the query is a natural question, clean it
            if "?" in query or len(query.split()) > 6:
                slog.warning(f"[{self.username}] AUTO-FIX EMAIL: Query '{query}' too complex. Resetting to empty (latest mail).")
                query = ""

            return await _impl_read_email(self.outlook_id, query)

        async def calendar_wrapper(filter: str = "today"):
            """
            EXCLUSIVE TOOL FOR: CALENDAR, AGENDA, APPOINTMENTS, MEETINGS.

            CRITICAL INSTRUCTIONS FOR THE AI:
            1. USE THIS TOOL ONLY IF THE USER ASKS "WHAT DO I HAVE TO DO", "MEETINGS", "APPOINTMENTS".
            2. DO NOT USE THIS TOOL IF THE USER ONLY ASKS ABOUT EMAIL.
            3. Parameter 'filter': accepts ONLY 'today', 'tomorrow', 'week'.
            """
            # Input normalization
            val = filter.lower().strip()
            if "oggi" in val: val = "today"
            if "domani" in val: val = "tomorrow"
            if "settimana" in val: val = "week"

            return await _impl_get_calendar(self.outlook_id, val)

        # 2. FUNCTION TOOL CREATION
        return [
            FunctionTool.from_defaults(fn=read_email_wrapper, name="tool_read_email"),
            FunctionTool.from_defaults(fn=calendar_wrapper, name="tool_get_calendar"),
        ]

    async def get_rag_engine(self):
        if not self.rag_engine: self.rag_engine = await run.io_bound(load_rag_engine, self.role)
        return self.rag_engine

    async def get_simple_engine(self):
        if not self.simple_engine: self.simple_engine = await run.io_bound(load_simple_local_engine)
        return self.simple_engine

    async def get_agent_engine(self):
        """Returns an Agent (Compatible with Workflow v0.12+)."""
        if self.agent_engine:
            return self.agent_engine

        slog.info(f"[{self.username}] Building Outlook Agent...")

        # STRATEGY A: WORKFLOW (New Standard)
        if WorkflowReActAgent:
            try:
                # New syntax: direct constructor, no 'from_tools'
                self.agent_engine = WorkflowReActAgent(
                    llm=Settings.llm,
                    tools=self.tools,
                    system_prompt="Sei un assistente operativo. Usa i tool se richiesto, altrimenti rispondi."
                )
                slog.info(f"[{self.username}] Agent loaded: WorkflowReActAgent")
                return self.agent_engine
            except Exception as e:
                slog.warning(f"[{self.username}] WorkflowReActAgent error: {e}")

        # STRATEGY B: LEGACY (Old Standard)
        if LegacyReActAgent:
            try:
                # Old syntax: factory 'from_tools'
                self.agent_engine = LegacyReActAgent.from_tools(
                    tools=self.tools,
                    llm=Settings.llm,
                    verbose=True,
                    memory=ChatMemoryBuffer.from_defaults(token_limit=16384)
                )
                slog.info(f"[{self.username}] Agent loaded: LegacyReActAgent")
                return self.agent_engine
            except Exception as e:
                slog.warning(f"[{self.username}] LegacyReActAgent error: {e}")

        # FALLBACK
        slog.error(f"[{self.username}] CRITICAL AGENT FAILURE. Using Simple Chat.")
        return await self.get_simple_engine()

    async def get_cloud_engine(self):
        if not self.cloud_engine: self.cloud_engine = await run.io_bound(load_cloud_engine)
        return self.cloud_engine

    async def decide_engine(self, text):
        text_lower = text.lower()
        if "@rag" in text_lower or "@cerca" in text_lower: return "RAG"
        if "@simple" in text_lower or "@chat" in text_lower: return "SIMPLE"
        if "@outlook" in text_lower or "@agent" in text_lower: return "AGENT"

        found_chat_triggers = [t for t in GLOBAL_CHAT_TRIGGERS if t in text_lower]
        clean_text = text_lower
        for t in found_chat_triggers: clean_text = clean_text.replace(t, " ")

        if any(t in clean_text for t in GLOBAL_TRIGGERS_LIST): return "RAG"

        # Quick keyword check for Outlook
        outlook_keywords = ["email", "posta", "calendario", "agenda", "appuntamento", "riunione"]
        if any(kw in text_lower for kw in outlook_keywords):
            return "AGENT"

        if found_chat_triggers: return "SIMPLE"

        try:
            prompt = (f"Classify intent: '{text}'. Reply 'RAG' (documents), 'AGENT' (email/calendar), or 'SIMPLE' (chat). One word.")
            resp = await Settings.llm.acomplete(prompt)
            decision = str(resp).strip().upper()
            if "RAG" in decision: return "RAG"
            if "AGENT" in decision: return "AGENT"
            return "SIMPLE"
        except: return "RAG"

    def _format_history_as_text(self, limit=6):
        if not self.global_history: return ""
        history_text = "--- RECENT CHAT CONTEXT ---\n"
        recent_msgs = self.global_history[-limit:]
        for msg in recent_msgs:
            role_label = "USER" if msg.role == MessageRole.USER else "AI"
            content_preview = str(msg.content)[:800]
            history_text += f"{role_label}: {content_preview}\n"
        history_text += "--- END CONTEXT ---\n"
        return history_text

    async def run_chat_action(self, user_text, mode_override=None):
        engine = None
        used_mode = "SIMPLE"

        text_lower = user_text.lower()
        force_rag = "@rag" in text_lower or "@cerca" in text_lower

        if self.is_cloud:
            engine = await self.get_cloud_engine()
            used_mode = "CLOUD"
        elif force_rag:
            engine = await self.get_rag_engine()
            used_mode = "RAG"
        elif mode_override == "FILE_READER" or self.uploaded_context:
            engine = await self.get_simple_engine()
            used_mode = "FILE READER"
        else:
            decision = await self.decide_engine(user_text)
            if decision == "RAG":
                engine = await self.get_rag_engine()
                used_mode = "RAG"
            elif decision == "AGENT":
                engine = await self.get_agent_engine()
                used_mode = "AGENT"
            else:
                engine = await self.get_simple_engine()
                used_mode = "SIMPLE"

        # Sync Memory
        if hasattr(engine, 'memory') and engine.memory:
            engine.memory.chat_history = [m for m in self.global_history]

        clean_query = user_text.replace("@rag", "").replace("@cerca", "").replace("@simple", "").replace("@chat", "").strip()
        if not clean_query: clean_query = user_text

        history_str = self._format_history_as_text()
        final_input = ""
        has_file = bool(self.uploaded_context)

        if used_mode == "CLOUD":
            final_input = (f"{history_str}\nISTRUZIONI: Usa la tua conoscenza globale.\nDOMANDA: {clean_query}")
        elif used_mode == "FILE READER" and has_file:
             slog.info(f"[{self.username}] LOCAL: File injection ({len(self.uploaded_context)} chars).")
             final_input = (
                 f"{history_str}\n"
                 f"ISTRUZIONI: Rispondi basandoti SUL SEGUENTE TESTO DEL FILE.\n"
                 f"--- FILE UTENTE ---\n{self.uploaded_context}\n--- FINE FILE ---\n\n"
                 f"DOMANDA UTENTE: {clean_query}"
             )
        else:
             final_input = (f"{history_str}\nDOMANDA UTENTE: {clean_query}")

        slog.info(f"[{self.username}] {used_mode} -> {clean_query[:40]}...")

        # --- EXECUTION FIX: WORKFLOW vs CHAT ENGINE HANDLING ---
        response_obj = None
        response_text = ""

        try:
            # Case 1: Workflow Agent (New)
            if hasattr(engine, 'run'):
                # Workflows use .run() instead of .achat()
                response_obj = await engine.run(user_msg=final_input)
                response_text = str(response_obj)

            # Case 2: Chat Engine (Old or Simple)
            elif hasattr(engine, 'achat'):
                response_obj = await engine.achat(final_input)
                response_text = str(response_obj)

            # Case 3: Synchronous fallback
            else:
                response_obj = await run.io_bound(engine.chat, final_input)
                response_text = str(response_obj)

        except Exception as e:
            slog.error(f"[{self.username}] Engine execution error: {e}", exc_info=True)
            response_text = "Sorry, a technical error occurred while processing the request."

        self.global_history.append(ChatMessage(role=MessageRole.USER, content=clean_query))
        self.global_history.append(ChatMessage(role=MessageRole.ASSISTANT, content=response_text))

        # Return both the raw object (for source_nodes) and the text
        return response_obj, response_text, used_mode
