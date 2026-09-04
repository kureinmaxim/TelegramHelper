#!/usr/bin/env python3
"""Show version info - SSH callable version."""
import os
import sys

# Add project root to path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

try:
    from version import __version__
    print(f"TelegramHelper v{__version__}")
except ImportError:
    print("TelegramHelper (version unknown)")

# Show bot status
from scripts.bot_status import check_status
print("")
check_status()
