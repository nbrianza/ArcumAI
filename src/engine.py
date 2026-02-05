import sys
import os
import chromadb
from nicegui import run
from functools import partial  # <--- IMPORTANTE PER I TOOL DINAMICI
from llama_index.core.llms import ChatMessage, MessageRole

# LlamaIndex Imports
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.llms.gemini import Gemini


from src.utils import load_global_triggers, load_chat_triggers
GLOBAL_TRIGGERS_LIST = load_global_triggers()  # RAG (it, en, de, fr)
GLOBAL_CHAT_TRIGGERS = load_chat_triggers()    # CHAT (chat.txt)


# Import Configurazione
from src.config import (
    DB_PATH, CHROMA_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, init_settings,
    DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, ROLE_PROMPTS
)


# --- 1. CARICAMENTO MOTORI ---

def load_rag_engine(user_role="DEFAULT"):
    """Motore RAG: Cerca nel Database Vettoriale."""
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
    print(f"🎭 Engine RAG Caricato | Profilo: {user_role}")


    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE, 
        llm=Settings.llm 
    )

def load_simple_local_engine():
    """Motore Locale: Llama. Vede i file caricati."""

    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un analista di dati. Rispondi basandoti ESCLUSIVAMENTE sul testo fornito, se presente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=16384)
    )

def load_cloud_engine():
    """Motore Cloud: Gemini. Vede chat + Conoscenza Globale."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: raise ValueError("Manca GOOGLE_API_KEY nel file .env")
    
    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=api_key)
    
    return SimpleChatEngine.from_defaults(
        # Prompt aggiornato per incoraggiare l'uso della conoscenza
        system_prompt=(
            "Sei Gemini, un'IA avanzata di Google. "
            "Hai accesso allo storico della conversazione per capire il contesto locale, "
            "ma devi usare la tua VASTA CONOSCENZA GENERALE per rispondere a domande su aziende, concetti, o dati esterni. "
            "Non limitarti a riassumere la chat."
        ),
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192)
    )


# --- IMPLEMENTAZIONE TOOL PURA (Slegata dall'utente) ---
async def _impl_read_email(target_outlook_id: str, query: str):
    """Implementazione interna che richiede l'ID esplicito."""
    if not target_outlook_id:
        return "ERRORE: Nessun account Outlook collegato a questo utente ArcumAI."
    
    return await bridge_manager.send_mcp_request(
        target_outlook_id, 
        "search_emails", 
        {"query": query}
    )

async def _impl_get_calendar(target_outlook_id: str, date_filter: str = "today"):
    """Implementazione interna calendario."""
    if not target_outlook_id:
        return "ERRORE: Nessun account Outlook collegato."
        
    return await bridge_manager.send_mcp_request(
        target_outlook_id,
        "get_calendar",
        {"filter": date_filter}
    )


# --- 2. CLASSE SESSIONE ---

class UserSession:
    def __init__(self, username, role="DEFAULT"): # <--- Aggiunto username
        self.username = username
        self.role = role
        self.is_cloud = False
        self.uploaded_context = ""   
        self.global_history = []     
        self.rag_engine = None    
        self.simple_engine = None 
        self.cloud_engine = None  
        
        # 1. RECUPERA ID OUTLOOK DAL JSON
        self.outlook_id = self._get_outlook_id()
        
        # 2. CREA I TOOL SPECIFICI PER QUESTO UTENTE
        self.tools = self._create_user_tools()

    def _get_outlook_id(self):
        """Legge users.json e trova l'ID Outlook associato."""
        users = load_users()
        user_info = users.get(self.username, {})
        return user_info.get("outlook_id", None) # Ritorna None se non c'è

    def _create_user_tools(self):
        """Crea FunctionTools con l'ID utente già iniettato."""
        
        # Creiamo una versione della funzione che ha già il primo argomento fissato
        read_email_bound = partial(_impl_read_email, self.outlook_id)
        calendar_bound = partial(_impl_get_calendar, self.outlook_id)
        
        return [
            FunctionTool.from_defaults(
                fn=read_email_bound, 
                async_fn=read_email_bound,
                name="tool_read_email",
                description="Legge le email da Outlook. Query opzionale (es. 'from:mario')."
            ),
            FunctionTool.from_defaults(
                fn=calendar_bound, 
                async_fn=calendar_bound,
                name="tool_get_calendar",
                description="Legge il calendario. Filter: 'today' o 'tomorrow'."
            ),
        ]
  
    async def get_rag_engine(self):
        if not self.rag_engine: self.rag_engine = await run.io_bound(load_rag_engine, self.role)
        return self.rag_engine

    async def get_simple_engine(self):
        if not self.simple_engine: self.simple_engine = await run.io_bound(load_simple_local_engine)
        return self.simple_engine

    async def get_cloud_engine(self):
        if not self.cloud_engine: self.cloud_engine = await run.io_bound(load_cloud_engine)
        return self.cloud_engine

    async def decide_engine(self, text):
        text_lower = text.lower()
        
        # 1. Comandi espliciti (Hanno sempre la priorità)
        if "@rag" in text_lower or "@cerca" in text_lower: return "RAG"
        if "@simple" in text_lower or "@chat" in text_lower or "@outlook" in text_lower: return "SIMPLE"
        
        # --- 2. LOGICA SOTTRATTIVA IBRIDA ---
        
        # A. Identifichiamo i trigger di Chat presenti (es. "ciao", "grazie")
        found_chat_triggers = [t for t in GLOBAL_CHAT_TRIGGERS if t in text_lower]
        
        # B. Creiamo una versione "pulita" del testo rimuovendo i saluti
        # Questo evita che un semplice "Ciao" attivi il RAG se "ciao" fosse per errore anche nei file RAG
        clean_text = text_lower
        for t in found_chat_triggers:
            clean_text = clean_text.replace(t, " ") 
            
        # C. Verifica Trigger RAG sul testo RIMANENTE
        # Esempio: "Ciao Apple" -> toglie "ciao", resta "apple" -> RAG True
        # Esempio: "Ciao" -> toglie "ciao", resta " " -> RAG False
        if any(t in clean_text for t in GLOBAL_TRIGGERS_LIST): 
            return "RAG"
        
        # D. Se non ci sono trigger RAG, ma c'erano trigger Chat -> SIMPLE
        if found_chat_triggers:
            return "SIMPLE"
            
        # -------------------------------------

        # 3. Fallback Intelligente (LLM)
        # Se non abbiamo trovato nessuna parola chiave, lasciamo decidere all'LLM
        try:
            prompt = (f"Classify intent: '{text}'. Reply 'RAG' if it requires looking up specific documents, numbers, or facts. "
                      "Reply 'SIMPLE' if it is just a greeting, general chitchat or a question about previous messages. One word only.")
            resp = await Settings.llm.acomplete(prompt)
            decision = str(resp).strip().upper()
            if "RAG" in decision: return "RAG"
            return "SIMPLE"
        except: 
            # In caso di errore API, il fallback sicuro è RAG (meglio cercare che ignorare)
            return "RAG"


    def _format_history_as_text(self, limit=6):
        if not self.global_history: return ""
        history_text = "--- CONTESTO DALLA CHAT RECENTE ---\n"
        recent_msgs = self.global_history[-limit:]
        for msg in recent_msgs:
            role_label = "UTENTE" if msg.role == MessageRole.USER else "AI (LOCALE)"
            content_preview = str(msg.content)[:800] 
            history_text += f"{role_label}: {content_preview}\n"
        history_text += "--- FINE CONTESTO ---\n"
        return history_text

    async def run_chat_action(self, user_text, mode_override=None):
        engine = None
        used_mode = "SIMPLE"

        # A. Rilevamento Intenti
        text_lower = user_text.lower()
        force_rag = "@rag" in text_lower or "@cerca" in text_lower

        # B. Selezione Motore
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
            else:
                engine = await self.get_simple_engine()
                used_mode = "SIMPLE"

        # C. Sync Memoria (Formale)
        if hasattr(engine, 'memory'):
            engine.memory.chat_history = [m for m in self.global_history]

        clean_query = user_text.replace("@rag", "").replace("@cerca", "").replace("@simple", "").replace("@chat", "").strip()
        if not clean_query: clean_query = user_text

        # D. Costruzione Prompt (Logica Ibrida)
        history_str = self._format_history_as_text()
        final_input = ""
        
        has_file = bool(self.uploaded_context)
        allow_file_injection = (used_mode == "FILE READER" or used_mode == "RAG" or used_mode == "SIMPLE")

        # CASO 1: CLOUD -> USA STORICO + TUA CONOSCENZA (Fix richiesto)
        if used_mode == "CLOUD":
            print(f"🛡️ CLOUD: Invio storico per contesto + Richiesta potenza Cloud.")
            final_input = (
                f"{history_str}\n"
                f"ISTRUZIONI IMPORTANTI:\n"
                f"1. Usa lo storico sopra SOLO per capire a cosa si riferisce l'utente (es. se dice 'questa azienda' o 'il documento').\n"
                f"2. Per rispondere, USA LA TUA CONOSCENZA GENERALE, le tue eventuali capacità di ricerca ONLINE, e le tue capacità di analisi. NON limitarti a ripetere lo storico.\n"
                f"3. Se l'utente chiede informazioni su un'azienda o un argomento citato nello storico, cerca nella tua memoria globale e fornisci dettagli completi.\n\n"
                f"DOMANDA UTENTE: {clean_query}"
            )

        # CASO 2: LOCALE CON FILE
        elif has_file and allow_file_injection:
             print(f"✅ LOCALE: Iniezione File ({len(self.uploaded_context)} chars).")
             final_input = (
                 f"{history_str}\n"
                 f"ISTRUZIONI: Rispondi basandoti SUL SEGUENTE TESTO DEL FILE.\n"
                 f"--- FILE UTENTE ---\n{self.uploaded_context}\n--- FINE FILE ---\n\n"
                 f"DOMANDA UTENTE: {clean_query}"
             )
        
        # CASO 3: STANDARD
        else:
             final_input = (
                 f"{history_str}\n"
                 f"DOMANDA UTENTE: {clean_query}"
             )

        print(f"⚙️ {used_mode} (History: {len(self.global_history)}) -> {clean_query[:40]}...")

        # E. Esecuzione
        if hasattr(engine, 'memory'): engine.memory.reset()
        response = await engine.achat(final_input)

        # F. Aggiornamento Storia
        self.global_history.append(ChatMessage(role=MessageRole.USER, content=clean_query))
        self.global_history.append(ChatMessage(role=MessageRole.ASSISTANT, content=str(response)))

        return response, used_mode
    

from llama_index.core.tools import FunctionTool

from src.auth import load_users     # <--- NECESSARIO PER LEGGERE JSON
from src.bridge import bridge_manager



# --- DEFINIZIONE TOOLS OUTLOOK ---

async def tool_read_email(query: str):
    """
    Cerca le email in Outlook. 
    Usa questo tool se l'utente chiede 'leggi le mail', 'cerca mail da Mario', 'ultime fatture'.
    Args:
        query (str): Cosa cercare (es. "from:mario", "subject:fattura", "unread").
    """
    # TODO: In produzione useremo l'utente della sessione corrente.
    # Per ora usiamo un placeholder per testare.
    current_user = "admin" 
    
    return await bridge_manager.send_mcp_request(
        current_user, 
        "search_emails", 
        {"query": query}
    )

async def tool_get_calendar(date_filter: str = "today"):
    """
    Controlla il calendario di Outlook.
    Usa questo tool se l'utente chiede 'cosa ho da fare oggi?', 'impegni domani'.
    Args:
        date_filter (str): 'today', 'tomorrow', o una data 'YYYY-MM-DD'.
    """
    current_user = "admin"
    
    return await bridge_manager.send_mcp_request(
        current_user,
        "get_calendar",
        {"filter": date_filter}
    )

# Lista pronta da passare all'Agent o al ChatEngine
outlook_tools = [
    FunctionTool.from_defaults(fn=tool_read_email, async_fn=tool_read_email),
    FunctionTool.from_defaults(fn=tool_get_calendar, async_fn=tool_get_calendar),
]    