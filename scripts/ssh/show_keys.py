#!/usr/bin/env python3
"""Show API keys - SSH callable version of /api command."""
import os
import sys
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)


def allow_full_secret_output() -> bool:
    """Explicit opt-in for full secret output over SSH helper."""
    return os.getenv("TELEGRAMHELPER_ALLOW_SECRET_REVEAL", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

def show_keys(app_id: str = None, full: bool = False):
    """Show API keys for an app_id or all keys."""
    if full and not allow_full_secret_output():
        print("⛔ Full secret output is disabled by default.")
        print("Temporarily set TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true on the server only if absolutely necessary.")
        return

    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    
    if not os.path.exists(keys_file):
        print("❌ app_keys.json not found")
        return
    
    with open(keys_file, 'r') as f:
        data = json.load(f)
    
    app_keys = data.get('app_keys', {})
    
    if not app_keys:
        print("📭 No API keys configured")
        return
    
    def mask_key(key):
        """Mask key for security unless full display requested."""
        if full or not key:
            return key
        return key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
    
    if app_id:
        # Show specific app
        if app_id not in app_keys:
            print(f"❌ App ID '{app_id}' not found")
            print(f"\nAvailable: {', '.join(app_keys.keys())}")
            return
        
        keys = app_keys[app_id]
        print(f"\n🔑 Keys for {app_id}:")
        print(f"   API Key: {mask_key(keys.get('api_key', 'N/A'))}")
        print(f"   Encryption Key: {mask_key(keys.get('encryption_key', 'N/A'))}")
    else:
        # Show all
        print(f"\n🔑 Configured App Keys ({len(app_keys)} apps):\n")
        for aid, keys in app_keys.items():
            print(f"📱 {aid}:")
            print(f"   API: {mask_key(keys.get('api_key', 'N/A'))}")
            print(f"   Enc: {mask_key(keys.get('encryption_key', 'N/A'))}")
            print()
    
    if not full:
        print("💡 Use --full to show complete keys")

if __name__ == "__main__":
    app_id = None
    full = False
    
    for arg in sys.argv[1:]:
        if arg == '--full' or arg == '-f':
            full = True
        elif arg == '--all' or arg == '-a':
            app_id = None
        elif not arg.startswith('-'):
            app_id = arg
    
    show_keys(app_id, full)
