import sys
import os
import time

# Configurazione Path per importare moduli interni
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

try:
    import bcrypt
    from src.auth import add_user, delete_user, load_users
    # IMPORTIAMO LA LISTA DINAMICA DAL CONFIG
    from src.config import VALID_ROLES 
except ImportError as e:
    print(f"\n❌ ERRORE: Mancano librerie o file config. {e}")
    sys.exit(1)

def print_menu():
    print("\n🔐 ARCUM_AI USER MANAGER")
    print("============================")
    print("1. 📜 Lista Utenti")
    print("2. ➕ Aggiungi/Aggiorna Utente")
    print("3. ❌ Elimina Utente")
    print("4. 🚪 Esci")
    print("============================")

def main():
    while True:
        try:
            print_menu()
            choice = input("👉 Scelta: ")

            if choice == "1":
                users = load_users()
                if not users:
                    print("\n⚠️  Nessun utente trovato.")
                else:
                    print("\n👥 UTENTI REGISTRATI:")
                    for u, data in users.items():
                        role = data.get('role', 'N/A')
                        name = data.get('name', 'Sconosciuto')
                        outlook = data.get('outlook_id', '')
                        outlook_str = f" | Outlook: {outlook}" if outlook else ""
                        print(f"   👤 User: {u:<15} | Ruolo: {role:<10} | Nome: {name}{outlook_str}")
            
            elif choice == "2":
                print("\n📝 NUOVO UTENTE")
                username = input("   Username: ").strip()
                if not username: continue
                
                password = input("   Password: ").strip()
                name = input("   Nome e Cognome: ").strip()
                
                # --- LOGICA DINAMICA QUI ---
                roles_str = ", ".join(VALID_ROLES)
                print(f"   Ruoli disponibili: [{roles_str}]")
                
                role = input(f"   Ruolo (Default {VALID_ROLES[0]}): ").strip().upper()
                
                # Se l'input è vuoto o non valido, usa il primo ruolo della lista come default
                if role not in VALID_ROLES:
                    print(f"   ⚠️ Ruolo non riconosciuto. Imposto default: {VALID_ROLES[0]}")
                    role = VALID_ROLES[0]
                
                outlook_id = input("   Outlook ID (vuoto se non collegato): ").strip()

                if add_user(username, password, role, name, outlook_id):
                    print(f"   ✅ Utente {username} ({role}) salvato.")
                else:
                    print(f"   ❌ Utente non salvato. Controlla i requisiti della password.")
            
            elif choice == "3":
                username = input("\n🗑️  Username da eliminare: ")
                if delete_user(username):
                    print("   ✅ Utente eliminato.")
                else:
                    print("   ⚠️  Utente non trovato.")
            
            elif choice == "4":
                print("👋 Uscita...")
                sys.exit()
            
            else:
                print("⚠️  Scelta non valida.")
                
        except KeyboardInterrupt:
            print("\n🛑 Interrotto.")
            sys.exit()
        except Exception as e:
            print(f"\n❌ Errore: {e}")

if __name__ == "__main__":
    main()