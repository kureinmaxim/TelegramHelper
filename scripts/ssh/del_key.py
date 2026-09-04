#!/usr/bin/env python3
"""Delete API key - SSH callable version of /del_api_key command."""
import os
import sys
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def delete_key(app_id: str):
    """Delete API keys for an app_id."""
    if not app_id:
        print("Usage: python3 del_key.py <app_id>")
        print("Example: python3 del_key.py test-client")
        sys.exit(1)
    
    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    
    if not os.path.exists(keys_file):
        print("❌ app_keys.json not found")
        return False
    
    with open(keys_file, 'r') as f:
        data = json.load(f)
    
    app_keys = data.get('app_keys', {})
    
    if app_id not in app_keys:
        print(f"❌ App ID '{app_id}' not found")
        print(f"Available: {', '.join(app_keys.keys())}")
        return False
    
    # Confirm
    confirm = input(f"Delete keys for '{app_id}'? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return False
    
    # Delete
    del app_keys[app_id]
    data['app_keys'] = app_keys
    
    with open(keys_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"✅ Keys for '{app_id}' deleted")
    return True

if __name__ == "__main__":
    app_id = sys.argv[1] if len(sys.argv) > 1 else None
    delete_key(app_id)
