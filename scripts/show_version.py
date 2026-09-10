#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Show the project version on the server.

Usage:
    python3 scripts/show_version.py
"""

import os
import sys
from pathlib import Path

# Add project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_version_from_pyproject():
    """Read version from pyproject.toml"""
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("version"):
                        # version = "3.1.1"
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception as e:
            return f"Error: {e}"
    return "Not found"


def get_git_info():
    """Collect git info"""
    import subprocess

    info = {}

    try:
        # Current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        info["branch"] = result.stdout.strip() if result.returncode == 0 else "N/A"

        # Last commit
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit"] = result.stdout.strip() if result.returncode == 0 else "N/A"

        # Last commit date
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit_date"] = result.stdout.strip() if result.returncode == 0 else "N/A"

        # Last commit message
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit_message"] = result.stdout.strip() if result.returncode == 0 else "N/A"

        # Status (uncommitted changes)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_root
        )
        if result.returncode == 0:
            info["dirty"] = bool(result.stdout.strip())
        else:
            info["dirty"] = None

    except FileNotFoundError:
        info["error"] = "git is not installed"
    except Exception as e:
        info["error"] = str(e)

    return info


def get_allowed_apps():
    """List allowed apps (parse without importing)"""
    # Read security.py directly so we do not need fastapi and other deps
    security_file = project_root / "security.py"

    if not security_file.exists():
        return "security.py not found"

    try:
        with open(security_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Look for ALLOWED_APPS = { ... }
        import re

        # Pattern for app_id in quotes (not commented out)
        # Lines like: "app-id": {
        apps = []
        in_allowed_apps = False
        brace_count = 0

        for line in content.split('\n'):
            stripped = line.strip()

            # Skip comments
            if stripped.startswith('#'):
                continue

            # Start of ALLOWED_APPS
            if 'ALLOWED_APPS' in line and '=' in line and '{' in line:
                in_allowed_apps = True
                brace_count = line.count('{') - line.count('}')
                continue

            if in_allowed_apps:
                brace_count += line.count('{') - line.count('}')

                # Look for app_id (quoted string before a colon)
                match = re.match(r'\s*["\']([a-zA-Z0-9_-]+)["\']\s*:\s*\{', line)
                if match:
                    apps.append(match.group(1))

                # End of ALLOWED_APPS
                if brace_count <= 0:
                    break

        return apps if apps else "No apps found"

    except Exception as e:
        return f"Parse error: {e}"


def get_docker_info():
    """Check Docker container status."""
    # 1. Are we inside a container?
    if os.path.exists("/.dockerenv"):
        return "Inside container (✅ Active)"

    try:
        with open("/proc/1/cgroup", "r") as f:
            if "docker" in f.read():
                return "Inside container (✅ Active)"
    except:
        pass

    # 2. If we are on the host, check whether the container is running
    try:
        import subprocess
        # Look for project containers (telegram and dockhand)
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            all_containers = result.stdout.strip().split('\n')
            # Keep only ours
            containers = [c for c in all_containers if "telegram" in c or "dockhand" in c]

            if containers:
                return f"Containers running: {', '.join(containers)} ✅"
            else:
                return "Container is NOT running ❌ (but Docker is installed)"
        else:
            return "Docker check error (maybe no permissions)"
    except FileNotFoundError:
        return "Docker is not installed ❌"
    except Exception as e:
        return f"Error: {e}"


def main():
    print("=" * 60)
    print("📦 TelegramHelper version info")
    print("=" * 60)

    # Version from pyproject.toml
    version = get_version_from_pyproject()
    print(f"\n🏷️  Version: {version}")

    # Project path
    print(f"📁 Path: {project_root}")

    # Docker
    docker_info = get_docker_info()
    print(f"🐳 Docker: {docker_info}")

    # Git
    print("\n" + "-" * 60)
    print("📊 Git info:")
    print("-" * 60)

    git_info = get_git_info()

    if "error" in git_info:
        print(f"❌ {git_info['error']}")
    elif git_info.get('branch') == 'N/A':
        print("⚠️  Git repository not found")
        print("   (.git is not synced via rsync)")
        print("   This is normal for a production server.")
    else:
        print(f"🌿 Branch: {git_info.get('branch', 'N/A')}")
        print(f"🔖 Commit: {git_info.get('commit', 'N/A')}")
        print(f"📅 Date: {git_info.get('commit_date', 'N/A')}")
        print(f"💬 Message: {git_info.get('commit_message', 'N/A')}")

        if git_info.get("dirty"):
            print("⚠️  Uncommitted changes!")
        elif git_info.get("dirty") is False:
            print("✅ Working tree is clean")

    # Allowed apps
    print("\n" + "-" * 60)
    print("🔐 Allowed apps (ALLOWED_APPS):")
    print("-" * 60)

    apps = get_allowed_apps()
    if isinstance(apps, list):
        for app_id in apps:
            print(f"  • {app_id}")
    else:
        print(f"❌ {apps}")

    # Python
    print("\n" + "-" * 60)
    print("🐍 Python info:")
    print("-" * 60)
    print(f"  Version: {sys.version}")
    print(f"  Path: {sys.executable}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
