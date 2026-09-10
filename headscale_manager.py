# -*- coding: utf-8 -*-
"""
Module for managing Headscale (self-hosted Tailscale control plane).

Talks to the Headscale Docker container via docker exec
to create users, generate Pre-Auth keys, and monitor nodes.

Configuration structure (headscale_config.json):
{
    "enabled": false,
    "container_name": "headscale",
    "server_url": "https://headscale.example.com",
    "default_user": "1",
    "key_expiration": "24h"
}

default_user — numeric ID (CLI ``-u`` uint) or username; the name is resolved
via ``users list`` (Headscale no longer accepts a string name in -u).
"""

import json
import os
import re
import subprocess
import logging
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Thread safety
_hs_lock = threading.Lock()

# Path to the Headscale configuration file
_HS_CONFIG_PATH = os.getenv(
    "HEADSCALE_CONFIG_PATH",
    os.path.join(os.getcwd(), "headscale_config.json"),
)

DEFAULT_CONFIG = {
    "enabled": False,
    "container_name": "headscale",
    "server_url": "",
    "default_user": "1",
    "key_expiration": "24h",
    "created_at": None,
    "updated_at": None,
}


# === Argument parsing (shared by the Telegram bot and SSH CLI) ===

_DURATION_RE = re.compile(r"\d+[smhd]")


def parse_user_expiration(args: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse /headscale_gen arguments into (user, expiration).

    Position-independent: a token like ``720h``/``30m``/``7d`` is the key
    lifetime, anything else is a username. Used in both the Telegram handler
    (handlers.headscale_gen) and the SSH CLI (admin_cli) so the logic stays aligned.
    """
    user: Optional[str] = None
    expiration: Optional[str] = None
    for arg in args:
        if _DURATION_RE.fullmatch(arg):
            expiration = arg
        else:
            user = arg
    return user, expiration


# === Internal helpers ===

def _load_config() -> Dict:
    """Load Headscale configuration from file."""
    with _hs_lock:
        if not os.path.exists(_HS_CONFIG_PATH):
            return dict(DEFAULT_CONFIG)
        try:
            with open(_HS_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
                return config
        except Exception as e:
            logger.error(f"Error loading Headscale config: {e}")
            return dict(DEFAULT_CONFIG)


def _save_config(config: Dict) -> bool:
    """Save Headscale configuration to file."""
    with _hs_lock:
        try:
            config["updated_at"] = datetime.now().isoformat()
            if not config.get("created_at"):
                config["created_at"] = config["updated_at"]

            directory = os.path.dirname(_HS_CONFIG_PATH) or "."
            os.makedirs(directory, exist_ok=True)

            with open(_HS_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception as e:
            logger.error(f"Error saving Headscale config: {e}")
            return False


def _docker_exec(config: Dict, *args: str, timeout: int = 15) -> Tuple[bool, str]:
    """Run a headscale command inside the Docker container.

    Docker CLI lives on the host, while the bot runs in a container without docker.
    So we go through host_run (nsenter into the host namespace, requires
    ``pid: host``) — the same trick as get_host_tailscale_client_summary.
    """
    from host_utils import host_run

    container = config.get("container_name", "headscale")
    cmd = ["docker", "exec", container, "headscale"] + list(args)
    try:
        result = host_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or f"Exit code {result.returncode}"
    except FileNotFoundError:
        return False, "docker/nsenter not found in PATH"
    except subprocess.TimeoutExpired:
        return False, f"Timeout ({timeout}s) while running the command"
    except Exception as e:
        return False, str(e)


# === Public API ===

def is_headscale_enabled() -> bool:
    """Check whether Headscale is enabled."""
    return _load_config().get("enabled", False)


def get_config() -> Dict:
    """Get the current Headscale configuration."""
    return _load_config()


def enable_headscale() -> Tuple[bool, str]:
    """Enable Headscale."""
    config = _load_config()
    config["enabled"] = True
    if _save_config(config):
        return True, "✅ Headscale enabled"
    return False, "❌ Failed to save"


def disable_headscale() -> Tuple[bool, str]:
    """Disable Headscale."""
    config = _load_config()
    config["enabled"] = False
    if _save_config(config):
        return True, "✅ Headscale disabled"
    return False, "❌ Failed to save"


def set_server_url(url: str) -> Tuple[bool, str]:
    """Set the Headscale coordinator URL."""
    url = url.strip().rstrip("/")
    if not url:
        return False, "❌ URL cannot be empty"

    config = _load_config()
    config["server_url"] = url
    if _save_config(config):
        return True, f"✅ URL set: {url}"
    return False, "❌ Failed to save"


def set_container_name(name: str) -> Tuple[bool, str]:
    """Set the Headscale Docker container name."""
    name = name.strip()
    if not name:
        return False, "❌ Container name cannot be empty"

    config = _load_config()
    config["container_name"] = name
    if _save_config(config):
        return True, f"✅ Container: {name}"
    return False, "❌ Failed to save"


def create_user(username: str) -> Tuple[bool, str]:
    """Create a user in Headscale."""
    username = username.strip()
    if not username:
        return False, "❌ Username cannot be empty"

    config = _load_config()
    ok, output = _docker_exec(config, "users", "create", username)
    if ok:
        return True, f"✅ User created: {username}"
    # "already exists" is not an error
    if "already exists" in output.lower():
        return True, f"ℹ️ User already exists: {username}"
    return False, f"❌ Error: {output}"


def _resolve_user_id(config: Dict, user: Optional[str]) -> Tuple[bool, str, str]:
    """Resolve user to a numeric ID for the Headscale CLI.

    Modern Headscale (`preauthkeys create -u`) accepts only a uint ID,
    not a username. A name (for example ``your-user``) is resolved via ``users list -o json``.
    An already-numeric string ("1") is returned as-is.
    """
    raw = (user or config.get("default_user") or "").strip()
    if not raw:
        return False, "❌ user is not set (default_user in headscale_config.json)", ""
    if raw.isdigit():
        return True, raw, raw

    ok, output = _docker_exec(config, "users", "list", "-o", "json")
    if not ok:
        return False, f"❌ Failed to get users list: {output}", ""
    try:
        users = json.loads(output)
    except json.JSONDecodeError:
        return False, f"❌ Invalid JSON users list:\n{output[:200]}", ""
    if not isinstance(users, list):
        users = []

    needle = raw.lower()
    for u in users:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        names = [
            str(u.get("name") or ""),
            str(u.get("username") or ""),
            str(u.get("display_name") or ""),
        ]
        if any(n.lower() == needle for n in names if n):
            if uid is None:
                continue
            return True, str(uid), f"{raw}→{uid}"

    known = ", ".join(
        f"{u.get('id')}:{u.get('username') or u.get('name') or '?'}"
        for u in users
        if isinstance(u, dict)
    ) or "—"
    return (
        False,
        f"❌ User \"{raw}\" not found. Need an ID from `users list` "
        f"(often here: 1 = your-user). Known: {known}",
        "",
    )


def create_preauth_key(
    user: Optional[str] = None,
    reusable: bool = True,
    expiration: Optional[str] = None,
) -> Tuple[bool, str, str]:
    """
    Create a Pre-Auth key for connecting a client.

    Returns:
        (success, message, key)
    """
    config = _load_config()
    expiration = expiration or config.get("key_expiration", "24h")

    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err, ""

    args = ["preauthkeys", "create", "--user", uid_or_err, "--expiration", expiration]
    if reusable:
        args.append("--reusable")

    ok, output = _docker_exec(config, *args)
    if not ok:
        return False, f"❌ Error: {output}", ""

    # Headscale prints the key on the last line or in a table
    key = _parse_preauth_key(output)
    if key:
        return (
            True,
            f"✅ Pre-Auth key created (user: {uid_label}, expiration: {expiration})",
            key,
        )
    return True, f"✅ Key created, but the output could not be parsed:\n{output}", output


def _parse_preauth_key(output: str) -> str:
    """Extract a Pre-Auth key from headscale output."""
    # Headscale >= 0.23 prints the key on its own line
    lines = output.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        # Keys are usually long hex/base64 strings
        if len(line) > 20 and " " not in line:
            return line
    # Fallback: return the entire output
    return output.strip()


def list_preauth_keys(user: Optional[str] = None) -> Tuple[bool, str, List[Dict]]:
    """List the user's Pre-Auth keys (for revoke/audit).

    Returns:
        (success, message, keys) — keys as a list of dicts from headscale JSON.
    """
    config = _load_config()
    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err, []
    ok, output = _docker_exec(
        config, "preauthkeys", "list", "--user", uid_or_err, "-o", "json"
    )
    if not ok:
        return False, f"❌ Error: {output}", []
    try:
        keys = json.loads(output)
        if not isinstance(keys, list):
            keys = []
        return True, f"✅ Keys for {uid_label}: {len(keys)}", keys
    except json.JSONDecodeError:
        return False, f"❌ Invalid JSON:\n{output[:200]}", []


def revoke_preauth_key(key: str, user: Optional[str] = None) -> Tuple[bool, str]:
    """Revoke (expire) a Pre-Auth key.

    Nodes already connected with this key stay in the network — to revoke the
    node itself use ``nodes delete`` / Headplane. Revoking the key only prevents
    registering new devices with it.
    """
    key = key.strip()
    if not key:
        return False, "❌ Key cannot be empty"

    config = _load_config()
    ok_uid, uid_or_err, uid_label = _resolve_user_id(config, user)
    if not ok_uid:
        return False, uid_or_err
    ok, output = _docker_exec(
        config, "preauthkeys", "expire", "--user", uid_or_err, key
    )
    if ok:
        return True, f"✅ Pre-Auth key revoked (user: {uid_label})"
    return False, f"❌ Error: {output}"


def list_nodes() -> Tuple[bool, str, List[Dict]]:
    """Get the list of connected nodes."""
    config = _load_config()
    ok, output = _docker_exec(config, "nodes", "list", "-o", "json")
    if not ok:
        return False, f"❌ Error: {output}", []

    try:
        nodes = json.loads(output)
        if not isinstance(nodes, list):
            nodes = []
        return True, f"✅ Nodes found: {len(nodes)}", nodes
    except json.JSONDecodeError:
        return False, f"❌ Invalid JSON:\n{output[:200]}", []


def list_users() -> Tuple[bool, str, List[str]]:
    """Get the list of Headscale users."""
    config = _load_config()
    ok, output = _docker_exec(config, "users", "list", "-o", "json")
    if not ok:
        return False, f"❌ Error: {output}", []

    try:
        users = json.loads(output)
        if not isinstance(users, list):
            users = []
        names = [u.get("name", "?") for u in users if isinstance(u, dict)]
        return True, f"✅ Users: {len(names)}", names
    except json.JSONDecodeError:
        return False, f"❌ Invalid JSON:\n{output[:200]}", []


def _is_container_running(container_name: str) -> bool:
    """Check whether the Docker container is running (docker on the host, see host_run)."""
    try:
        from host_utils import host_run

        result = host_run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def get_headplane_status() -> Dict:
    """
    Headplane (Web UI) status next to Headscale.

    Headplane is a separate container (see compose.headplane.yaml).
    Running or not is determined by a container named ``headplane``.
    Access is only via SSH tunnel: ``ssh -L 3000:127.0.0.1:3000``.

    Config lives in ``headplane/config.yaml`` (gitignored); parameters
    are not duplicated in headscale_config.json — single source of truth.
    """
    container = "headplane"
    return {
        "container_name": container,
        "container_running": _is_container_running(container),
        # Headplane always listens on loopback per compose.headplane.yaml.
        "tunnel_hint": "ssh -L 3000:127.0.0.1:3000 root@<VPS_IP>",
        "browser_url": "http://127.0.0.1:3000/admin",
    }


def get_status() -> Dict:
    """Get Headscale status (container, nodes, URL) plus Headplane status."""
    config = _load_config()
    status = {
        "enabled": config.get("enabled", False),
        "server_url": config.get("server_url", ""),
        "container_name": config.get("container_name", "headscale"),
        "container_running": False,
        "node_count": 0,
        "user_count": 0,
        "headplane": get_headplane_status(),
    }

    status["container_running"] = _is_container_running(status["container_name"])

    # Get node count
    if status["container_running"]:
        ok, _, nodes = list_nodes()
        if ok:
            status["node_count"] = len(nodes)
        ok, _, users = list_users()
        if ok:
            status["user_count"] = len(users)

    return status


def get_host_tailscale_client_summary() -> Tuple[bool, str]:
    """
    IPv4/IPv6 addresses of the local Tailscale client on the **host** (not the headscale container).

    Used for /headscale: a VPS often has tailscale installed and joined to Headscale.
    Inside Docker with ``pid: host`` commands run in the host namespace (see host_utils.host_run).
    """
    try:
        from host_utils import host_run
    except ImportError:
        return False, "❌ host_utils module is unavailable."

    candidates = ("tailscale", "/usr/bin/tailscale", "/usr/sbin/tailscale")
    last_detail = ""

    try:
        for bin_path in candidates:
            r = host_run(
                [bin_path, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0:
                if not (r.stdout and r.stdout.strip()):
                    return (
                        False,
                        "❌ Tailscale on the host answers, but tailscale ip -4 returned no address.\n\n"
                        "Check on the server: tailscale status and, if needed, tailscale up.",
                    )
                v4_lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                v6_lines: List[str] = []
                r6 = host_run(
                    [bin_path, "ip", "-6"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r6.returncode == 0 and r6.stdout:
                    v6_lines = [
                        ln.strip()
                        for ln in r6.stdout.splitlines()
                        if ln.strip()
                    ]

                lines_msg = [
                    "🌐 Tailscale client on this server (host):",
                    "",
                    "IPv4:",
                    "\n".join(v4_lines),
                ]
                if v6_lines:
                    lines_msg.extend(["", "IPv6:", "\n".join(v6_lines)])
                lines_msg.extend(
                    [
                        "",
                        "If there are no addresses — run tailscale up on the server with your Headscale "
                        "(see HEADSCALE_GUIDE.md).",
                    ]
                )
                return True, "\n".join(lines_msg)

            err = (r.stderr or r.stdout or "").strip()
            if err:
                last_detail = err
            # "executable not found" — try the next path
            if r.returncode != 0 and (
                "not found" in err.lower() or "No such file" in err
            ):
                continue
            # Binary exists, but tailscale is not up / not in the network
            if r.returncode != 0:
                hint = err or f"exit code {r.returncode}"
                return (
                    False,
                    "❌ The tailscale command exists on the host, but no address was obtained.\n\n"
                    f"Details: {hint}\n\n"
                    "Usually you need: tailscale up --login-server <URL> --authkey <key> "
                    "(see HEADSCALE_GUIDE.md).",
                )

        # No path produced useful stdout
        if last_detail and ("not found" not in last_detail.lower()):
            return (
                False,
                "❌ Tailscale executable not found on the host, or the client is not in the network.\n\n"
                f"Last error: {last_detail}",
            )
        return (
            False,
            "❌ Tailscale client is not installed on this server "
            "(tailscale is not in PATH) or is unreachable from the bot container.\n\n"
            "Install Tailscale on the VPS and join Headscale — see HEADSCALE_GUIDE.md.",
        )
    except Exception as e:
        logger.error("get_host_tailscale_client_summary: %s", e)
        return False, f"❌ Error calling tailscale: {e}"


# === Exit node (internet exit via the VPS coordinator) ===
#
# How it works: the host node (the tailnet's own client) advertises itself as
# an exit node (route 0.0.0.0/0 + ::/0), Headscale approves that route, the
# client (phone) picks it in the Tailscale app. The bot brings the server side
# to "available", but the final exit-node choice is made on the device —
# the server cannot push it (it is a local client setting).

_EXIT_ROUTES = ("0.0.0.0/0", "::/0")
_TAILSCALE_BINS = ("tailscale", "/usr/bin/tailscale", "/usr/sbin/tailscale")


def _host_run_first(bins, args, timeout: int = 15):
    """Run the first available binary from ``bins`` on the host via host_run.

    Returns (CompletedProcess | None, bin_path | None). None if no
    path was found (FileNotFoundError / not found).
    """
    from host_utils import host_run

    last = None
    for bin_path in bins:
        try:
            r = host_run(
                [bin_path, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return None, None
        err = (r.stderr or r.stdout or "").lower()
        if r.returncode != 0 and ("not found" in err or "no such file" in err):
            last = r
            continue
        return r, bin_path
    return last, None


def _host_sysctl_get(key: str) -> Optional[str]:
    """Read a sysctl on the host (for example net.ipv4.ip_forward)."""
    r, _ = _host_run_first(("sysctl", "/sbin/sysctl", "/usr/sbin/sysctl"), ["-n", key], timeout=5)
    if r is not None and r.returncode == 0:
        return (r.stdout or "").strip()
    return None


def _host_sysctl_set(key: str, value: str) -> bool:
    """Set a sysctl on the host (runtime, not persistent)."""
    r, _ = _host_run_first(("sysctl", "/sbin/sysctl", "/usr/sbin/sysctl"), ["-w", f"{key}={value}"], timeout=5)
    return r is not None and r.returncode == 0


def _node_route_sets(node: Dict) -> Tuple[set, set]:
    """(available, approved) node routes from the nodes-list JSON.

    Headscale changes field names between versions — collect from all known
    keys so we are not tied to a specific version.
    """
    def pick(*keys) -> set:
        out: set = set()
        for k in keys:
            v = node.get(k)
            if isinstance(v, list):
                out.update(str(x) for x in v)
        return out

    available = pick("availableRoutes", "available_routes", "subnetRoutes", "subnet_routes")
    approved = pick("approvedRoutes", "approved_routes", "enabledRoutes", "enabled_routes")
    return available, approved


def _is_exit_routes(routes: set) -> bool:
    """The set contains both exit routes (or at least IPv4 0.0.0.0/0)."""
    return "0.0.0.0/0" in routes


def _find_exit_candidate(nodes: List[Dict]) -> Optional[Dict]:
    """Node that advertised an exit-node route (0.0.0.0/0 in available)."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        available, _ = _node_route_sets(node)
        if _is_exit_routes(available):
            return node
    return None


def _node_id(node: Dict) -> str:
    """Node ID for approve-routes (as a string)."""
    return str(node.get("id") or node.get("ID") or node.get("nodeId") or "").strip()


def _node_label(node: Dict) -> str:
    name = node.get("givenName") or node.get("name") or "?"
    ips = node.get("ipAddresses") or []
    ip = ips[0] if ips else "?"
    return f"{name} ({ip})"


def _approve_exit_routes(config: Dict, node_id: str) -> Tuple[bool, str]:
    """Approve the node's exit routes in Headscale.

    Try 0.26+ syntax first (``nodes approve-routes``); on failure
    return a clear error with a Headplane hint (older versions
    use ``routes enable``, which may be missing).
    """
    routes_csv = ",".join(_EXIT_ROUTES)
    ok, output = _docker_exec(
        config, "nodes", "approve-routes", "-i", node_id, "-r", routes_csv, timeout=20
    )
    if ok:
        return True, output or "routes approved"
    # Possibly an old Headscale version (no approve-routes).
    return False, output


def get_exit_node_status() -> Dict:
    """Exit-node state: host forwarding + advertise/approve in Headscale."""
    config = _load_config()
    status: Dict = {
        "container_running": _is_container_running(config.get("container_name", "headscale")),
        "ip_forward_v4": _host_sysctl_get("net.ipv4.ip_forward"),
        "ip_forward_v6": _host_sysctl_get("net.ipv6.conf.all.forwarding"),
        "advertising": False,   # the node advertised itself as an exit node
        "approved": False,      # Headscale approved the exit route
        "node_label": "",
        "error": "",
    }
    if not status["container_running"]:
        status["error"] = "headscale container is not running"
        return status

    ok, _msg, nodes = list_nodes()
    if not ok:
        status["error"] = "failed to get the node list"
        return status

    cand = _find_exit_candidate(nodes)
    if cand is not None:
        status["advertising"] = True
        status["node_label"] = _node_label(cand)
        _avail, approved = _node_route_sets(cand)
        status["approved"] = _is_exit_routes(approved)
    return status


def enable_exit_node() -> Tuple[bool, str]:
    """Make the VPS coordinator an exit node: advertise on the host + approve in HS.

    Returns (ok, a human-readable step-by-step report).
    """
    config = _load_config()
    steps: List[str] = []

    # 1. Host advertises itself as an exit node (non-destructive set, no full up).
    r, bin_path = _host_run_first(_TAILSCALE_BINS, ["set", "--advertise-exit-node"], timeout=20)
    if r is None and bin_path is None:
        return False, "❌ tailscale not found on the host. Install the client and join Headscale (HEADSCALE_GUIDE.md §Exit node)."
    if r is not None and r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        return False, f"❌ tailscale set --advertise-exit-node failed: {detail}"
    steps.append("✅ host advertised itself as an exit node")

    # 2. IP forwarding on the host (runtime). Persistent — see HEADSCALE_GUIDE.md.
    for key, label in (
        ("net.ipv4.ip_forward", "IPv4"),
        ("net.ipv6.conf.all.forwarding", "IPv6"),
    ):
        cur = _host_sysctl_get(key)
        if cur == "1":
            steps.append(f"✅ forwarding {label} already enabled")
        elif _host_sysctl_set(key, "1"):
            steps.append(f"✅ forwarding {label} enabled (runtime; persistent — see the guide)")
        else:
            steps.append(f"⚠️ forwarding {label} could not be enabled — check on the host manually")

    # 3. Approve the exit route in Headscale (Headscale needs to see the advertise).
    ok, _msg, nodes = list_nodes()
    cand = _find_exit_candidate(nodes) if ok else None
    if cand is None:
        steps.append(
            "⚠️ Headscale does not yet see the node's exit route. In 5–10 sec "
            "retry /exit_node_on or approve 0.0.0.0/0 and ::/0 in Headplane."
        )
        return True, "\n".join(steps)

    node_id = _node_id(cand)
    if not node_id:
        steps.append("⚠️ could not determine the node ID — approve the route in Headplane.")
        return True, "\n".join(steps)

    appr_ok, appr_out = _approve_exit_routes(config, node_id)
    if appr_ok:
        steps.append(f"✅ Headscale approved the exit route for {_node_label(cand)}")
        steps.append("")
        steps.append("🎉 Exit node is ready. Next — pick it on the device (see /exit_node).")
    else:
        steps.append(
            f"⚠️ auto-approve failed ({appr_out}). Enable routes "
            f"0.0.0.0/0 and ::/0 for node {_node_label(cand)} in Headplane."
        )
    return True, "\n".join(steps)


def disable_exit_node() -> Tuple[bool, str]:
    """Stop being an exit node (disable advertise on the host).

    The route stays approved in Headscale, but without advertise clients cannot
    use it. This is reversible: another /exit_node_on brings everything back.
    """
    r, bin_path = _host_run_first(_TAILSCALE_BINS, ["set", "--advertise-exit-node=false"], timeout=20)
    if r is None and bin_path is None:
        return False, "❌ tailscale not found on the host."
    if r is not None and r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        return False, f"❌ Failed to disable advertise: {detail}"
    return True, "✅ Exit node disabled: the host no longer advertises 0.0.0.0/0.\nClients that picked it will lose internet exit via the VPS."


def exit_node_client_instructions(node_label: str = "") -> str:
    """Step-by-step guide for the user: how to pick the exit node on a device."""
    target = node_label or "your VPS coordinator node"
    return (
        "🌐 Internet exit via the VPS — how to enable it on a device\n\n"
        f"Exit node: {target}\n\n"
        "📱 iPhone / iPad:\n"
        "  1. Open the Tailscale app\n"
        "  2. Menu (≡) → Exit Node\n"
        f"  3. Select {target}\n"
        "  4. (opt.) Allow LAN access — if you need access to the local network\n\n"
        "🤖 Android: Tailscale → ⋮ → Use exit node → select the node\n\n"
        "💻 macOS / Windows: Tailscale tray menu → Exit Node → select the node\n\n"
        "🐧 Linux: sudo tailscale set --exit-node=<name-or-IP> --exit-node-allow-lan-access\n\n"
        "Check: open https://ifconfig.me — it should show your VPS IP.\n"
        "The choice is remembered: set it once, then Tailscale keeps the exit itself."
    )


def export_client_instructions(preauth_key: str) -> str:
    """Generate instructions for connecting a client to Headscale."""
    config = _load_config()
    server_url = config.get("server_url", "https://headscale.example.com")

    return f"""== Connecting to Headscale ==

Coordinator URL: {server_url}
Pre-Auth key: {preauth_key}

--- Linux / macOS ---
tailscale up --login-server {server_url} --authkey {preauth_key}

--- Windows ---
1. Shift-click the Tailscale tray icon
2. Preferences → Log in to custom control panel
3. URL: {server_url}
4. Or via PowerShell:
   tailscale up --login-server {server_url} --authkey {preauth_key}
"""
