#!/usr/bin/env python3
"""vps_setup.py — interactive TelegramHelper component installer (Python + rich).

Run via `bash scripts/vps_setup.sh` (it installs rich/prompt_toolkit on a
clean VPS and invokes this script). Orchestrates existing scripts/install_*.sh.

Base set (ALWAYS installed): Docker, Hysteria2.
Optional: API (Telegram bot), VLESS-Reality, MTProto, NaiveProxy,
Headscale(+Headplane), HA server + Reticulum (stubs).

  bash scripts/vps_setup.sh            # normal mode
  bash scripts/vps_setup.sh --dry-run  # show the plan without running it
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SUDO = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else ["sudo"]

DRY = False
results = {}  # title -> "ok" | "FAILED" | "dry-run"


def sh(cmd, shell=False):
    """Run a command (list or str). Returns True on success. Honours --dry-run."""
    pretty = cmd if isinstance(cmd, str) else " ".join(cmd)
    if DRY:
        console.print(f"  [dim][dry-run] {pretty}[/dim]")
        return True
    return subprocess.run(cmd, shell=shell, cwd=REPO_ROOT).returncode == 0


def unit_active(unit):
    if DRY:
        return True
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def wait_active(unit, tries=6, delay=1.0):
    """Wait until the service is active (the bot polls — it does not start instantly)."""
    for _ in range(tries):
        if unit_active(unit):
            return True
        time.sleep(delay)
    return False


def step(title, cmd, *, shell=False, check_unit=None, check_fn=None):
    console.rule(f"[bold]{title}")
    ran = sh(cmd, shell=shell)
    if DRY:
        results[title] = "dry-run"
        return True
    # Success is not the bootstrap exit code (it may complain about .env
    # placeholders) but whether the service is actually alive:
    # check_fn — arbitrary check (e.g. Docker container), check_unit — systemd.
    if check_fn is not None:
        ok = check_fn()
    elif check_unit:
        ok = wait_active(check_unit)
    else:
        ok = ran
    results[title] = "ok" if ok else "FAILED"
    console.print(
        f"[green]✓ {title}[/green]"
        if ok
        else f"[red]✗ {title}[/red] — see output above / journalctl"
    )
    return ok


def docker_service_running(service):
    """True if the compose service is in the running state."""
    if DRY:
        return True
    try:
        out = subprocess.run(
            ["docker", "compose", "ps", "--status", "running", "--services"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        return service in out.stdout.split()
    except Exception:
        return False


def detected_state():
    table = Table(title="Already installed on this VPS", show_header=False, box=None)
    checks = [
        ("Docker", shutil.which("docker") is not None),
        ("Telegram bot (systemd, telegramhelper)", unit_active("telegramhelper")),
        (
            "Telegram bot (docker, telegram-helper)",
            docker_service_running("telegram-helper"),
        ),
        ("Hysteria2", unit_active("hysteria-server")),
        ("MTProto", unit_active("mtproto-proxy")),
        ("Xray/VLESS", shutil.which("xray") is not None),
        ("HA stack (reticulum-bridge)", unit_active("ha-reticulum-bridge")),
        ("I2P layer (i2pd, path 2)", unit_active("i2pd")),
    ]
    for name, present in checks:
        table.add_row(
            "●" if present else "○",
            f"[green]{name}[/green]" if present else f"[dim]{name}[/dim]",
        )
    console.print(table)


def headscale_coordinator(server_url):
    """Headscale server: config (server_url/listen_addr) + ONLY the headscale
    service (not the full compose → no 127.0.0.1:8000 clash with the bot) + Headplane."""
    console.rule("[bold]Headscale (coordinator)")
    sh(["mkdir", "-p", "headscale/config", "headscale/data"])
    cfg = "headscale/config/config.yaml"
    if not os.path.exists(os.path.join(REPO_ROOT, cfg)):
        sh(
            "curl -sL https://raw.githubusercontent.com/juanfont/headscale/main/"
            f"config-example.yaml -o {cfg}",
            shell=True,
        )
    sh(["sed", "-i", f"s|server_url:.*|server_url: {server_url}|", cfg])
    sh(["sed", "-i", "s|listen_addr:.*|listen_addr: 0.0.0.0:8080|", cfg])
    ok = sh(
        SUDO
        + [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.headscale.yaml",
            "up",
            "-d",
            "headscale",
        ]
    )
    results["Headscale (coordinator)"] = (
        "dry-run" if DRY else ("ok" if ok else "FAILED")
    )
    if not DRY:
        console.print("[green]✓ Headscale[/green]" if ok else "[red]✗ Headscale[/red]")
    if ok or DRY:
        step("Headplane Web UI", ["bash", "scripts/install_headplane.sh"])
    console.print(
        "[dim]External clients reach the coordinator over public HTTPS "
        "(reverse-proxy/domain); compose binds 127.0.0.1:8080.[/dim]"
    )


def tailscale_client(login_server, authkey):
    """Install tailscale and join this node to another Headscale coordinator."""
    console.rule("[bold]Tailscale node (client)")
    if shutil.which("tailscale") is None:
        sh("curl -fsSL https://tailscale.com/install.sh | sh", shell=True)
    cmd = SUDO + ["tailscale", "up", "--login-server", login_server]
    if authkey:
        cmd += ["--authkey", authkey]
    ok = sh(cmd)
    results["Tailscale node (client)"] = (
        "dry-run" if DRY else ("ok" if ok else "FAILED")
    )
    if not DRY:
        console.print(
            "[green]✓ Tailscale node connected[/green]"
            if ok
            else "[red]✗ tailscale up failed — check URL/authkey[/red]"
        )


def main():
    global DRY
    parser = argparse.ArgumentParser(description="TelegramHelper VPS installer (rich)")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan without running it"
    )
    DRY = parser.parse_args().dry_run

    console.print(
        Panel.fit(
            "[bold cyan]TelegramHelper — VPS installer[/bold cyan]\n"
            f"repository: {REPO_ROOT}",
            border_style="cyan",
        )
    )
    if DRY:
        console.print(
            "[yellow]--dry-run MODE: nothing changes, commands are shown only.[/yellow]"
        )
    detected_state()

    console.print(
        Panel(
            "[bold]The base set is ALWAYS installed:[/bold]\n"
            "  • [green]Docker[/green] (needed by the bot/Headscale)\n"
            "  • [green]Hysteria2[/green] (UDP VPN transport)",
            title="Required",
            border_style="green",
        )
    )

    # --- optional selection (can go back and re-answer) ---
    # All answers live in want/params/hs_role and we re-ask in a loop: if you
    # do not confirm at the end, previous answers become the new defaults, so
    # you only fix the wrong item and skip the rest with Enter.
    want = {
        "api": True,
        "api_mode": "systemd",
        "vless": False,
        "naive": False,
        "mtproto": False,
        "headscale": False,
        "ha": True,
        "ha_adapter": False,
        "i2p": False,
        "dockhand": False,
    }
    params = {}
    hs_role = "client"

    while True:
        console.rule("Optional components")
        console.print(
            "[dim]Enter — previous answer / default. At the end you can go back "
            "and re-answer.[/dim]"
        )
        want["api"] = Confirm.ask(
            "API — Telegram bot (will be [b]started[/b], asks for BOT_TOKEN / ADMIN_USER_IDS)?",
            default=want["api"],
        )
        if want["api"]:
            console.print(
                "[dim]systemd — recommended if this VPS will also run Docker services "
                "(Headscale/Dockhand/HA stack) — fewer port/resource clashes.\n"
                "docker — simpler if the bot is the only service on the VPS.\n"
                "Before the build the installer fills .env with prompts: BOT_TOKEN, "
                "ADMIN_USER_IDS; API_SECRET_KEY and HMAC_SECRET are generated.\n"
                "If the fields are already set in .env — it will not ask again.[/dim]"
            )
            want["api_mode"] = Prompt.ask(
                "  How to install the bot",
                choices=["systemd", "docker"],
                default=want.get("api_mode", "systemd"),
            )
        want["vless"] = Confirm.ask("VLESS-Reality?", default=want["vless"])
        want["naive"] = Confirm.ask(
            "NaiveProxy (needs a domain + DNS)?", default=want["naive"]
        )
        want["mtproto"] = Confirm.ask("MTProto?", default=want["mtproto"])
        want["headscale"] = Confirm.ask(
            "Headscale / Tailscale node?", default=want["headscale"]
        )
        if want["headscale"]:
            hs_role = Prompt.ask(
                "  Role of this VPS in the mesh",
                choices=["coordinator", "client"],
                default=hs_role,
            )
            # Ask role params NOW so they do not mix with the other questions.
            if hs_role == "coordinator":
                params["hs_server_url"] = Prompt.ask(
                    "  Headscale server_url (coordinator domain; external clients need HTTPS)",
                    default=params.get(
                        "hs_server_url", "https://headscale.example.com"
                    ),
                )
            else:  # client
                params["hs_login"] = Prompt.ask(
                    "  Headscale coordinator URL (--login-server), e.g. https://headscale.example.com:8443",
                    default=params.get("hs_login", ""),
                )
                params["hs_authkey"] = Prompt.ask(
                    "  Coordinator pre-auth key", default=params.get("hs_authkey", "")
                )
        want["ha"] = Confirm.ask(
            "HA server + Reticulum (Mi-Home stubs, for tests)?", default=want["ha"]
        )
        # I2P layer (path 2: native i2pd tunnels) wraps the local bridge port
        # as a hidden I2P service. Only makes sense together with the HA stack.
        if want["ha"]:
            want["i2p"] = Confirm.ask(
                "  + I2P access to the bridge (i2pd, path 2 — no public port/SAM)?",
                default=want["i2p"],
            )
            want["ha_adapter"] = Confirm.ask(
                "  + ha-adapter → real HA on the NAS (tailnet :8123, needs mesh)?",
                default=want["ha_adapter"],
            )
            if want["ha_adapter"]:
                console.print(
                    "[dim]Needs a Headscale client on this VPS and a Long-Lived Token from HA.\n"
                    "Adapter :50057, the bridge will switch to it; optionally "
                    "public :50061 for ApiHA without SSH.[/dim]"
                )
                params["ha_url"] = Prompt.ask(
                    "  HA_URL (NAS on the mesh)",
                    default=params.get("ha_url", "http://100.64.0.2:8123"),
                )
                params["ha_token"] = Prompt.ask(
                    "  HA_TOKEN (Long-Lived from the HA profile)",
                    default=params.get("ha_token", ""),
                    password=True,
                )
                want["ha_switch_bridge"] = Confirm.ask(
                    "  Switch the bridge to adapter :50057?",
                    default=want.get("ha_switch_bridge", True),
                )
                want["ha_public_rns"] = Confirm.ask(
                    "  Open the bridge to the internet (0.0.0.0:50061) for ApiHA without SSH?",
                    default=want.get("ha_public_rns", True),
                )
                if not (params.get("ha_token") or "").strip():
                    console.print(
                        "[yellow]No HA_TOKEN — skipping adapter — "
                        "later: bash scripts/install_ha_adapter.sh[/yellow]"
                    )
                    want["ha_adapter"] = False
        else:
            want["i2p"] = False
            want["ha_adapter"] = False
        want["dockhand"] = Confirm.ask(
            "Dockhand (Streamlit Docker diagnostics, :8501 localhost)?",
            default=want["dockhand"],
        )

        # --- port 443 clash: VLESS vs NaiveProxy (must pick one) ---
        if want["vless"] and want["naive"]:
            console.print(
                "[yellow]VLESS-Reality and NaiveProxy both need 443/TCP — pick one owner.[/yellow]"
            )
            owner = Prompt.ask(
                "Who owns 443/TCP?", choices=["vless", "naiveproxy"], default="vless"
            )
            want["vless"] = owner == "vless"
            want["naive"] = owner == "naiveproxy"

        # --- parameters ---
        params["hy2_port"] = Prompt.ask(
            "Hysteria2 port (UDP)", default=params.get("hy2_port", "443")
        )
        if want["mtproto"]:
            params["mtp_port"] = Prompt.ask(
                "MTProto port", default=params.get("mtp_port", "993")
            )
            params["mtp_domain"] = Prompt.ask(
                "MTProto fake-TLS domain", default=params.get("mtp_domain", "google.com")
            )
        if want["naive"]:
            params["naive_domain"] = Prompt.ask(
                "NaiveProxy domain (required)", default=params.get("naive_domain", "")
            )
            params["naive_port"] = Prompt.ask(
                "NaiveProxy HTTPS port", default=params.get("naive_port", "443")
            )
            if not params["naive_domain"].strip():
                console.print("[red]NaiveProxy without a domain — skipping.[/red]")
                want["naive"] = False

        # --- preflight summary ---
        plan = Table(title="Will run", box=None)
        plan.add_column("Component")
        plan.add_column("Action")
        plan.add_row("Docker", "install if missing (always)")
        plan.add_row(
            "Hysteria2", f"install on port {params['hy2_port']}/UDP (always)"
        )
        if want["api"]:
            plan.add_row(
                "Telegram bot (API)",
                f"install and start via {want['api_mode']} (will ask for token)",
            )
        if want["vless"]:
            plan.add_row("VLESS-Reality", "install (from /opt/TelegramHelper)")
        if want["mtproto"]:
            plan.add_row(
                "MTProto", f"port {params['mtp_port']}, domain {params['mtp_domain']}"
            )
        if want["naive"]:
            plan.add_row(
                "NaiveProxy",
                f"domain {params['naive_domain']}, port {params['naive_port']}",
            )
        if want["headscale"] and hs_role == "coordinator":
            plan.add_row(
                "Headscale (coordinator)",
                f"headscale server + Headplane, server_url {params['hs_server_url']}",
            )
        elif want["headscale"]:
            plan.add_row(
                "Tailscale node (client)", f"join {params['hs_login']}"
            )
        if want["ha"]:
            plan.add_row("HA + Reticulum", "stub server + bridge (127.0.0.1)")
        if want.get("ha_adapter"):
            plan.add_row(
                "HA adapter",
                f"{params.get('ha_url')} → :50057"
                + ("; bridge→adapter" if want.get("ha_switch_bridge") else "")
                + ("; public :50061" if want.get("ha_public_rns") else ""),
            )
        if want["i2p"]:
            plan.add_row("I2P (path 2)", "i2pd + server tunnel ha-bridge → bridge")
        if want["dockhand"]:
            plan.add_row("Dockhand", "docker compose up -d dockhand (:8501 localhost)")
        console.print(plan)

        if Confirm.ask("[bold]Confirm and start the install?[/bold]", default=True):
            break
        if not Confirm.ask(
            "Go back and re-answer (previous answers become defaults)?", default=True
        ):
            console.print("[yellow]Cancelled.[/yellow]")
            return 0
        # otherwise — loop again with current answers as defaults

    # --- install in dependency order ---
    # 1) Docker (always)
    if shutil.which("docker") is None:
        step(
            "Docker (get.docker.com)",
            "curl -fsSL https://get.docker.com | sh",
            shell=True,
        )
    else:
        console.print("[dim]Docker already installed — skipping.[/dim]")
        results["Docker"] = "ok"

    # 2) Hysteria2 (always). check_unit — judge by whether the service is
    # actually alive, not by the installer exit code (Type=simple can crash after start).
    step(
        "Hysteria2",
        SUDO + ["bash", "scripts/install_hysteria2.sh", "--port", params["hy2_port"]],
        check_unit="hysteria-server",
    )
    if not DRY and results.get("Hysteria2") == "FAILED":
        console.print(
            "[yellow]Hysteria2 is installed, but the service is not active — "
            "after install check: journalctl -u hysteria-server -n 30[/yellow]"
        )

    # 3) API/bot (if chosen) — install and check liveness. Mode was chosen above
    # (want["api_mode"]): systemd (install_telegramhelper_vps.sh) or docker
    # (install_telegramhelper_docker.sh, telegram-helper service from compose.yaml).
    if want["api"] and want["api_mode"] == "docker":
        step(
            "Telegram bot (API, docker)",
            SUDO + ["bash", "scripts/install_telegramhelper_docker.sh"],
            check_fn=lambda: docker_service_running("telegram-helper"),
        )
        if not DRY and results.get("Telegram bot (API, docker)") == "FAILED":
            console.print(
                "[yellow]Container is not running — trying force-recreate...[/yellow]"
            )
            sh(
                SUDO
                + [
                    "docker",
                    "compose",
                    "up",
                    "-d",
                    "--force-recreate",
                    "telegram-helper",
                ]
            )
            if docker_service_running("telegram-helper"):
                results["Telegram bot (API, docker)"] = "ok"
                console.print(
                    "[green]✓ Telegram bot (API, docker) — came up after force-recreate[/green]"
                )
            else:
                console.print(
                    "[red]API was selected, but container telegram-helper is not running — check "
                    "docker compose logs telegram-helper and BOT_TOKEN/ADMIN_USER_IDS in .env.[/red]"
                )
    elif want["api"]:
        step(
            "Telegram bot (API)",
            SUDO + ["bash", "scripts/install_telegramhelper_vps.sh"],
            check_unit="telegramhelper",
        )
        if not DRY and results.get("Telegram bot (API)") == "FAILED":
            # API selected → the bot MUST run: try to bring it up and re-check
            console.print("[yellow]Bot is not active — trying restart...[/yellow]")
            sh(SUDO + ["systemctl", "restart", "telegramhelper"])
            if wait_active("telegramhelper"):
                results["Telegram bot (API)"] = "ok"
                console.print(
                    "[green]✓ Telegram bot (API) — came up after restart[/green]"
                )
            else:
                console.print(
                    "[red]API was selected, but the bot is not active — check "
                    "journalctl -u telegramhelper and BOT_TOKEN/ADMIN_USER_IDS in .env.[/red]"
                )

    # 4) VLESS
    if want["vless"]:
        step("VLESS-Reality", SUDO + ["bash", "scripts/setup_vless_server.sh"])

    # 5) MTProto
    if want["mtproto"]:
        step(
            "MTProto",
            SUDO
            + [
                "bash",
                "scripts/install_mtproto.sh",
                "--port",
                params["mtp_port"],
                "--domain",
                params["mtp_domain"],
            ],
        )

    # 6) NaiveProxy
    if want["naive"]:
        step(
            "NaiveProxy",
            SUDO
            + [
                "bash",
                "scripts/install_naiveproxy.sh",
                "--domain",
                params["naive_domain"],
                "--port",
                params["naive_port"],
            ],
        )

    # 7) Headscale coordinator OR Tailscale client (Docker is already there)
    if want["headscale"] and hs_role == "coordinator":
        headscale_coordinator(params["hs_server_url"])
    elif want["headscale"]:
        tailscale_client(params["hs_login"], params.get("hs_authkey", ""))

    # 8) HA + Reticulum
    if want["ha"]:
        step("HA + Reticulum", ["bash", "scripts/install_ha_stack.sh"])

    # 8a) Real HA via tailnet (after stubs + preferably after Headscale client)
    if want.get("ha_adapter"):
        adapter_cmd = [
            "bash",
            "scripts/install_ha_adapter.sh",
            "--ha-url",
            params.get("ha_url", "http://100.64.0.2:8123"),
            "--ha-token",
            params.get("ha_token", ""),
        ]
        if want.get("ha_switch_bridge", True):
            adapter_cmd.append("--switch-bridge")
        if want.get("ha_public_rns"):
            adapter_cmd.append("--public-rns")
        step(
            "HA adapter (real HA)",
            adapter_cmd,
            check_unit="ha-adapter-grpc",
        )

    # 8b) I2P layer (path 2) — i2pd + server tunnel on top of the already-up bridge.
    #     check_unit=i2pd: judge by daemon liveness (b32 is built later, asynchronously).
    if want["i2p"]:
        step(
            "I2P (path 2)", ["bash", "scripts/install_i2p_bridge.sh"], check_unit="i2pd"
        )

    # 9) Dockhand (Streamlit diagnostics) — listens on 127.0.0.1:8501 (via SSH tunnel).
    #    --no-deps: do NOT pull telegram-helper (the bot is systemd and holds :8000 →
    #    otherwise the bot container clashes). docker-socket-proxy is started
    #    explicitly — dockhand needs it for Docker access.
    if want["dockhand"]:
        step(
            "Dockhand",
            SUDO
            + [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
                "--no-deps",
                "docker-socket-proxy",
                "dockhand",
            ],
        )

    # --- final report ---
    console.rule("[bold]Summary")
    report = Table(box=None)
    report.add_column("Component")
    report.add_column("Status")
    for title, status in results.items():
        mark = {
            "ok": "[green]active/ok[/green]",
            "FAILED": "[red]FAILED[/red]",
            "dry-run": "[dim]dry-run[/dim]",
        }.get(status, status)
        report.add_row(title, mark)
    console.print(report)

    failed = [t for t, s in results.items() if s == "FAILED"]
    if failed:
        console.print(
            Panel(
                f"Errors in: [red]{', '.join(failed)}[/red]\n"
                "Check journalctl -u <service>. Details — DEPLOY.md.",
                border_style="red",
            )
        )
        return 1
    done_msg = (
        "[green]Done. All selected components are installed.[/green]\n"
        "HA stack: bridge hash — journalctl -u ha-reticulum-bridge | grep destination.\n"
        "Reticulum tests — RETICULUM_GUIDE.md (this repo). The bot does not start the stack."
    )
    if want["i2p"]:
        done_msg += (
            "\nI2P (path 2): bridge b32 — curl -s http://127.0.0.1:7070/?page=i2p_tunnels "
            "| sed 's/<[^>]*>/ /g' | grep -iE 'ha-bridge|\\.b32'. Details — RETICULUM_GUIDE.md §8."
        )
    console.print(Panel(done_msg, border_style="green"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted (Ctrl+C). Nothing installed — run again.[/yellow]"
        )
        sys.exit(130)
