#!/usr/bin/env python3
"""vps_setup.py — интерактивный установщик компонентов TelegramHelper (Python + rich).

Запускать через `bash scripts/vps_setup.sh` (он доставит rich/prompt_toolkit на
чистом VPS и вызовет этот скрипт). Оркестрирует существующие scripts/install_*.sh.

Базовый набор (ставится ВСЕГДА): Docker, Hysteria2.
Опционально: API (Telegram-бот), VLESS-Reality, MTProto, NaiveProxy,
Headscale(+Headplane), HA-сервер + Reticulum (заглушки).

  bash scripts/vps_setup.sh            # обычный режим
  bash scripts/vps_setup.sh --dry-run  # показать план без выполнения
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
    """Выполнить команду (list или str). Возвращает True при успехе. Учитывает --dry-run."""
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
    """Подождать, пока сервис станет active (бот поллит — стартует не мгновенно)."""
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
    # Истина успеха — не код выхода бутстрапа (он может ругаться из-за
    # предупреждения о плейсхолдерах .env), а реальная живость сервиса:
    # check_fn — произвольная проверка (напр. Docker-контейнер), check_unit — systemd.
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
        else f"[red]✗ {title}[/red] — см. вывод выше / journalctl"
    )
    return ok


def docker_service_running(service):
    """True если сервис compose в состоянии running."""
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
    table = Table(title="Уже установлено на этом VPS", show_header=False, box=None)
    checks = [
        ("Docker", shutil.which("docker") is not None),
        ("Telegram-бот (systemd, telegramhelper)", unit_active("telegramhelper")),
        (
            "Telegram-бот (docker, telegram-helper)",
            docker_service_running("telegram-helper"),
        ),
        ("Hysteria2", unit_active("hysteria-server")),
        ("MTProto", unit_active("mtproto-proxy")),
        ("Xray/VLESS", shutil.which("xray") is not None),
        ("HA-стек (reticulum-bridge)", unit_active("ha-reticulum-bridge")),
        ("I2P-слой (i2pd, путь 2)", unit_active("i2pd")),
    ]
    for name, present in checks:
        table.add_row(
            "●" if present else "○",
            f"[green]{name}[/green]" if present else f"[dim]{name}[/dim]",
        )
    console.print(table)


def headscale_coordinator(server_url):
    """Сервер Headscale: config (server_url/listen_addr) + ТОЛЬКО сервис headscale
    (не весь compose → нет конфликта 127.0.0.1:8000 с ботом) + Headplane."""
    console.rule("[bold]Headscale (координатор)")
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
    results["Headscale (координатор)"] = (
        "dry-run" if DRY else ("ok" if ok else "FAILED")
    )
    if not DRY:
        console.print("[green]✓ Headscale[/green]" if ok else "[red]✗ Headscale[/red]")
    if ok or DRY:
        step("Headplane Web UI", ["bash", "scripts/install_headplane.sh"])
    console.print(
        "[dim]Внешним клиентам координатор доступен по публичному HTTPS "
        "(reverse-proxy/домен); compose биндит 127.0.0.1:8080.[/dim]"
    )


def tailscale_client(login_server, authkey):
    """Поставить tailscale и подключить ноду к чужому координатору Headscale."""
    console.rule("[bold]Tailscale-нода (клиент)")
    if shutil.which("tailscale") is None:
        sh("curl -fsSL https://tailscale.com/install.sh | sh", shell=True)
    cmd = SUDO + ["tailscale", "up", "--login-server", login_server]
    if authkey:
        cmd += ["--authkey", authkey]
    ok = sh(cmd)
    results["Tailscale-нода (клиент)"] = (
        "dry-run" if DRY else ("ok" if ok else "FAILED")
    )
    if not DRY:
        console.print(
            "[green]✓ Tailscale-нода подключена[/green]"
            if ok
            else "[red]✗ tailscale up не прошёл — проверь URL/authkey[/red]"
        )


def main():
    global DRY
    parser = argparse.ArgumentParser(description="TelegramHelper VPS installer (rich)")
    parser.add_argument(
        "--dry-run", action="store_true", help="показать план без выполнения"
    )
    DRY = parser.parse_args().dry_run

    console.print(
        Panel.fit(
            "[bold cyan]TelegramHelper — установщик VPS[/bold cyan]\n"
            f"репозиторий: {REPO_ROOT}",
            border_style="cyan",
        )
    )
    if DRY:
        console.print(
            "[yellow]РЕЖИМ --dry-run: ничего не меняется, только показ команд.[/yellow]"
        )
    detected_state()

    console.print(
        Panel(
            "[bold]Базовый набор устанавливается ВСЕГДА:[/bold]\n"
            "  • [green]Docker[/green] (нужен боту/Headscale)\n"
            "  • [green]Hysteria2[/green] (UDP VPN-транспорт)",
            title="Обязательно",
            border_style="green",
        )
    )

    # --- выбор опциональных (с возможностью вернуться и переответить) ---
    # Все ответы храним в want/params/hs_role и переспрашиваем в цикле: если в
    # конце не подтвердить — прошлые ответы становятся значениями по умолчанию,
    # так что правишь только ошибочный пункт, остальное проматываешь Enter.
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
        console.rule("Выбор опциональных компонентов")
        console.print(
            "[dim]Enter — прошлый ответ / умолчание. В конце можно вернуться "
            "и переответить.[/dim]"
        )
        want["api"] = Confirm.ask(
            "API — Telegram-бот (будет [b]запущен[/b], спросит BOT_TOKEN / ADMIN_USER_IDS)?",
            default=want["api"],
        )
        if want["api"]:
            console.print(
                "[dim]systemd — рекомендуется, если на этом VPS будут ещё Docker-сервисы "
                "(Headscale/Dockhand/HA-стек) — меньше конфликтов по портам/ресурсам.\n"
                "docker — проще, если бот — единственный сервис на VPS.\n"
                "Перед сборкой установщик настроит .env с подсказками: BOT_TOKEN, "
                "ADMIN_USER_IDS; API_SECRET_KEY и HMAC_SECRET сгенерирует сам.\n"
                "Если поля уже заполнены в .env — повторно не спросит.[/dim]"
            )
            want["api_mode"] = Prompt.ask(
                "  Как ставить бота",
                choices=["systemd", "docker"],
                default=want.get("api_mode", "systemd"),
            )
        want["vless"] = Confirm.ask("VLESS-Reality?", default=want["vless"])
        want["naive"] = Confirm.ask(
            "NaiveProxy (нужен домен + DNS)?", default=want["naive"]
        )
        want["mtproto"] = Confirm.ask("MTProto?", default=want["mtproto"])
        want["headscale"] = Confirm.ask(
            "Headscale / Tailscale-нода?", default=want["headscale"]
        )
        if want["headscale"]:
            hs_role = Prompt.ask(
                "  Роль этого VPS в mesh",
                choices=["coordinator", "client"],
                default=hs_role,
            )
            # Параметры роли спрашиваем СРАЗУ, чтобы не мешались с другими вопросами.
            if hs_role == "coordinator":
                params["hs_server_url"] = Prompt.ask(
                    "  Headscale server_url (домен координатора; внешним клиентам нужен HTTPS)",
                    default=params.get(
                        "hs_server_url", "https://headscale.example.com"
                    ),
                )
            else:  # client
                params["hs_login"] = Prompt.ask(
                    "  URL координатора Headscale (--login-server), напр. https://headscale.домен:8443",
                    default=params.get("hs_login", ""),
                )
                params["hs_authkey"] = Prompt.ask(
                    "  Pre-auth key координатора", default=params.get("hs_authkey", "")
                )
        want["ha"] = Confirm.ask(
            "HA-сервер + Reticulum (заглушки Mi-Home, для тестов)?", default=want["ha"]
        )
        # I2P-слой (путь 2: нативные туннели i2pd) оборачивает локальный порт моста
        # в скрытый сервис I2P. Имеет смысл только вместе с HA-стеком.
        if want["ha"]:
            want["i2p"] = Confirm.ask(
                "  + I2P-доступ к мосту (i2pd, путь 2 — без публичного порта/SAM)?",
                default=want["i2p"],
            )
            want["ha_adapter"] = Confirm.ask(
                "  + ha-adapter → реальный HA на NAS (tailnet :8123, нужен mesh)?",
                default=want["ha_adapter"],
            )
            if want["ha_adapter"]:
                console.print(
                    "[dim]Нужен Headscale-клиент на этом VPS и Long-Lived Token из HA.\n"
                    "Адаптер :50057, мост переключится на него; опционально "
                    "публичный :50061 для ApiHA без SSH.[/dim]"
                )
                params["ha_url"] = Prompt.ask(
                    "  HA_URL (NAS в mesh)",
                    default=params.get("ha_url", "http://100.64.0.2:8123"),
                )
                params["ha_token"] = Prompt.ask(
                    "  HA_TOKEN (Long-Lived из профиля HA)",
                    default=params.get("ha_token", ""),
                    password=True,
                )
                want["ha_switch_bridge"] = Confirm.ask(
                    "  Переключить мост на adapter :50057?",
                    default=want.get("ha_switch_bridge", True),
                )
                want["ha_public_rns"] = Confirm.ask(
                    "  Открыть мост наружу (0.0.0.0:50061) для ApiHA без SSH?",
                    default=want.get("ha_public_rns", True),
                )
                if not (params.get("ha_token") or "").strip():
                    console.print(
                        "[yellow]Без HA_TOKEN adapter пропущу — "
                        "потом: bash scripts/install_ha_adapter.sh[/yellow]"
                    )
                    want["ha_adapter"] = False
        else:
            want["i2p"] = False
            want["ha_adapter"] = False
        want["dockhand"] = Confirm.ask(
            "Dockhand (Streamlit-диагностика Docker, :8501 localhost)?",
            default=want["dockhand"],
        )

        # --- конфликт 443: VLESS vs NaiveProxy (без выхода) ---
        if want["vless"] and want["naive"]:
            console.print(
                "[yellow]VLESS-Reality и NaiveProxy оба занимают 443/TCP — нужен один владелец.[/yellow]"
            )
            owner = Prompt.ask(
                "Кто владеет 443/TCP?", choices=["vless", "naiveproxy"], default="vless"
            )
            want["vless"] = owner == "vless"
            want["naive"] = owner == "naiveproxy"

        # --- параметры ---
        params["hy2_port"] = Prompt.ask(
            "Hysteria2 порт (UDP)", default=params.get("hy2_port", "443")
        )
        if want["mtproto"]:
            params["mtp_port"] = Prompt.ask(
                "MTProto порт", default=params.get("mtp_port", "993")
            )
            params["mtp_domain"] = Prompt.ask(
                "MTProto fake-TLS домен", default=params.get("mtp_domain", "google.com")
            )
        if want["naive"]:
            params["naive_domain"] = Prompt.ask(
                "NaiveProxy домен (обязателен)", default=params.get("naive_domain", "")
            )
            params["naive_port"] = Prompt.ask(
                "NaiveProxy HTTPS порт", default=params.get("naive_port", "443")
            )
            if not params["naive_domain"].strip():
                console.print("[red]NaiveProxy без домена — пропускаю.[/red]")
                want["naive"] = False

        # --- пре-флайт сводка ---
        plan = Table(title="Будет выполнено", box=None)
        plan.add_column("Компонент")
        plan.add_column("Действие")
        plan.add_row("Docker", "поставить, если нет (всегда)")
        plan.add_row(
            "Hysteria2", f"установить на порт {params['hy2_port']}/UDP (всегда)"
        )
        if want["api"]:
            plan.add_row(
                "Telegram-бот (API)",
                f"установить и запустить через {want['api_mode']} (спросит токен)",
            )
        if want["vless"]:
            plan.add_row("VLESS-Reality", "установить (из /opt/TelegramHelper)")
        if want["mtproto"]:
            plan.add_row(
                "MTProto", f"порт {params['mtp_port']}, домен {params['mtp_domain']}"
            )
        if want["naive"]:
            plan.add_row(
                "NaiveProxy",
                f"домен {params['naive_domain']}, порт {params['naive_port']}",
            )
        if want["headscale"] and hs_role == "coordinator":
            plan.add_row(
                "Headscale (координатор)",
                f"сервер headscale + Headplane, server_url {params['hs_server_url']}",
            )
        elif want["headscale"]:
            plan.add_row(
                "Tailscale-нода (клиент)", f"подключить к {params['hs_login']}"
            )
        if want["ha"]:
            plan.add_row("HA + Reticulum", "stub-сервер + мост (127.0.0.1)")
        if want.get("ha_adapter"):
            plan.add_row(
                "HA adapter",
                f"{params.get('ha_url')} → :50057"
                + ("; мост→adapter" if want.get("ha_switch_bridge") else "")
                + ("; public :50061" if want.get("ha_public_rns") else ""),
            )
        if want["i2p"]:
            plan.add_row("I2P (путь 2)", "i2pd + server-туннель ha-bridge → мост")
        if want["dockhand"]:
            plan.add_row("Dockhand", "docker compose up -d dockhand (:8501 localhost)")
        console.print(plan)

        if Confirm.ask("[bold]Подтвердить и начать установку?[/bold]", default=True):
            break
        if not Confirm.ask(
            "Вернуться и переответить (прошлые ответы — по умолчанию)?", default=True
        ):
            console.print("[yellow]Отменено.[/yellow]")
            return 0
        # иначе — повтор цикла с текущими ответами как умолчаниями

    # --- установка в порядке зависимостей ---
    # 1) Docker (всегда)
    if shutil.which("docker") is None:
        step(
            "Docker (get.docker.com)",
            "curl -fsSL https://get.docker.com | sh",
            shell=True,
        )
    else:
        console.print("[dim]Docker уже установлен — пропускаю.[/dim]")
        results["Docker"] = "ok"

    # 2) Hysteria2 (всегда). check_unit — судим по реальной живости сервиса,
    # а не по коду выхода установщика (Type=simple может крашнуться после старта).
    step(
        "Hysteria2",
        SUDO + ["bash", "scripts/install_hysteria2.sh", "--port", params["hy2_port"]],
        check_unit="hysteria-server",
    )
    if not DRY and results.get("Hysteria2") == "FAILED":
        console.print(
            "[yellow]Hysteria2 установлен, но сервис не active — "
            "после установки глянь: journalctl -u hysteria-server -n 30[/yellow]"
        )

    # 3) API/бот (если выбран) — установить и проверить живость. Способ — выбор выше
    # (want["api_mode"]): systemd (install_telegramhelper_vps.sh) или docker
    # (install_telegramhelper_docker.sh, сервис telegram-helper из compose.yaml).
    if want["api"] and want["api_mode"] == "docker":
        step(
            "Telegram-бот (API, docker)",
            SUDO + ["bash", "scripts/install_telegramhelper_docker.sh"],
            check_fn=lambda: docker_service_running("telegram-helper"),
        )
        if not DRY and results.get("Telegram-бот (API, docker)") == "FAILED":
            console.print(
                "[yellow]Контейнер не running — пробую force-recreate...[/yellow]"
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
                results["Telegram-бот (API, docker)"] = "ok"
                console.print(
                    "[green]✓ Telegram-бот (API, docker) — поднялся после force-recreate[/green]"
                )
            else:
                console.print(
                    "[red]API выбран, но контейнер telegram-helper не running — проверь "
                    "docker compose logs telegram-helper и BOT_TOKEN/ADMIN_USER_IDS в .env.[/red]"
                )
    elif want["api"]:
        step(
            "Telegram-бот (API)",
            SUDO + ["bash", "scripts/install_telegramhelper_vps.sh"],
            check_unit="telegramhelper",
        )
        if not DRY and results.get("Telegram-бот (API)") == "FAILED":
            # API выбран → бот ОБЯЗАН работать: пробуем поднять и перепроверить
            console.print("[yellow]Бот не active — пробую перезапустить...[/yellow]")
            sh(SUDO + ["systemctl", "restart", "telegramhelper"])
            if wait_active("telegramhelper"):
                results["Telegram-бот (API)"] = "ok"
                console.print(
                    "[green]✓ Telegram-бот (API) — поднялся после restart[/green]"
                )
            else:
                console.print(
                    "[red]API выбран, но бот не active — проверь "
                    "journalctl -u telegramhelper и BOT_TOKEN/ADMIN_USER_IDS в .env.[/red]"
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

    # 7) Headscale координатор ИЛИ Tailscale-клиент (Docker уже есть)
    if want["headscale"] and hs_role == "coordinator":
        headscale_coordinator(params["hs_server_url"])
    elif want["headscale"]:
        tailscale_client(params["hs_login"], params.get("hs_authkey", ""))

    # 8) HA + Reticulum
    if want["ha"]:
        step("HA + Reticulum", ["bash", "scripts/install_ha_stack.sh"])

    # 8a) Реальный HA через tailnet (после stubs + желательно после Headscale client)
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

    # 8b) I2P-слой (путь 2) — i2pd + server-туннель поверх уже поднятого моста.
    #     check_unit=i2pd: судим по живости демона (b32 строится дольше, асинхронно).
    if want["i2p"]:
        step(
            "I2P (путь 2)", ["bash", "scripts/install_i2p_bridge.sh"], check_unit="i2pd"
        )

    # 9) Dockhand (Streamlit-диагностика) — слушает 127.0.0.1:8501 (через SSH-туннель).
    #    --no-deps: НЕ тянуть telegram-helper (бот стоит как systemd и держит :8000 →
    #    иначе контейнер-бот конфликтует). docker-socket-proxy поднимаем явно — он
    #    нужен dockhand для доступа к Docker.
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

    # --- финальный отчёт ---
    console.rule("[bold]Итог")
    report = Table(box=None)
    report.add_column("Компонент")
    report.add_column("Статус")
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
                f"Ошибки в: [red]{', '.join(failed)}[/red]\n"
                "Проверь journalctl -u <сервис>. Детали — DEPLOY.md.",
                border_style="red",
            )
        )
        return 1
    done_msg = (
        "[green]Готово. Все выбранные компоненты установлены.[/green]\n"
        "HA-стек: hash моста — journalctl -u ha-reticulum-bridge | grep destination.\n"
        "Тесты Reticulum — RETICULUM_TESTING.md (репо ApiRgRPC)."
    )
    if want["i2p"]:
        done_msg += (
            "\nI2P (путь 2): b32 моста — curl -s http://127.0.0.1:7070/?page=i2p_tunnels "
            "| sed 's/<[^>]*>/ /g' | grep -iE 'ha-bridge|\\.b32'. Детали — I2P_GUIDE.md."
        )
    console.print(Panel(done_msg, border_style="green"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Прервано (Ctrl+C). Ничего не установлено — запусти заново.[/yellow]"
        )
        sys.exit(130)
