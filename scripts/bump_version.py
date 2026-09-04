#!/usr/bin/env python3
"""
Bump version and sync versions across project files.

Usage:
    python scripts/bump_version.py              # Show all versions and sync
    python scripts/bump_version.py --bump patch # Bump patch version
    python scripts/bump_version.py --sync       # Force sync all files
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


VERSION_RE = re.compile(r'^(version\s*=\s*")(?P<version>[^"]+)(")[\s]*$', re.MULTILINE)
META_SECTION_RE = re.compile(r'^\[tool\.telegramhelper\.metadata\][\s]*$', re.MULTILINE)
RELEASE_DATE_RE = re.compile(r'^(release_date\s*=\s*")(?P<date>[^"]+)(")[\s]*$', re.MULTILINE)
DEVELOPER_RE = re.compile(r'^(developer\s*=\s*")(?P<dev>[^"]+)(")[\s]*$', re.MULTILINE)
LAST_UPDATED_RE = re.compile(r'^(last_updated\s*=\s*")(?P<date>[^"]+)(")[\s]*$', re.MULTILINE)
DOCKHAND_UI_VERSION_RE = re.compile(
    r'^(DOCKHAND_UI_VERSION:\s*str\s*=\s*")(?P<version>[^"]+)(")[\s]*$',
    re.MULTILINE,
)

# Files that contain hardcoded versions to sync
VERSION_LOCATIONS = [
    {
        "file": "api.py",
        "patterns": [
            (r'version="[\d.]+"', 'version="{version}"'),
        ],
        "description": "FastAPI version"
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump version and sync versions across project files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/bump_version.py              # Show all versions
  python scripts/bump_version.py --bump patch # Bump patch (1.2.3 -> 1.2.4)
  python scripts/bump_version.py --bump minor # Bump minor (1.2.3 -> 1.3.0)
  python scripts/bump_version.py --sync       # Sync all files to pyproject.toml version
        """
    )
    parser.add_argument("--file", default="pyproject.toml", help="Path to pyproject.toml")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--version", help="Set explicit version (e.g. 1.2.3)")
    group.add_argument("--bump", choices=["major", "minor", "patch"], help="Bump version part")
    group.add_argument("--sync", action="store_true", help="Sync all files to pyproject.toml version")
    parser.add_argument("--release-date", dest="release_date", help="Release date in DD.MM.YYYY")
    parser.add_argument("--developer", help="Set developer name")
    parser.add_argument("--no-release-date", action="store_true", help="Do not change release_date")
    parser.add_argument(
        "--dockhand",
        action="store_true",
        help="Update DOCKHAND_UI_VERSION in dockhand/app.py only",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)


def write_text(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


def get_current_version(content: str) -> str:
    m = VERSION_RE.search(content)
    if not m:
        print("Error: could not find version in [project] section", file=sys.stderr)
        sys.exit(1)
    return m.group("version")


def bump_version_str(v: str, which: str) -> str:
    parts = v.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        print(f"Error: unsupported version format '{v}'. Expected X.Y.Z", file=sys.stderr)
        sys.exit(1)
    major, minor, patch = map(int, parts)
    if which == "major":
        major += 1; minor = 0; patch = 0
    elif which == "minor":
        minor += 1; patch = 0
    elif which == "patch":
        patch += 1
    return f"{major}.{minor}.{patch}"


def bump_dockhand_version_str(v: str, which: str) -> str:
    """
    Bump Dockhand UI version with optional 'v' prefix.
    Supported formats:
      - v3 / 3
      - v1.2.3 / 1.2.3
    """
    raw = v.strip()
    has_prefix = raw.lower().startswith("v")
    core = raw[1:] if has_prefix else raw
    parts = core.split(".")

    if not all(p.isdigit() for p in parts):
        print(
            f"Error: unsupported Dockhand version format '{v}'. "
            "Expected vN or vX.Y.Z",
            file=sys.stderr,
        )
        sys.exit(1)

    # Single-number UI generation style: v3 -> v4
    if len(parts) == 1:
        num = int(parts[0]) + 1
        return f"v{num}" if has_prefix else str(num)

    # Semver style for Dockhand, if used in the future.
    if len(parts) == 3:
        major, minor, patch = map(int, parts)
        if which == "major":
            major += 1
            minor = 0
            patch = 0
        elif which == "minor":
            minor += 1
            patch = 0
        elif which == "patch":
            patch += 1
        out = f"{major}.{minor}.{patch}"
        return f"v{out}" if has_prefix else out

    print(
        f"Error: unsupported Dockhand version format '{v}'. "
        "Expected vN or vX.Y.Z",
        file=sys.stderr,
    )
    sys.exit(1)


def get_dockhand_version(content: str) -> str:
    m = DOCKHAND_UI_VERSION_RE.search(content)
    if not m:
        print(
            "Error: could not find DOCKHAND_UI_VERSION in dockhand/app.py",
            file=sys.stderr,
        )
        sys.exit(1)
    return m.group("version")


def ensure_metadata_section(content: str) -> str:
    if META_SECTION_RE.search(content):
        return content
    # Append metadata section at the end if missing
    today_ymd = datetime.now().strftime("%Y-%m-%d")
    today_dmy = datetime.now().strftime("%d.%m.%Y")
    to_append = (
        "\n\n# Tool-specific metadata for the project\n"
        "[tool.telegramhelper.metadata]\n"
        f"release_date = \"{today_dmy}\"\n"
        f"developer = \"\"\n"
        f"last_updated = \"{today_ymd}\"\n"
    )
    return content.rstrip() + "\n" + to_append


def scan_version_locations(project_dir: Path, version: str) -> list:
    """Scan project for version references and check if they match."""
    results = []
    
    for loc in VERSION_LOCATIONS:
        file_path = project_dir / loc["file"]
        if not file_path.exists():
            results.append({
                "file": loc["file"],
                "description": loc["description"],
                "status": "❓ Not found",
                "current": None,
                "needs_sync": False
            })
            continue
        
        content = file_path.read_text(encoding="utf-8")
        found_versions = []
        
        for pattern, _ in loc["patterns"]:
            matches = re.findall(pattern, content)
            for m in matches:
                # Extract version from match like version="2.1.0"
                v_match = re.search(r'[\d.]+', m)
                if v_match:
                    found_versions.append(v_match.group())
        
        if found_versions:
            current_v = found_versions[0]
            needs_sync = current_v != version
            results.append({
                "file": loc["file"],
                "description": loc["description"],
                "status": "⚠️ Out of sync" if needs_sync else "✅ OK",
                "current": current_v,
                "needs_sync": needs_sync
            })
        else:
            results.append({
                "file": loc["file"],
                "description": loc["description"],
                "status": "❓ No version found",
                "current": None,
                "needs_sync": False
            })
    
    return results


def sync_version_in_file(file_path: Path, patterns: list, version: str) -> bool:
    """Sync version in a single file."""
    if not file_path.exists():
        return False
    
    content = file_path.read_text(encoding="utf-8")
    original = content
    
    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement.format(version=version), content)
    
    if content != original:
        file_path.write_text(content, encoding="utf-8")
        return True
    return False


def sync_all_versions(project_dir: Path, version: str) -> int:
    """Sync version in all tracked files. Returns count of updated files."""
    updated = 0
    
    for loc in VERSION_LOCATIONS:
        file_path = project_dir / loc["file"]
        if sync_version_in_file(file_path, loc["patterns"], version):
            print(f"  ✅ {loc['file']} - updated to {version}")
            updated += 1
    
    return updated


def show_version_summary(project_dir: Path, version: str, release_date: str, last_updated: str):
    """Show summary of all version locations."""
    print("\n" + "=" * 50)
    print("📦 TelegramSimple — Version Summary")
    print("=" * 50)
    print(f"\n🏷️  pyproject.toml (source of truth)")
    print(f"   Version:      {version}")
    print(f"   Release date: {release_date}")
    print(f"   Last updated: {last_updated}")
    
    # Scan other files
    results = scan_version_locations(project_dir, version)
    
    if results:
        print(f"\n📁 Other files:")
        for r in results:
            v_str = f" ({r['current']})" if r['current'] else ""
            print(f"   {r['status']} {r['file']}{v_str} — {r['description']}")
    
    # Check if sync needed
    needs_sync = any(r['needs_sync'] for r in results)
    
    print("\n" + "-" * 50)
    if needs_sync:
        print("⚠️  Some files are out of sync!")
        print("   Run: python scripts/bump_version.py --sync")
    else:
        print("✅ All version references are in sync!")
    print()


def main():
    args = parse_args()

    if args.dockhand:
        dockhand_path = Path("dockhand/app.py")
        dockhand_content = read_text(dockhand_path)
        current_dockhand = get_dockhand_version(dockhand_content)

        if not args.version and not args.bump and not args.sync:
            print("\n" + "=" * 50)
            print("Dockhand UI Version")
            print("=" * 50)
            print("\ndockhand/app.py")
            print(f"   DOCKHAND_UI_VERSION: {current_dockhand}\n")
            return

        if args.sync:
            print(
                "Error: --sync is not supported with --dockhand. "
                "Use --bump or --version.",
                file=sys.stderr,
            )
            sys.exit(1)

        new_dockhand = None
        if args.version:
            new_dockhand = args.version.strip()
            if not re.match(r"^v?\d+(\.\d+\.\d+)?$", new_dockhand):
                print(
                    "Error: --version for --dockhand must be vN or vX.Y.Z",
                    file=sys.stderr,
                )
                sys.exit(1)
        elif args.bump:
            new_dockhand = bump_dockhand_version_str(current_dockhand, args.bump)

        if not new_dockhand:
            print(
                "Error: in --dockhand mode provide --bump or --version",
                file=sys.stderr,
            )
            sys.exit(1)

        updated_content = DOCKHAND_UI_VERSION_RE.sub(
            rf'\g<1>{new_dockhand}\3',
            dockhand_content,
            count=1,
        )
        write_text(dockhand_path, updated_content)

        print(f"\nUpdated {dockhand_path}")
        print(
            f"   DOCKHAND_UI_VERSION: {current_dockhand} → {new_dockhand}\n"
        )
        return

    path = Path(args.file)
    project_dir = path.parent if path.parent != Path('.') else Path('.')
    
    # If path is just filename, use current dir
    if not path.exists() and Path(args.file).name == args.file:
        # Try to find pyproject.toml in current dir or parent
        for try_path in [Path('pyproject.toml'), Path('../pyproject.toml')]:
            if try_path.exists():
                path = try_path
                project_dir = try_path.parent
                break
    
    content = read_text(path)
    content = ensure_metadata_section(content)
    
    current_version = get_current_version(content)
    
    # Extract current metadata
    release_match = RELEASE_DATE_RE.search(content)
    updated_match = LAST_UPDATED_RE.search(content)
    release_date = release_match.group("date") if release_match else "N/A"
    last_updated = updated_match.group("date") if updated_match else "N/A"
    
    # No args mode - just show summary
    if not args.version and not args.bump and not args.sync and not args.developer and not args.release_date:
        show_version_summary(project_dir, current_version, release_date, last_updated)
        return
    
    # Sync mode
    if args.sync:
        print(f"\n🔄 Syncing all files to version {current_version}...")
        updated = sync_all_versions(project_dir, current_version)
        if updated == 0:
            print("   No files needed updating.")
        else:
            print(f"\n✅ Updated {updated} file(s)")
        return
    
    # Determine new version
    new_version = None
    if args.version:
        new_version = args.version.strip()
        if not re.match(r"^\d+\.\d+\.\d+$", new_version):
            print("Error: --version must be in X.Y.Z format", file=sys.stderr)
            sys.exit(1)
    elif args.bump:
        new_version = bump_version_str(current_version, args.bump)

    # Apply version change
    if new_version:
        content = VERSION_RE.sub(rf'\g<1>{new_version}\3', content, count=1)

    # Dates
    today_ymd = datetime.now().strftime("%Y-%m-%d")
    today_dmy = datetime.now().strftime("%d.%m.%Y")

    # last_updated always set to today
    if LAST_UPDATED_RE.search(content):
        content = LAST_UPDATED_RE.sub(rf'\g<1>{today_ymd}\3', content, count=1)

    # release_date set if provided or if version changed and not disabled
    if args.release_date:
        rd = args.release_date.strip()
        if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", rd):
            print("Error: --release-date must be DD.MM.YYYY", file=sys.stderr)
            sys.exit(1)
        if RELEASE_DATE_RE.search(content):
            content = RELEASE_DATE_RE.sub(rf'\g<1>{rd}\3', content, count=1)
    elif new_version and not args.no_release_date:
        if RELEASE_DATE_RE.search(content):
            content = RELEASE_DATE_RE.sub(rf'\g<1>{today_dmy}\3', content, count=1)

    # developer
    if args.developer is not None:
        dev = args.developer
        if DEVELOPER_RE.search(content):
            content = DEVELOPER_RE.sub(rf'\g<1>{dev}\3', content, count=1)

    write_text(path, content)
    
    print(f"\n✅ Updated {path}")
    if new_version:
        print(f"   Version: {current_version} → {new_version}")
        # Auto-sync other files
        print(f"\n🔄 Syncing other files...")
        sync_all_versions(project_dir, new_version)
    print(f"   last_updated: {today_ymd}")
    if args.release_date or (new_version and not args.no_release_date):
        print(f"   release_date: {args.release_date or today_dmy}")
    if args.developer is not None:
        print(f"   developer: {args.developer}")
    print()


if __name__ == "__main__":
    main() 