# -*- coding: utf-8 -*-
"""
Helpers for running host commands from a Docker container.

When the bot runs inside Docker with ``pid: host``, host commands
(systemctl, journalctl, etc.) are launched via ``nsenter`` into PID 1.
Outside Docker the commands run directly.
"""

import os
import subprocess
from typing import List


def is_docker() -> bool:
    """Detect if running inside a Docker container."""
    return os.path.exists("/.dockerenv") or bool(os.environ.get("DOCKER_CONTAINER"))


def host_run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command on the host via nsenter if inside Docker.

    Requires ``pid: host`` in compose.yaml and ``user: "0:0"``.
    Falls back to direct subprocess.run() when not in Docker.
    """
    if is_docker():
        cmd = [
            "nsenter", "--target", "1",
            "--mount", "--uts", "--ipc", "--net", "--pid",
            "--",
        ] + cmd
    return subprocess.run(cmd, **kwargs)


def host_write_text(path: str, content: str, mode: str = "0644") -> subprocess.CompletedProcess:
    """Atomically write text to a host path, even when called from Docker.

    The file contents go through stdin so secrets do not appear in argv.
    """
    script = (
        'set -e; '
        'path="$1"; mode="$2"; dir="$(dirname "$path")"; '
        'mkdir -p "$dir"; '
        'tmp="$(mktemp "$dir/.tmp.XXXXXX")"; '
        'cat > "$tmp"; '
        'chmod "$mode" "$tmp"; '
        'mv "$tmp" "$path"'
    )
    return host_run(
        ["sh", "-c", script, "host_write_text", path, mode],
        input=content,
        capture_output=True,
        text=True,
        timeout=15,
    )
