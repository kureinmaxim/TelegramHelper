#!/usr/bin/env python3
"""Sync host mtproto-proxy systemd unit from mtproto_config.json."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    os.chdir(project_root)
    sys.path.insert(0, str(project_root))

    import mtproto_manager  # Imported after sys.path setup

    success, message = mtproto_manager.apply_config()
    print(message)
    if success:
        print()
        print("Проверьте результат:")
        print("systemctl show -p ExecStart mtproto-proxy --no-pager | cat")
        print("systemctl status mtproto-proxy --no-pager | cat")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
