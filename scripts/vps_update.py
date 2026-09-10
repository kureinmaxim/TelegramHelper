#!/usr/bin/env python3
"""vps_update.py — interactive TelegramHelper component updater (Python + rich).

Run ON the VPS via `bash scripts/vps_update.sh` (it installs rich). Detects
what is installed and offers to update each component with the proven steps
from POST_DEPLOY.md. For non-standard scenarios (host-network mesh-only, etc.)
see POST_DEPLOY.md.

  bash scripts/vps_update.sh             # normal mode
  bash scripts/vps_update.sh --dry-run   # show actions without running them
"""

import argparse
import os
import re
import subprocess
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm as _RichConfirm
from rich.prompt import Prompt
from rich.table import Table

# The terminal may send non-UTF-8 bytes (SSH client on CP1251/KOI8, or a
# Cyrillic "y"-lookalike instead of Latin "y") — input() then raises
# UnicodeDecodeError. Replace unreadable bytes instead of crashing the
# updater on the first prompt.
if hasattr(sys.stdin, "reconfigure"):
    try:
        sys.stdin.reconfigure(errors="replace")
    except Exception:
        pass

console = Console()


class Confirm(_RichConfirm):
    """Confirm that also accepts Cyrillic yes/no (Unicode escapes, not letters).

    A Cyrillic lookalike of "y" is visually identical to Latin "y", so we
    accept both layouts even though prompts are now in English.
    """

    # Cyrillic yes/no mapped to y/n (kept as escapes so source has no Cyrillic)
    _RU = {
        "\u0443": "y",
        "\u0434": "y",
        "\u0434\u0430": "y",
        "\u043d": "n",
        "\u043d\u0435\u0442": "n",
    }

    def process_response(self, value: str) -> bool:
        v = value.strip().lower()
        return super().process_response(self._RU.get(v, v))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SUDO = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else ["sudo"]

DRY = False
failed = []  # names of steps that failed


def sh(cmd):
    """Run a command list. True on success. Honours --dry-run."""
    if DRY:
        console.print(f"  [dim][dry-run] {' '.join(cmd)}[/dim]")
        return True
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode == 0


def cap(cmd):
    """Command stdout (empty string if the binary is missing)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT).stdout
    except FileNotFoundError:
        return ""


def do_step(name, cmd):
    console.rule(f"[bold]{name}")
    ok = sh(cmd)
    if DRY:
        return
    console.print(f"[green]✓ {name}[/green]" if ok else f"[red]✗ {name}[/red]")
    if not ok:
        failed.append(name)


def unit_exists(unit):
    return any(
        line.startswith(unit)
        for line in cap(["systemctl", "list-unit-files", "--no-pager"]).splitlines()
    )


def docker_svc(name):
    return name in [
        l.strip() for l in cap(["docker", "compose", "ps", "--services"]).splitlines()
    ]


def docker_running(name):
    return name in [
        l.strip() for l in cap(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    ]


def hy2_unit():
    for line in cap(["systemctl", "list-unit-files", "--no-pager"]).splitlines():
        m = re.match(r"^(hysteria[a-z-]*\.service)", line)
        if m:
            return m.group(1)
    return None


def main():
    global DRY
    parser = argparse.ArgumentParser(description="TelegramHelper VPS updater (rich)")
    parser.add_argument(
        "--dry-run", action="store_true", help="show actions without running them"
    )
    DRY = parser.parse_args().dry_run

    console.print(
        Panel.fit(
            "[bold cyan]TelegramHelper — VPS update[/bold cyan]\n"
            f"repository: {REPO_ROOT}",
            border_style="cyan",
        )
    )
    if DRY:
        console.print(
            "[yellow]--dry-run MODE: nothing changes, commands are shown only.[/yellow]"
        )

    # --- 1. git pull --- POST_DEPLOY.md §0
    console.rule("Updating code")
    if Confirm.ask(f"Run git pull in {REPO_ROOT}?", default=True):
        if not sh(["git", "pull", "--ff-only"]):
            console.print(
                "[yellow]git pull failed (local changes/conflict?) — fix it by hand.[/yellow]"
            )

    # --- 1.5 compose subnets vs existing networks --- DEPLOY.md §8.9
    # If the declared subnet does not match the existing network, `compose up`
    # tries to recreate the network and takes the bot down (network is held
    # by dockhand/headplane).
    if docker_svc("telegram-helper"):
        if not sh(["bash", "scripts/preflight_subnets.sh"]):
            if Confirm.ask(
                "Compose subnets do not match existing Docker networks. "
                "Write the existing subnets into .env automatically?",
                default=True,
            ):
                do_step(
                    "Compose subnets (.env fix)",
                    ["bash", "scripts/preflight_subnets.sh", "--fix"],
                )
            else:
                console.print(
                    "[yellow]compose up may recreate the network and take the bot down — "
                    "see DEPLOY.md §8.9.[/yellow]"
                )

    # --- 2. Bot --- POST_DEPLOY.md §1/§2
    if docker_svc("telegram-helper"):
        if Confirm.ask("Update the bot (docker compose up -d --build)?", default=True):
            name = "Bot (docker rebuild)"
            console.rule(f"[bold]{name}")
            ok = sh(
                SUDO + ["docker", "compose", "up", "-d", "--build", "telegram-helper"]
            )
            if not ok and not DRY:
                # BuildKit builds in an isolated network and may fail to resolve
                # deb.debian.org (apt exit 100) even when host DNS works.
                # Proven workaround (POST_DEPLOY.md §10): build with host network,
                # then recreate only the bot. Tag comes from compose.yaml.
                console.print(
                    "[yellow]compose build failed — retrying with "
                    "--network=host (POST_DEPLOY.md §10)…[/yellow]"
                )
                ok = sh(
                    SUDO
                    + [
                        "docker",
                        "build",
                        "--network=host",
                        "-t",
                        "telegram-helper-lite:latest",
                        ".",
                    ]
                ) and sh(
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
            if not DRY:
                console.print(
                    f"[green]✓ {name}[/green]" if ok else f"[red]✗ {name}[/red]"
                )
                if not ok:
                    failed.append(name)
    elif unit_exists("telegramhelper"):
        if Confirm.ask(
            "Update the bot (systemd): deps + restart?", default=True
        ):
            do_step(
                "Bot (systemd bootstrap)",
                ["bash", "scripts/install_telegramhelper_vps.sh"],
            )
    elif Confirm.ask(
        "The bot is not installed on this VPS yet. Install now?", default=False
    ):
        mode = Prompt.ask(
            "How to install the bot", choices=["systemd", "docker"], default="systemd"
        )
        if mode == "docker":
            do_step(
                "Bot (docker, first install)",
                SUDO + ["bash", "scripts/install_telegramhelper_docker.sh"],
            )
        else:
            do_step(
                "Bot (systemd, first install)",
                SUDO + ["bash", "scripts/install_telegramhelper_vps.sh"],
            )

    # --- 3. Dockhand --- POST_DEPLOY.md §3
    if docker_svc("dockhand"):
        if Confirm.ask(
            "Update Dockhand (docker compose up -d --build dockhand)?", default=True
        ):
            do_step(
                "Dockhand",
                SUDO + ["docker", "compose", "up", "-d", "--build", "dockhand"],
            )

    # --- 4. VLESS / Xray ---
    if cap(["which", "xray"]).strip() or os.path.exists("/usr/local/bin/xray"):
        if Confirm.ask(
            "Re-sync the VLESS config and restart Xray?", default=True
        ):
            do_step("VLESS (re-sync)", SUDO + ["bash", "scripts/setup_vless_server.sh"])

    # --- 5. MTProto ---
    if unit_exists("mtproto-proxy"):
        if Confirm.ask("Restart MTProto (mtproto-proxy)?", default=True):
            do_step("MTProto restart", SUDO + ["systemctl", "restart", "mtproto-proxy"])

    # --- 6. Hysteria2 (detect unit name) ---
    hy2 = hy2_unit()
    if hy2:
        if Confirm.ask(f"Restart Hysteria2 ({hy2})?", default=True):
            do_step("Hysteria2 restart", SUDO + ["systemctl", "restart", hy2])

    # --- 7. Headscale (+Headplane) --- POST_DEPLOY.md §4/§4a
    if docker_running("headscale"):
        if Confirm.ask("Bring up / update Headscale (compose up -d)?", default=True):
            do_step(
                "Headscale",
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
                ],
            )
        if docker_running("headplane"):
            if Confirm.ask("Update Headplane Web UI?", default=True):
                do_step("Headplane", ["bash", "scripts/install_headplane.sh"])

    # --- 8. HA stack + Reticulum --- POST_DEPLOY.md §12
    if unit_exists("ha-reticulum-bridge") or unit_exists("ha-stub-grpc"):
        if Confirm.ask("Update the HA stack (restart services)?", default=True):
            do_step(
                "HA stack restart",
                SUDO
                + [
                    "systemctl",
                    "restart",
                    "ha-stub-grpc",
                    "ha-stub-udp",
                    "ha-reticulum-bridge",
                ],
            )
        if Confirm.ask(
            "Reinstall the HA stack from scratch (venv+units)?", default=False
        ):
            do_step("HA stack reinstall", ["bash", "scripts/install_ha_stack.sh"])

    # --- 9. Disk cleanup --- CLEANUP_SERVER.md
    # Every `compose up --build` adds a layer to the docker build cache (on
    # your-vps it reached 1.9 GB in two days), so we offer cleanup here,
    # right after rebuilds, instead of waiting until the disk hits 100%.
    console.rule("Disk cleanup")
    before = cap(["df", "-h", "/"]).strip()
    if before:
        console.print(before)
    if Confirm.ask("Clean the disk (docker prune + journald + apt)?", default=True):
        do_step("Disk cleanup", SUDO + ["bash", "scripts/vps_maintenance.sh", "--run"])
        after = cap(["df", "-h", "/"]).strip()
        if after:
            console.print(after)

    if unit_exists("telegramhelper-maintenance.timer"):
        console.print(
            "[green]✓ Auto-cleanup is enabled (weekly, Sun 04:00 UTC).[/green]\n"
            "  What ate the disk: [bold]bash scripts/vps_maintenance.sh --report[/bold]"
        )
    elif Confirm.ask(
        "Auto-cleanup is not configured. Enable the weekly timer?", default=True
    ):
        do_step(
            "Auto-cleanup (timer)",
            SUDO + ["bash", "scripts/vps_maintenance.sh", "--install"],
        )

    # --- summary ---
    console.rule("[bold]Summary")
    if not failed:
        console.print(
            Panel(
                "[green]✅ Update finished.[/green]\n"
                "Complex scenarios (host-network mesh-only, Gmail tokens) — POST_DEPLOY.md.",
                border_style="green",
            )
        )
        return 0
    report = Table(box=None)
    report.add_column("Step")
    report.add_column("Status")
    for name in failed:
        report.add_row(name, "[red]FAILED[/red]")
    console.print(report)
    console.print(
        Panel(
            f"Errors in: [red]{', '.join(failed)}[/red] — see output / journalctl.",
            border_style="red",
        )
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted (Ctrl+C).[/yellow]")
        sys.exit(130)
