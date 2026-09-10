# -*- coding: utf-8 -*-
"""
Rclone-based offsite backups for TelegramHelper runtime files.

The module intentionally uses `rclone copyto` with a temporary tar.gz archive:
no FUSE, no mounts, no broad filesystem access, and no secret values in output.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple


PROJECT_ROOT = Path(os.getenv("TELEGRAMHELPER_PROJECT_ROOT", "/app")).resolve()
DEFAULT_REMOTE = os.getenv("RCLONE_REMOTE", "").strip()
DEFAULT_PREFIX = os.getenv("RCLONE_BACKUP_PREFIX", "telegramhelper").strip().strip("/")
DEFAULT_TIMEOUT = int(os.getenv("RCLONE_BACKUP_TIMEOUT", "300") or "300")
LOG_MAX_MB = int(os.getenv("RCLONE_BACKUP_LOG_MAX_MB", "20") or "20")

RUNTIME_FILES = [
    ".env",
    "users.json",
    "app_keys.json",
    "vless_config.json",
    "naiveproxy_config.json",
    "hysteria2_config.json",
    "tuic_config.json",
    "anytls_config.json",
    "xhttp_config.json",
    "mtproto_config.json",
    "headscale_config.json",
    "bot.log",
]


@dataclass
class RcloneCommandResult:
    ok: bool
    output: str
    returncode: int = 0


@dataclass
class BackupResult:
    ok: bool
    message: str
    remote_path: str = ""
    files: Tuple[str, ...] = ()


def _env() -> dict:
    env = os.environ.copy()
    config_path = env.get("RCLONE_CONFIG", "").strip()
    if config_path:
        env["RCLONE_CONFIG"] = config_path
    return env


def _run_rclone(args: List[str], timeout: int = DEFAULT_TIMEOUT) -> RcloneCommandResult:
    try:
        completed = subprocess.run(
            ["rclone", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
            check=False,
        )
    except FileNotFoundError:
        return RcloneCommandResult(False, "rclone is not installed in the container", 127)
    except subprocess.TimeoutExpired:
        return RcloneCommandResult(False, f"rclone did not respond within {timeout}s", 124)

    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return RcloneCommandResult(completed.returncode == 0, output, completed.returncode)


def _remote_base(remote: Optional[str] = None) -> str:
    value = (remote or DEFAULT_REMOTE).strip()
    return value.rstrip("/")


def _backup_prefix() -> str:
    return DEFAULT_PREFIX or "telegramhelper"


def _remote_join(*parts: str) -> str:
    remote = _remote_base()
    clean_parts = [part.strip("/") for part in parts if part.strip("/")]
    suffix = "/".join(clean_parts)
    separator = "" if remote.endswith(":") else "/"
    return f"{remote}{separator}{suffix}" if suffix else remote


def _hostname() -> str:
    return socket.gethostname().split(".")[0] or "vps"


def _safe_member_name(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(PROJECT_ROOT)
        return str(rel)
    except ValueError:
        return f"extra/{path.name}"


def _candidate_files() -> List[Path]:
    files = [PROJECT_ROOT / name for name in RUNTIME_FILES]

    extra = os.getenv("RCLONE_BACKUP_EXTRA_PATHS", "").strip()
    if extra:
        for raw in extra.split(","):
            item = raw.strip()
            if item:
                files.append(Path(item).expanduser())

    existing: List[Path] = []
    for path in files:
        if not path.exists() or not path.is_file():
            continue
        if path.name == "bot.log" and path.stat().st_size > LOG_MAX_MB * 1024 * 1024:
            continue
        existing.append(path)
    return existing


def _target_path(timestamp: str) -> str:
    prefix = _backup_prefix()
    filename = f"{timestamp}-{_hostname()}.tar.gz"
    return _remote_join(prefix, _hostname(), filename)


def is_configured() -> bool:
    return bool(_remote_base())


def get_status() -> dict:
    version = _run_rclone(["version"], timeout=10)
    config_path = os.getenv("RCLONE_CONFIG", "").strip() or "rclone default"
    config_exists = Path(config_path).exists() if config_path != "rclone default" else None
    files = _candidate_files()

    return {
        "installed": version.ok,
        "version": version.output.splitlines()[0] if version.output else "not available",
        "remote": _remote_base() or "not configured",
        "prefix": _backup_prefix(),
        "config_path": config_path,
        "config_exists": config_exists,
        "file_count": len(files),
        "files": [path.name for path in files],
    }


def format_status() -> str:
    status = get_status()
    config_note = ""
    if status["config_exists"] is False:
        config_note = "\n⚠️ RCLONE_CONFIG is set, but the file was not found."

    return (
        "🗄️ Rclone backup\n\n"
        f"Installed: {'✅' if status['installed'] else '❌'} {status['version']}\n"
        f"Remote: {status['remote']}\n"
        f"Prefix: {status['prefix']}\n"
        f"Config: {status['config_path']}{config_note}\n"
        f"Files selected: {status['file_count']}\n"
        f"In archive: {', '.join(status['files']) if status['files'] else 'none'}"
    )


def test_remote() -> RcloneCommandResult:
    remote = _remote_base()
    if not remote:
        return RcloneCommandResult(False, "RCLONE_REMOTE is not configured", 2)
    return _run_rclone(["lsd", remote], timeout=30)


def list_backups(limit: int = 20) -> RcloneCommandResult:
    remote = _remote_base()
    if not remote:
        return RcloneCommandResult(False, "RCLONE_REMOTE is not configured", 2)
    prefix_path = _remote_join(_backup_prefix(), _hostname())
    result = _run_rclone(["lsf", prefix_path, "--files-only"], timeout=30)
    if not result.ok:
        return result
    lines = [line for line in result.output.splitlines() if line.strip()]
    latest = "\n".join(lines[-limit:]) if lines else "(no backup archives found)"
    return RcloneCommandResult(True, latest, 0)


def create_backup() -> BackupResult:
    remote = _remote_base()
    if not remote:
        return BackupResult(False, "RCLONE_REMOTE is not configured")

    files = _candidate_files()
    if not files:
        return BackupResult(False, "Runtime files for backup were not found")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _target_path(timestamp)

    with tempfile.TemporaryDirectory(prefix="telegramhelper-backup-") as tmpdir:
        archive_path = Path(tmpdir) / f"{timestamp}-{_hostname()}.tar.gz"
        manifest = {
            "created_at": timestamp,
            "host": _hostname(),
            "project_root": str(PROJECT_ROOT),
            "files": [_safe_member_name(path) for path in files],
        }
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as archive:
            for path in files:
                archive.add(path, arcname=_safe_member_name(path), recursive=False)
            archive.add(manifest_path, arcname="manifest.json", recursive=False)

        result = _run_rclone(["copyto", str(archive_path), target], timeout=DEFAULT_TIMEOUT)
        if not result.ok:
            return BackupResult(False, result.output or "rclone copyto failed", target)

    return BackupResult(
        True,
        "Backup uploaded successfully",
        target,
        tuple(_safe_member_name(path) for path in files),
    )


def format_backup_result(result: BackupResult) -> str:
    if not result.ok:
        return f"❌ Backup failed\n\n{result.message}"
    return (
        "✅ Backup is ready\n\n"
        f"Remote: {result.remote_path}\n"
        f"Files: {len(result.files)}"
    )


def format_command_result(title: str, result: RcloneCommandResult) -> str:
    status = "✅" if result.ok else "❌"
    body = result.output.strip() or "(empty output)"
    return f"{status} {title}\n\n{body}"
