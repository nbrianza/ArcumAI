from fpdf import FPDF
import datetime

# --- CONFIGURAZIONE DOCUMENTO ---
TITLE = "ArcumAI - Manuale Tecnico Completo"
SUBTITLE = "Architettura, Security, Hybrid Engine & Guida Operativa"
VERSION = "v9.1 (Full: Hybrid + Security + Legacy Tools)"
DATE = datetime.datetime.now().strftime("%d/%m/%Y")

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.set_text_color(0, 51, 102) # Blu Ticino
        self.cell(0, 10, TITLE, border=False, ln=True, align='L')
        
        self.set_font('Helvetica', 'I', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, f"{SUBTITLE} [{VERSION}] - {DATE}", ln=True, align='L')
        
        self.ln(5)
        self.set_draw_color(200, 200, 200)
        self.line(10, 25, 200, 25)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Pagina {self.page_no()}/{self.alias_nb_pages()} | TicinoVault Documentation', align='C')

    def chapter_title(self, label):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(0, 51, 102)
        self.ln(5)
        self.cell(0, 10, label, ln=True, align='L')
        self.ln(2)

    def section_title(self, label):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(50, 50, 50)
        self.ln(2)
        self.cell(0, 8, label, ln=True, align='L')

    def body_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(0, 0, 0)
        clean = text.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("⚠️", "[!]")
        self.multi_cell(0, 6, clean)
        self.ln(2)

    def code_block(self, code):
        self.set_font('Courier', '', 9)
        self.set_fill_color(245, 245, 245)
        self.set_text_color(0, 0, 0)
        safe_code = code.replace("├──", "+--").replace("└──", "+--").replace("│", "|")
        self.multi_cell(0, 5, safe_code, fill=True, border=1)
        self.ln(5)

    def file_card(self, filename, folder, desc, details=None):
        self.set_font('Courier', 'B', 11)
        self.set_text_color(0, 100, 0) # Verde Code
        self.cell(0, 6, f"[{folder}] {filename}", ln=True)
        
        self.set_font('Helvetica', '', 10)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 6, desc)
        
        if details:
            self.set_font('Helvetica', 'I', 9)
            self.set_text_color(80, 80, 80)
            self.multi_cell(0, 5, f">> {details}")
        self.ln(4)

# --- GENERAZIONE CONTENUTO ---
pdf = PDF()
pdf.alias_nb_pages()
pdf.add_page()

# 1. INTRODUZIONE
pdf.chapter_title("1. Overview del Progetto")
pdf.body_text(
    "TicinoVault è un sistema RAG locale avanzato per studi fiduciari. "
    "Garantisce sovranità dei dati (On-Premise) e offre funzionalità avanzate come:\n"
    "- Motore di Ricerca Ibrido (Vettoriale + Parole Chiave)\n"
    "- Gestione Documentale Ricorsiva (Sottocartelle)\n"
    "- Sicurezza RBAC (Role Based Access Control) con hashing."
)

# 2. DIPENDENZE (PIP)
pdf.chapter_title("2. Gestione Dipendenze (Requirements)")
pdf.body_text("Librerie necessarie per il funzionamento completo.")

pdf.section_title("A. Core AI & Search")
pdf.code_block(
    "llama-index-core            # Framework RAG\n"
    "llama-index-llms-ollama     # LLM Locale\n"
    "llama-index-embeddings-huggingface  # Embeddings\n"
    "llama-index-vector-stores-chroma    # DB Vettoriale\n"
    "chromadb                    # Backend Database\n"
    "rank_bm25                   # Keyword Search (Hybrid Engine)"
)

pdf.section_title("B. Security & System")
pdf.code_block(
    "bcrypt                      # Hashing password sicuro\n"
    "watchdog                    # Monitoraggio Cartelle\n"
    "chainlit                    # UI Chat Web"
)

pdf.section_title("C. File Processing")
pdf.code_block(
    "pytesseract, pdf2image      # Stack OCR\n"
    "pypdf, extract-msg          # Parsers PDF/Email"
)

pdf.code_block("pip install llama-index-core llama-index-llms-ollama llama-index-embeddings-huggingface llama-index-vector-stores-chroma chromadb rank_bm25 bcrypt watchdog chainlit pytesseract pdf2image pypdf extract-msg fpdf2")

pdf.add_page()

# 3. MAPPA FILE
pdf.chapter_title("3. Mappa Completa dei File")
tree = (
    "TicinoVault/\n"
    "+-- app.py               <-- Web Chat (Updated: Hybrid + Auth)\n"
    "+-- main.py              <-- Ingestion (Updated: Recursive)\n"
    "+-- watcher.py           <-- Automazione (Updated: Robust Retry)\n"
    "+-- admin_tool.py        <-- Gestione Utenti (Nuovo)\n"
    "+-- rag_query.py         <-- CLI Chat (Legacy/Debug)\n"
    "+-- diagnose_pdf.py      <-- Debug Tool OCR (Legacy)\n"
    "+-- src/\n"
    "|   +-- auth.py          <-- Security Core (Nuovo)\n"
    "|   +-- config.py        <-- Config & Role Prompts\n"
    "|   +-- database.py      <-- Connessione DB\n"
    "|   +-- engine.py        <-- Motore Ibrido (Vector + BM25)\n"
    "|   +-- logger.py        <-- Logging System\n"
    "|   +-- readers.py       <-- Smart OCR & Parsers\n"
    "|   +-- utils.py         <-- Shared Logic (Clean + Retry)\n"
    "+-- data_nuovi/          <-- Inbox (Input Sistema)\n"
    "+-- input_utente/        <-- Drop Zone (Input Utente)\n"
    "+-- users.json           <-- DB Utenti Criptato"
)
pdf.code_block(tree)

# 4. MODULI ROOT
pdf.chapter_title("4. Moduli Principali (Root)")

pdf.file_card("watcher.py", "ROOT", 
    "Sentinella Ricorsiva v9.",
    "Monitora 'input_utente' (incluse sottocartelle). Usa logica 'Robust Retry' da utils per gestire file bloccati o lenti.")

pdf.file_card("main.py", "ROOT", 
    "Motore Ingestion v9.",
    "Pipeline ETL. Salva ora il 'file_path' relativo per mantenere la struttura delle cartelle in archivio. Genera indici Chroma e BM25.")

pdf.file_card("app.py", "ROOT", 
    "Interfaccia Web Chainlit.",
    "Include: Login Sicuro (Admin/Legal/Executive), Motore Ibrido (ContextChatEngine manuale), Citazioni cliccabili anche per sottocartelle.")

pdf.file_card("admin_tool.py", "ROOT", 
    "Gestore Utenti CLI (Nuovo).",
    "Menu interattivo per creare utenti, assegnare ruoli e resettare password nel file users.json.")

# --- RIPRISTINATI I FILE ORIGINALI ---
pdf.file_card("rag_query.py", "ROOT (Tool)", 
    "CLI Chat (Terminale).",
    "Permette di interrogare il sistema senza avviare l'interfaccia web. Utile per debug rapido.")

pdf.file_card("diagnose_pdf.py", "ROOT (Tool)", 
    "Tool diagnostica PDF.",
    "Analizza i metadati e la qualità del testo di un PDF per capire se richiede OCR.")
# -------------------------------------

pdf.add_page()

# 5. MODULI SRC
pdf.chapter_title("5. Librerie Interne (SRC)")

pdf.file_card("auth.py", "SRC", 
    "Security Core.",
    "Funzioni: hash_password (bcrypt), verify_password, load/save users. Gestisce l'accesso RBAC.")

pdf.file_card("config.py", "SRC", 
    "Configurazione Globale.",
    "Centralizza i path e definisce i System Prompts per i vari ruoli (Admin, Legal, Executive).")

pdf.file_card("engine.py", "SRC", 
    "Motore di Ricerca Ibrido.",
    "Costruisce il 'QueryFusionRetriever' combinando la ricerca semantica (ChromaDB) con quella per parole chiave (BM25).")

pdf.file_card("readers.py", "SRC", 
    "Smart OCR & Parsers.",
    "Include 'SmartPDFReader' (con soglia qualità 10%) e parser per email (.msg, .eml).")

pdf.file_card("utils.py", "SRC", 
    "Utility Condivise (Refactored).",
    "Contiene ora la logica 'sposta_file_con_struttura' con retry automatici e la funzione 'get_all_nodes_from_chroma' per il BM25.")

pdf.file_card("database.py", "SRC", 
    "Database Persistence.",
    "Gestisce la connessione persistente a ChromaDB.")

pdf.file_card("logger.py", "SRC", 
    "Logging.",
    "Configura i log su file (rotazione automatica) e console.")

# 6. GUIDA OPERATIVA
pdf.chapter_title("6. Guida Operativa")

pdf.section_title("A. Prima Configurazione")
pdf.body_text(
    "1. Eseguire 'python admin_tool.py' per creare il primo utente Admin.\n"
    "2. Il file 'users.json' verrà creato automaticamente."
)

pdf.section_title("B. Ingestion Documenti")
pdf.body_text(
    "1. Avviare 'python watcher.py' (lasciare aperto).\n"
    "2. Trascinare file o intere cartelle in 'input_utente'.\n"
    "3. Il sistema indicizza e sposta tutto in 'data_archivio' mantenendo la struttura."
)

pdf.section_title("C. Utilizzo Piattaforma")
pdf.body_text(
    "1. Lanciare 'chainlit run app.py -w'.\n"
    "2. Login con le credenziali create.\n"
    "3. Scegliere un ruolo (es. LEGAL per analisi contratti) e interrogare i documenti."
)

# GENERAZIONE
output_filename = "TicinoVault_Manuale_v9_Full.pdf"
try:
    pdf.output(output_filename)
    print(f"\n✅ MANUALE V9.1 GENERATO: {output_filename}")
    print("   -> Include: Nuove Feature + Tool di Diagnostica originali.")
except Exception as e:
    print(f"\n❌ Errore generazione PDF: {e}")