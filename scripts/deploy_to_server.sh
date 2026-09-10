#!/bin/bash
# Deploy the bot to a server

# Defaults
SERVER_USER="root"
SERVER_HOST="YOUR_SERVER_IP"
SERVER_PATH="/opt/TelegramHelper"
SSH_PORT="YOUR_SSH_PORT"  # Set your SSH port

# Check arguments
if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <server_user@server_host> [server_path] [ssh_port]"
  echo "Example: $0 root@YOUR_SERVER_IP /opt/TelegramSimple YOUR_SSH_PORT"
  echo ""
  echo "Or run with no args to use the defaults:"
  echo "  Server: $SERVER_USER@$SERVER_HOST:$SSH_PORT"
  echo "  Path: $SERVER_PATH"
  echo ""
  read -p "Use defaults? (Y/n): " use_defaults
  if [ "$use_defaults" = "n" ] || [ "$use_defaults" = "N" ]; then
    exit 1
  fi
fi

# Parse arguments
if [ "$#" -ge 1 ]; then
  SERVER_CONNECTION="$1"
  SERVER_USER=$(echo $SERVER_CONNECTION | cut -d@ -f1)
  SERVER_HOST=$(echo $SERVER_CONNECTION | cut -d@ -f2)
fi

if [ "$#" -ge 2 ]; then
  SERVER_PATH="$2"
fi

if [ "$#" -ge 3 ]; then
  SSH_PORT="$3"
fi

SSH_OPTS="-p $SSH_PORT"

echo "========================================"
echo "🚀 Deploy TelegramSimple"
echo "========================================"
echo "📌 Server: $SERVER_USER@$SERVER_HOST:$SSH_PORT"
echo "📁 Path: $SERVER_PATH"
echo ""

# Check server reachability
echo "🔍 Checking server connection..."
ssh $SSH_OPTS -q $SERVER_USER@$SERVER_HOST exit
if [ $? -ne 0 ]; then
  echo "❌ Failed to connect to the server"
  exit 1
fi
echo "✅ Connection established"

# Create the directory on the server if missing
echo "📂 Creating directory on the server..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "mkdir -p $SERVER_PATH"

# Copy project files
echo "📤 Copying files to the server..."
rsync -avz --delete \
  --exclude 'venv' --exclude '.env' --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'bot.log' \
  --exclude 'app_keys.json' --exclude 'users.json' --exclude 'vless_config.json' \
  -e "ssh $SSH_OPTS" \
  ./ $SERVER_USER@$SERVER_HOST:$SERVER_PATH/

# Check .env on the server
echo "⚙️ Checking settings on the server..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "if [ ! -f $SERVER_PATH/.env ]; then cp $SERVER_PATH/example.env $SERVER_PATH/.env; echo '⚠️ Created .env from the example. Please edit it!'; fi"

# Check Docker on the server
echo "🐳 Checking Docker on the server..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "command -v docker >/dev/null 2>&1 || { echo '❌ Docker is not installed'; exit 1; }"

# Create data files and restart the container
echo "🔄 Restarting the container..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "cd $SERVER_PATH && \
  if [ ! -f app_keys.json ]; then echo '{\"app_keys\": {}, \"default\": {}}' > app_keys.json; fi && \
  if [ ! -f users.json ]; then echo '{}' > users.json; fi && \
  if [ ! -f vless_config.json ]; then echo '{}' > vless_config.json; fi && \
  chmod 600 app_keys.json users.json vless_config.json 2>/dev/null; \
  docker compose down && docker compose up -d --build"

# Wait for start and show version
echo ""
echo "⏳ Waiting for the container to start..."
sleep 3

echo ""
echo "========================================"
echo "📊 Version check on the server"
echo "========================================"
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "cd $SERVER_PATH && python3 scripts/show_version.py"

echo ""
echo "========================================"
echo "✅ Deploy finished!"
echo "========================================"
echo ""
echo "Useful commands:"
echo "  📋 Logs: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && docker compose logs -f\""
echo "  🔄 Restart: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && docker compose restart\""
echo "  📊 Version: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && python3 scripts/show_version.py\""
