#!/usr/bin/env python3
"""Check if bot is enabled or disabled."""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)

def check_status():
    disabled_file = os.path.join(PROJECT_DIR, '.bot_disabled')
    env_file = os.path.join(PROJECT_DIR, '.env')
    
    if os.path.exists(disabled_file):
        with open(disabled_file, 'r') as f:
            info = f.read().strip()
        print("🔒 Bot Status: DISABLED")
        print(f"   {info}")
        print("\nTo restore: ./scripts/change_token.sh")
        return False
    
    # Check if BOT_TOKEN exists in .env
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            content = f.read()
        if 'BOT_TOKEN=' in content and '#DISABLED_BOT_TOKEN=' not in content:
            print("✅ Bot Status: ENABLED")
            return True
    
    print("⚠️ Bot Status: UNKNOWN (no token found)")
    return False

if __name__ == "__main__":
    enabled = check_status()
    sys.exit(0 if enabled else 1)
