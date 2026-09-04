#!/usr/bin/env python3
"""Show help - SSH callable version of /help command."""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def show_help():
    """Show available SSH commands."""
    print("""
🔧 TelegramHelper SSH Commands
==============================

Usage: python3 scripts/ssh/<command>.py [args]

📋 System & Info:
  ver.py              - Version and bot status
  info.py             - Server information
  help.py             - This help message

🔑 API Key Management:
  show_keys.py [app] [--full]  - Show API keys
  gen_key.py [app]             - Generate API + encryption keys
  del_key.py <app>             - Delete keys for app_id

🔐 Encryption Keys:
  enc_key.py show <app> [--full]  - Show encryption key
  enc_key.py gen <app>            - Generate encryption key
  enc_key.py del <app>            - Delete encryption key
  enc_key.py chacha               - Generate ChaCha20 key
  enc_key.py pqc                  - Generate PQC key

👥 User Management:
  list_users.py                   - List all users
  users.py setcity <id> <city>    - Set user city
  users.py setgreeting <id> <txt> - Set user greeting
  users.py special_add <id>       - Mark user as special
  users.py special_remove <id>    - Remove special status

🤖 AI Settings:
  ai_model.py                     - Show current model
  ai_model.py <model_name>        - Set AI model

🔐 VLESS-Reality:
  vless.py status                 - Show VLESS status
  vless.py on                     - Enable VLESS
  vless.py off                    - Disable VLESS
  vless.py config                 - Show full config
  vless.py set <param> <value>    - Set parameter
  vless.py gen_keys               - Generate VLESS keys

🔒 Bot Management:
  ../disable_bot.sh               - Disable Telegram bot
  ../bot_status.py                - Check bot status
  ../change_token.sh              - Set token / restore bot

💡 Examples:
  python3 scripts/ssh/ver.py
  python3 scripts/ssh/show_keys.py apiai-v3 --full
  python3 scripts/ssh/enc_key.py gen test-client
  python3 scripts/ssh/vless.py set server 1.2.3.4
  ./scripts/disable_bot.sh
""")

if __name__ == "__main__":
    show_help()
