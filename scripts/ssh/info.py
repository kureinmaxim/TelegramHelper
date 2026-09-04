#!/usr/bin/env python3
"""Show server info - SSH callable version of /info command."""
import os
import sys
import socket
import platform
import subprocess

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_DIR)

def get_uptime():
    """Get system uptime."""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h {int((uptime_seconds % 3600) // 60)}m"
    except:
        return "N/A"

def get_memory():
    """Get memory usage."""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        mem_total = int(lines[0].split()[1]) // 1024  # MB
        mem_available = int(lines[2].split()[1]) // 1024  # MB
        mem_used = mem_total - mem_available
        pct = int(mem_used * 100 / mem_total)
        return f"{mem_used}MB/{mem_total}MB ({pct}%)"
    except:
        return "N/A"

def get_disk():
    """Get disk usage for root."""
    try:
        result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True)
        line = result.stdout.strip().split('\n')[1]
        parts = line.split()
        return f"{parts[2]}/{parts[1]} ({parts[4]})"
    except:
        return "N/A"

def get_docker_info():
    """Get Docker container count."""
    try:
        result = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True)
        running = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
        result_all = subprocess.run(['docker', 'ps', '-aq'], capture_output=True, text=True)
        total = len(result_all.stdout.strip().split('\n')) if result_all.stdout.strip() else 0
        return f"{running}/{total} running"
    except:
        return "N/A"

def get_vless_status():
    """Check VLESS/Xray service status."""
    try:
        result = subprocess.run(['systemctl', 'is-active', 'xray'], capture_output=True, text=True)
        status = result.stdout.strip()
        if status == 'active':
            # Get port from xray config
            config_path = '/usr/local/etc/xray/config.json'
            if os.path.exists(config_path):
                import json
                with open(config_path, 'r') as f:
                    cfg = json.load(f)
                port = cfg.get('inbounds', [{}])[0].get('port', '?')
                return f"Active (:{port})"
            return "Active"
        return status.capitalize() if status else "Not installed"
    except:
        return "N/A"

def mask_key(key, show=4):
    """Mask API key showing only first N chars."""
    if not key or len(key) < show + 4:
        return "***"
    return f"{key[:show]}...{key[-2:]}"

def show_info():
    """Show server information."""
    print("\n📊 Server Information")
    print("=" * 40)
    
    # Hostname + uptime
    print(f"🖥️  Hostname: {socket.gethostname()} (up {get_uptime()})")
    
    # IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        print(f"🌐 IP: {ip}")
    except:
        print("🌐 IP: Unable to determine")
    
    # Platform + resources
    print(f"💻 OS: {platform.system()} {platform.release()}")
    print(f"💾 Resources: RAM {get_memory()}, Disk {get_disk()}")
    print(f"🐍 Python: {platform.python_version()}")
    
    # Docker status + containers
    docker_file = os.path.join(PROJECT_DIR, 'compose.yaml')
    if os.path.exists(docker_file):
        print(f"🐳 Docker: {get_docker_info()}")
    else:
        print("🐳 Docker: Not configured")
    
    # VLESS status
    print(f"🛡️ VLESS: {get_vless_status()}")
    
    # Bot status
    disabled_file = os.path.join(PROJECT_DIR, '.bot_disabled')
    print(f"🤖 Bot: {'Disabled' if os.path.exists(disabled_file) else 'Enabled'}")
    
    # API status with masked keys
    env_file = os.path.join(PROJECT_DIR, '.env')
    if os.path.exists(env_file):
        env_vars = {}
        with open(env_file, 'r') as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    key, _, val = line.partition('=')
                    env_vars[key.strip()] = val.strip().strip('"').strip("'")
        
        anthropic_key = env_vars.get('ANTHROPIC_API_KEY', '')
        openai_key = env_vars.get('OPENAI_API_KEY', '')
        
        if anthropic_key:
            print(f"🔌 Anthropic: {mask_key(anthropic_key)}")
        else:
            print("🔌 Anthropic: Not configured")
        
        if openai_key:
            print(f"🔌 OpenAI: {mask_key(openai_key)}")
        else:
            print("🔌 OpenAI: Not configured")
    
    # Keys count with list
    keys_file = os.path.join(PROJECT_DIR, 'app_keys.json')
    if os.path.exists(keys_file):
        import json
        with open(keys_file, 'r') as f:
            data = json.load(f)
        app_keys = data.get('app_keys', {})
        app_count = len(app_keys)
        key_names = ', '.join(list(app_keys.keys())[:3])
        if app_count > 3:
            key_names += f", +{app_count - 3}"
        print(f"🔑 App Keys: {app_count} ({key_names})")
    
    print()

if __name__ == "__main__":
    show_info()
