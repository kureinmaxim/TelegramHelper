#!/bin/bash
# 
# Install & Setup Xray for TelegramSimple
# 
# 1. Installs Xray-core
# 2. Generates initial keys if needed (via vless_manager)
# 3. Syncs config to Xray
# 4. Restarts Xray service
#

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Setting up VLESS-Reality Server ===${NC}"

# 1. Check Root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

# Project root is derived from the script location (scripts/..), no hardcoding
# and no legacy /opt/TelegramSimple. Override with PROJECT_DIR if needed.
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# 2. Install Xray (if missing)
if ! command -v xray &> /dev/null; then
    echo -e "${YELLOW}Installing Xray-core...${NC}"
    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    echo -e "${GREEN}Xray-core is already installed.${NC}"
fi

# 3. Ensure TelegramSimple dependency
if [ ! -f "$PROJECT_DIR/vless_manager.py" ]; then
    echo "Error: $PROJECT_DIR/vless_manager.py not found."
    echo "Please deploy the project first to $PROJECT_DIR"
    exit 1
fi

# 4. Sync Config
echo -e "${YELLOW}Syncing Xray configuration...${NC}"
cd "$PROJECT_DIR"
if [ -f "scripts/sync_xray_config.py" ]; then
    python3 scripts/sync_xray_config.py
else
    echo "Error: scripts/sync_xray_config.py not found"
    exit 1
fi

# 5. Restart Xray
echo -e "${YELLOW}Restarting Xray service...${NC}"
systemctl restart xray
systemctl enable xray

if systemctl is-active --quiet xray; then
    echo -e "${GREEN}✅ Xray service is RUNNING!${NC}"
else
    echo -e "${YELLOW}⚠️ Xray service failed to start. Check logs: journalctl -u xray -n 20${NC}"
fi

echo -e "${GREEN}Setup Complete.${NC}"
