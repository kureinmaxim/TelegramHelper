#!/usr/bin/env python3
"""vps_update.py — интерактивное обновление компонентов TelegramHelper (Python + rich).

Запускать НА VPS через `bash scripts/vps_update.sh` (он доставит rich). Детектит
установленное и предлагает обновить каждый компонент проверенными действиями из
POST_DEPLOY.md. Для нестандартных сценариев (host-network mesh-only и т.п.) —
см. POST_DEPLOY.md.

  bash scripts/vps_update.sh             # обычный режим
  bash scripts/vps_update.sh --dry-run   # показать действия без выполнения
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

# Терминал может прислать байты не в UTF-8 (SSH-клиент с CP1251/KOI8 или
# кириллическая «у» вместо «y») — input() тогда падает UnicodeDecodeError.
# Нечитаемые байты заменяем, а не роняем весь апдейтер на первом вопросе.
if hasattr(sys.stdin, "reconfigure"):
    try:
        sys.stdin.reconfigure(errors="replace")
    except Exception:
        pass

console = Console()


class Confirm(_RichConfirm):
    """Confirm, понимающий кириллицу: «у/д/да» = yes, «н/нет» = no.

    Кириллическая «у» на глаз неотличима от латинской «y», и скрипт
    по-русски сам провоцирует русскую раскладку — принимаем оба варианта.
    """

    _RU = {"у": "y", "д": "y", "да": "y", "н": "n", "нет": "n"}

    def process_response(self, value: str) -> bool:
        v = value.strip().lower()
        return super().process_response(self._RU.get(v, v))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SUDO = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else ["sudo"]

DRY = False
failed = []  # имена шагов, завершившихся с ошибкой


def sh(cmd):
    """Выполнить команду-list. True при успехе. Учитывает --dry-run."""
    if DRY:
        console.print(f"  [dim][dry-run] {' '.join(cmd)}[/dim]")
        return True
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode == 0


def cap(cmd):
    """stdout команды (пустая строка, если бинарь отсутствует)."""
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
        "--dry-run", action="store_true", help="показать действия без выполнения"
    )
    DRY = parser.parse_args().dry_run

    console.print(
        Panel.fit(
            "[bold cyan]TelegramHelper — обновление VPS[/bold cyan]\n"
            f"репозиторий: {REPO_ROOT}",
            border_style="cyan",
        )
    )
    if DRY:
        console.print(
            "[yellow]РЕЖИМ --dry-run: ничего не меняется, только показ команд.[/yellow]"
        )

    # --- 1. git pull --- POST_DEPLOY.md §0
    console.rule("Обновление кода")
    if Confirm.ask(f"Сделать git pull в {REPO_ROOT}?", default=True):
        if not sh(["git", "pull", "--ff-only"]):
            console.print(
                "[yellow]git pull не прошёл (локальные правки/конфликт?) — реши вручную.[/yellow]"
            )

    # --- 1.5 Подсети compose vs существующие сети --- DEPLOY.md §8.9
    # Если объявленная подсеть не совпадает с существующей сетью, `compose up`
    # пытается пересоздать сеть и роняет бота (сеть занята dockhand/headplane).
    if docker_svc("telegram-helper"):
        if not sh(["bash", "scripts/preflight_subnets.sh"]):
            if Confirm.ask(
                "Подсети compose не совпадают с существующими сетями Docker. "
                "Прописать существующие подсети в .env автоматически?",
                default=True,
            ):
                do_step(
                    "Подсети compose (.env fix)",
                    ["bash", "scripts/preflight_subnets.sh", "--fix"],
                )
            else:
                console.print(
                    "[yellow]compose up может пересоздать сеть и уронить бота — "
                    "см. DEPLOY.md §8.9.[/yellow]"
                )

    # --- 2. Бот --- POST_DEPLOY.md §1/§2
    if docker_svc("telegram-helper"):
        if Confirm.ask("Обновить бота (docker compose up -d --build)?", default=True):
            name = "Бот (docker rebuild)"
            console.rule(f"[bold]{name}")
            ok = sh(
                SUDO + ["docker", "compose", "up", "-d", "--build", "telegram-helper"]
            )
            if not ok and not DRY:
                # BuildKit собирает в изолированной сети и может не резолвить
                # deb.debian.org (apt exit 100), хотя DNS хоста работает.
                # Проверенный обход (POST_DEPLOY.md §10): сборка с сетью хоста,
                # затем recreate только бота. Тег из compose.yaml.
                console.print(
                    "[yellow]compose build упал — пробую сборку с "
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
            "Обновить бота (systemd): зависимости + перезапуск?", default=True
        ):
            do_step(
                "Бот (systemd bootstrap)",
                ["bash", "scripts/install_telegramhelper_vps.sh"],
            )
    elif Confirm.ask(
        "Бот ещё не установлен на этом VPS. Установить сейчас?", default=False
    ):
        mode = Prompt.ask(
            "Как ставить бота", choices=["systemd", "docker"], default="systemd"
        )
        if mode == "docker":
            do_step(
                "Бот (docker, первая установка)",
                SUDO + ["bash", "scripts/install_telegramhelper_docker.sh"],
            )
        else:
            do_step(
                "Бот (systemd, первая установка)",
                SUDO + ["bash", "scripts/install_telegramhelper_vps.sh"],
            )

    # --- 3. Dockhand --- POST_DEPLOY.md §3
    if docker_svc("dockhand"):
        if Confirm.ask(
            "Обновить Dockhand (docker compose up -d --build dockhand)?", default=True
        ):
            do_step(
                "Dockhand",
                SUDO + ["docker", "compose", "up", "-d", "--build", "dockhand"],
            )

    # --- 4. VLESS / Xray ---
    if cap(["which", "xray"]).strip() or os.path.exists("/usr/local/bin/xray"):
        if Confirm.ask(
            "Пересинхронизировать VLESS-конфиг и перезапустить Xray?", default=True
        ):
            do_step("VLESS (re-sync)", SUDO + ["bash", "scripts/setup_vless_server.sh"])

    # --- 5. MTProto ---
    if unit_exists("mtproto-proxy"):
        if Confirm.ask("Перезапустить MTProto (mtproto-proxy)?", default=True):
            do_step("MTProto restart", SUDO + ["systemctl", "restart", "mtproto-proxy"])

    # --- 6. Hysteria2 (имя юнита детектим) ---
    hy2 = hy2_unit()
    if hy2:
        if Confirm.ask(f"Перезапустить Hysteria2 ({hy2})?", default=True):
            do_step("Hysteria2 restart", SUDO + ["systemctl", "restart", hy2])

    # --- 7. Headscale (+Headplane) --- POST_DEPLOY.md §4/§4a
    if docker_running("headscale"):
        if Confirm.ask("Поднять/обновить Headscale (compose up -d)?", default=True):
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
            if Confirm.ask("Обновить Headplane Web UI?", default=True):
                do_step("Headplane", ["bash", "scripts/install_headplane.sh"])

    # --- 8. HA-стек + Reticulum --- POST_DEPLOY.md §12
    if unit_exists("ha-reticulum-bridge") or unit_exists("ha-stub-grpc"):
        if Confirm.ask("Обновить HA-стек (перезапустить сервисы)?", default=True):
            do_step(
                "HA-стек restart",
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
            "Переустановить HA-стек начисто (venv+юниты заново)?", default=False
        ):
            do_step("HA-стек reinstall", ["bash", "scripts/install_ha_stack.sh"])

    # --- 9. Чистка диска --- CLEANUP_SERVER.md
    # Каждый `compose up --build` докладывает слой в docker build cache (на
    # your-vps он набрал 1.9 GB за двое суток), поэтому чистку предлагаем
    # прямо здесь, сразу после пересборок, а не когда диск упрётся в 100%.
    console.rule("Чистка диска")
    before = cap(["df", "-h", "/"]).strip()
    if before:
        console.print(before)
    if Confirm.ask("Почистить диск (docker prune + journald + apt)?", default=True):
        do_step("Чистка диска", SUDO + ["bash", "scripts/vps_maintenance.sh", "--run"])
        after = cap(["df", "-h", "/"]).strip()
        if after:
            console.print(after)

    if unit_exists("telegramhelper-maintenance.timer"):
        console.print(
            "[green]✓ Авто-чистка включена (еженедельно, вс 04:00 UTC).[/green]\n"
            "  Что съело диск: [bold]bash scripts/vps_maintenance.sh --report[/bold]"
        )
    elif Confirm.ask(
        "Авто-чистка не настроена. Включить еженедельный таймер?", default=True
    ):
        do_step(
            "Авто-чистка (таймер)",
            SUDO + ["bash", "scripts/vps_maintenance.sh", "--install"],
        )

    # --- итог ---
    console.rule("[bold]Итог")
    if not failed:
        console.print(
            Panel(
                "[green]✅ Обновление завершено.[/green]\n"
                "Сложные сценарии (host-network mesh-only, Gmail-токены) — POST_DEPLOY.md.",
                border_style="green",
            )
        )
        return 0
    report = Table(box=None)
    report.add_column("Шаг")
    report.add_column("Статус")
    for name in failed:
        report.add_row(name, "[red]FAILED[/red]")
    console.print(report)
    console.print(
        Panel(
            f"Ошибки в: [red]{', '.join(failed)}[/red] — см. вывод / journalctl.",
            border_style="red",
        )
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Прервано (Ctrl+C).[/yellow]")
        sys.exit(130)
