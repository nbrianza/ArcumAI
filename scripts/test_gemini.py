import os
import google.generativeai as genai

# --- INSERISCI QUI LA TUA CHIAVE PER IL TEST ---
# (Se è già nel file .env o nelle variabili di sistema, questo la sovrascrive per il test)
MY_KEY = "AIzaSyCCXYIspIGJvIaeZNayod18W5CUTx6WH9I" # <--- INCOLLA LA TUA CHIAVE QUI TRA LE VIRGOLETTE

os.environ["GOOGLE_API_KEY"] = MY_KEY
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