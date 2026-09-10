# -*- coding: utf-8 -*-
"""
Admin CLI Module - Execute bot commands without Telegram context.

This module provides a way to execute admin commands from an sing-box client
when the Telegram bot is disabled.

Author: Kurein M.N.
Date: 15.12.2025
"""

import logging
import os
import secrets
import base64
from typing import Tuple, Optional, List

from config import Config
import vless_manager
import rclone_manager
from utils import get_app_version, escape_markdown

logger = logging.getLogger(__name__)


def _mask_secret(value: str) -> str:
    """Return a masked secret for remote/admin output."""
    if not value:
        return "***"
    if len(value) <= 12:
        return "***"
    return f"{value[:8]}...{value[-4:]}"


class AdminCLI:
    """Execute bot admin commands without Telegram."""
    
    # Supported commands (command -> handler method name)
    COMMANDS = {
        "/help": "_cmd_help",
        "/ver": "_cmd_version",
        "/dockhand": "_cmd_dockhand",
        "/headscale": "_cmd_headscale_host_ip",
        "/headscale_status": "_cmd_headscale_status",
        "/headscale_list_nodes": "_cmd_headscale_list_nodes",
        "/headscale_gen": "_cmd_headscale_gen",
        "/headscale_revoke": "_cmd_headscale_revoke",
        "/vless_status": "_cmd_vless_status",
        "/vless_config": "_cmd_vless_config",
        "/vless_set_port": "_cmd_vless_set_port",
        "/vless_on": "_cmd_vless_on",
        "/vless_off": "_cmd_vless_off",
        "/bot_status": "_cmd_bot_status",
        "/disable_bot": "_cmd_disable_bot",
        "/enable_bot": "_cmd_enable_bot",
        "/api": "_cmd_api",
        "/encryption_key": "_cmd_encryption_key",
        "/backup_status": "_cmd_backup_status",
        "/backup_test": "_cmd_backup_test",
        "/backup_now": "_cmd_backup_now",
        "/backup_list": "_cmd_backup_list",
        "/info": "_cmd_info",
        "/vless_set_port": "_cmd_vless_set_port",
        "/vless_link": "_cmd_vless_link",
        "/exit_node": "_cmd_exit_node",
        "/exit_node_on": "_cmd_exit_node_on",
        "/exit_node_off": "_cmd_exit_node_off",
        "/links": "_cmd_links",
        "/list_users": "_cmd_list_users",
        "/qr": "_cmd_qr",
        "/reticulum_status": "_cmd_reticulum_status",
        "/reticulum_restart": "_cmd_reticulum_restart",
        "/reticulum_hash": "_cmd_reticulum_hash",
        "/reticulum_i2p": "_cmd_reticulum_i2p",
        "/reticulum_health": "_cmd_reticulum_health",
    }
    
    def __init__(self, config: Config = None):
        """Initialize AdminCLI with optional config."""
        self.config = config or Config()
    
    def execute(self, command: str, args: List[str] = None) -> Tuple[bool, str]:
        """
        Execute an admin command.
        
        Args:
            command: Command string like "/help" or "/vless_status"
            args: Optional list of command arguments
            
        Returns:
            Tuple of (success: bool, response: str)
        """
        args = args or []
        cmd = command.lower().strip()
        
        # Check if command is supported
        if cmd not in self.COMMANDS:
            available = ", ".join(sorted(self.COMMANDS.keys()))
            return False, f"❌ Unknown command: {cmd}\n\nAvailable commands:\n{available}"
        
        # Get handler method
        handler_name = self.COMMANDS[cmd]
        handler = getattr(self, handler_name, None)
        
        if not handler:
            return False, f"❌ Handler not implemented: {handler_name}"
        
        try:
            result = handler(args)
            return True, result
        except Exception as e:
            logger.error(f"Error executing {cmd}: {e}")
            return False, f"❌ Error: {str(e)}"
    
    # === COMMAND HANDLERS ===
    
    def _cmd_help(self, args: List[str]) -> str:
        """Show available commands."""
        return """📚 Admin CLI Commands

🔧 System:
/help - This help message
/ver - Version information
/dockhand - Dockhand SSH tunnel hint (localhost:8501)
/headscale - Tailscale client IP on this host (if installed)
/info - Server info

🤖 Bot Management:
/bot_status - Check if bot is enabled/disabled
/disable_bot - Disable Telegram bot
/enable_bot - Enable Telegram bot

🌐 Exit node (internet via VPS):
/exit_node - Exit node status + per-device guide
/exit_node_on - Enable exit node (advertise + approve)
/exit_node_off - Disable exit node

🕸️ Headscale (mesh):
/headscale - Tailscale IP of this host
/headscale_status - Headscale + Headplane status (nodes, URL)
/headscale_list_nodes - Connected mesh nodes
/headscale_gen [user] [ttl] - Pre-Auth key (ttl e.g. 720h)
/headscale_revoke <key> [user] - Revoke a key (no args — list)

🛡️ VLESS-Reality:
/vless_status - VLESS status
/vless_config - Show configuration
/vless_on - Enable VLESS
/vless_off - Disable VLESS

👤 Profiles:
/list_users - All bot users (admins/special/regular) with Telegram ID
/links [telegram_user_id] - All profile links; no ID — admin profiles (SSH = admin)
/qr [telegram_user_id] [variant] - QR of a link in the terminal; no variant — available list

🗄️ Backups:
/backup_status - Rclone backup status
/backup_test - Test configured remote
/backup_now - Create offsite backup
/backup_list - List recent backups

 Keys (apiai-v3):
/api - Show masked API key
/encryption_key - Show masked encryption key"""

    def _cmd_backup_status(self, args: List[str]) -> str:
        """Show rclone backup status."""
        return rclone_manager.format_status()

    def _cmd_backup_test(self, args: List[str]) -> str:
        """Test configured rclone remote."""
        result = rclone_manager.test_remote()
        return rclone_manager.format_command_result("Rclone remote test", result)

    def _cmd_backup_now(self, args: List[str]) -> str:
        """Create an offsite backup now."""
        result = rclone_manager.create_backup()
        return rclone_manager.format_backup_result(result)

    def _cmd_backup_list(self, args: List[str]) -> str:
        """List recent rclone backups."""
        result = rclone_manager.list_backups()
        return rclone_manager.format_command_result("Rclone backups", result)
    
    def _cmd_version(self, args: List[str]) -> str:
        """Show version info."""
        version_info = get_app_version()
        card = vless_manager.get_vless_version_card_fields()
        vless_status = card["status"]
        server_raw = (card.get("server") or "").strip()
        public_hint = (card.get("public_hint") or "").strip()
        missing_keys = card.get("missing_keys") or []

        if server_raw:
            server_block = f"Server (VLESS client endpoint): {server_raw}"
        else:
            server_block = (
                "Server (VLESS): not set — no public IP or hostname in config for clients.\n"
                "Use: /vless_set_server or /vless_sync."
            )
            if public_hint:
                server_block += (
                    f"\nVPS address from .env/environment: {public_hint} "
                    "(often the same value you should set as VLESS server).\n"
                    f"Example: /vless_set_server {public_hint}"
                )

        gaps_en = {
            "server": "server address",
            "uuid": "client UUID (root config or clients[] entry)",
            "public_key": "Reality keys",
            "short_id": "short id",
        }
        cfg_line = ""
        if missing_keys:
            labels = [gaps_en[k] for k in missing_keys if k in gaps_en]
            if labels:
                cfg_line = (
                    "\nMissing in vless_config.json for a complete Reality setup: "
                    + ", ".join(labels)
                    + "."
                )

        return f"""📋 Version Information

🔖 Version: {version_info.get('version', 'N/A')}
📦 Name: {version_info.get('name', 'TelegramHelper')}
📝 Description: {version_info.get('description') or 'N/A'}

🛡️ VLESS-Reality:
Status: {"🟢 Enabled" if vless_status["enabled"] else "🔴 Disabled"}
Configured: {"✅ Yes" if vless_status["configured"] else "❌ No"}
{server_block}{cfg_line}"""

    def _cmd_dockhand(self, args: List[str]) -> str:
        """Print SSH tunnel commands for Dockhand (same logic as Telegram /dockhand)."""
        from dockhand_tunnel_hints import build_ssh_tunnel_command, get_dockhand_ssh_params

        params = get_dockhand_ssh_params()
        cmd = build_ssh_tunnel_command(params, background=False)
        cmd_bg = build_ssh_tunnel_command(params, background=True)
        lines = [
            "Dockhand — open Streamlit UI via SSH tunnel (port 8501 on server loopback only).",
            "",
            "Copy on your PC (PowerShell / cmd / Terminal):",
            cmd,
            "",
            "Then open in browser on the same PC:",
            "http://localhost:8501",
            "",
            "Background (macOS/Linux):",
            cmd_bg,
            "",
            f"Resolved: {params.user}@{params.host} SSH port {params.port}",
        ]
        if params.notes:
            lines.extend(["", "⚠️ " + params.notes[0]])
        if params.host_is_placeholder:
            lines.extend(
                [
                    "",
                    "⚠️ Set DOCKHAND_SSH_HOST in .env or configure VLESS server (/vless_set_server).",
                ]
            )
        return "\n".join(lines)

    def _cmd_headscale_host_ip(self, args: List[str]) -> str:
        """Same as Telegram /headscale — mesh IP of Tailscale client on the VPS host."""
        import headscale_manager

        _ok, text = headscale_manager.get_host_tailscale_client_summary()
        return text

    def _cmd_headscale_status(self, args: List[str]) -> str:
        """Same as Telegram /headscale_status — Headscale + Headplane state."""
        import headscale_manager

        st = headscale_manager.get_status()
        hp = st.get("headplane", {}) or {}
        lines = [
            "🕸️ Headscale",
            "",
            f"State: {'🟢 enabled' if st.get('enabled') else '🔴 disabled'}",
            f"URL: {st.get('server_url') or 'not set'}",
            f"Container ({st.get('container_name', 'headscale')}): "
            f"{'🟢 running' if st.get('container_running') else '🔴 stopped'}",
            f"Nodes: {st.get('node_count', 0)} · Users: {st.get('user_count', 0)}",
            "",
            f"Headplane (Web UI): "
            f"{'🟢 running' if hp.get('container_running') else '🔴 stopped'}",
        ]
        if hp.get("container_running"):
            lines.append(f"  Tunnel: {hp.get('tunnel_hint', '')}")
            lines.append(f"  Browser: {hp.get('browser_url', '')}")
        return "\n".join(lines)

    def _cmd_headscale_list_nodes(self, args: List[str]) -> str:
        """Same as Telegram /headscale_list_nodes — connected mesh nodes."""
        import headscale_manager

        ok, message, nodes = headscale_manager.list_nodes()
        if not ok:
            return message
        if not nodes:
            return "📭 No nodes (nobody is connected to the mesh)."

        lines = [f"📋 Headscale nodes ({len(nodes)}):", ""]
        for n in nodes:
            if not isinstance(n, dict):
                continue
            name = n.get("givenName") or n.get("name") or "?"
            ips = n.get("ipAddresses") or []
            ip = ips[0] if ips else "?"
            online = "🟢" if n.get("online") else "⚪"
            lines.append(f"  {online} {name} — {ip}")
        return "\n".join(lines)

    def _cmd_headscale_gen(self, args: List[str]) -> str:
        """Same as Telegram /headscale_gen [user] [ttl] — issue a Pre-Auth key."""
        import headscale_manager

        user, expiration = headscale_manager.parse_user_expiration(args)
        ok, message, key = headscale_manager.create_preauth_key(
            user=user, expiration=expiration)
        if ok and key:
            return f"{message}\n\n{headscale_manager.export_client_instructions(key)}"
        return message

    def _cmd_headscale_revoke(self, args: List[str]) -> str:
        """Same as Telegram /headscale_revoke <key> [user] — expire a Pre-Auth key.

        With no args, lists active keys.
        """
        import headscale_manager

        if not args:
            ok, _msg, keys = headscale_manager.list_preauth_keys()
            if not ok:
                return _msg
            if not keys:
                return ("No active Pre-Auth keys.\n"
                        "Usage: /headscale_revoke <key> [user]")
            lines = ["🔑 Pre-Auth keys (pass a key to revoke):", ""]
            for k in keys:
                if not isinstance(k, dict):
                    continue
                kid = str(k.get("key", k.get("id", "?")))
                used = "used" if k.get("used") else "active"
                reusable = "reusable" if k.get("reusable") else "one-time"
                lines.append(f"  {kid} — {used}, {reusable}")
            return "\n".join(lines)

        key = args[0]
        user = args[1] if len(args) > 1 else None
        ok, message = headscale_manager.revoke_preauth_key(key, user=user)
        return message

    def _cmd_info(self, args: List[str]) -> str:
        """Show server info."""
        import socket
        import platform
        import os
        
        hostname = socket.gethostname()
        python_version = platform.python_version()
        
        # Get Linux distro info
        system = platform.system()
        if system == "Linux":
            try:
                # Try to read /etc/os-release for distro info
                with open('/etc/os-release', 'r') as f:
                    os_release = {}
                    for line in f:
                        if '=' in line:
                            key, value = line.strip().split('=', 1)
                            os_release[key] = value.strip('"')
                    distro = os_release.get('PRETTY_NAME', os_release.get('NAME', 'Linux'))
                    system = distro
            except:
                system = "Linux"
        
        # Check if running inside Docker container
        docker_status = "not detected"
        if os.path.exists('/.dockerenv'):
            docker_status = "🐳 Running inside container"
        elif os.path.exists('/proc/1/cgroup'):
            try:
                with open('/proc/1/cgroup', 'r') as f:
                    if 'docker' in f.read():
                        docker_status = "🐳 Running inside container"
            except:
                pass
        
        return f"""📊 Server Information

🖥️ Hostname: {hostname}
💻 System: {system}
🐍 Python: {python_version}
🐳 Docker: {docker_status}"""
    
    def _cmd_vless_status(self, args: List[str]) -> str:
        """Show VLESS status."""
        status = vless_manager.get_vless_status()
        
        status_emoji = "🟢" if status["enabled"] else "🔴"
        config_emoji = "✅" if status["configured"] else "❌"
        
        return f"""🛡️ VLESS-Reality Status

State: {status_emoji} {"Enabled" if status["enabled"] else "Disabled"}
Configuration: {config_emoji} {"Configured" if status["configured"] else "Not configured"}

Parameters:
• Server: {status.get("server") or "not set"}
• Port: {status.get("port", 443)}
• SNI: {status.get("sni", "www.microsoft.com")}
• Fingerprint: {status.get("fingerprint", "chrome")}

Keys:
• UUID: {"✅" if status["has_uuid"] else "❌"}
• Public Key: {"✅" if status["has_public_key"] else "❌"}
• Private Key: {"✅" if status["has_private_key"] else "❌"}
• Short ID: {"✅" if status["has_short_id"] else "❌"}

Updated: {status.get("updated_at", "never")}"""
    
    def _cmd_vless_config(self, args: List[str]) -> str:
        """Show VLESS configuration."""
        config = vless_manager.get_vless_config()
        
        if not config:
            return "❌ VLESS not configured. Use /vless_sync in Telegram bot."
        
        # Mask sensitive values
        uuid = config.get("uuid", "")
        masked_uuid = f"{uuid[:8]}...{uuid[-4:]}" if len(uuid) > 12 else "***"
        
        public_key = config.get("public_key", "")
        masked_key = f"{public_key[:8]}...{public_key[-4:]}" if len(public_key) > 12 else "***"
        
        return f"""🛡️ VLESS Configuration

Server: {config.get("server", "not set")}
Port: {config.get("port", 443)}
UUID: {masked_uuid}
Public Key: {masked_key}
Short ID: {config.get("short_id", "not set")}
SNI: {config.get("sni", "www.microsoft.com")}
Fingerprint: {config.get("fingerprint", "chrome")}

💡 For full keys, use Telegram bot."""
    
    def _cmd_vless_on(self, args: List[str]) -> str:
        """Enable VLESS."""
        success, message = vless_manager.enable_vless()
        return message
    
    def _cmd_vless_off(self, args: List[str]) -> str:
        """Disable VLESS."""
        success, message = vless_manager.disable_vless()
        return message
    
    def _cmd_vless_set_port(self, args: List[str]) -> str:
        """Change VLESS port."""
        if not args or len(args) < 1:
            return """❌ Usage: /vless_set_port <port>

Example: /vless_set_port 8443

⚠️ After changing the port, remember to:
1. Restart Xray: systemctl restart xray
2. Open the port in the firewall: ufw allow <port>/tcp"""
        
        try:
            port = int(args[0])
        except ValueError:
            return f"❌ Invalid port: {args[0]}. Port must be a number."
        
        # Change port (message already includes restart reminder)
        success, message = vless_manager.set_vless_port(port)
        return message

    def _cmd_vless_link(self, args: List[str]) -> str:
        """Get VLESS import link."""
        link = vless_manager.generate_vless_link()
        if not link:
            return "❌ VLESS not fully configured (missing server/uuid/keys)"
        return link
    
    def _cmd_reticulum_status(self, args: List[str]) -> str:
        """HA-stack and Reticulum bridge status (for SSH CLI)."""
        import reticulum_manager
        st = reticulum_manager.get_status()
        if not st["installed"]:
            return "🛰 HA-stack / Reticulum is not installed on this server."

        def m(b):
            return "🟢" if b else "🔴"

        svc = st["services"]
        lines = [
            "🛰 Reticulum / HA-stack",
            f"{m(svc.get('ha-reticulum-bridge'))} ha-reticulum-bridge",
            f"{m(svc.get('ha-stub-grpc'))} ha-stub-grpc",
            f"{m(svc.get('ha-stub-udp'))} ha-stub-udp",
            f"Bridge listening on :50061 — {'yes' if st['listening'] else 'no'}",
            f"Bridge hash: {st['bridge_hash'] or '(appears in the startup log)'}",
        ]
        if st.get("i2pd_installed"):
            lines += [
                f"{m(st.get('i2pd_active'))} i2pd (I2P, path 2)",
                f"I2P b32: {st.get('i2p_b32') or '(tunnel building / none)'}",
            ]
        return "\n".join(lines)

    def _cmd_reticulum_restart(self, args: List[str]) -> str:
        """Restart the HA-stack (bridge + stub gRPC/UDP)."""
        import reticulum_manager
        ok, msg = reticulum_manager.restart()
        return ("✅ " if ok else "❌ ") + msg

    def _cmd_reticulum_hash(self, args: List[str]) -> str:
        """Bridge destination hash (for client connections)."""
        import reticulum_manager
        h = reticulum_manager.get_bridge_hash()
        return f"🛰 Bridge hash: {h}" if h else "Bridge hash not found (bridge not running or missing from the startup log)."

    def _cmd_reticulum_i2p(self, args: List[str]) -> str:
        """I2P path status (i2pd + b32 of the ha-bridge server tunnel)."""
        import reticulum_manager
        i = reticulum_manager.get_i2p_status()
        if not i["installed"]:
            return "🛰 i2pd is not installed — I2P path (stage 3) is not configured."
        m2 = "🟢" if i["active"] else "🔴"
        return "\n".join([
            "🛰 Reticulum I2P (path 2)",
            f"{m2} i2pd",
            f"Bridge b32: {i['b32'] or '(ha-bridge server tunnel building / none)'}",
            "Client: i2pd client tunnel → this b32, RNS over TCP on 127.0.0.1:50061.",
        ])

    def _cmd_reticulum_health(self, args: List[str]) -> str:
        """i2pd health (network status, tunnel success, leasesets)."""
        import reticulum_manager
        h = reticulum_manager.get_i2p_health()
        if not h["installed"]:
            return "🛰 i2pd is not installed — I2P path (stage 3) is not configured."
        if not h["active"]:
            return "🔴 i2pd is not running. Start it: systemctl start i2pd"
        return "\n".join([
            "🩺 i2pd health (I2P, path 2)",
            f"Network status:  {h['network'] or '—'}",
            f"Tunnel success:  {h['success_rate'] or '—'}",
            f"Routers:         {h['routers'] or '—'}  (floodfills {h['floodfills'] or '—'})",
            f"LeaseSets:       {h['leasesets'] or '—'}",
            f"Transit tunnels: {h['transit'] or '—'}",
            f"Uptime:          {h['uptime'] or '—'}",
            "💡 Fresh node: low success rate and LeaseSets=0 is normal for the first minutes.",
        ])

    def _cmd_bot_status(self, args: List[str]) -> str:
        """Check if Telegram bot is enabled or disabled."""
        import os
        
        project_dir = "/opt/TelegramSimple" if os.path.exists("/opt/TelegramSimple") else os.getcwd()
        env_path = os.path.join(project_dir, ".env")
        
        # Check actual BOT_TOKEN status in .env file
        bot_token_active = False
        bot_token_commented = False
        
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('BOT_TOKEN='):
                        bot_token_active = True
                        break
                    elif line.startswith('#BOT_TOKEN=') or line.startswith('# BOT_TOKEN='):
                        bot_token_commented = True
        except Exception as e:
            return f"❌ Cannot read .env file: {e}"
        
        # Check marker file
        marker_path = os.path.join(project_dir, ".bot_disabled")
        marker_exists = os.path.exists(marker_path)
        
        if bot_token_active:
            # BOT_TOKEN is active - bot should be running
            if marker_exists:
                # Cleanup stale marker
                try:
                    os.remove(marker_path)
                except:
                    pass
            return """🤖 Bot Status

� Status: ENABLED (Running)
BOT_TOKEN is active in .env

To disable: /disable_bot"""
        elif bot_token_commented:
            # BOT_TOKEN is commented out
            disabled_info = ""
            if marker_exists:
                try:
                    with open(marker_path, 'r') as f:
                        disabled_info = f"\n📅 {f.read().strip()}"
                except:
                    pass
            return f"""🤖 Bot Status

🔴 Status: DISABLED
BOT_TOKEN is commented out in .env{disabled_info}

To enable: /enable_bot"""
        else:
            return """🤖 Bot Status

⚠️ Status: UNKNOWN
BOT_TOKEN not found in .env file"""

    def _cmd_exit_node(self, args: List[str]) -> str:
        """Exit node status + per-device guide (read-only)."""
        import headscale_manager

        st = headscale_manager.get_exit_node_status()
        ready = bool(st.get("advertising") and st.get("approved"))
        lines = []
        if st.get("error"):
            lines.append(f"🔴 Exit node unavailable: {st['error']}")
        elif ready:
            lines.append("🟢 Exit node is ready — you can exit to the internet via the VPS.")
        else:
            lines.append("🟡 Exit node is not up yet. Enable: /exit_node_on")

        adv = "✅" if st.get("advertising") else "❌"
        appr = "✅" if st.get("approved") else "❌"
        lines += [
            "",
            f"{adv} advertise on the host",
            f"{appr} route approved in Headscale",
            f"forwarding IPv4: {st.get('ip_forward_v4') or '?'}, "
            f"IPv6: {st.get('ip_forward_v6') or '?'}",
        ]
        if ready:
            lines += ["", headscale_manager.exit_node_client_instructions(st.get("node_label") or "")]
        return "\n".join(lines)

    def _cmd_exit_node_on(self, args: List[str]) -> str:
        """Enable exit node (advertise + forwarding + approve)."""
        import headscale_manager

        ok, report = headscale_manager.enable_exit_node()
        return ("" if ok else "❌ ") + report

    def _cmd_exit_node_off(self, args: List[str]) -> str:
        """Disable exit node."""
        import headscale_manager

        ok, report = headscale_manager.disable_exit_node()
        return report

    def _cmd_links(self, args: List[str]) -> str:
        """All bot-managed profile links for a user (mirror of /my_profile)."""
        note = ""
        if not args:
            # SSH access = admin: with no ID, show the admin's profiles by default.
            admin_ids = self.config.resolved_admin_user_ids()
            if not admin_ids:
                return ("❌ ADMIN_USER_IDS is not set — pass an ID explicitly: "
                        "/links <telegram_user_id>\n"
                        "All users and their IDs: /list_users")
            uid = admin_ids[0]
            note = f"ℹ️ No ID given — showing admin profiles ({uid})."
            if len(admin_ids) > 1:
                others = ", ".join(str(x) for x in admin_ids[1:])
                note += f" Other admins: {others} — /links <ID>."
        else:
            try:
                uid = int(args[0])
            except ValueError:
                return (f"❌ Invalid telegram_user_id: {args[0]}. Expected a number "
                        "(or call with no args — admin profiles). "
                        "All IDs: /list_users")

        import provision_manager
        import hysteria2_manager

        profiles = provision_manager.profiles_for_user(uid)
        existing = {
            proto: p for proto, p in profiles.items()
            if p.get("exists") and p.get("uri")
        }
        if not existing:
            return (f"📭 No bot-managed profiles for user_id={uid}.\n"
                    "Create: /provision <telegram_user_id> in the Telegram bot.")

        proto_labels = {
            "vless": "🛡 VLESS-Reality",
            "hysteria2": "⚡ Hysteria2",
            "mtproto": "💬 MTProto",
            "tuic": "🚀 TUIC",
            "anytls": "🔒 AnyTLS",
            "xhttp": "🌐 XHTTP",
            "mieru": "🛰 Mieru",
        }
        lines = [f"🔐 Profiles user_id={uid}",
                 "⚠️ Links = VPN access — treat them like passwords.", ""]
        if note:
            lines.insert(1, note)
        for proto, p in existing.items():
            label = proto_labels.get(proto, proto)
            uri = str(p["uri"])
            lines.append(f"{label} — {p.get('client_name', proto)}")
            if proto == "vless":
                lines.append("  Modern URI (Karing / Clash Meta / sing-box):")
                lines.append(f"  {uri}")
            elif proto == "hysteria2":
                lines.append("  Primary hy2:// (Karing and compatible clients):")
                lines.append(f"  {uri}")
                alias = hysteria2_manager.to_hysteria2_uri(uri)
                if alias and alias != uri:
                    lines.append("  Alias hysteria2:// (Karing and clients without hy2://):")
                    lines.append(f"  {alias}")
            elif proto == "mtproto":
                lines.append("  Telegram-only: open in Telegram, do not paste "
                             "into Karing / Clash Meta:")
                lines.append(f"  {uri}")
            else:
                lines.append(f"  {uri}")
            lines.append("")
        lines.append("QR codes for these links come from the bot: /my_profile or "
                     "/profiles <uid>.")
        return "\n".join(lines)

    def _cmd_list_users(self, args: List[str]) -> str:
        """Bot users with Telegram ID (mirror of /list_users in the bot)."""
        from storage import list_users as storage_list_users

        special, users = storage_list_users()
        special_set = set(special)
        admin_ids = self.config.resolved_admin_user_ids()
        admin_set = set(admin_ids)

        def fmt_user(uid: int, prefs: dict) -> str:
            parts = [str(uid)]
            if prefs.get("username"):
                parts.append(f"@{prefs['username']}")
            name = ((prefs.get("first_name") or "")
                    + (" " + prefs["last_name"] if prefs.get("last_name") else "")).strip()
            if name:
                parts.append(name)
            if prefs.get("city"):
                parts.append(f"🏙 {prefs['city']}")
            if prefs.get("last_seen"):
                parts.append(f"🕒 {str(prefs['last_seen'])[:10]}")
            return "  " + " — ".join(parts)

        lines = [f"👑 Administrators ({len(admin_ids)}):"]
        lines += [fmt_user(uid, users.get(uid, {})) for uid in admin_ids] or ["  -"]

        special_only = [uid for uid in special if uid not in admin_set]
        lines.append(f"\n⭐ Special users ({len(special_only)}):")
        lines += [fmt_user(uid, users.get(uid, {})) for uid in special_only] or ["  -"]

        regular = {uid: p for uid, p in users.items()
                   if uid not in special_set and uid not in admin_set}
        lines.append(f"\n👥 Regular users ({len(regular)}):")
        if regular:
            lines += [fmt_user(uid, prefs) for uid, prefs in
                      sorted(regular.items(),
                             key=lambda kv: kv[1].get("last_seen", ""),
                             reverse=True)]
        else:
            lines.append("  -")

        lines.append("\nUser profile links: /links <ID from the list above>")
        return "\n".join(lines)

    def _profile_link_variants(self, uid: int) -> List[Tuple[str, str, str]]:
        """All profile link variants for a user: (key, description, uri)."""
        import provision_manager
        import hysteria2_manager

        variants: List[Tuple[str, str, str]] = []
        for proto, p in provision_manager.profiles_for_user(uid).items():
            if not (p.get("exists") and p.get("uri")):
                continue
            uri = str(p["uri"])
            if proto == "vless":
                variants.append(
                    ("vless", "VLESS modern (Karing / Clash Meta / sing-box)", uri))
            elif proto == "hysteria2":
                variants.append(
                    ("hy2", "Hysteria2 hy2:// (Karing and compatible)", uri))
                alias = hysteria2_manager.to_hysteria2_uri(uri)
                if alias and alias != uri:
                    variants.append(
                        ("hysteria2", "Hysteria2 alias hysteria2:// (Karing)", alias))
            elif proto == "mtproto":
                variants.append(
                    ("mtproto", "MTProto (Telegram-only)", uri))
            else:
                variants.append((proto, proto, uri))
        return variants

    def _default_admin_uid(self) -> Optional[int]:
        """First ID from ADMIN_USER_IDS — default user for the SSH CLI."""
        admin_ids = self.config.resolved_admin_user_ids()
        return admin_ids[0] if admin_ids else None

    def _cmd_qr(self, args: List[str]) -> str:
        """QR code of a profile link in the terminal (Unicode half-blocks)."""
        # /qr [uid] [variant] | /qr [variant] — uid defaults to admin
        rest = list(args)
        uid = None
        if rest and rest[0].isdigit() and len(rest[0]) >= 5:
            uid = int(rest.pop(0))
        variant = rest[0].lower() if rest else None

        note = ""
        if uid is None:
            uid = self._default_admin_uid()
            if uid is None:
                return ("❌ ADMIN_USER_IDS is not set — pass an ID explicitly: "
                        "/qr <telegram_user_id> [variant]")
            note = f"ℹ️ No ID given — default user: admin ({uid})."

        variants = self._profile_link_variants(uid)
        if not variants:
            return (f"📭 No bot-managed profiles for user_id={uid}.\n"
                    "Create: /provision <telegram_user_id> in the Telegram bot.")

        if not variant:
            lines = [f"📷 QR in the terminal — pick a variant (user_id={uid}):"]
            if note:
                lines.insert(0, note)
            width = max(len(k) for k, _l, _u in variants)
            for key, label, _uri in variants:
                lines.append(f"  /qr {key:<{width}}  — {label}")
            lines.append("")
            lines.append("Another user: /qr <telegram_user_id> <variant> "
                         "(ID — from /list_users)")
            return "\n".join(lines)

        match = next((v for v in variants if v[0] == variant), None)
        if match is None:
            available = ", ".join(k for k, _l, _u in variants)
            return f"❌ Variant '{variant}' not found. Available: {available}"

        _key, label, uri = match
        import io
        import qrcode

        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        qr_text = buf.getvalue().rstrip("\n")

        head = [f"📷 {label} — user_id={uid}"]
        if note:
            head.append(note)
        head.append(uri)
        return ("\n".join(head) + "\n\n" + qr_text + "\n\n"
                "Scan with the VPN client's camera. If it will not read — enlarge "
                "the terminal window / shrink the font; text link: /links")

    def _cmd_disable_bot(self, args: List[str]) -> str:
        """Disable Telegram bot."""
        import subprocess
        import os
        
        # Check if already disabled
        marker_path = "/opt/TelegramSimple/.bot_disabled"
        local_marker = ".bot_disabled"
        if os.path.exists(marker_path) or os.path.exists(local_marker):
            return "⚠️ Bot is already disabled"
        
        try:
            # Run disable script non-interactively
            script_path = "/opt/TelegramSimple/scripts/disable_bot.sh"
            local_script = "scripts/disable_bot.sh"
            
            actual_script = script_path if os.path.exists(script_path) else local_script
            
            # Run the commands directly instead of script (to avoid interactive prompt)
            project_dir = "/opt/TelegramSimple" if os.path.exists("/opt/TelegramSimple") else os.getcwd()
            env_path = os.path.join(project_dir, ".env")
            
            # Backup token
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    env_content = f.read()
                
                # Comment out BOT_TOKEN
                new_content = env_content.replace("BOT_TOKEN=", "#DISABLED_BOT_TOKEN=")
                
                with open(env_path, 'w') as f:
                    f.write(new_content)
            
            # Create marker
            marker = os.path.join(project_dir, ".bot_disabled")
            from datetime import datetime
            with open(marker, 'w') as f:
                f.write(f"Disabled at: {datetime.now()}")
            
            # Restart container
            result = subprocess.run(
                ["docker", "compose", "restart"],
                cwd=project_dir,
                capture_output=True, text=True, timeout=60
            )
            
            if result.returncode == 0:
                return """🔒 Bot Disabled Successfully!

• BOT_TOKEN commented out in .env
• Marker file .bot_disabled created
• Container restarted

API is still accessible.
To restore: /enable_bot"""
            else:
                return f"""⚠️ Bot disabled but container restart failed:
{result.stderr}

Run manually: docker compose restart"""
                
        except subprocess.TimeoutExpired:
            return "❌ Timeout during container restart"
        except Exception as e:
            return f"❌ Error disabling bot: {e}"
    
    def _cmd_enable_bot(self, args: List[str]) -> str:
        """Enable Telegram bot (restore from disabled state)."""
        import subprocess
        import os
        
        # Check if actually disabled
        marker_path = "/opt/TelegramSimple/.bot_disabled"
        local_marker = ".bot_disabled"
        project_dir = "/opt/TelegramSimple" if os.path.exists("/opt/TelegramSimple") else os.getcwd()
        
        marker = marker_path if os.path.exists(marker_path) else local_marker
        if not os.path.exists(marker):
            return "ℹ️ Bot is already enabled"
        
        try:
            env_path = os.path.join(project_dir, ".env")
            
            # Restore BOT_TOKEN
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    env_content = f.read()
                
                # Uncomment BOT_TOKEN
                new_content = env_content.replace("#DISABLED_BOT_TOKEN=", "BOT_TOKEN=")
                
                with open(env_path, 'w') as f:
                    f.write(new_content)
            
            # Remove marker
            if os.path.exists(marker):
                os.remove(marker)
            
            # Restart container
            result = subprocess.run(
                ["docker", "compose", "restart"],
                cwd=project_dir,
                capture_output=True, text=True, timeout=60
            )
            
            if result.returncode == 0:
                return """✅ Bot Enabled Successfully!

• BOT_TOKEN restored in .env
• Marker file removed
• Container restarted

Bot is now running!"""
            else:
                return f"""⚠️ Bot enabled but container restart failed:
{result.stderr}

Run manually: docker compose restart"""
                
        except subprocess.TimeoutExpired:
            return "❌ Timeout during container restart"
        except Exception as e:
            return f"❌ Error enabling bot: {e}"
    
    def _cmd_xray_status(self, args: List[str]) -> str:
        """Check Xray service status."""
        import subprocess
        
        try:
            # Try systemctl status
            result = subprocess.run(
                ["systemctl", "is-active", "xray"],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            
            if status == "active":
                # Get more info
                info_result = subprocess.run(
                    ["systemctl", "show", "xray", "--property=MainPID,ActiveEnterTimestamp"],
                    capture_output=True, text=True, timeout=5
                )
                info_lines = info_result.stdout.strip().split('\n')
                pid = ""
                uptime = ""
                for line in info_lines:
                    if line.startswith("MainPID="):
                        pid = line.split("=")[1]
                    elif line.startswith("ActiveEnterTimestamp="):
                        uptime = line.split("=")[1]
                
                return f"""📦 Xray Status

✅ Status: Active (Running)
🔢 PID: {pid}
⏰ Started: {uptime or 'unknown'}

💡 Commands:
• systemctl restart xray - restart
• journalctl -u xray -n 50 - logs"""
            else:
                return f"""📦 Xray Status

❌ Status: {status}

💡 To start: systemctl start xray"""
                
        except FileNotFoundError:
            return "❌ systemctl not available (not running on systemd)"
        except subprocess.TimeoutExpired:
            return "❌ Timeout checking Xray status"
        except Exception as e:
            return f"❌ Error checking Xray: {e}"
    
    def _cmd_xray_config(self, args: List[str]) -> str:
        """Show Xray config location and basic info."""
        import os
        
        config_paths = [
            "/usr/local/etc/xray/config.json",
            "/etc/xray/config.json",
            "/opt/xray/config.json"
        ]
        
        config_path = None
        for path in config_paths:
            if os.path.exists(path):
                config_path = path
                break
        
        if not config_path:
            return "❌ Xray config not found in standard locations"
        
        try:
            import json
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Extract basic info
            inbounds = config.get("inbounds", [])
            outbounds = config.get("outbounds", [])
            
            inbound_info = []
            for ib in inbounds:
                port = ib.get("port", "?")
                protocol = ib.get("protocol", "?")
                inbound_info.append(f"  • {protocol} on port {port}")
            
            return f"""📦 Xray Configuration

📍 Path: {config_path}
📥 Inbounds: {len(inbounds)}
{chr(10).join(inbound_info) if inbound_info else '  (none)'}
📤 Outbounds: {len(outbounds)}

💡 Sync config: python3 scripts/sync_xray_config.py"""
            
        except Exception as e:
            return f"❌ Error reading Xray config: {e}"
    
    def _cmd_api(self, args: List[str]) -> str:
        """Show masked API key for apiai-v3."""
        try:
            from app_keys import get_api_key
            
            # Get apiai-v3 key directly (main client)
            key = get_api_key("apiai-v3")
            
            if key:
                return f"""🔑 API Key (apiai-v3)

{_mask_secret(key)}

⚠️ Full secret reveal is disabled in Admin CLI by default"""
            else:
                # Fallback to default
                default_key = os.getenv("API_SECRET_KEY", "")
                if default_key:
                    return f"""🔑 API Key (default)

{_mask_secret(default_key)}

⚠️ apiai-v3 key not found, using default"""
                else:
                    return "❌ No API keys configured"
            
        except ImportError:
            api_key = os.getenv("API_SECRET_KEY", "")
            if api_key:
                return f"""🔑 API Key (default)

{_mask_secret(api_key)}"""
            return "❌ No API key configured"
    
    def _cmd_encryption_key(self, args: List[str]) -> str:
        """Show masked encryption key for apiai-v3."""
        try:
            from app_keys import get_encryption_key
            
            # Get apiai-v3 key directly (main client)
            key = get_encryption_key("apiai-v3")
            
            if key:
                return f"""🔐 Encryption Key (apiai-v3)

{_mask_secret(key)}

⚠️ Full secret reveal is disabled in Admin CLI by default"""
            else:
                # Fallback to default
                default_key = os.getenv("ENCRYPTION_KEY", "")
                if default_key:
                    return f"""🔐 Encryption Key (default)

{_mask_secret(default_key)}

⚠️ apiai-v3 key not found, using default"""
                else:
                    return "❌ No encryption keys configured"
            
        except ImportError:
            enc_key = os.getenv("ENCRYPTION_KEY", "")
            if enc_key:
                return f"""🔐 Encryption Key (default)

{_mask_secret(enc_key)}"""
            return "❌ No encryption key configured"



# Global instance
admin_cli = AdminCLI()


def execute_admin_command(command: str, args: List[str] = None) -> Tuple[bool, str]:
    """
    Execute an admin command.
    
    This is the main entry point for the API endpoint.
    
    Args:
        command: Command string like "/help"
        args: Optional command arguments
        
    Returns:
        Tuple of (success, response_text)
    """
    return admin_cli.execute(command, args)
