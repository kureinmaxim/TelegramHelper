#!/usr/bin/env python3
"""List users - SSH callable version of /list_users command."""
import os
import sys
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def list_users():
    """List all registered users."""
    users_file = os.path.join(PROJECT_DIR, 'users.json')
    
    if not os.path.exists(users_file):
        print("📭 No users registered yet")
        return
    
    with open(users_file, 'r') as f:
        users = json.load(f)
    
    if not users:
        print("📭 No users registered yet")
        return
    
    print(f"\n👥 Registered Users ({len(users)}):\n")
    for user_id, data in users.items():
        username = data.get('username', 'N/A')
        first_name = data.get('first_name', 'N/A')
        city = data.get('city', '-')
        is_special = data.get('is_special', False)
        special_mark = " ⭐" if is_special else ""
        print(f"  {user_id}: @{username} ({first_name}) | City: {city}{special_mark}")
    print()

if __name__ == "__main__":
    list_users()
