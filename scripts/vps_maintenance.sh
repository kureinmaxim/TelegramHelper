#!/usr/bin/env bash
# vps_maintenance.sh — авто-очистка диска на VPS, чтобы не упирался в 100%.
#
# Главные источники роста диска для TelegramHelper:
#   1. Docker builder cache  (растёт при каждом `docker compose build`;
#      на your-vps набрал 1.9 GB за двое суток — это самый быстрый источник)
#   2. Неиспользуемые Docker images  (после rebuild старые висят)
#   3. systemd journal       (без лимита — может занять гигабайты)
#
# Контейнерные логи UЖE ограничены через `logging.options.max-size`
# в compose.yaml, их трогать не нужно.
#
# Использование:
#   sudo ./vps_maintenance.sh              # разовый прогон чистки
#   sudo ./vps_maintenance.sh --install    # настроить авто-чистку
#                                          #  • systemd-timer раз в неделю
#                                          #  • journald cap 500 MB
#                                          # (выполнить ОДИН РАЗ на сервере)
#   sudo ./vps_maintenance.sh --uninstall  # снять авто-чистку
#   sudo ./vps_maintenance.sh --status     # показать текущее состояние
#   sudo ./vps_maintenance.sh --report     # полная диагностика диска (read-only)
#   sudo ./vps_maintenance.sh --with-rust  # + кэши crates.io и rust-docs
#                                          # (комбинируется: `--run --with-rust`)
#
# Безопасность:
#   • Не трогает запущенные контейнеры (telegram-helper-lite, dockhand,
#     headscale, xray и т.п.) — `prune` удаляет только неиспользуемое.
#   • Не удаляет volumes (там данные приложения).
#   • Не удаляет dev-данные (/root, /home), swap-файлы и Rust `target/` —
#     про них только ПРЕДУПРЕЖДАЕТ. Там лежат рабочие бинарники: сервисы
#     host-side binaries live outside this repo.

set -euo pipefail

UNIT_NAME="telegramhelper-maintenance"
SERVICE_FILE="/etc/systemd/system/${UNIT_NAME}.service"
TIMER_FILE="/etc/systemd/system/${UNIT_NAME}.timer"
SCRIPT_PATH="/usr/local/sbin/${UNIT_NAME}.sh"
JOURNAL_CAP="500M"
WITH_RUST=0   # включается флагом --with-rust

# ── runtime mode ────────────────────────────────────────────────────────────
require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Нужны root-привилегии. Запусти через sudo." >&2
    exit 1
  fi
}

show_disk() {
  echo "── Диск ────────────────────────────────────────────"
  df -h /
  if command -v docker >/dev/null 2>&1; then
    echo "── Docker ──────────────────────────────────────────"
    docker system df || true
  fi
  echo "── Journald ────────────────────────────────────────"
  journalctl --disk-usage 2>/dev/null || true
}

# ── Предупреждения о том, что скрипт НЕ трогает сам ──────────────────────────
# Это данные владельца и ручные артефакты: удалять их автоматически нельзя,
# но и молчать о них неправильно — обычно именно они и забивают диск.
report_dev_data() {
  local found=0
  local header="── Внимание: авто-чистка это НЕ трогает ────────────"

  # 1. swap-файлы, включённые руками (нет в /etc/fstab). Обычно это временный
  #    build-swap под тяжёлую сборку (Rust/LLVM), про который потом забывают:
  #    на your-vps такой /swapfile-build занимал 4 GB при 0 B использования.
  local sw
  while read -r sw; do
    [[ -z "${sw}" || "${sw}" != /* || ! -f "${sw}" ]] && continue
    if ! grep -qE "^[[:space:]]*${sw}[[:space:]]" /etc/fstab 2>/dev/null; then
      [[ ${found} -eq 0 ]] && echo "${header}" && found=1
      echo "  ⚠ swap-файл ${sw} ($(du -h "${sw}" 2>/dev/null | cut -f1)) активен,"
      echo "    но его НЕТ в /etc/fstab → включён вручную под разовую сборку."
      echo "    Если сборок не планируется:  swapoff ${sw} && rm -f ${sw}"
    fi
  done < <(swapon --show=NAME --noheadings 2>/dev/null)

  # 2. Тяжёлые (>=1 GB) dev-каталоги и тулчейны в /root и /home.
  local d size path
  for d in /root /home/*; do
    [[ -d "${d}" ]] || continue
    while read -r size path; do
      [[ "${path}" == "${d}" ]] && continue
      [[ ${found} -eq 0 ]] && echo "${header}" && found=1
      printf '  • %-6s %s\n' "${size}" "${path}"
    done < <(du -hxd1 "${d}" 2>/dev/null | awk '$1 ~ /[0-9]G$/')
  done

  if [[ ${found} -eq 1 ]]; then
    echo "  Кэши crates.io и rust-docs чистятся флагом --with-rust."
    echo "  Проекты, target/ и медиа — только вручную: CLEANUP_SERVER.md §Dev-данные."
  fi
}

# ── Rust-кэши (только по флагу --with-rust) ──────────────────────────────────
# Чистим ТОЛЬКО перекачиваемое: реестр crates.io, исходники зависимостей и
# checkout'ы git-крейтов. `target/` и сами тулчейны не трогаем — в target/
# лежат рабочие бинарники (systemd-юниты стартуют прямо оттуда), а из
# rustup убираем лишь rust-docs, которые на сервере не нужны (~0.9 GB).
clean_rust_caches() {
  local home_dir
  for home_dir in /root /home/*; do
    [[ -d "${home_dir}/.cargo" ]] || continue
    echo "[clean] ${home_dir}/.cargo: registry/cache, registry/src, git/checkouts"
    rm -rf "${home_dir}/.cargo/registry/cache" \
           "${home_dir}/.cargo/registry/src" \
           "${home_dir}/.cargo/git/checkouts" 2>/dev/null || true
  done
  if command -v rustup >/dev/null 2>&1; then
    echo "[clean] rustup component remove rust-docs"
    rustup component remove rust-docs >/dev/null 2>&1 || true
  fi
}

# ── Полная диагностика диска (read-only) ─────────────────────────────────────
# Отдельный режим, потому что два факта регулярно уводят диагностику не туда:
#   • `du -hxd1 /` НЕ показывает файлы, лежащие прямо в корне (swap-файлы!),
#     из-за чего сумма каталогов не сходится с `df`;
#   • при Docker с containerd-store каталога overlay2 просто нет — это норма,
#     а не «утечка слоёв», и процедура save→reset→load тут не нужна.
disk_report() {
  echo "══ ДИАГНОСТИКА ДИСКА ═══════════════════════════════"
  df -h /
  echo
  echo "── RAM / SWAP ──"
  free -h
  swapon --show 2>/dev/null || echo "swap не активен"
  echo
  echo "── Крупные файлы прямо в корне (du их не покажет) ──"
  find / -maxdepth 1 -xdev -type f -size +100M -exec ls -lh {} \; 2>/dev/null \
    | awk '{printf "  %-6s %s\n", $5, $9}'
  echo
  echo "── Каталоги / ──"
  du -hxd1 / 2>/dev/null | sort -h | tail -12
  echo
  if command -v docker >/dev/null 2>&1; then
    echo "── Docker ──"
    docker system df || true
    if [[ -d /var/lib/docker/overlay2 ]]; then
      echo "overlay2: $(du -sh /var/lib/docker/overlay2 2>/dev/null | cut -f1), слоёв: $(ls /var/lib/docker/overlay2 2>/dev/null | wc -l)"
      echo "  (если размер сильно больше суммы образов, а prune реклеймит 0 —"
      echo "   это утечка слоёв, см. CLEANUP_SERVER.md §Docker overlay2 leak)"
    elif [[ -d /var/lib/containerd ]]; then
      echo "overlay2: каталога нет → образы в containerd-store"
      echo "  ($(du -sh /var/lib/containerd 2>/dev/null | cut -f1) в /var/lib/containerd)."
      echo "  Это НОРМА для Docker 28+ со snapshotter'ом, а не утечка слоёв."
    else
      echo "overlay2: данных Docker на диске не найдено (демон не запущен?)."
    fi
    echo
  fi
  echo "── Journald ──"
  journalctl --disk-usage 2>/dev/null || true
  echo
  report_dev_data
}

run_cleanup() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] === VPS maintenance start ==="

  if command -v docker >/dev/null 2>&1; then
    echo "[clean] docker builder prune -af"
    docker builder prune -af || true
    echo "[clean] docker image prune -af"
    docker image prune -af || true
    echo "[clean] docker container prune -f"
    docker container prune -f || true
    # Volumes НЕ трогаем — там данные.
  else
    echo "[skip] docker не установлен — пропуск Docker-чистки"
  fi

  echo "[clean] journalctl --vacuum-size=${JOURNAL_CAP}"
  journalctl --vacuum-size="${JOURNAL_CAP}" || true

  echo "[clean] apt-get clean"
  apt-get clean || true

  echo "[clean] /var/lib/apt/lists/partial/*"
  rm -rf /var/lib/apt/lists/partial/* 2>/dev/null || true

  echo "[clean] старые ротированные логи в /var/log (*.gz/*.xz/*.log.N > 7d)"
  find /var/log -type f \( -name "*.gz" -o -name "*.xz" -o -name "*.log.*" \) \
    -mtime +7 -delete 2>/dev/null || true

  if [[ "${WITH_RUST}" -eq 1 ]]; then
    clean_rust_caches
  fi

  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] === VPS maintenance done ==="
  echo
  show_disk
  echo
  report_dev_data
}

install_autoclean() {
  require_root

  echo "── Установка systemd-таймера авто-чистки ──────────"

  # 1. Кладём сам скрипт в /usr/local/sbin
  install -m 0755 "$0" "${SCRIPT_PATH}"
  echo "✓ скрипт скопирован: ${SCRIPT_PATH}"

  # 2. systemd service (oneshot)
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=TelegramHelper VPS maintenance (docker prune + journald vacuum)
After=docker.service

[Service]
Type=oneshot
ExecStart=${SCRIPT_PATH}
EOF
  echo "✓ service: ${SERVICE_FILE}"

  # 3. systemd timer — раз в неделю в 04:00 по воскресеньям
  cat > "${TIMER_FILE}" <<EOF
[Unit]
Description=Weekly TelegramHelper VPS maintenance

[Timer]
OnCalendar=Sun 04:00:00
Persistent=true
RandomizedDelaySec=15m
Unit=${UNIT_NAME}.service

[Install]
WantedBy=timers.target
EOF
  echo "✓ timer: ${TIMER_FILE}"

  # 4. Лимит journald (если ещё не выставлен)
  if ! grep -qE "^SystemMaxUse=" /etc/systemd/journald.conf; then
    sed -i 's/^#SystemMaxUse=.*/SystemMaxUse=500M/' /etc/systemd/journald.conf
    if ! grep -qE "^SystemMaxUse=" /etc/systemd/journald.conf; then
      echo "SystemMaxUse=500M" >> /etc/systemd/journald.conf
    fi
    systemctl restart systemd-journald
    echo "✓ journald: SystemMaxUse=500M (рестарт сервиса)"
  else
    echo "✓ journald: SystemMaxUse уже задан"
  fi

  systemctl daemon-reload
  systemctl enable --now "${UNIT_NAME}.timer"

  echo
  echo "✅ Готово. Чистка будет запускаться каждое воскресенье в 04:00 UTC."
  echo "Ручной прогон:        sudo systemctl start ${UNIT_NAME}.service"
  echo "Логи последнего:      sudo journalctl -u ${UNIT_NAME}.service -n 100"
  echo "Расписание:           sudo systemctl list-timers ${UNIT_NAME}.timer"
}

uninstall_autoclean() {
  require_root

  echo "── Удаление авто-чистки ──"
  systemctl disable --now "${UNIT_NAME}.timer" 2>/dev/null || true
  systemctl disable --now "${UNIT_NAME}.service" 2>/dev/null || true
  rm -f "${TIMER_FILE}" "${SERVICE_FILE}" "${SCRIPT_PATH}"
  systemctl daemon-reload
  echo "✓ удалено: ${TIMER_FILE}, ${SERVICE_FILE}, ${SCRIPT_PATH}"
  echo "Лимит journald НЕ снимается — поправь /etc/systemd/journald.conf вручную, если нужно."
}

show_status() {
  echo "── Статус авто-чистки ──"
  if systemctl is-enabled "${UNIT_NAME}.timer" >/dev/null 2>&1; then
    systemctl status "${UNIT_NAME}.timer" --no-pager || true
    echo
    systemctl list-timers "${UNIT_NAME}.timer" --no-pager || true
  else
    echo "Авто-чистка НЕ установлена. Запусти: sudo $0 --install"
  fi
  echo
  show_disk
  echo
  echo "Полная диагностика диска: sudo $0 --report"
}

# ── вход ─────────────────────────────────────────────────────────────────────
# --with-rust — модификатор, а не режим: вычитаем его из аргументов, первый
# оставшийся аргумент считаем режимом.
MODE=""
for arg in "$@"; do
  case "${arg}" in
    --with-rust) WITH_RUST=1 ;;
    *) [[ -z "${MODE}" ]] && MODE="${arg}" ;;
  esac
done

case "${MODE}" in
  --install)   install_autoclean ;;
  --uninstall) uninstall_autoclean ;;
  --status)    show_status ;;
  --report)    disk_report ;;
  ""|--run)    require_root; run_cleanup ;;
  -h|--help)
    sed -n '2,30p' "$0"
    ;;
  *)
    echo "Неизвестный режим: ${MODE}" >&2
    echo "Используй --install, --uninstall, --status, --report, --run" >&2
    echo "(любой режим можно дополнить флагом --with-rust) или --help." >&2
    exit 2
    ;;
esac
