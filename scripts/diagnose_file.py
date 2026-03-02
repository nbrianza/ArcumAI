import sys
import chromadb
from pathlib import Path

# Configuration (must match config.py)
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = BASE_DIR / "chroma_db"
COLLECTION_NAME = "arcum_docs"

def inspect_file(filename_substring):
    print(f"🕵️  FILE INSPECTION: Searching for '{filename_substring}'...")
    print(f"    DB Path: {DB_PATH}")

    try:
        client = chromadb.PersistentClient(path=str(DB_PATH))
        # Use get_or_create to avoid errors if the collection is not loaded
        collection = client.get_or_create_collection(COLLECTION_NAME)

        # 1. DOWNLOAD ALL METADATA (Safe "brute force" approach)
        #    Since you have few files, this is instant.
        all_docs = collection.get(include=["metadatas"])

        found_ids = []

        # 2. FILTER IN PYTHON (Infallible)
        print(f"    📂 Scanning {len(all_docs['ids'])} documents in the DB...")

        for i, meta in enumerate(all_docs['metadatas']):
            # Check if the search string is in the filename
            if filename_substring.lower() in meta.get('filename', '').lower():
                found_id = all_docs['ids'][i]
                found_ids.append(found_id)

        if not found_ids:
            print(f"❌ NO FILE FOUND containing: '{filename_substring}'")
            return

        print(f"✅ FOUND {len(found_ids)} MATCHING CHUNKS!")

        # 3. RETRIEVE THE CONTENT OF THE FIRST RESULT
        target_id = found_ids[0]
        full_doc = collection.get(ids=[target_id], include=["documents", "metadatas"])

        doc_content = full_doc['documents'][0]
        metadata = full_doc['metadatas'][0]

        print("\n--- 📋 RETRIEVED METADATA ---")
        for k, v in metadata.items():
            print(f"   • {k}: {v}")

        print("\n--- 🔍 ACTUAL CONTENT (First 600 characters) ---")
        if not doc_content.strip():
            print("⚠️  WARNING: CONTENT IS EMPTY OR BLANK! [GHOST FILE] ⚠️")
        else:
            print(f"'{doc_content[:600]}...'")
            print("\n--- END PREVIEW ---")

    except Exception as e:
        print(f"💥 Unexpected error: {e}")

if __name__ == "__main__":
    # Example: search for part of the filename
    # You can change this string to search for other files
    target = "Brianza_Jane Ivonne_Lugano.tipf2024.pdf"

    # If a command-line argument is passed, use that instead
    if len(sys.argv) > 1:
        target = sys.argv[1]

    inspect_file(target)
