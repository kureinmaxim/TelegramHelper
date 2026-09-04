#!/usr/bin/env python3
"""User management - SSH callable versions of user commands."""
import os
import sys
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def load_users():
    users_file = os.path.join(PROJECT_DIR, 'users.json')
    if os.path.exists(users_file):
        with open(users_file, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    users_file = os.path.join(PROJECT_DIR, 'users.json')
    with open(users_file, 'w') as f:
        json.dump(users, f, indent=2)

def set_city(user_id: str, city: str):
    """Set city for a user."""
    users = load_users()
    if user_id not in users:
        print(f"❌ User {user_id} not found")
        return
    users[user_id]['city'] = city
    save_users(users)
    print(f"✅ City set to '{city}' for user {user_id}")

def set_greeting(user_id: str, greeting: str):
    """Set greeting for a user."""
    users = load_users()
    if user_id not in users:
        print(f"❌ User {user_id} not found")
        return
    users[user_id]['greeting'] = greeting
    save_users(users)
    print(f"✅ Greeting set for user {user_id}")

def special_add(user_id: str):
    """Add user as special."""
    users = load_users()
    if user_id not in users:
        users[user_id] = {}
    users[user_id]['is_special'] = True
    save_users(users)
    print(f"✅ User {user_id} marked as special")

def special_remove(user_id: str):
    """Remove special status from user.""" 
    users = load_users()
    if user_id in users:
        users[user_id]['is_special'] = False
        save_users(users)
        print(f"✅ Special status removed from user {user_id}")
    else:
        print(f"❌ User {user_id} not found")

def print_usage():
    print("""
👥 User Management SSH Commands

Usage: python3 users.py <command> <user_id> [value]

Commands:
  list                    - List all users
  setcity <id> <city>     - Set city for user
  setgreeting <id> <text> - Set greeting for user
  special_add <id>        - Mark user as special
  special_remove <id>     - Remove special status

Examples:
  python3 users.py list
  python3 users.py setcity 123456789 Moscow
  python3 users.py special_add 123456789
""")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    
    if cmd == 'list':
        from list_users import list_users
        list_users()
    elif cmd == 'setcity' and len(sys.argv) >= 4:
        set_city(sys.argv[2], ' '.join(sys.argv[3:]))
    elif cmd == 'setgreeting' and len(sys.argv) >= 4:
        set_greeting(sys.argv[2], ' '.join(sys.argv[3:]))
    elif cmd == 'special_add' and len(sys.argv) >= 3:
        special_add(sys.argv[2])
    elif cmd == 'special_remove' and len(sys.argv) >= 3:
        special_remove(sys.argv[2])
    else:
        print_usage()
