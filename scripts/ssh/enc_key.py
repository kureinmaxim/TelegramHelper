#!/usr/bin/env python3
"""Encryption key management - SSH callable versions of encryption commands."""
import os
import sys
import json
import secrets

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def load_keys():
    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    if os.path.exists(keys_file):
        with open(keys_file, 'r') as f:
            return json.load(f)
    return {"app_keys": {}, "default": {}}

def save_keys(data):
    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    with open(keys_file, 'w') as f:
        json.dump(data, f, indent=2)

def show_enc_key(app_id: str, full: bool = False):
    """Show encryption key for app_id."""
    data = load_keys()
    
    if app_id:
        keys = data.get('app_keys', {}).get(app_id, {})
        enc_key = keys.get('encryption_key', 'N/A')
        if not full and len(enc_key) > 12:
            enc_key = enc_key[:8] + "..." + enc_key[-4:]
        print(f"🔐 Encryption key for {app_id}: {enc_key}")
    else:
        print("Usage: python3 enc_key.py show <app_id> [--full]")

def gen_enc_key(app_id: str):
    """Generate new encryption key."""
    if not app_id:
        app_id = input("Enter APP_ID: ").strip()
    
    data = load_keys()
    enc_key = secrets.token_hex(32)
    
    if app_id not in data.get('app_keys', {}):
        data['app_keys'][app_id] = {}
    
    data['app_keys'][app_id]['encryption_key'] = enc_key
    save_keys(data)
    
    print(f"✅ Generated encryption key for {app_id}:")
    print(f"   {enc_key}")

def del_enc_key(app_id: str):
    """Delete encryption key."""
    if not app_id:
        print("Usage: python3 enc_key.py del <app_id>")
        return
    
    data = load_keys()
    if app_id in data.get('app_keys', {}):
        if 'encryption_key' in data['app_keys'][app_id]:
            del data['app_keys'][app_id]['encryption_key']
            save_keys(data)
            print(f"✅ Encryption key deleted for {app_id}")
        else:
            print(f"❌ No encryption key found for {app_id}")
    else:
        print(f"❌ App ID '{app_id}' not found")

def gen_chacha_key():
    """Generate ChaCha20-Poly1305 key."""
    key = secrets.token_hex(32)
    print(f"🔑 ChaCha20-Poly1305 key: {key}")

def gen_pqc_key():
    """Generate Post-Quantum Cryptography key placeholder."""
    key = secrets.token_hex(64)
    print(f"🔑 PQC key (placeholder): {key[:32]}...{key[-8:]}")

def print_usage():
    print("""
🔐 Encryption Key Management

Usage: python3 enc_key.py <command> [app_id] [options]

Commands:
  show <app_id> [--full]  - Show encryption key
  gen <app_id>            - Generate new encryption key
  del <app_id>            - Delete encryption key
  chacha                  - Generate ChaCha20-Poly1305 key
  pqc                     - Generate PQC key

Examples:
  python3 enc_key.py show apiai-v3 --full
  python3 enc_key.py gen test-client
  python3 enc_key.py chacha
""")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    app_id = sys.argv[2] if len(sys.argv) > 2 else None
    full = '--full' in sys.argv or '-f' in sys.argv
    
    if cmd == 'show':
        show_enc_key(app_id, full)
    elif cmd == 'gen':
        gen_enc_key(app_id)
    elif cmd == 'del':
        del_enc_key(app_id)
    elif cmd == 'chacha':
        gen_chacha_key()
    elif cmd == 'pqc':
        gen_pqc_key()
    else:
        print_usage()
