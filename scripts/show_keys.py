#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Show active API and encryption keys on the server.

Usage:
    python3 scripts/show_keys.py
    python3 scripts/show_keys.py --app-id example-app
    python3 scripts/show_keys.py --all
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Add project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import ALLOWED_APPS from security.py
try:
    from security import ALLOWED_APPS
except ImportError:
    # Fallback if security.py is unavailable
    ALLOWED_APPS = {
        "example-app": {
            "name": "BOM Categorizer Modern Edition v5",
            "version": "5.x",
        },
        "apiai-v3": {
            "name": "ApiAi Tauri Edition v3",
            "version": "3.x",
        },
        "test-client": {
            "name": "Test Client (Development)",
            "version": "dev",
        }
    }

# Key helpers without importing app_keys
def _load_keys_from_file():
    """Load keys from the file directly"""
    keys_file = project_root / "app_keys.json"
    if not keys_file.exists():
        return {"app_keys": {}, "default": {}}
    try:
        with open(keys_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"app_keys": {}, "default": {}}
    except Exception as e:
        print(f"⚠️  Error reading app_keys.json: {e}")
        return {"app_keys": {}, "default": {}}

def get_api_key_from_data(app_id: str = None, data: dict = None):
    """Get API key from loaded data"""
    if data is None:
        data = _load_keys_from_file()

    # If app_id is set and there is a per-app key
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            api_key = app_keys[app_id].get("api_key")
            if api_key:
                return api_key

    # Fallback to default key from app_keys.json
    default_key = data.get("default", {}).get("api_key")
    if default_key:
        return default_key

    # Last fallback — env vars or .env
    env_key = os.getenv("API_SECRET_KEY")
    if env_key:
        return env_key

    # Try reading from .env
    env_file = project_root / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("API_SECRET_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    return None

def get_encryption_key_from_data(app_id: str = None, data: dict = None):
    """Get encryption key from loaded data"""
    if data is None:
        data = _load_keys_from_file()

    # If app_id is set and there is a per-app key
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            enc_key = app_keys[app_id].get("encryption_key")
            if enc_key:
                return enc_key

    # Fallback to default key from app_keys.json
    default_key = data.get("default", {}).get("encryption_key")
    if default_key:
        return default_key

    # Last fallback — env vars or .env
    env_key = os.getenv("ENCRYPTION_KEY")
    if env_key:
        return env_key

    # Try reading from .env
    env_file = project_root / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("ENCRYPTION_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass

    # Last fallback — API_SECRET_KEY
    return get_api_key_from_data(app_id, data)

def list_app_ids_from_data(data: dict = None):
    """List all app_id values that have keys"""
    if data is None:
        data = _load_keys_from_file()
    app_keys = data.get("app_keys", {})
    return list(app_keys.keys())


def allow_full_secret_output() -> bool:
    """Whether an explicit full reveal is allowed from the local CLI."""
    return os.getenv("TELEGRAMHELPER_ALLOW_SECRET_REVEAL", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def print_key_info(app_id: str = None, show_full: bool = False, data: dict = None):
    """Print key info for app_id"""

    # Load data if not passed in
    if data is None:
        data = _load_keys_from_file()

    # Get keys
    api_key = get_api_key_from_data(app_id, data)
    enc_key = get_encryption_key_from_data(app_id, data)

    # Determine key source
    has_individual = False

    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            has_individual = True
            app_info = app_keys[app_id]
            created = app_info.get("created_at", "N/A")
            updated = app_info.get("updated_at", "N/A")

    # Header
    if app_id:
        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
        print(f"\n🔑 Keys for: {app_id} ({app_name})")
        if has_individual:
            print(f"   📅 Created: {created}")
            print(f"   🔄 Updated: {updated}")
            print(f"   ✅ Per-app keys")
        else:
            print(f"   ⚠️  Using default keys from .env")
    else:
        print(f"\n🔑 Default keys (from .env)")

    print("-" * 60)

    # API key
    if api_key:
        if show_full:
            print(f"🔐 API Key: {api_key}")
        else:
            masked = api_key[:8] + "..." + api_key[-8:] if len(api_key) > 16 else "***"
            print(f"🔐 API Key: {masked}")
    else:
        print(f"🔐 API Key: ❌ Not set")

    # Encryption key
    if enc_key:
        if show_full:
            print(f"🔒 Encryption Key: {enc_key}")
        else:
            masked = enc_key[:8] + "..." + enc_key[-8:] if len(enc_key) > 16 else "***"
            print(f"🔒 Encryption Key: {masked}")
    else:
        print(f"🔒 Encryption Key: ❌ Not set")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Show active API and encryption keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/show_keys.py                    # Default keys
  python3 scripts/show_keys.py --all               # All keys
  python3 scripts/show_keys.py --app-id example-app
  python3 scripts/show_keys.py --app-id example-app --full
        """
    )

    parser.add_argument(
        "--app-id",
        type=str,
        help="Application ID (e.g. example-app)"
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Show keys for every app_id"
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full keys (unmasked)"
    )

    args = parser.parse_args()

    if args.full and not allow_full_secret_output():
        print("⛔ Full secret output is disabled by default.")
        print("Temporarily set `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` only on a local server if you really need it.")
        sys.exit(1)

    print("=" * 60)
    print("🔍 Active API keys")
    print("=" * 60)

    # Check that app_keys.json exists
    keys_file = project_root / "app_keys.json"
    if keys_file.exists():
        print(f"✅ Keys file found: {keys_file}")
    else:
        print(f"⚠️  Keys file not found: {keys_file}")
        print("   Using keys from .env only")

    print()

    if args.all:
        # Load data once
        data = _load_keys_from_file()

        # Show all keys
        print("📋 Default keys:")
        print_key_info(None, args.full, data)

        # List all app_id values with keys
        app_ids_with_keys = list_app_ids_from_data(data)

        # Also show every allowed app_id
        print("📋 Per-app keys:")
        for app_id in ALLOWED_APPS.keys():
            if app_id in app_ids_with_keys:
                print_key_info(app_id, args.full, data)
            else:
                app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                print(f"\n⚠️  {app_id} ({app_name}): no per-app keys")
                print("   Using default keys from .env\n")

    elif args.app_id:
        # Check that app_id is allowed
        if args.app_id not in ALLOWED_APPS:
            print(f"❌ Error: app_id '{args.app_id}' is not in ALLOWED_APPS")
            print(f"\nAllowed app_id values:")
            for aid, info in ALLOWED_APPS.items():
                print(f"  - {aid}: {info.get('name', 'N/A')}")
            sys.exit(1)

        print_key_info(args.app_id, args.full)
    else:
        # Show default keys only
        print_key_info(None, args.full)
        print("\n💡 Use --all to see every key")
        print("💡 Use --app-id <app_id> for a specific app")


if __name__ == "__main__":
    main()
