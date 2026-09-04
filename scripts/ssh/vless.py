#!/usr/bin/env python3
"""VLESS-Reality management - SSH callable versions of /vless_* commands."""
import os
import sys
import json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

def show_status():
    """Show VLESS-Reality status."""
    try:
        import vless_manager

        status = vless_manager.get_vless_status()

        print("\n🔐 VLESS-Reality Status")
        print("=" * 40)
        print(f"Status: {'✅ Enabled' if status.get('enabled') else '❌ Disabled'}")
        print(f"Configured: {'✅ Yes' if status.get('configured') else '❌ No'}")

        print("\nConfiguration:")
        print(f"  Server: {status.get('server', 'N/A')}")
        print(f"  Port: {status.get('port', 'N/A')}")
        print(f"  SNI: {status.get('sni', 'N/A')}")
        print(f"  Fingerprint: {status.get('fingerprint', 'N/A')}")
        print()
    except ImportError:
        print("❌ vless_manager module not available")
    except Exception as e:
        print(f"❌ Error: {e}")

def enable():
    """Enable VLESS-Reality."""
    try:
        import vless_manager
        success, message = vless_manager.enable_vless()
        print(message if message else ("✅ VLESS-Reality enabled" if success else "❌ Error"))
    except Exception as e:
        print(f"❌ Error: {e}")

def disable():
    """Disable VLESS-Reality."""
    try:
        import vless_manager
        success, message = vless_manager.disable_vless()
        print(message if message else ("✅ VLESS-Reality disabled" if success else "❌ Error"))
    except Exception as e:
        print(f"❌ Error: {e}")

def show_config():
    """Show full VLESS config."""
    try:
        import vless_manager
        config = vless_manager.get_vless_config(include_secrets=False)
        print(json.dumps(config, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"❌ Error: {e}")

def set_param(param: str, value: str):
    """Set a VLESS parameter."""
    config_file = os.path.join(PROJECT_DIR, 'vless_config.json')
    
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            config = json.load(f)
    else:
        config = {}
    
    config[param] = value
    
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"✅ Set {param} = {value}")

def gen_keys():
    """Generate VLESS keys."""
    try:
        import vless_manager
        success, keys, message = vless_manager.generate_all_keys()
        if message:
            print(message)
        if success:
            print("🔑 Generated VLESS keys:")
            print(f"  UUID: {keys.get('uuid')}")
            print(f"  Public Key: {keys.get('public_key')}")
            print(f"  Short ID: {keys.get('short_id')}")
    except Exception as e:
        print(f"❌ Error: {e}")


def list_clients():
    """List VLESS clients."""
    try:
        import vless_manager
        clients = vless_manager.list_clients()
        if not clients:
            print("No clients found.")
            return
        print("VLESS clients:")
        for client in clients:
            name = client.get("name", "client")
            uuid = client.get("uuid", "")
            print(f"  - {name}: {uuid}")
    except Exception as e:
        print(f"❌ Error: {e}")


def add_client(name: str, client_uuid: str | None = None):
    """Add VLESS client."""
    try:
        import vless_manager
        success, message, client = vless_manager.add_client(name, client_uuid)
        print(message)
        if success and client:
            print(f"UUID: {client.get('uuid')}")
    except Exception as e:
        print(f"❌ Error: {e}")


def del_client(name_or_uuid: str):
    """Delete VLESS client."""
    try:
        import vless_manager
        success, message = vless_manager.remove_client(name_or_uuid)
        print(message)
    except Exception as e:
        print(f"❌ Error: {e}")


def export_config(kind: str):
    """Export client/server/subscription configs."""
    try:
        import vless_manager
        if kind == "xray_client":
            print(json.dumps(vless_manager.export_xray_config(is_server=False), indent=2))
        elif kind == "xray_server":
            print(json.dumps(vless_manager.export_xray_config(is_server=True), indent=2))
        elif kind == "sub_base64":
            print(vless_manager.export_subscription_base64())
        elif kind == "sub_raw":
            print("\n".join(vless_manager.export_subscription_list()))
        elif kind == "singbox":
            print(json.dumps(vless_manager.export_singbox_config(), indent=2))
        elif kind == "clash":
            print(vless_manager.export_clash_meta_config())
        else:
            print("Unknown export type.")
    except Exception as e:
        print(f"❌ Error: {e}")

def sync_config():
    """Sync keys from xray config to vless_config.json."""
    try:
        import vless_manager
        success, msg = vless_manager.sync_from_xray_config()
        print(msg)
        return success
    except Exception as e:
        print(f"❌ Error syncing: {e}")
        return False

def print_usage():
    print("""
🔐 VLESS-Reality SSH Commands

Usage: python3 vless.py <command> [args]

Commands:
  status          - Show VLESS status
  on              - Enable VLESS-Reality
  off             - Disable VLESS-Reality
  config          - Show full configuration (JSON)
  sync            - Sync keys from xray config (and show config)
  set <param> <v> - Set parameter (server, port, uuid, sni, etc.)
  gen_keys        - Generate new VLESS keys
  list_clients    - List VLESS clients
  add_client <n> [uuid] - Add VLESS client
  del_client <id> - Delete client by name or UUID
  export <type>   - Export configs: xray_client|xray_server|sub_base64|sub_raw|singbox|clash

Examples:
  python3 vless.py status
  python3 vless.py sync
  python3 vless.py config
  python3 vless.py set server 1.2.3.4
""")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_status()
        print_usage()
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    
    if cmd == 'status':
        show_status()
    elif cmd == 'on' or cmd == 'enable':
        enable()
    elif cmd == 'off' or cmd == 'disable':
        disable()
    elif cmd == 'config':
        show_config()
    elif cmd == 'sync':
        sync_config()
        show_config()
    elif cmd == 'set' and len(sys.argv) >= 4:
        set_param(sys.argv[2], sys.argv[3])
    elif cmd == 'gen_keys':
        gen_keys()
    elif cmd == 'list_clients':
        list_clients()
    elif cmd == 'add_client' and len(sys.argv) >= 3:
        add_client(sys.argv[2], sys.argv[3] if len(sys.argv) >= 4 else None)
    elif cmd == 'del_client' and len(sys.argv) >= 3:
        del_client(sys.argv[2])
    elif cmd == 'export' and len(sys.argv) >= 3:
        export_config(sys.argv[2])
    else:
        print_usage()
