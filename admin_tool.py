import sys
import os
import time

# Path setup to import internal modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

try:
    import bcrypt
    from src.auth import add_user, delete_user, load_users
    # Import the dynamic role list from config
    from src.config import VALID_ROLES
except ImportError as e:
    print(f"\n❌ ERROR: Missing libraries or config files. {e}")
    sys.exit(1)

def print_menu():
    print("\n🔐 ARCUM_AI USER MANAGER")
    print("============================")
    print("1. 📜 List Users")
    print("2. ➕ Add/Update User")
    print("3. ❌ Delete User")
    print("4. 🚪 Exit")
    print("============================")

def main():
    while True:
        try:
            print_menu()
            choice = input("👉 Choice: ")

            if choice == "1":
                users = load_users()
                if not users:
                    print("\n⚠️  No users found.")
                else:
                    print("\n👥 REGISTERED USERS:")
                    for u, data in users.items():
                        role = data.get('role', 'N/A')
                        name = data.get('name', 'Unknown')
                        outlook = data.get('outlook_id', '')
                        outlook_str = f" | Outlook: {outlook}" if outlook else ""
                        print(f"   👤 User: {u:<15} | Role: {role:<10} | Name: {name}{outlook_str}")

            elif choice == "2":
                print("\n📝 NEW USER")
                username = input("   Username: ").strip()
                if not username: continue

                password = input("   Password: ").strip()
                name = input("   Full Name: ").strip()

                # --- DYNAMIC ROLE LOGIC ---
                roles_str = ", ".join(VALID_ROLES)
                print(f"   Available roles: [{roles_str}]")

                role = input(f"   Role (Default {VALID_ROLES[0]}): ").strip().upper()

                # If input is empty or invalid, use the first role as default
                if role not in VALID_ROLES:
                    print(f"   ⚠️ Unrecognized role. Setting default: {VALID_ROLES[0]}")
                    role = VALID_ROLES[0]

                outlook_id = input("   Outlook ID (leave empty if not linked): ").strip()

                if add_user(username, password, role, name, outlook_id):
                    print(f"   ✅ User {username} ({role}) saved.")
                else:
                    print(f"   ❌ User not saved. Check password requirements.")

            elif choice == "3":
                username = input("\n🗑️  Username to delete: ")
                if delete_user(username):
                    print("   ✅ User deleted.")
                else:
                    print("   ⚠️  User not found.")

            elif choice == "4":
                print("👋 Exiting...")
                sys.exit()

            else:
                print("⚠️  Invalid choice.")

        except KeyboardInterrupt:
            print("\n🛑 Interrupted.")
            sys.exit()
        except Exception as e:
            print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()
