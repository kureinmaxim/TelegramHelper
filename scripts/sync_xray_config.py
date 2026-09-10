#!/usr/bin/env python3
"""
Sync Xray Config
Generates Xray config using vless_manager.py and writes it to /usr/local/etc/xray/config.json
This allows the TelegramHelper bot to control the actual Xray server.
"""

import sys
import os
import json
import logging

# Add parent directory to path to import vless_manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from vless_manager import export_xray_config, get_vless_status, generate_all_keys
except ImportError as e:
    print(f"Error importing vless_manager: {e}")
    sys.exit(1)


def _reality_private_key(cfg):
    """Reality privateKey from the xray config ('' if missing). Empty = xray will not start."""
    try:
        return cfg["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] or ""
    except (KeyError, IndexError, TypeError):
        return ""

XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"

def sync_config():
    print("Generating Xray configuration...")

    # Get server config from vless_manager
    xray_config = export_xray_config(is_server=True)
    status = get_vless_status()

    # Fresh install: empty Reality privateKey → xray dies with
    # 'Failed to build REALITY config: empty "privateKey"'. Generate keys
    # (uuid + x25519 + short_id + default client) and re-export the config
    # so VLESS starts out of the box. Customize later via the bot (/vless_*).
    if not _reality_private_key(xray_config):
        print("Reality privateKey is empty — generating VLESS keys (uuid + x25519 + default client)...")
        ok, _keys, msg = generate_all_keys()
        print(f"  {msg}")
        xray_config = export_xray_config(is_server=True)
        status = get_vless_status()
        if not _reality_private_key(xray_config):
            print("⚠️ Failed to generate Reality keys (no xray x25519 and cryptography?). "
                  "Generate via the bot: /vless_gen_keys, then systemctl restart xray.")

    if not status['configured']:
        print("Warning: VLESS is not fully configured in vless_manager. Using partial config.")
    
    # Write to Xray config file
    try:
        os.makedirs(os.path.dirname(XRAY_CONFIG_PATH), exist_ok=True)
        
        with open(XRAY_CONFIG_PATH, 'w') as f:
            json.dump(xray_config, f, indent=2)
            
        print(f"Successfully wrote Xray config to {XRAY_CONFIG_PATH}")
        print(f"Server Port: {status.get('port', 443)}")
        # Print UUID/SNI only if VLESS is already configured. On a fresh
        # install there are no clients/serverNames yet — that is NOT an error
        # (config was written above, xray starts), so do not index empty lists.
        try:
            inbound = xray_config.get("inbounds", [{}])[0]
            clients = inbound.get("settings", {}).get("clients", []) or []
            snis = (inbound.get("streamSettings", {})
                    .get("realitySettings", {}).get("serverNames", []) or [])
            if clients:
                print(f"UUID: {clients[0].get('id', '')}")
            if snis:
                print(f"SNI: {snis[0]}")
            if not clients:
                print("VLESS is not configured yet (no clients). Config is written, xray "
                      "is running — add a client via the bot (/vless_*), then "
                      "restart: systemctl restart xray.")
        except (KeyError, IndexError, TypeError):
            pass  # diagnostic output must not crash the sync

    except PermissionError:
        print(f"Error: Permission denied writing to {XRAY_CONFIG_PATH}. Run as root.")
        sys.exit(1)
    except Exception as e:
        print(f"Error writing config: {e}")
        sys.exit(1)

if __name__ == "__main__":
    sync_config()
