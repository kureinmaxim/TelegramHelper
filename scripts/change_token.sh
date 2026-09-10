#!/bin/bash
# Quick BOT_TOKEN change on the server
# Usage: ./change_token.sh <new_token>

# Softer error handling instead of strict set -e
set -E  # Error handling in functions
trap 'echo "❌ Error on line $LINENO: $BASH_COMMAND"' ERR

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Initialise variables
RESTART_ONLY=false

# === NEW: keep venv dependencies up to date ===
if [ -d "venv" ] && [ -f "requirements.txt" ]; then
    echo "📦 Updating dependencies from requirements.txt in the local venv..."
    source venv/bin/activate 2>/dev/null || true
    pip install --upgrade pip >/dev/null 2>&1 || true
    pip install -r requirements.txt >/dev/null 2>&1 || true
    deactivate 2>/dev/null || true
    echo "✅ Dependencies updated (including ddgs)"
else
    echo "ℹ️ venv or requirements.txt not found — skipping dependency install"
fi

# Check args, env var, file, or interactive input
if [ $# -eq 0 ] && [ -z "$NEW_BOT_TOKEN" ]; then
    # Check for a temporary token file
    if [ -f "/tmp/new_bot_token.txt" ]; then
        echo "📁 Temporary token file found: /tmp/new_bot_token.txt"
        echo "Using the token from the file (the file will be deleted after use)"
        echo ""
        
        NEW_TOKEN=$(cat /tmp/new_bot_token.txt)
        rm -f /tmp/new_bot_token.txt
        
        echo "✅ Token loaded from file (file deleted)"
        echo ""
    else
        # Check whether a token is already set in .env
        if [ -f ".env" ] && grep -q "BOT_TOKEN=" .env; then
            CURRENT_TOKEN=$(grep "BOT_TOKEN=" .env | cut -d'=' -f2)
            echo "🔍 Current token found in .env: ${CURRENT_TOKEN:0:10}..."
            echo ""
            echo "❓ What do you want to do?"
            echo "y - replace the token and restart"
            echo "N - cancel"
            echo "r - restart with the current token (code will update)"
            read -p "Choose (y/N/r): " -r response
            
            # Keep ASCII letters only (strips Cyrillic, spaces, specials)
            response=$(printf '%s' "$response" | LC_ALL=C tr -cd 'a-zA-Z' | tr '[:upper:]' '[:lower:]')
            
            # Use the last character (in case junk was typed first)
            response="${response: -1}"
            
            # Check choice
            if [[ "$response" == "r" ]]; then
                echo "🔄 Restart mode with the current token..."
                RESTART_ONLY=true
            elif [[ "$response" == "y" ]]; then
                echo "🔄 Token replacement mode..."
            else
                echo "✅ Operation cancelled. Token unchanged."
                exit 0
            fi
            
            # If restart-without-token-change was chosen, skip the prompt
            if [ "$RESTART_ONLY" = false ]; then
                echo "🔑 Enter a new BOT_TOKEN (token will not be saved in history):"
                echo "Get a token from @BotFather in Telegram"
                echo ""
                echo "💡 Alternatively, create /tmp/new_bot_token.txt with the token"
                echo ""
                
                # Interactive input with hidden typing
                read -s -p "BOT_TOKEN: " NEW_TOKEN
                echo ""
                
                # Check that the token was entered
                if [ -z "$NEW_TOKEN" ]; then
                    echo "❌ Error: token not entered"
                    exit 1
                fi
                
                echo "✅ Token received (not saved in command history)"
                echo ""
            fi
        else
            echo "🔑 Enter a new BOT_TOKEN (token will not be saved in history):"
            echo "Get a token from @BotFather in Telegram"
            echo ""
            echo "💡 Alternatively, create /tmp/new_bot_token.txt with the token"
            echo ""
            
            # Interactive input with hidden typing
            read -s -p "BOT_TOKEN: " NEW_TOKEN
            echo ""
            
            # Check that the token was entered
            if [ -z "$NEW_TOKEN" ]; then
                echo "❌ Error: token not entered"
                exit 1
            fi
            
            echo "✅ Token received (not saved in command history)"
            echo ""
        fi
    fi
elif [ -n "$NEW_BOT_TOKEN" ]; then
    # Use the environment variable
    NEW_TOKEN="$NEW_BOT_TOKEN"
    echo "⚠️ Warning: token passed via environment variable"
    echo "Interactive input is recommended for better security"
    echo ""
else
    # Use the command-line argument
    NEW_TOKEN="$1"
    echo "⚠️ Warning: token passed as a command-line argument"
    echo "The token will be saved in shell history!"
    echo "Interactive input is recommended for better security"
    echo ""
fi

# Detect run mode (local or server)
if [ -f ".env" ]; then
    # Local run
    ENV_FILE=".env"
    CONTAINER_NAME="telegram-helper-lite"
    IMAGE_NAME="telegram-helper-lite:latest"
    echo "🏠 Local mode: using .env"
elif [ -f "/etc/telegramhelper.env" ]; then
    # Server mode
    ENV_FILE="/etc/telegramhelper.env"
    CONTAINER_NAME="telegram-helper-lite"
    IMAGE_NAME="telegram-helper-lite:latest"
    echo "🖥️ Server mode: using /etc/telegramhelper.env"
else
    echo "❌ Error: environment file not found"
    echo "Create .env in the current directory or /etc/telegramhelper.env on the server"
    exit 1
fi

echo "🔑 Changing BOT_TOKEN for TelegramSimple"
echo "======================================"
echo ""

# Check that the environment file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ File $ENV_FILE not found"
    echo "Make sure the file exists and is readable"
    exit 1
fi

# Check permissions
if [ ! -r "$ENV_FILE" ]; then
    echo "❌ No permission to read $ENV_FILE"
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        echo "Run the script with sudo"
    else
        echo "Check permissions on .env"
    fi
    exit 1
fi

# Check token format (only if restart-only was not selected)
if [ "$RESTART_ONLY" = false ]; then
    if [[ ! "$NEW_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        echo "❌ Invalid token format: $NEW_TOKEN"
        echo "Token must match: <digits>:<letters_digits>"
        echo "Example: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
        exit 1
    fi

    echo "✅ New token: ${NEW_TOKEN:0:10}..."
    echo ""
fi

# Create a backup
BACKUP_FILE="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "💾 Creating backup: $BACKUP_FILE"

# Choose the copy command based on mode
if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
    sudo cp "$ENV_FILE" "$BACKUP_FILE"
else
    cp "$ENV_FILE" "$BACKUP_FILE"
fi
echo "✅ Backup created"
echo ""

# Stop the current container
echo "⏹️ Stopping the current container..."

# Choose the Docker command based on mode
if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
    DOCKER_CMD="sudo docker"
    COMPOSE_CMD="sudo docker compose"
else
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
fi

# Check Docker availability (later Docker steps are skipped if it is missing)
if command -v docker >/dev/null 2>&1; then
    DOCKER_AVAILABLE=true
else
    DOCKER_AVAILABLE=false
fi

# Check whether Docker Compose is in use (if Docker is available)
if [ "$DOCKER_AVAILABLE" = true ]; then
    # Stop ALL containers with telegram in the name (avoids "port is already allocated")
    echo "🛑 Stopping all telegram containers..."
    $DOCKER_CMD stop $($DOCKER_CMD ps -q --filter name=telegram) 2>/dev/null || true
    $DOCKER_CMD rm $($DOCKER_CMD ps -aq --filter name=telegram) 2>/dev/null || true
    
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Docker Compose detected, using compose commands"
        
        # Stop via Docker Compose
        $COMPOSE_CMD down 2>/dev/null || echo "ℹ️ docker compose down was not run"
        echo "✅ Container stopped via Docker Compose"
    else
        # Use plain Docker commands
        if $DOCKER_CMD ps -q -f name="$CONTAINER_NAME" | grep -q .; then
            $DOCKER_CMD stop "$CONTAINER_NAME" 2>/dev/null || echo "ℹ️ Container already stopped"
            echo "✅ Container stopped"
        else
            echo "ℹ️ Container already stopped or does not exist"
        fi

        # Remove the container
        if $DOCKER_CMD ps -aq -f name="$CONTAINER_NAME" | grep -q .; then
            $DOCKER_CMD rm "$CONTAINER_NAME" 2>/dev/null || echo "ℹ️ Container already removed"
            echo "✅ Container removed"
        else
            echo "ℹ️ Container already removed"
        fi
    fi
    
    # Prune unused images and build cache (free disk space)
    echo "🧹 Cleaning Docker (unused images and cache)..."
    $DOCKER_CMD system prune -f 2>/dev/null || true
    $DOCKER_CMD builder prune -f 2>/dev/null || true
    echo "✅ Docker cleaned"
else
    echo "ℹ️ Docker is not installed — skipping container stop"
fi
echo ""

# Update the token in the file (only if restart-only was not selected)
if [ "$RESTART_ONLY" = false ]; then
    echo "✏️ Updating token in $ENV_FILE..."
    if grep -q "BOT_TOKEN=" "$ENV_FILE"; then
        # Replace the existing token
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            # macOS and Linux compatibility
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sudo sed -i '' "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            else
                sudo sed -i "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            fi
        else
            # macOS and Linux compatibility
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            else
                sed -i "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            fi
        fi
        echo "✅ Existing token updated"
    else
        # Append a new token if missing
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            echo "BOT_TOKEN=$NEW_TOKEN" | sudo tee -a "$ENV_FILE" > /dev/null
        else
            echo "BOT_TOKEN=$NEW_TOKEN" >> "$ENV_FILE"
        fi
        echo "✅ New token added"
    fi
    echo ""

    # Verify the token was updated
    if grep -q "BOT_TOKEN=$NEW_TOKEN" "$ENV_FILE"; then
        echo "✅ Token successfully updated in the file"
    else
        echo "❌ Error: token was not updated"
        echo "Restoring from backup..."
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            sudo cp "$BACKUP_FILE" "$ENV_FILE"
        else
            cp "$BACKUP_FILE" "$ENV_FILE"
        fi
        exit 1
    fi
    echo ""
else
    echo "🔄 Restart mode: token is not changed"
    echo ""
fi

# AI setup dialog — ALWAYS shown (regardless of mode)
echo "🔧 AI setup:"
echo "1) Update API keys"
echo "2) Change the default provider"
echo "3) Leave as-is"
read -p "❓ What do you want to do? (1/2/3, by default 3): " -r ai_config_choice

if [[ "$ai_config_choice" == "1" ]]; then
    echo "🔑 Updating API keys:"
    read -p "❓ Update ANTHROPIC_API_KEY? (y/N): " -r update_anthropic
    if [[ "$update_anthropic" =~ ^[Yy]$ ]]; then
        echo "🔑 Enter a new ANTHROPIC_API_KEY (will not be saved in history):"
        read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
        echo ""
        if [ -n "$new_anthropic_key" ]; then
            echo "🔍 Checking the entered key:"
            echo "   Start: ${new_anthropic_key:0:10}..."
            echo "   End: ...${new_anthropic_key: -10}"
            echo "   Length: ${#new_anthropic_key} chars"
            echo ""
            
            read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
            if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                    sudo sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                else
                    if [[ "$OSTYPE" == "darwin"* ]]; then
                        sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                    else
                        sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                    fi
                fi
                echo "✅ ANTHROPIC_API_KEY updated"
            else
                echo "❌ Key not updated, try again"
            fi
        else
            echo "⚠️  Key not entered, ANTHROPIC_API_KEY not updated"
        fi
    fi
    
    read -p "❓ Update OPENAI_API_KEY? (y/N): " -r update_openai
    if [[ "$update_openai" =~ ^[Yy]$ ]]; then
        echo "🔑 Enter a new OPENAI_API_KEY (will not be saved in history):"
        read -s -p "OPENAI_API_KEY: " new_openai_key
        echo ""
        if [ -n "$new_openai_key" ]; then
            echo "🔍 Checking the entered key:"
            echo "   Start: ${new_openai_key:0:10}..."
            echo "   End: ...${new_openai_key: -10}"
            echo "   Length: ${#new_openai_key} chars"
            echo ""
            
            read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
            if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                    sudo sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                else
                    if [[ "$OSTYPE" == "darwin"* ]]; then
                        sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                    else
                        sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                    fi
                fi
                echo "✅ OPENAI_API_KEY updated"
            else
                echo "❌ Key not updated, try again"
            fi
        else
            echo "⚠️  Key not entered, OPENAI_API_KEY not updated"
        fi
    fi
    

    
elif [[ "$ai_config_choice" == "2" ]]; then
    echo "🤖 Change default AI provider:"
    echo "1) Anthropic (Claude) - recommended"
    echo "2) OpenAI (GPT)"
    read -p "❓ Choose the new provider (1/2): " -r new_provider_choice
    
    if [[ "$new_provider_choice" == "2" ]]; then
        NEW_DEFAULT_PROVIDER="openai"
        echo "✅ OpenAI selected as the new default provider"
    else
        NEW_DEFAULT_PROVIDER="anthropic"
        echo "✅ Anthropic selected as the new default provider"
    fi
    
    # Update DEFAULT_AI_PROVIDER in the file
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        sudo sed -i "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
    else
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
        else
            sed -i "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
        fi
    fi
    
    echo "✅ Default provider changed to $NEW_DEFAULT_PROVIDER"
    
    # Check whether an API key exists for the new provider
    if [[ "$NEW_DEFAULT_PROVIDER" == "anthropic" ]]; then
        echo "🔍 Checking ANTHROPIC_API_KEY for the new provider..."
        
        # Check whether a working key already exists
        if grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
            CURRENT_ANTHROPIC_KEY=$(grep "ANTHROPIC_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)
            echo "✅ Existing ANTHROPIC_API_KEY found: ${CURRENT_ANTHROPIC_KEY:0:10}..."
            echo ""
            echo "❓ What do you want to do with ANTHROPIC_API_KEY?"
            echo "1) Keep the existing key"
            echo "2) Enter a new key"
            read -p "Choose (1/2): " -r key_choice_anthropic
            
            if [[ "$key_choice_anthropic" == "2" ]]; then
                echo "🔑 Enter a new ANTHROPIC_API_KEY (will not be saved in history):"
                read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
                echo ""
                if [ -n "$new_anthropic_key" ]; then
                    echo "🔍 Checking the entered key:"
                    echo "   Start: ${new_anthropic_key:0:10}..."
                    echo "   End: ...${new_anthropic_key: -10}"
                    echo "   Length: ${#new_anthropic_key} chars"
                    echo ""
                    
                    read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        fi
                        echo "✅ ANTHROPIC_API_KEY updated"
                    else
                        echo "❌ Key not updated, try again"
                    fi
                else
                    echo "⚠️  Key not entered, ANTHROPIC_API_KEY not updated"
                fi
            else
                echo "✅ Existing ANTHROPIC_API_KEY kept"
            fi
        else
            echo "⚠️  Anthropic requires ANTHROPIC_API_KEY"
            read -p "❓ Add ANTHROPIC_API_KEY now? (y/N): " -r add_anthropic
            if [[ "$add_anthropic" =~ ^[Yy]$ ]]; then
                echo "🔑 Enter ANTHROPIC_API_KEY (will not be saved in history):"
                read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
                echo ""
                if [ -n "$new_anthropic_key" ]; then
                    echo "🔍 Checking the entered key:"
                    echo "   Start: ${new_anthropic_key:0:10}..."
                    echo "   End: ...${new_anthropic_key: -10}"
                    echo "   Length: ${#new_anthropic_key} chars"
                    echo ""
                    
                    read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        fi
                        echo "✅ ANTHROPIC_API_KEY added"
                    else
                        echo "❌ Key not added, try again"
                    fi
                else
                    echo "⚠️  Key not entered, ANTHROPIC_API_KEY not added"
                fi
            fi
        fi
        
    elif [[ "$NEW_DEFAULT_PROVIDER" == "openai" ]]; then
        echo "🔍 Checking OPENAI_API_KEY for the new provider..."
        
        # Check whether a working key already exists
        if grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
            CURRENT_OPENAI_KEY=$(grep "OPENAI_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)
            echo "✅ Existing OPENAI_API_KEY found: ${CURRENT_OPENAI_KEY:0:10}..."
            echo ""
            echo "❓ What do you want to do with OPENAI_API_KEY?"
            echo "1) Keep the existing key"
            echo "2) Enter a new key"
            read -p "Choose (1/2): " -r key_choice_openai
            
            if [[ "$key_choice_openai" == "2" ]]; then
                echo "🔑 Enter a new OPENAI_API_KEY (will not be saved in history):"
                read -s -p "OPENAI_API_KEY: " new_openai_key
                echo ""
                if [ -n "$new_openai_key" ]; then
                    echo "🔍 Checking the entered key:"
                    echo "   Start: ${new_openai_key:0:10}..."
                    echo "   End: ...${new_openai_key: -10}"
                    echo "   Length: ${#new_openai_key} chars"
                    echo ""
                    
                    read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        fi
                        echo "✅ OPENAI_API_KEY updated"
                    else
                        echo "❌ Key not updated, try again"
                    fi
                else
                    echo "⚠️  Key not entered, OPENAI_API_KEY not updated"
                fi
            else
                echo "✅ Existing OPENAI_API_KEY kept"
            fi
        else
            echo "⚠️  OpenAI requires OPENAI_API_KEY"
            read -p "❓ Add OPENAI_API_KEY now? (y/N): " -r add_openai
            if [[ "$add_openai" =~ ^[Yy]$ ]]; then
                echo "🔑 Enter OPENAI_API_KEY (will not be saved in history):"
                read -s -p "OPENAI_API_KEY: " new_openai_key
                echo ""
                if [ -n "$new_openai_key" ]; then
                    echo "🔍 Checking the entered key:"
                    echo "   Start: ${new_openai_key:0:10}..."
                    echo "   End: ...${new_openai_key: -10}"
                    echo "   Length: ${#new_openai_key} chars"
                    echo ""
                    
                    read -p "❓ Is the key entered correctly? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        fi
                        echo "✅ OPENAI_API_KEY added"
                    else
                        echo "❌ Key not added, try again"
                    fi
                else
                    echo "⚠️  Key not entered, OPENAI_API_KEY not added"
                fi
            fi
        fi
    fi
    
else
    echo "✅ AI settings left unchanged"
fi

echo ""

# Fact-check search preferences (locale and result language)
echo "🌐 Fact-check search setup (sources and result language):"
echo "1) Apply recommended values (EN first)"
echo "2) Enter custom values"
echo "3) Leave as-is (default)"
read -p "❓ What do you want to do? (1/2/3, by default 3): " -r fact_cfg_choice

if [[ "$fact_cfg_choice" == "1" ]] || [[ "$fact_cfg_choice" == "2" ]]; then
    if [[ "$fact_cfg_choice" == "1" ]]; then
        NEW_FACTCHECK_REGION="us-en"
        NEW_FACTCHECK_ACCEPT_LANGUAGE="en-US,en;q=0.9,ru;q=0.3"
        NEW_FACTCHECK_ALLOWED_LANGS="en,ru"
    else
        echo ""
        read -p "FACTCHECK_REGION (e.g. us-en): " -r NEW_FACTCHECK_REGION
        read -p "FACTCHECK_ACCEPT_LANGUAGE (e.g. en-US,en;q=0.9,ru;q=0.3): " -r NEW_FACTCHECK_ACCEPT_LANGUAGE
        read -p "FACTCHECK_ALLOWED_LANGS (e.g. en,ru): " -r NEW_FACTCHECK_ALLOWED_LANGS
        # Default values if left empty
        NEW_FACTCHECK_REGION=${NEW_FACTCHECK_REGION:-us-en}
        NEW_FACTCHECK_ACCEPT_LANGUAGE=${NEW_FACTCHECK_ACCEPT_LANGUAGE:-en-US,en;q=0.9,ru;q=0.3}
        NEW_FACTCHECK_ALLOWED_LANGS=${NEW_FACTCHECK_ALLOWED_LANGS:-en,ru}
    fi

    echo "✏️ Applying fact-check settings in $ENV_FILE..."

    update_kv() {
        local key="$1"
        local value="$2"
        local target_file="$3"
        local use_sudo="$4"  # true/false

        if grep -q "^${key}=" "$target_file" 2>/dev/null; then
            if [ "$use_sudo" = "true" ]; then
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    sudo sed -i '' "s|^${key}=.*|${key}=${value}|" "$target_file"
                else
                    sudo sed -i "s|^${key}=.*|${key}=${value}|" "$target_file"
                fi
            else
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    sed -i '' "s|^${key}=.*|${key}=${value}|" "$target_file"
                else
                    sed -i "s|^${key}=.*|${key}=${value}|" "$target_file"
                fi
            fi
        else
            if [ "$use_sudo" = "true" ]; then
                echo "${key}=${value}" | sudo tee -a "$target_file" >/dev/null
            else
                echo "${key}=${value}" >> "$target_file"
            fi
        fi
    }

    # Update the primary env file
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" "$ENV_FILE" true
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" "$ENV_FILE" true
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" "$ENV_FILE" true
    else
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" "$ENV_FILE" false
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" "$ENV_FILE" false
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" "$ENV_FILE" false
    fi

    # Also update local .env for Docker Compose (if it exists)
    if [ -f ".env" ]; then
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" ".env" false
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" ".env" false
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" ".env" false
    fi

    echo "✅ Fact-check settings applied"
    echo "   FACTCHECK_REGION=$NEW_FACTCHECK_REGION"
    echo "   FACTCHECK_ACCEPT_LANGUAGE=$NEW_FACTCHECK_ACCEPT_LANGUAGE"
    echo "   FACTCHECK_ALLOWED_LANGS=$NEW_FACTCHECK_ALLOWED_LANGS"
else
    echo "ℹ️ Fact-check settings left unchanged"
fi

#
# Rebuild the image with NO cache (always, except cancel)
echo "🔨 Rebuilding the Docker image with NO cache..."
if [ "$DOCKER_AVAILABLE" = true ]; then
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        # Server mode
        sudo docker build --no-cache -t "$IMAGE_NAME" .
    else
        # Local mode
        docker build --no-cache -t "$IMAGE_NAME" .
    fi
    echo "✅ Image rebuilt successfully"
else
    echo "❌ Docker is not installed. Install Docker and Docker Compose, then retry."
    echo "   Docs: https://docs.docker.com/engine/install/"
    echo "   Quick start (Ubuntu/Debian):"
    echo "     apt-get update && apt-get install -y ca-certificates curl gnupg"
    echo "     install -m 0755 -d /etc/apt/keyrings"
    echo "     curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo \$ID)/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg"
    echo "     chmod a+r /etc/apt/keyrings/docker.gpg"
    echo "     echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(. /etc/os-release; echo \$ID) \$(. /etc/os-release; echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list"
    echo "     apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    exit 1
fi

# Built-in API key generator (fallback if api_key.sh is missing)
generate_api_key() {
    python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
    openssl rand -hex 32 2>/dev/null || \
    head -c 32 /dev/urandom | xxd -p
}

# Write API key into .env
set_api_key() {
    local key="$1"
    local env_file="${2:-.env}"
    
    if grep -q "^API_SECRET_KEY=" "$env_file" 2>/dev/null; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$env_file"
        else
            sed -i "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$key|" "$env_file"
        fi
    else
        echo "API_SECRET_KEY=$key" >> "$env_file"
    fi
}

# ApiAi API key management (ALWAYS shown)
echo ""
echo "🔑 API key for ApiAi:"

# Show current key
CURRENT_API_KEY=$(grep "^API_SECRET_KEY=" .env 2>/dev/null | cut -d'=' -f2)
if [ -n "$CURRENT_API_KEY" ] && [ "$CURRENT_API_KEY" != "your_very_long_random_secret_key_here_64_chars_minimum" ]; then
    echo "   Current: ${CURRENT_API_KEY:0:16}..."
    echo ""
    read -p "   Generate a new key? (y/N): " -r api_response
    api_response=$(echo "$api_response" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    
    if [[ "$api_response" == "y" ]]; then
        # Use api_key.sh if available, otherwise the built-in function
        if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
            bash "$SCRIPT_DIR/api_key.sh" generate
        else
            echo "   🔄 Generating a new API key..."
            NEW_API_KEY=$(generate_api_key)
            set_api_key "$NEW_API_KEY" ".env"
            echo "   ✅ New key generated: ${NEW_API_KEY:0:16}..."
            echo ""
            echo "   📋 Copy this key into ApiAi:"
            echo "      $NEW_API_KEY"
            echo ""
        fi
    else
        echo "   ✅ Keeping the current key"
        # Show info via api_key.sh if available
        if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
            bash "$SCRIPT_DIR/api_key.sh" show
        else
            echo "   🔑 Current key: $CURRENT_API_KEY"
            echo ""
        fi
    fi
else
    echo "   ⚠️  Key not set, generating a new one..."
    # Use api_key.sh if available, otherwise the built-in function
    if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
        bash "$SCRIPT_DIR/api_key.sh" generate
    else
        NEW_API_KEY=$(generate_api_key)
        set_api_key "$NEW_API_KEY" ".env"
        echo "   ✅ New key generated: ${NEW_API_KEY:0:16}..."
        echo ""
        echo "   📋 Copy this key into ApiAi:"
        echo "      $NEW_API_KEY"
        echo ""
    fi
fi

# Start a new container
echo "🚀 Starting a new container..."

# Check whether Docker Compose is in use
if [ "$DOCKER_AVAILABLE" = true ]; then
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Starting via Docker Compose"
        
        # Use the modern docker compose command
        $COMPOSE_CMD up -d
    else
        # Use a plain Docker command
        $DOCKER_CMD run -d \
            --name "$CONTAINER_NAME" \
            --env-file "$ENV_FILE" \
            --restart unless-stopped \
            "$IMAGE_NAME"
    fi
    
    # Keep secrets inaccessible to other system users
    if [ -f ".env" ]; then
        chmod 600 .env
        echo "✅ .env permissions set to 600"
    fi
    
    # Set permissions on app_keys.json so the container can persist keys
    if [ -f "app_keys.json" ]; then
        chmod 600 app_keys.json
        echo "✅ app_keys.json permissions set to 600"
    elif [ ! -f "app_keys.json" ]; then
        # Create the file if missing
        echo '{"app_keys": {}, "default": {}}' > app_keys.json
        chmod 600 app_keys.json
        echo "✅ Created app_keys.json with mode 600"
    fi
fi

# Check startup status
echo ""
echo "📊 Checking startup status..."
sleep 5

# Resolve the actual container name
# compose.yaml sets container_name: telegram-helper-lite
ACTUAL_CONTAINER_NAME="$CONTAINER_NAME"

echo "🔍 Looking for container: $ACTUAL_CONTAINER_NAME"

if [ "$DOCKER_AVAILABLE" = true ] && $DOCKER_CMD ps -q -f name="$ACTUAL_CONTAINER_NAME" | grep -q .; then
    echo "✅ Container started successfully"
    
    # Show container info
    echo ""
    echo "📋 Container info:"
    $DOCKER_CMD ps | grep "$ACTUAL_CONTAINER_NAME"
    
    # Check environment variables
    echo ""
    echo "🔍 Checking environment variables:"
    if $DOCKER_CMD exec "$ACTUAL_CONTAINER_NAME" env | grep -q "BOT_TOKEN"; then
        echo "✅ BOT_TOKEN found in the container"
        echo "Token: $($DOCKER_CMD exec "$ACTUAL_CONTAINER_NAME" env | grep BOT_TOKEN | cut -d'=' -f2 | cut -c1-10)..."
    else
        echo "❌ BOT_TOKEN not found in the container"
    fi
    
    
    # Show recent logs
    echo ""
    echo "📝 Latest container logs:"
    $DOCKER_CMD logs --tail 10 "$ACTUAL_CONTAINER_NAME"
    
else
    echo "❌ Error: container did not start"
    echo ""
    echo "📝 Error logs:"
    $DOCKER_CMD logs "$ACTUAL_CONTAINER_NAME" 2>/dev/null || echo "Logs unavailable"
    
    echo ""
    echo "🔄 Restoring from backup..."
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        sudo cp "$BACKUP_FILE" "$ENV_FILE"
    else
        cp "$BACKUP_FILE" "$ENV_FILE"
    fi
    
    echo "🔄 Restarting the old container..."
    
    # Check whether Docker Compose is in use
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Restoring via Docker Compose"
        
        # Use the modern docker compose command
        $COMPOSE_CMD up -d
    else
        # Use a plain Docker command
        $DOCKER_CMD run -d \
            --name "$CONTAINER_NAME" \
            --env-file "$ENV_FILE" \
            --restart unless-stopped \
            "$IMAGE_NAME"
    fi
    
    exit 1
fi

echo ""

# Final message depending on mode
if [ "$RESTART_ONLY" = true ]; then
    echo "🎉 Container restarted successfully!"
    echo ""
    echo "📋 What was done:"
    echo "   • Backup created: $BACKUP_FILE"
    echo "   • Token was NOT changed"
    echo "   • AI settings updated (if changed)"
    echo "   • Docker image rebuilt with NO cache"
    echo "   • Container restarted: $CONTAINER_NAME"
else
    echo "🎉 Token updated successfully!"
    echo ""
    echo "📋 What was done:"
    echo "   • Backup created: $BACKUP_FILE"
    echo "   • Token updated in: $ENV_FILE"
    echo "   • AI settings updated (if changed)"
    echo "   • Docker image rebuilt with NO cache"
    echo "   • Container restarted: $CONTAINER_NAME"
fi
echo ""
echo "🔍 To verify the bot:"
echo "   • Send /start to the bot in Telegram"
if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
    echo "   • Check logs: $COMPOSE_CMD logs -f"
    echo "   • Check status: $DOCKER_CMD ps"
else
    echo "   • Check logs: $DOCKER_CMD logs -f $CONTAINER_NAME"
    echo "   • Check status: $DOCKER_CMD ps | grep $CONTAINER_NAME"
fi
echo ""
echo "🤖 For the /ai command:"
# Check current AI settings
if grep -q "DEFAULT_AI_PROVIDER=" "$ENV_FILE"; then
    CURRENT_PROVIDER=$(grep "DEFAULT_AI_PROVIDER=" "$ENV_FILE" | cut -d'=' -f2)
    echo "   ✅ AI is configured:"
    echo "      • Provider: $CURRENT_PROVIDER"
    
    if grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
        echo "      • ANTHROPIC_API_KEY is set"
    else
        echo "      • ⚠️  ANTHROPIC_API_KEY is not set"
    fi
    
    if grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
        echo "      • OPENAI_API_KEY is set"
    else
        echo "      • ⚠️  OPENAI_API_KEY is not set"
    fi
    
    if [[ "$CURRENT_PROVIDER" == "anthropic" ]] && grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
        echo "   🚀 The /ai command should work with Claude!"
    elif [[ "$CURRENT_PROVIDER" == "openai" ]] && grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
        echo "   🚀 The /ai command should work with GPT!"
    else
        echo "   ⚠️  /ai needs an API key for provider $CURRENT_PROVIDER"
    fi
else
    echo "   ❌ DEFAULT_AI_PROVIDER not found in $ENV_FILE"
    echo "   ⚠️  Configure AI manually for the /ai command"
fi
echo ""
echo "🛡️ VLESS-Reality / Xray:"
# Check Xray status on the host
if command -v xray &> /dev/null; then
    XRAY_VERSION=$(xray version 2>&1 | head -1)
    echo "   ✅ Xray installed: $XRAY_VERSION"
    
    # Check service status
    if systemctl is-active --quiet xray 2>/dev/null; then
        echo "   ✅ Xray is running"
    else
        echo "   ⚠️  Xray is not running"
        echo "   To start: systemctl start xray"
    fi
    
    # Check port 443
    if ss -tlnp 2>/dev/null | grep -q ":443 "; then
        echo "   ✅ Port 443 is listening"
    else
        echo "   ⚠️  Port 443 is not listening"
    fi
else
    echo "   ❌ Xray is not installed"
    echo "   To install:"
    echo "   bash -c \"\\\$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install"
fi
echo ""
echo "   📋 VLESS-Reality setup:"
echo "      1. /vless_sync         - generate keys"
echo "      2. /vless_export       - get '🖥️ Xray Server Config'"
echo "      3. SSH: nano /usr/local/etc/xray/config.json"
echo "         (paste JSON, Ctrl+O, Ctrl+X)"
echo "      4. SSH: systemctl restart xray"
echo "      5. /vless_test         - test the connection"
echo ""
echo "🔄 To roll back:"
if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
    echo "   sudo cp $BACKUP_FILE $ENV_FILE"
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "   sudo docker compose restart"
    else
        echo "   sudo docker restart $CONTAINER_NAME"
    fi
else
    echo "   cp $BACKUP_FILE $ENV_FILE"
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "   docker compose restart"
    else
        echo "   docker restart $CONTAINER_NAME"
    fi
fi
echo ""
echo "🔒 Security recommendations:"
echo "   • 🥇 INTERACTIVE INPUT (safest): just run ./scripts/change_token.sh"
echo "   • 🥈 TEMP FILE: echo \"token\" > /tmp/new_bot_token.txt && ./scripts/change_token.sh"
echo "   • 🥉 Environment variable: export NEW_BOT_TOKEN=\"token\" && ./scripts/change_token.sh"
echo "   • 🥉 Command-line argument: ./scripts/change_token.sh TOKEN (NOT RECOMMENDED!)"
echo ""
echo "🔍 Security check:"
echo "   • Check shell history: history | grep -i token"
echo "   • If needed, delete history entries: history -d <command_number>"
echo "   • Unset variables: unset NEW_BOT_TOKEN"
