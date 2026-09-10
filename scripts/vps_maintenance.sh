#!/usr/bin/env bash
# vps_maintenance.sh — automatic disk cleanup on the VPS so it does not hit 100%.
#
# Main disk-growth sources for TelegramHelper:
#   1. Docker builder cache  (grows on every `docker compose build`;
#      on your-vps it reached 1.9 GB in two days — the fastest source)
#   2. Unused Docker images  (old ones linger after rebuild)
#   3. systemd journal       (uncapped — can eat gigabytes)
#
# Container logs are ALREADY capped via `logging.options.max-size`
# in compose.yaml; do not touch them.
#
# Usage:
#   sudo ./vps_maintenance.sh              # one-shot cleanup
#   sudo ./vps_maintenance.sh --install    # set up auto-cleanup
#                                          #  • weekly systemd timer
#                                          #  • journald cap 500 MB
#                                          # (run ONCE on the server)
#   sudo ./vps_maintenance.sh --uninstall  # remove auto-cleanup
#   sudo ./vps_maintenance.sh --status     # show current state
#   sudo ./vps_maintenance.sh --report     # full disk diagnostics (read-only)
#   sudo ./vps_maintenance.sh --with-rust  # + crates.io and rust-docs caches
#                                          # (combine as: `--run --with-rust`)
#
# Safety:
#   • Does not touch running containers (telegram-helper-lite, dockhand,
#     headscale, xray, etc.) — `prune` removes unused objects only.
#   • Does not delete volumes (app data lives there).
#   • Does not delete dev data (/root, /home), swap files, or Rust `target/` —
#     it only WARNS about them. Working binaries live there: services
#     host-side binaries live outside this repo.

set -euo pipefail

UNIT_NAME="telegramhelper-maintenance"
SERVICE_FILE="/etc/systemd/system/${UNIT_NAME}.service"
TIMER_FILE="/etc/systemd/system/${UNIT_NAME}.timer"
SCRIPT_PATH="/usr/local/sbin/${UNIT_NAME}.sh"
JOURNAL_CAP="500M"
WITH_RUST=0   # enabled by --with-rust

# ── runtime mode ────────────────────────────────────────────────────────────
require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "Root is required. Run via sudo." >&2
    exit 1
  fi
}

show_disk() {
  echo "── Disk ────────────────────────────────────────────"
  df -h /
  if command -v docker >/dev/null 2>&1; then
    echo "── Docker ──────────────────────────────────────────"
    docker system df || true
  fi
  echo "── Journald ────────────────────────────────────────"
  journalctl --disk-usage 2>/dev/null || true
}

# ── Warnings about things this script does NOT touch ─────────────────────────
# Owner data and hand-made artefacts: must not delete them automatically,
# but staying silent is also wrong — they are usually what fills the disk.
report_dev_data() {
  local found=0
  local header="── Note: auto-cleanup does NOT touch these ─────────"

  # 1. Swap files enabled by hand (not in /etc/fstab). Usually a temporary
  #    build-swap for a heavy compile (Rust/LLVM) that then gets forgotten:
  #    on your-vps such a /swapfile-build used 4 GB with 0 B in use.
  local sw
  while read -r sw; do
    [[ -z "${sw}" || "${sw}" != /* || ! -f "${sw}" ]] && continue
    if ! grep -qE "^[[:space:]]*${sw}[[:space:]]" /etc/fstab 2>/dev/null; then
      [[ ${found} -eq 0 ]] && echo "${header}" && found=1
      echo "  ⚠ swap file ${sw} ($(du -h "${sw}" 2>/dev/null | cut -f1)) is active,"
      echo "    but it is NOT in /etc/fstab → enabled by hand for a one-off build."
      echo "    If no builds are planned:  swapoff ${sw} && rm -f ${sw}"
    fi
  done < <(swapon --show=NAME --noheadings 2>/dev/null)

  # 2. Heavy (>=1 GB) dev directories and toolchains in /root and /home.
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
    echo "  crates.io and rust-docs caches are cleaned with --with-rust."
    echo "  Projects, target/, and media — by hand only: CLEANUP_SERVER.md §Dev data."
  fi
}

# ── Rust caches (only with --with-rust) ──────────────────────────────────────
# Clean ONLY re-downloadable data: crates.io registry, dependency sources, and
# git-crate checkouts. Leave `target/` and the toolchains alone — target/
# holds working binaries (systemd units start from there), and from rustup
# we only drop rust-docs, which are unused on the server (~0.9 GB).
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

# ── Full disk diagnostics (read-only) ────────────────────────────────────────
# Separate mode because two facts regularly send diagnosis the wrong way:
#   • `du -hxd1 /` does NOT show files sitting in the root itself (swap files!),
#     so the directory sum does not match `df`;
#   • with Docker's containerd-store there is simply no overlay2 dir — that is
#     normal, not a "layer leak", and save→reset→load is not needed.
disk_report() {
  echo "══ DISK DIAGNOSTICS ════════════════════════════════"
  df -h /
  echo
  echo "── RAM / SWAP ──"
  free -h
  swapon --show 2>/dev/null || echo "swap is not active"
  echo
  echo "── Large files in the root itself (du will not show them) ──"
  find / -maxdepth 1 -xdev -type f -size +100M -exec ls -lh {} \; 2>/dev/null \
    | awk '{printf "  %-6s %s\n", $5, $9}'
  echo
  echo "── Directories under / ──"
  du -hxd1 / 2>/dev/null | sort -h | tail -12
  echo
  if command -v docker >/dev/null 2>&1; then
    echo "── Docker ──"
    docker system df || true
    if [[ -d /var/lib/docker/overlay2 ]]; then
      echo "overlay2: $(du -sh /var/lib/docker/overlay2 2>/dev/null | cut -f1), layers: $(ls /var/lib/docker/overlay2 2>/dev/null | wc -l)"
      echo "  (if the size is much larger than the image sum and prune reclaims 0 —"
      echo "   this is a layer leak, see CLEANUP_SERVER.md §Docker overlay2 leak)"
    elif [[ -d /var/lib/containerd ]]; then
      echo "overlay2: no directory → images live in containerd-store"
      echo "  ($(du -sh /var/lib/containerd 2>/dev/null | cut -f1) in /var/lib/containerd)."
      echo "  This is NORMAL for Docker 28+ with a snapshotter, not a layer leak."
    else
      echo "overlay2: no Docker data on disk (daemon not running?)."
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
    # Do NOT touch volumes — data lives there.
  else
    echo "[skip] docker is not installed — skipping Docker cleanup"
  fi

  echo "[clean] journalctl --vacuum-size=${JOURNAL_CAP}"
  journalctl --vacuum-size="${JOURNAL_CAP}" || true

  echo "[clean] apt-get clean"
  apt-get clean || true

  echo "[clean] /var/lib/apt/lists/partial/*"
  rm -rf /var/lib/apt/lists/partial/* 2>/dev/null || true

  echo "[clean] old rotated logs in /var/log (*.gz/*.xz/*.log.N > 7d)"
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

  echo "── Installing the auto-cleanup systemd timer ───────"

  # 1. Copy the script itself to /usr/local/sbin
  install -m 0755 "$0" "${SCRIPT_PATH}"
  echo "✓ script copied: ${SCRIPT_PATH}"

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

  # 3. systemd timer — weekly at 04:00 on Sundays
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

  # 4. journald cap (if not already set)
  if ! grep -qE "^SystemMaxUse=" /etc/systemd/journald.conf; then
    sed -i 's/^#SystemMaxUse=.*/SystemMaxUse=500M/' /etc/systemd/journald.conf
    if ! grep -qE "^SystemMaxUse=" /etc/systemd/journald.conf; then
      echo "SystemMaxUse=500M" >> /etc/systemd/journald.conf
    fi
    systemctl restart systemd-journald
    echo "✓ journald: SystemMaxUse=500M (service restarted)"
  else
    echo "✓ journald: SystemMaxUse already set"
  fi

  systemctl daemon-reload
  systemctl enable --now "${UNIT_NAME}.timer"

  echo
  echo "✅ Done. Cleanup will run every Sunday at 04:00 UTC."
  echo "Manual run:           sudo systemctl start ${UNIT_NAME}.service"
  echo "Last run logs:        sudo journalctl -u ${UNIT_NAME}.service -n 100"
  echo "Schedule:             sudo systemctl list-timers ${UNIT_NAME}.timer"
}

uninstall_autoclean() {
  require_root

  echo "── Removing auto-cleanup ──"
  systemctl disable --now "${UNIT_NAME}.timer" 2>/dev/null || true
  systemctl disable --now "${UNIT_NAME}.service" 2>/dev/null || true
  rm -f "${TIMER_FILE}" "${SERVICE_FILE}" "${SCRIPT_PATH}"
  systemctl daemon-reload
  echo "✓ removed: ${TIMER_FILE}, ${SERVICE_FILE}, ${SCRIPT_PATH}"
  echo "journald cap is NOT removed — edit /etc/systemd/journald.conf by hand if needed."
}

show_status() {
  echo "── Auto-cleanup status ──"
  if systemctl is-enabled "${UNIT_NAME}.timer" >/dev/null 2>&1; then
    systemctl status "${UNIT_NAME}.timer" --no-pager || true
    echo
    systemctl list-timers "${UNIT_NAME}.timer" --no-pager || true
  else
    echo "Auto-cleanup is NOT installed. Run: sudo $0 --install"
  fi
  echo
  show_disk
  echo
  echo "Full disk diagnostics: sudo $0 --report"
}

# ── entry ────────────────────────────────────────────────────────────────────
# --with-rust is a modifier, not a mode: strip it from args, treat the first
# remaining argument as the mode.
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
    echo "Unknown mode: ${MODE}" >&2
    echo "Use --install, --uninstall, --status, --report, --run" >&2
    echo "(any mode can be combined with --with-rust) or --help." >&2
    exit 2
    ;;
esac
