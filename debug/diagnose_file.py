import sys
import chromadb
from pathlib import Path

# Configurazione (deve coincidere con config.py)
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = BASE_DIR / "chroma_db"
COLLECTION_NAME = "arcum_docs"

def inspect_file(filename_substring):
    print(f"🕵️  ISPEZIONE FILE: Cerco '{filename_substring}'...")
    print(f"    Path DB: {DB_PATH}")
    
    try:
        client = chromadb.PersistentClient(path=str(DB_PATH))
        # Usa get_or_create per evitare errori se la collezione non è caricata
        collection = client.get_or_create_collection(COLLECTION_NAME)
        
        # 1. SCARICHIAMO TUTTI I METADATI (Approccio "Force Brute" sicuro)
        #    Dato che hai pochi file, è istantaneo.
        all_docs = collection.get(include=["metadatas"])
        
        found_ids = []
        
        # 2. FILTRIAMO IN PYTHON (Infallibile)
        print(f"    📂 Scansione di {len(all_docs['ids'])} documenti nel DB...")
        
        for i, meta in enumerate(all_docs['metadatas']):
            # Controlliamo se la stringa cercata è nel nome del file
            if filename_substring.lower() in meta.get('filename', '').lower():
                found_id = all_docs['ids'][i]
                found_ids.append(found_id)
        
        if not found_ids:
            print(f"❌ NESSUN FILE TROVATO che contenga: '{filename_substring}'")
            return

        print(f"✅ TROVATI {len(found_ids)} CHUNK CORRISPONDENTI!")
        
        # 3. RECUPERIAMO IL CONTENUTO DEL PRIMO RISULTATO
        target_id = found_ids[0]
        full_doc = collection.get(ids=[target_id], include=["documents", "metadatas"])
        
        doc_content = full_doc['documents'][0]
        metadata = full_doc['metadatas'][0]
        
        print("\n--- 📋 METADATI RECUPERATI ---")
        for k, v in metadata.items():
            print(f"   • {k}: {v}")
            
        print("\n--- 🔍 CONTENUTO REALE (Primi 600 caratteri) ---")
        if not doc_content.strip():
            print("⚠️  ATTENZIONE: IL CONTENUTO È VUOTO O BIANCO! [GHOST FILE] ⚠️")
        else:
            print(f"'{doc_content[:600]}...'")
            print("\n--- FINE ANTEPRIMA ---")
            
    except Exception as e:
        print(f"💥 Errore imprevisto: {e}")

if __name__ == "__main__":
    # Esempio: cerca una parte del nome
    # Puoi cambiare questa stringa per cercare altri file
    target = "Brianza_Jane Ivonne_Lugano.tipf2024.pdf" 
    
    # Se passi un argomento da riga di comando, usa quello
    if len(sys.argv) > 1:
        target = sys.argv[1]
        
    inspect_file(target)