# -*- coding: utf-8 -*-
"""
CLI Dashboard — интерактивное меню бота в терминале (по SSH, без Telegram).

Зачем: когда Telegram недоступен (например, нужен VPN до самого Telegram),
админ заходит на сервер по SSH и управляет ботом через это меню. Логика
команд НЕ дублируется — каждая команда выполняется через AdminCLI.execute(),
тот же слой, что обслуживает /admin_command в REST API.

Запуск:
  на хосте:        ./scripts/bot_cli.sh                  (docker exec -it ...)
  в контейнере:    python3 cli_dashboard.py              # интерактивное меню
  одной командой:  python3 cli_dashboard.py /vless_status [args...]

Оформление — rich (см. requirements.txt). Если rich не установлен,
дашборд деградирует до простого текстового вывода (тот же набор команд).

Author: Kurein M.N.
Date: 11.06.2026
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# .env до AdminCLI/Config: systemd-бот и main.py грузят dotenv, нативный
# bot_cli.sh — нет; без этого ADMIN_USER_IDS пуст и /list_users врёт.
try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parent / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file)
except ImportError:
    pass

# Дашборд — терминальный инструмент: его вывод идёт через rich/print, а не через
# logging. Глушим import-time лог-шум бот-стека (config.py пишет на INFO/WARNING:
# "Configuration loaded successfully", "Admin user IDs: …", "BOT_TOKEN is not set").
# Делаем это ДО импорта admin_cli (который инстанцирует Config и выставляет
# root-logger по LOG_LEVEL). Реальные ошибки (ERROR) остаются видимы; явно
# заданный LOG_LEVEL (напр. DEBUG для отладки) уважаем.
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
except ImportError:  # graceful degradation — как и в остальном проекте
    RICH = False
    console = None

import cli_prompt
from admin_cli import AdminCLI
from utils import get_app_version

# Статические подсказки аргументов для автодополнения (TAB).
_ARG_HINTS = {"/headscale_gen": ["24h", "720h", "7d"]}


# (command, краткое описание, требует подтверждения, аргументы)
# Аргументы: None — без аргументов; (подсказка, required) — спросить; при
# required=False пустой ввод запускает команду без аргументов (а не отменяет).
# Команды берутся из AdminCLI.COMMANDS — меню только группирует и оформляет их.
MENU_SECTIONS: List[Tuple[str, List[Tuple[str, str, bool, Optional[Tuple[str, bool]]]]]] = [
    ("🔧 Система", [
        ("/info", "Информация о сервере", False, None),
        ("/ver", "Версия и статус VLESS", False, None),
        ("/dockhand", "SSH-туннель к Dockhand (8501)", False, None),
        ("/headscale", "Tailscale IP этого хоста", False, None),
    ]),
    ("🤖 Бот", [
        ("/bot_status", "Включён ли Telegram-бот", False, None),
        ("/enable_bot", "Включить бота (restart)", True, None),
        ("/disable_bot", "Выключить бота (restart)", True, None),
    ]),
    ("🌐 Exit node (интернет через VPS)", [
        ("/exit_node", "Статус + гайд по устройствам", False, None),
        ("/exit_node_on", "Включить exit node", True, None),
        ("/exit_node_off", "Выключить exit node", True, None),
    ]),
    ("🕸️ Headscale (mesh)", [
        ("/headscale_status", "Статус Headscale + Headplane", False, None),
        ("/headscale_list_nodes", "Список нод mesh", False, None),
        ("/headscale_gen", "Pre-Auth ключ ([user] [срок], напр. 720h)", False,
         ("[user] [срок] (Enter — дефолт)", False)),
        ("/headscale_revoke", "Отозвать Pre-Auth ключ (Enter — список)", True,
         ("<key> [user] (Enter — показать список ключей)", False)),
    ]),
    ("🛰 Reticulum / HA-стек", [
        ("/reticulum_status", "Статус сервисов + bridge hash + I2P", False, None),
        ("/reticulum_hash", "Bridge destination hash (для клиентов)", False, None),
        ("/reticulum_i2p", "I2P-путь: статус i2pd + b32 (путь 2)", False, None),
        ("/reticulum_health", "Здоровье i2pd: сеть/tunnel success/leasesets", False, None),
        ("/reticulum_restart", "Перезапустить HA-стек (bridge + stub)", True, None),
    ]),
    ("🛡️ VLESS-Reality", [
        ("/vless_status", "Статус VLESS", False, None),
        ("/vless_config", "Конфигурация (ключи маскированы)", False, None),
        ("/vless_link", "Ссылка для импорта клиента", False, None),
        ("/vless_on", "Включить VLESS", True, None),
        ("/vless_off", "Выключить VLESS", True, None),
        ("/vless_set_port", "Сменить порт VLESS", True, ("порт (например 8443)", True)),
    ]),
    ("👤 Профили пользователей", [
        ("/list_users", "Все пользователи бота с Telegram ID", False, None),
        ("/links", "Ссылки профилей по TG-ID; Enter — профили админа", False,
         ("telegram_user_id (Enter — админ по умолчанию, все ID: /list_users)", False)),
        ("/qr", "QR-код ссылки в терминале; Enter — список вариантов", False,
         ("[TG-ID] вариант (vless | hy2 | ...; Enter — показать список)", False)),
    ]),
    ("🗄️ Бэкапы (rclone)", [
        ("/backup_status", "Статус offsite-бэкапов", False, None),
        ("/backup_list", "Список последних бэкапов", False, None),
        ("/backup_test", "Проверить настроенный remote", False, None),
        ("/backup_now", "Сделать бэкап сейчас", True, None),
    ]),
    ("🔑 Ключи (маскированные)", [
        ("/api", "API-ключ apiai-v3", False, None),
        ("/encryption_key", "Ключ шифрования apiai-v3", False, None),
    ]),
]


def _flat_menu() -> List[Tuple[str, str, bool, Optional[str]]]:
    """Сквозная нумерация пунктов по всем секциям."""
    items = []
    for _title, entries in MENU_SECTIONS:
        items.extend(entries)
    return items


def _print_plain(text: str) -> None:
    print(text)


def _show_result(ok: bool, text: str, command: str) -> None:
    if RICH:
        style = "green" if ok else "red"
        # Text() — чтобы rich не интерпретировал [скобки] в ссылках/QR как разметку
        console.print(Panel(Text(text), title=command, border_style=style, expand=False))
    else:
        _print_plain(f"--- {command} ---\n{text}\n")


def render_menu() -> None:
    """Нарисовать заголовок и таблицу команд."""
    version = get_app_version().get("version", "?")
    if RICH:
        console.print(Panel(
            f"[bold]TelegramHelper — CLI Dashboard[/bold]  v{version}\n"
            "Меню бота по SSH: те же команды, что в Telegram, но в терминале.",
            border_style="cyan", expand=False,
        ))
        num = 0
        for title, entries in MENU_SECTIONS:
            table = Table(title=title, title_justify="left",
                          box=box.SIMPLE, show_header=False, padding=(0, 1))
            table.add_column("№", style="bold cyan", width=4, justify="right")
            table.add_column("Команда", style="yellow", min_width=18)
            table.add_column("Описание")
            for command, descr, confirm, _arg in entries:
                num += 1
                mark = " ⚠" if confirm else ""
                table.add_row(str(num), command, descr + mark)
            console.print(table)
        console.print("[dim]номер — выполнить · команда — напрямую (/vless_status) · "
                      "m — меню · q — выход · ⚠ — спросит подтверждение[/dim]")
    else:
        print(f"\nTelegramHelper — CLI Dashboard v{version}")
        print("(установите rich для красивого вида: pip install rich)\n")
        num = 0
        for title, entries in MENU_SECTIONS:
            print(title)
            for command, descr, confirm, _arg in entries:
                num += 1
                mark = " [подтверждение]" if confirm else ""
                print(f"  {num:>3}. {command:<18} {descr}{mark}")
            print()
        print("номер — выполнить, команда — напрямую, m — меню, q — выход")


class _Cancelled(Exception):
    """Пользователь отменил ввод (Ctrl+C / Ctrl+D / пустой обязательный аргумент)."""


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
        return answer in ("y", "yes", "д", "да")
    except (EOFError, KeyboardInterrupt):
        return False


def run_command(cli: AdminCLI, command: str, args: List[str],
                confirm: bool, arg_spec: Optional[Tuple[str, bool]]) -> None:
    """Выполнить команду через AdminCLI с подтверждением и запросом аргументов."""
    try:
        if arg_spec and not args:
            hint, required = arg_spec
            raw = _ask(f"Аргументы — {hint}")
            if raw:
                args = raw.split()
            elif required:
                _show_result(True, "Отменено (нужны аргументы).", command)
                return
            # необязательный аргумент + Enter → запускаем без аргументов
        if confirm and not _confirm(
                f"Выполнить {command}{' ' + ' '.join(args) if args else ''}?"):
            _show_result(True, "Отменено.", command)
            return
    except _Cancelled:
        _show_result(True, "Отменено.", command)
        return
    ok, text = cli.execute(command, args)
    _show_result(ok, text, command)


def _prompt_choice(session: cli_prompt.PromptSession) -> str:
    """Главный prompt выбора: prompt_toolkit (TAB/история) или фолбэк на _ask."""
    if session.available:
        try:
            return session.ask("\nВыбор: ")
        except (EOFError, KeyboardInterrupt):
            raise _Cancelled()
    return _ask("\n[bold cyan]Выбор[/bold cyan]" if RICH else "\nВыбор")


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
        # Прямой ввод команды: "/vless_set_port 8443"
        if choice.startswith("/"):
            parts = choice.split()
            cmd, descr, confirm, arg = by_command.get(
                parts[0].lower(), (parts[0], "", False, None))
            run_command(cli, cmd, parts[1:], confirm, arg if len(parts) == 1 else None)
            continue
        # Ввод по номеру пункта
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            cmd, descr, confirm, arg = items[int(choice) - 1]
            run_command(cli, cmd, [], confirm, arg)
            continue
        _show_result(False, f"Не понял ввод: {choice!r}. m — меню, q — выход.", "?")


def main() -> int:
    cli = AdminCLI()
    argv = sys.argv[1:]
    # One-shot режим: python3 cli_dashboard.py /vless_status [args...]
    if argv:
        command = argv[0] if argv[0].startswith("/") else "/" + argv[0]
        ok, text = cli.execute(command, argv[1:])
        # В one-shot выводим без рамок — удобно для скриптов/пайпов
        print(text)
        return 0 if ok else 1
    try:
        interactive_loop(cli)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
