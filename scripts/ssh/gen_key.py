#!/usr/bin/env python3
"""Generate new API key - SSH callable version."""
import os
import sys
import secrets
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def generate_key(app_id: str = None):
    """Generate new API and encryption keys."""
    if not app_id:
        app_id = input("Enter APP_ID (e.g., apiai-v3): ").strip()
        if not app_id:
            print("Error: APP_ID required")
            sys.exit(1)
    
    # Generate keys
    api_key = secrets.token_hex(32)
    enc_key = secrets.token_hex(32)
    
    print(f"\n🔑 Generated keys for {app_id}:")
    print(f"   API Key: {api_key}")
    print(f"   Encryption Key: {enc_key}")
    
    # Update app_keys.json
    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    if os.path.exists(keys_file):
        with open(keys_file, 'r') as f:
            data = json.load(f)
    else:
        data = {"app_keys": {}, "default": {}}
    
    data["app_keys"][app_id] = {
        "api_key": api_key,
        "encryption_key": enc_key
    }
    
    # Save
    save = input("\nSave to app_keys.json? [Y/n]: ").strip().lower()
    if save != 'n':
        with open(keys_file, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✅ Saved to {keys_file}")
    
    return api_key, enc_key

if __name__ == "__main__":
    app_id = sys.argv[1] if len(sys.argv) > 1 else None
    generate_key(app_id)
