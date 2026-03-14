import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

# Load API key from .env file (never hardcode secrets in source code)
env_file = Path(__file__).parent.parent / ".env"
load_dotenv(env_file, override=True)

MY_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not MY_KEY:
    print("❌ GOOGLE_API_KEY not found. Set it in your .env file.")
    sys.exit(1)

genai.configure(api_key=MY_KEY)

print(f"🔍 Test connessione con chiave: {MY_KEY[:10]}...")

try:
    print("\n📋 Elenco Modelli Disponibili per questa chiave:")
    found = False
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"   ✅ {m.name}")
            found = True
    
    if not found:
        print("   ❌ Nessun modello trovato. La chiave potrebbe essere errata o il progetto Google Cloud non ha le API abilitate.")
    else:
        print("\n🎉 Connessione riuscita! Usa uno dei nomi sopra in app.py")

except Exception as e:
    print(f"\n❌ ERRORE CRITICO DI CONNESSIONE:\n{e}")