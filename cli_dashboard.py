# -*- coding: utf-8 -*-
"""
CLI Dashboard — interactive bot menu in the terminal (over SSH, no Telegram).

Why: when Telegram is unreachable (e.g. you need a VPN just to reach Telegram),
the admin SSHs into the server and drives the bot from this menu. Command
logic is NOT duplicated — every command goes through AdminCLI.execute(),
the same layer that serves /admin_command in the REST API.

Run:
  on the host:     ./scripts/bot_cli.sh                  (docker exec -it ...)
  in the container: python3 cli_dashboard.py              # interactive menu
  one-shot:        python3 cli_dashboard.py /vless_status [args...]

UI uses rich (see requirements.txt). If rich is missing,
the dashboard falls back to plain text (same command set).

Author: Kurein M.N.
Date: 11.06.2026
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Load .env before AdminCLI/Config: the systemd bot and main.py load dotenv,
# native bot_cli.sh does not; without this ADMIN_USER_IDS is empty and /list_users lies.
try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parent / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file)
except ImportError:
    pass

# The dashboard is a terminal tool: output goes through rich/print, not
# logging. Mute import-time log noise from the bot stack (config.py logs INFO/WARNING:
# "Configuration loaded successfully", "Admin user IDs: …", "BOT_TOKEN is not set").
# Do this BEFORE importing admin_cli (which instantiates Config and sets the
# root logger from LOG_LEVEL). Real errors (ERROR) stay visible; an explicit
# LOG_LEVEL (e.g. DEBUG for troubleshooting) is respected.
os.environ.setdefault("LOG_LEVEL", "ERROR")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich import box

    RICH = True
    console = Console()
except ImportError:  # graceful degradation — same as the rest of the project
    RICH = False
    console = None

import cli_prompt
from admin_cli import AdminCLI
from utils import get_app_version

# Static argument hints for TAB completion.
_ARG_HINTS = {"/headscale_gen": ["24h", "720h", "7d"]}


# (command, short description, needs confirmation, arguments)
# Arguments: None — no args; (hint, required) — prompt; if
# required=False, empty input runs the command with no args (does not cancel).
# Commands come from AdminCLI.COMMANDS — the menu only groups and presents them.
MENU_SECTIONS: List[Tuple[str, List[Tuple[str, str, bool, Optional[Tuple[str, bool]]]]]] = [
    ("🔧 System", [
        ("/info", "Server info", False, None),
        ("/ver", "Version and VLESS status", False, None),
        ("/dockhand", "SSH tunnel to Dockhand (8501)", False, None),
        ("/headscale", "Tailscale IP of this host", False, None),
    ]),
    ("🤖 Bot", [
        ("/bot_status", "Whether the Telegram bot is enabled", False, None),
        ("/enable_bot", "Enable the bot (restart)", True, None),
        ("/disable_bot", "Disable the bot (restart)", True, None),
    ]),
    ("🌐 Exit node (internet via VPS)", [
        ("/exit_node", "Status + per-device guide", False, None),
        ("/exit_node_on", "Enable exit node", True, None),
        ("/exit_node_off", "Disable exit node", True, None),
    ]),
    ("🕸️ Headscale (mesh)", [
        ("/headscale_status", "Headscale + Headplane status", False, None),
        ("/headscale_list_nodes", "Mesh node list", False, None),
        ("/headscale_gen", "Pre-Auth key ([user] [ttl], e.g. 720h)", False,
         ("[user] [ttl] (Enter — default)", False)),
        ("/headscale_revoke", "Revoke a Pre-Auth key (Enter — list)", True,
         ("<key> [user] (Enter — show key list)", False)),
    ]),
    ("🛰 Reticulum / HA-stack", [
        ("/reticulum_status", "Service status + bridge hash + I2P", False, None),
        ("/reticulum_hash", "Bridge destination hash (for clients)", False, None),
        ("/reticulum_i2p", "I2P path: i2pd status + b32 (path 2)", False, None),
        ("/reticulum_health", "i2pd health: network/tunnel success/leasesets", False, None),
        ("/reticulum_restart", "Restart HA-stack (bridge + stub)", True, None),
    ]),
    ("🛡️ VLESS-Reality", [
        ("/vless_status", "VLESS status", False, None),
        ("/vless_config", "Config (keys masked)", False, None),
        ("/vless_link", "Client import URI", False, None),
        ("/vless_on", "Enable VLESS", True, None),
        ("/vless_off", "Disable VLESS", True, None),
        ("/vless_set_port", "Change VLESS port", True, ("port (e.g. 8443)", True)),
    ]),
    ("👤 User profiles", [
        ("/list_users", "All bot users with Telegram ID", False, None),
        ("/links", "Profile links by TG-ID; Enter — admin profiles", False,
         ("telegram_user_id (Enter — default admin, all IDs: /list_users)", False)),
        ("/qr", "QR of a link in the terminal; Enter — variant list", False,
         ("[TG-ID] variant (vless | hy2 | ...; Enter — show list)", False)),
    ]),
    ("🗄️ Backups (rclone)", [
        ("/backup_status", "Offsite backup status", False, None),
        ("/backup_list", "Recent backups", False, None),
        ("/backup_test", "Check the configured remote", False, None),
        ("/backup_now", "Run a backup now", True, None),
    ]),
    ("🔑 Keys (masked)", [
        ("/api", "apiai-v3 API key", False, None),
        ("/encryption_key", "apiai-v3 encryption key", False, None),
    ]),
]


def _flat_menu() -> List[Tuple[str, str, bool, Optional[str]]]:
    """Flat numbering of items across all sections."""
    items = []
    for _title, entries in MENU_SECTIONS:
        items.extend(entries)
    return items


def _print_plain(text: str) -> None:
    print(text)


def _show_result(ok: bool, text: str, command: str) -> None:
    if RICH:
        style = "green" if ok else "red"
        # Text() — so rich does not treat [brackets] in URIs/QR as markup
        console.print(Panel(Text(text), title=command, border_style=style, expand=False))
    else:
        _print_plain(f"--- {command} ---\n{text}\n")


def render_menu() -> None:
    """Draw the header and command table."""
    version = get_app_version().get("version", "?")
    if RICH:
        console.print(Panel(
            f"[bold]TelegramHelper — CLI Dashboard[/bold]  v{version}\n"
            "SSH bot menu: the same commands as in Telegram, in the terminal.",
            border_style="cyan", expand=False,
        ))
        num = 0
        for title, entries in MENU_SECTIONS:
            table = Table(title=title, title_justify="left",
                          box=box.SIMPLE, show_header=False, padding=(0, 1))
            table.add_column("#", style="bold cyan", width=4, justify="right")
            table.add_column("Command", style="yellow", min_width=18)
            table.add_column("Description")
            for command, descr, confirm, _arg in entries:
                num += 1
                mark = " ⚠" if confirm else ""
                table.add_row(str(num), command, descr + mark)
            console.print(table)
        console.print("[dim]number — run · command — directly (/vless_status) · "
                      "m — menu · q — quit · ⚠ — will ask for confirmation[/dim]")
    else:
        print(f"\nTelegramHelper — CLI Dashboard v{version}")
        print("(install rich for a nicer view: pip install rich)\n")
        num = 0
        for title, entries in MENU_SECTIONS:
            print(title)
            for command, descr, confirm, _arg in entries:
                num += 1
                mark = " [confirm]" if confirm else ""
                print(f"  {num:>3}. {command:<18} {descr}{mark}")
            print()
        print("number — run, command — directly, m — menu, q — quit")


class _Cancelled(Exception):
    """User cancelled input (Ctrl+C / Ctrl+D / empty required argument)."""


def _ask(prompt: str) -> str:
    try:
        if RICH:
            return Prompt.ask(prompt)
        return input(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise _Cancelled()


def _confirm(question: str) -> bool:
    try:
        if RICH:
            return Confirm.ask(question, default=False)
        answer = input(f"{question} [y/N]: ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def run_command(cli: AdminCLI, command: str, args: List[str],
                confirm: bool, arg_spec: Optional[Tuple[str, bool]]) -> None:
    """Run a command via AdminCLI, with confirmation and argument prompts."""
    try:
        if arg_spec and not args:
            hint, required = arg_spec
            raw = _ask(f"Arguments — {hint}")
            if raw:
                args = raw.split()
            elif required:
                _show_result(True, "Cancelled (arguments required).", command)
                return
            # optional argument + Enter → run with no arguments
        if confirm and not _confirm(
                f"Run {command}{' ' + ' '.join(args) if args else ''}?"):
            _show_result(True, "Cancelled.", command)
            return
    except _Cancelled:
        _show_result(True, "Cancelled.", command)
        return
    ok, text = cli.execute(command, args)
    _show_result(ok, text, command)


def _prompt_choice(session: cli_prompt.PromptSession) -> str:
    """Main choice prompt: prompt_toolkit (TAB/history) or fallback to _ask."""
    if session.available:
        try:
            return session.ask("\nChoice: ")
        except (EOFError, KeyboardInterrupt):
            raise _Cancelled()
    return _ask("\n[bold cyan]Choice[/bold cyan]" if RICH else "\nChoice")


def interactive_loop(cli: AdminCLI) -> None:
    items = _flat_menu()
    by_command = {cmd: (cmd, descr, confirm, arg)
                  for cmd, descr, confirm, arg in items}
    session = cli_prompt.PromptSession([cmd for cmd, *_ in items], _ARG_HINTS)
    render_menu()
    while True:
        try:
            choice = _prompt_choice(session).strip()
        except _Cancelled:
            print()
            break
        if not choice:
            continue
        low = choice.lower()
        if low in ("q", "quit", "exit", "0"):
            break
        if low in ("m", "menu", "h", "help"):
            render_menu()
            continue
        # Direct command: "/vless_set_port 8443"
        if choice.startswith("/"):
            parts = choice.split()
            cmd, descr, confirm, arg = by_command.get(
                parts[0].lower(), (parts[0], "", False, None))
            run_command(cli, cmd, parts[1:], confirm, arg if len(parts) == 1 else None)
            continue
        # Select by item number
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            cmd, descr, confirm, arg = items[int(choice) - 1]
            run_command(cli, cmd, [], confirm, arg)
            continue
        _show_result(False, f"Did not understand: {choice!r}. m — menu, q — quit.", "?")


def main() -> int:
    cli = AdminCLI()
    argv = sys.argv[1:]
    # One-shot mode: python3 cli_dashboard.py /vless_status [args...]
    if argv:
        command = argv[0] if argv[0].startswith("/") else "/" + argv[0]
        ok, text = cli.execute(command, argv[1:])
        # One-shot: print without frames — handy for scripts/pipes
        print(text)
        return 0 if ok else 1
    try:
        interactive_loop(cli)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
