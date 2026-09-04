#!/bin/bash
# Скрипт для быстрой смены BOT_TOKEN на сервере
# Использование: ./change_token.sh <новый_токен>

# Заменяем строгий set -e на более мягкую обработку ошибок
set -E  # Обработка ошибок в функциях
trap 'echo "❌ Ошибка в строке $LINENO: $BASH_COMMAND"' ERR

# Определяем директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Инициализируем переменные
RESTART_ONLY=false

# === НОВОЕ: гарантируем актуальные зависимости в venv ===
if [ -d "venv" ] && [ -f "requirements.txt" ]; then
    echo "📦 Обновляю зависимости из requirements.txt в локальном venv..."
    source venv/bin/activate 2>/dev/null || true
    pip install --upgrade pip >/dev/null 2>&1 || true
    pip install -r requirements.txt >/dev/null 2>&1 || true
    deactivate 2>/dev/null || true
    echo "✅ Зависимости обновлены (включая ddgs)"
else
    echo "ℹ️ venv или requirements.txt не найдены — пропускаю установку зависимостей"
fi

# Проверка аргументов, переменной окружения, файла или интерактивный ввод
if [ $# -eq 0 ] && [ -z "$NEW_BOT_TOKEN" ]; then
    # Проверяем, есть ли временный файл с токеном
    if [ -f "/tmp/new_bot_token.txt" ]; then
        echo "📁 Найден временный файл с токеном: /tmp/new_bot_token.txt"
        echo "Используем токен из файла (файл будет удален после использования)"
        echo ""
        
        NEW_TOKEN=$(cat /tmp/new_bot_token.txt)
        rm -f /tmp/new_bot_token.txt
        
        echo "✅ Токен загружен из файла (файл удален)"
        echo ""
    else
        # Проверяем, есть ли уже установленный токен в .env файле
        if [ -f ".env" ] && grep -q "BOT_TOKEN=" .env; then
            CURRENT_TOKEN=$(grep "BOT_TOKEN=" .env | cut -d'=' -f2)
            echo "🔍 Найден текущий токен в .env файле: ${CURRENT_TOKEN:0:10}..."
            echo ""
            echo "❓ Что хотите сделать?"
            echo "y - заменить токен и перезапустить"
            echo "N - отменить"
            echo "r - перезапустить с текущим токеном (код обновится)"
            read -p "Выберите (y/N/r): " -r response
            
            # Удаляем ВСЕ кроме ASCII букв (убирает русские символы, пробелы, спецсимволы)
            response=$(printf '%s' "$response" | LC_ALL=C tr -cd 'a-zA-Z' | tr '[:upper:]' '[:lower:]')
            
            # Берём последний символ (на случай если сначала ввели мусор)
            response="${response: -1}"
            
            # Проверяем
            if [[ "$response" == "r" ]]; then
                echo "🔄 Режим перезапуска с текущим токеном..."
                RESTART_ONLY=true
            elif [[ "$response" == "y" ]]; then
                echo "🔄 Режим замены токена..."
            else
                echo "✅ Операция отменена. Токен не изменен."
                exit 0
            fi
            
            # Если выбран режим перезапуска без смены токена, пропускаем ввод
            if [ "$RESTART_ONLY" = false ]; then
                echo "🔑 Введите новый BOT_TOKEN (токен не будет сохранен в истории):"
                echo "Получить токен можно у @BotFather в Telegram"
                echo ""
                echo "💡 Альтернативно, создайте файл /tmp/new_bot_token.txt с токеном"
                echo ""
                
                # Интерактивный ввод с скрытым вводом
                read -s -p "BOT_TOKEN: " NEW_TOKEN
                echo ""
                
                # Проверяем, что токен введен
                if [ -z "$NEW_TOKEN" ]; then
                    echo "❌ Ошибка: токен не введен"
                    exit 1
                fi
                
                echo "✅ Токен получен (не сохранен в истории команд)"
                echo ""
            fi
        else
            echo "🔑 Введите новый BOT_TOKEN (токен не будет сохранен в истории):"
            echo "Получить токен можно у @BotFather в Telegram"
            echo ""
            echo "💡 Альтернативно, создайте файл /tmp/new_bot_token.txt с токеном"
            echo ""
            
            # Интерактивный ввод с скрытым вводом
            read -s -p "BOT_TOKEN: " NEW_TOKEN
            echo ""
            
            # Проверяем, что токен введен
            if [ -z "$NEW_TOKEN" ]; then
                echo "❌ Ошибка: токен не введен"
                exit 1
            fi
            
            echo "✅ Токен получен (не сохранен в истории команд)"
            echo ""
        fi
    fi
elif [ -n "$NEW_BOT_TOKEN" ]; then
    # Используем переменную окружения
    NEW_TOKEN="$NEW_BOT_TOKEN"
    echo "⚠️ Внимание: токен передан через переменную окружения"
    echo "Рекомендуется использовать интерактивный ввод для большей безопасности"
    echo ""
else
    # Используем аргумент командной строки
    NEW_TOKEN="$1"
    echo "⚠️ Внимание: токен передан как аргумент командной строки"
    echo "Токен будет сохранен в истории команд!"
    echo "Рекомендуется использовать интерактивный ввод для большей безопасности"
    echo ""
fi

# Определяем тип запуска (локальный или серверный)
if [ -f ".env" ]; then
    # Локальный запуск
    ENV_FILE=".env"
    CONTAINER_NAME="telegram-helper-lite"
    IMAGE_NAME="telegram-helper-lite:latest"
    echo "🏠 Локальный режим: используем .env файл"
elif [ -f "/etc/telegramhelper.env" ]; then
    # Серверный режим
    ENV_FILE="/etc/telegramhelper.env"
    CONTAINER_NAME="telegram-helper-lite"
    IMAGE_NAME="telegram-helper-lite:latest"
    echo "🖥️ Серверный режим: используем /etc/telegramhelper.env"
else
    echo "❌ Ошибка: не найден файл с переменными окружения"
    echo "Создайте файл .env в текущей директории или /etc/telegramhelper.env на сервере"
    exit 1
fi

echo "🔑 Смена BOT_TOKEN для TelegramSimple"
echo "======================================"
echo ""

# Проверяем существование файла с переменными окружения
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Файл $ENV_FILE не найден"
    echo "Убедитесь, что файл существует и доступен для чтения"
    exit 1
fi

# Проверяем права доступа
if [ ! -r "$ENV_FILE" ]; then
    echo "❌ Нет прав на чтение файла $ENV_FILE"
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        echo "Запустите скрипт с sudo"
    else
        echo "Проверьте права доступа к файлу .env"
    fi
    exit 1
fi

# Проверяем формат токена (только если не выбран режим перезапуска)
if [ "$RESTART_ONLY" = false ]; then
    if [[ ! "$NEW_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        echo "❌ Неверный формат токена: $NEW_TOKEN"
        echo "Токен должен иметь формат: <число>:<буквы_цифры>"
        echo "Пример: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
        exit 1
    fi

    echo "✅ Новый токен: ${NEW_TOKEN:0:10}..."
    echo ""
fi

# Создаем резервную копию
BACKUP_FILE="${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
echo "💾 Создаем резервную копию: $BACKUP_FILE"

# Определяем команду для копирования в зависимости от режима
if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
    sudo cp "$ENV_FILE" "$BACKUP_FILE"
else
    cp "$ENV_FILE" "$BACKUP_FILE"
fi
echo "✅ Резервная копия создана"
echo ""

# Останавливаем текущий контейнер
echo "⏹️ Останавливаем текущий контейнер..."

# Определяем команду Docker в зависимости от режима
if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
    DOCKER_CMD="sudo docker"
    COMPOSE_CMD="sudo docker compose"
else
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
fi

# Проверяем доступность Docker (дальнейшие Docker-шаги будут пропущены, если его нет)
if command -v docker >/dev/null 2>&1; then
    DOCKER_AVAILABLE=true
else
    DOCKER_AVAILABLE=false
fi

# Проверяем, используется ли Docker Compose (если Docker доступен)
if [ "$DOCKER_AVAILABLE" = true ]; then
    # Останавливаем ВСЕ контейнеры с telegram в имени (решает проблему port is already allocated)
    echo "🛑 Останавливаем все telegram-контейнеры..."
    $DOCKER_CMD stop $($DOCKER_CMD ps -q --filter name=telegram) 2>/dev/null || true
    $DOCKER_CMD rm $($DOCKER_CMD ps -aq --filter name=telegram) 2>/dev/null || true
    
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Обнаружен Docker Compose, используем compose команды"
        
        # Останавливаем через Docker Compose
        $COMPOSE_CMD down 2>/dev/null || echo "ℹ️ docker compose down не выполнен"
        echo "✅ Контейнер остановлен через Docker Compose"
    else
        # Используем обычные Docker команды
        if $DOCKER_CMD ps -q -f name="$CONTAINER_NAME" | grep -q .; then
            $DOCKER_CMD stop "$CONTAINER_NAME" 2>/dev/null || echo "ℹ️ Контейнер уже остановлен"
            echo "✅ Контейнер остановлен"
        else
            echo "ℹ️ Контейнер уже остановлен или не существует"
        fi

        # Удаляем контейнер
        if $DOCKER_CMD ps -aq -f name="$CONTAINER_NAME" | grep -q .; then
            $DOCKER_CMD rm "$CONTAINER_NAME" 2>/dev/null || echo "ℹ️ Контейнер уже удален"
            echo "✅ Контейнер удален"
        else
            echo "ℹ️ Контейнер уже удален"
        fi
    fi
    
    # Очищаем неиспользуемые образы и кэш сборки (освобождаем место)
    echo "🧹 Очищаем Docker (неиспользуемые образы и кэш)..."
    $DOCKER_CMD system prune -f 2>/dev/null || true
    $DOCKER_CMD builder prune -f 2>/dev/null || true
    echo "✅ Docker очищен"
else
    echo "ℹ️ Docker не установлен — пропускаю остановку контейнеров"
fi
echo ""

# Обновляем токен в файле (только если не выбран режим перезапуска)
if [ "$RESTART_ONLY" = false ]; then
    echo "✏️ Обновляем токен в $ENV_FILE..."
    if grep -q "BOT_TOKEN=" "$ENV_FILE"; then
        # Заменяем существующий токен
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            # macOS и Linux совместимость
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sudo sed -i '' "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            else
                sudo sed -i "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            fi
        else
            # macOS и Linux совместимость
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            else
                sed -i "s/BOT_TOKEN=.*/BOT_TOKEN=$NEW_TOKEN/" "$ENV_FILE"
            fi
        fi
        echo "✅ Существующий токен обновлен"
    else
        # Добавляем новый токен, если его нет
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            echo "BOT_TOKEN=$NEW_TOKEN" | sudo tee -a "$ENV_FILE" > /dev/null
        else
            echo "BOT_TOKEN=$NEW_TOKEN" >> "$ENV_FILE"
        fi
        echo "✅ Новый токен добавлен"
    fi
    echo ""

    # Проверяем, что токен обновился
    if grep -q "BOT_TOKEN=$NEW_TOKEN" "$ENV_FILE"; then
        echo "✅ Токен успешно обновлен в файле"
    else
        echo "❌ Ошибка: токен не обновился"
        echo "Восстанавливаем из резервной копии..."
        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
            sudo cp "$BACKUP_FILE" "$ENV_FILE"
        else
            cp "$BACKUP_FILE" "$ENV_FILE"
        fi
        exit 1
    fi
    echo ""
else
    echo "🔄 Режим перезапуска: токен не изменяется"
    echo ""
fi

# Диалог настройки ИИ - ВСЕГДА показываем (независимо от режима)
echo "🔧 Настройка ИИ:"
echo "1) Обновить API ключи"
echo "2) Сменить провайдера по умолчанию"
echo "3) Оставить как есть"
read -p "❓ Что хотите сделать? (1/2/3, по умолчанию 3): " -r ai_config_choice

if [[ "$ai_config_choice" == "1" ]]; then
    echo "🔑 Обновление API ключей:"
    read -p "❓ Обновить ANTHROPIC_API_KEY? (y/N): " -r update_anthropic
    if [[ "$update_anthropic" =~ ^[Yy]$ ]]; then
        echo "🔑 Введите новый ANTHROPIC_API_KEY (не будет сохранен в истории):"
        read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
        echo ""
        if [ -n "$new_anthropic_key" ]; then
            echo "🔍 Проверка введенного ключа:"
            echo "   Начало: ${new_anthropic_key:0:10}..."
            echo "   Конец: ...${new_anthropic_key: -10}"
            echo "   Длина: ${#new_anthropic_key} символов"
            echo ""
            
            read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
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
                echo "✅ ANTHROPIC_API_KEY обновлен"
            else
                echo "❌ Ключ не обновлен, попробуйте снова"
            fi
        else
            echo "⚠️  Ключ не введен, ANTHROPIC_API_KEY не обновлен"
        fi
    fi
    
    read -p "❓ Обновить OPENAI_API_KEY? (y/N): " -r update_openai
    if [[ "$update_openai" =~ ^[Yy]$ ]]; then
        echo "🔑 Введите новый OPENAI_API_KEY (не будет сохранен в истории):"
        read -s -p "OPENAI_API_KEY: " new_openai_key
        echo ""
        if [ -n "$new_openai_key" ]; then
            echo "🔍 Проверка введенного ключа:"
            echo "   Начало: ${new_openai_key:0:10}..."
            echo "   Конец: ...${new_openai_key: -10}"
            echo "   Длина: ${#new_openai_key} символов"
            echo ""
            
            read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
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
                echo "✅ OPENAI_API_KEY обновлен"
            else
                echo "❌ Ключ не обновлен, попробуйте снова"
            fi
        else
            echo "⚠️  Ключ не введен, OPENAI_API_KEY не обновлен"
        fi
    fi
    

    
elif [[ "$ai_config_choice" == "2" ]]; then
    echo "🤖 Смена провайдера ИИ по умолчанию:"
    echo "1) Anthropic (Claude) - рекомендуется"
    echo "2) OpenAI (GPT)"
    read -p "❓ Выберите нового провайдера (1/2): " -r new_provider_choice
    
    if [[ "$new_provider_choice" == "2" ]]; then
        NEW_DEFAULT_PROVIDER="openai"
        echo "✅ Выбран OpenAI как новый провайдер по умолчанию"
    else
        NEW_DEFAULT_PROVIDER="anthropic"
        echo "✅ Выбран Anthropic как новый провайдер по умолчанию"
    fi
    
    # Обновляем DEFAULT_AI_PROVIDER в файле
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        sudo sed -i "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
    else
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
        else
            sed -i "s/DEFAULT_AI_PROVIDER=.*/DEFAULT_AI_PROVIDER=$NEW_DEFAULT_PROVIDER/" "$ENV_FILE"
        fi
    fi
    
    echo "✅ Провайдер по умолчанию изменен на $NEW_DEFAULT_PROVIDER"
    
    # Проверяем, есть ли API ключ для нового провайдера
    if [[ "$NEW_DEFAULT_PROVIDER" == "anthropic" ]]; then
        echo "🔍 Проверяем ANTHROPIC_API_KEY для нового провайдера..."
        
        # Проверяем, есть ли уже рабочий ключ
        if grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
            CURRENT_ANTHROPIC_KEY=$(grep "ANTHROPIC_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)
            echo "✅ Найден существующий ANTHROPIC_API_KEY: ${CURRENT_ANTHROPIC_KEY:0:10}..."
            echo ""
            echo "❓ Что хотите сделать с ANTHROPIC_API_KEY?"
            echo "1) Оставить существующий ключ"
            echo "2) Ввести новый ключ"
            read -p "Выберите (1/2): " -r key_choice_anthropic
            
            if [[ "$key_choice_anthropic" == "2" ]]; then
                echo "🔑 Введите новый ANTHROPIC_API_KEY (не будет сохранен в истории):"
                read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
                echo ""
                if [ -n "$new_anthropic_key" ]; then
                    echo "🔍 Проверка введенного ключа:"
                    echo "   Начало: ${new_anthropic_key:0:10}..."
                    echo "   Конец: ...${new_anthropic_key: -10}"
                    echo "   Длина: ${#new_anthropic_key} символов"
                    echo ""
                    
                    read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        fi
                        echo "✅ ANTHROPIC_API_KEY обновлен"
                    else
                        echo "❌ Ключ не обновлен, попробуйте снова"
                    fi
                else
                    echo "⚠️  Ключ не введен, ANTHROPIC_API_KEY не обновлен"
                fi
            else
                echo "✅ Существующий ANTHROPIC_API_KEY сохранен"
            fi
        else
            echo "⚠️  Для работы с Anthropic нужен ANTHROPIC_API_KEY"
            read -p "❓ Добавить ANTHROPIC_API_KEY сейчас? (y/N): " -r add_anthropic
            if [[ "$add_anthropic" =~ ^[Yy]$ ]]; then
                echo "🔑 Введите ANTHROPIC_API_KEY (не будет сохранен в истории):"
                read -s -p "ANTHROPIC_API_KEY: " new_anthropic_key
                echo ""
                if [ -n "$new_anthropic_key" ]; then
                    echo "🔍 Проверка введенного ключа:"
                    echo "   Начало: ${new_anthropic_key:0:10}..."
                    echo "   Конец: ...${new_anthropic_key: -10}"
                    echo "   Длина: ${#new_anthropic_key} символов"
                    echo ""
                    
                    read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$new_anthropic_key/" "$ENV_FILE"
                        fi
                        echo "✅ ANTHROPIC_API_KEY добавлен"
                    else
                        echo "❌ Ключ не добавлен, попробуйте снова"
                    fi
                else
                    echo "⚠️  Ключ не введен, ANTHROPIC_API_KEY не добавлен"
                fi
            fi
        fi
        
    elif [[ "$NEW_DEFAULT_PROVIDER" == "openai" ]]; then
        echo "🔍 Проверяем OPENAI_API_KEY для нового провайдера..."
        
        # Проверяем, есть ли уже рабочий ключ
        if grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
            CURRENT_OPENAI_KEY=$(grep "OPENAI_API_KEY=" "$ENV_FILE" | cut -d'=' -f2)
            echo "✅ Найден существующий OPENAI_API_KEY: ${CURRENT_OPENAI_KEY:0:10}..."
            echo ""
            echo "❓ Что хотите сделать с OPENAI_API_KEY?"
            echo "1) Оставить существующий ключ"
            echo "2) Ввести новый ключ"
            read -p "Выберите (1/2): " -r key_choice_openai
            
            if [[ "$key_choice_openai" == "2" ]]; then
                echo "🔑 Введите новый OPENAI_API_KEY (не будет сохранен в истории):"
                read -s -p "OPENAI_API_KEY: " new_openai_key
                echo ""
                if [ -n "$new_openai_key" ]; then
                    echo "🔍 Проверка введенного ключа:"
                    echo "   Начало: ${new_openai_key:0:10}..."
                    echo "   Конец: ...${new_openai_key: -10}"
                    echo "   Длина: ${#new_openai_key} символов"
                    echo ""
                    
                    read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        fi
                        echo "✅ OPENAI_API_KEY обновлен"
                    else
                        echo "❌ Ключ не обновлен, попробуйте снова"
                    fi
                else
                    echo "⚠️  Ключ не введен, OPENAI_API_KEY не обновлен"
                fi
            else
                echo "✅ Существующий OPENAI_API_KEY сохранен"
            fi
        else
            echo "⚠️  Для работы с OpenAI нужен OPENAI_API_KEY"
            read -p "❓ Добавить OPENAI_API_KEY сейчас? (y/N): " -r add_openai
            if [[ "$add_openai" =~ ^[Yy]$ ]]; then
                echo "🔑 Введите OPENAI_API_KEY (не будет сохранен в истории):"
                read -s -p "OPENAI_API_KEY: " new_openai_key
                echo ""
                if [ -n "$new_openai_key" ]; then
                    echo "🔍 Проверка введенного ключа:"
                    echo "   Начало: ${new_openai_key:0:10}..."
                    echo "   Конец: ...${new_openai_key: -10}"
                    echo "   Длина: ${#new_openai_key} символов"
                    echo ""
                    
                    read -p "❓ Ключ введен правильно? (y/N): " -r confirm_key
                    if [[ "$confirm_key" =~ ^[Yy]$ ]]; then
                        if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
                            sudo sed -i "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        else
                            sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=$new_openai_key/" "$ENV_FILE"
                        fi
                        echo "✅ OPENAI_API_KEY добавлен"
                    else
                        echo "❌ Ключ не добавлен, попробуйте снова"
                    fi
                else
                    echo "⚠️  Ключ не введен, OPENAI_API_KEY не добавлен"
                fi
            fi
        fi
    fi
    
else
    echo "✅ Настройки ИИ оставлены без изменений"
fi

echo ""

# Настройка предпочтений поиска фактов (локаль и язык результатов)
echo "🌐 Настройка поиска фактов (источники и язык результатов):"
echo "1) Установить рекомендуемые значения (EN приоритет)"
echo "2) Ввести свои значения"
echo "3) Оставить как есть (по умолчанию)"
read -p "❓ Что хотите сделать? (1/2/3, по умолчанию 3): " -r fact_cfg_choice

if [[ "$fact_cfg_choice" == "1" ]] || [[ "$fact_cfg_choice" == "2" ]]; then
    if [[ "$fact_cfg_choice" == "1" ]]; then
        NEW_FACTCHECK_REGION="us-en"
        NEW_FACTCHECK_ACCEPT_LANGUAGE="en-US,en;q=0.9,ru;q=0.3"
        NEW_FACTCHECK_ALLOWED_LANGS="en,ru"
    else
        echo ""
        read -p "FACTCHECK_REGION (напр. us-en): " -r NEW_FACTCHECK_REGION
        read -p "FACTCHECK_ACCEPT_LANGUAGE (напр. en-US,en;q=0.9,ru;q=0.3): " -r NEW_FACTCHECK_ACCEPT_LANGUAGE
        read -p "FACTCHECK_ALLOWED_LANGS (напр. en,ru): " -r NEW_FACTCHECK_ALLOWED_LANGS
        # Значения по умолчанию, если оставили пустым
        NEW_FACTCHECK_REGION=${NEW_FACTCHECK_REGION:-us-en}
        NEW_FACTCHECK_ACCEPT_LANGUAGE=${NEW_FACTCHECK_ACCEPT_LANGUAGE:-en-US,en;q=0.9,ru;q=0.3}
        NEW_FACTCHECK_ALLOWED_LANGS=${NEW_FACTCHECK_ALLOWED_LANGS:-en,ru}
    fi

    echo "✏️ Применяю настройки поиска фактов в $ENV_FILE..."

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

    # Обновляем основной файл окружения
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" "$ENV_FILE" true
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" "$ENV_FILE" true
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" "$ENV_FILE" true
    else
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" "$ENV_FILE" false
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" "$ENV_FILE" false
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" "$ENV_FILE" false
    fi

    # Также обновляем локальный .env для Docker Compose (если существует)
    if [ -f ".env" ]; then
        update_kv "FACTCHECK_REGION" "$NEW_FACTCHECK_REGION" ".env" false
        update_kv "FACTCHECK_ACCEPT_LANGUAGE" "$NEW_FACTCHECK_ACCEPT_LANGUAGE" ".env" false
        update_kv "FACTCHECK_ALLOWED_LANGS" "$NEW_FACTCHECK_ALLOWED_LANGS" ".env" false
    fi

    echo "✅ Настройки поиска фактов применены"
    echo "   FACTCHECK_REGION=$NEW_FACTCHECK_REGION"
    echo "   FACTCHECK_ACCEPT_LANGUAGE=$NEW_FACTCHECK_ACCEPT_LANGUAGE"
    echo "   FACTCHECK_ALLOWED_LANGS=$NEW_FACTCHECK_ALLOWED_LANGS"
else
    echo "ℹ️ Настройки поиска фактов оставлены без изменений"
fi

#
# Пересобираем образ БЕЗ кэша (всегда, кроме отмены)
echo "🔨 Пересобираем Docker образ БЕЗ кэша..."
if [ "$DOCKER_AVAILABLE" = true ]; then
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        # Серверный режим
        sudo docker build --no-cache -t "$IMAGE_NAME" .
    else
        # Локальный режим
        docker build --no-cache -t "$IMAGE_NAME" .
    fi
    echo "✅ Образ успешно пересобран"
else
    echo "❌ Docker не установлен. Установите Docker и Docker Compose, затем повторите команду."
    echo "   Документация: https://docs.docker.com/engine/install/"
    echo "   Быстрый старт (Ubuntu/Debian):"
    echo "     apt-get update && apt-get install -y ca-certificates curl gnupg"
    echo "     install -m 0755 -d /etc/apt/keyrings"
    echo "     curl -fsSL https://download.docker.com/linux/$(. /etc/os-release; echo \$ID)/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg"
    echo "     chmod a+r /etc/apt/keyrings/docker.gpg"
    echo "     echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(. /etc/os-release; echo \$ID) \$(. /etc/os-release; echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list"
    echo "     apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    exit 1
fi

# Встроенная функция генерации API ключа (fallback если api_key.sh отсутствует)
generate_api_key() {
    python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
    openssl rand -hex 32 2>/dev/null || \
    head -c 32 /dev/urandom | xxd -p
}

# Функция установки API ключа в .env
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

# Управление API ключом для ApiAi (ВСЕГДА показываем)
echo ""
echo "🔑 API ключ для ApiAi:"

# Показать текущий ключ
CURRENT_API_KEY=$(grep "^API_SECRET_KEY=" .env 2>/dev/null | cut -d'=' -f2)
if [ -n "$CURRENT_API_KEY" ] && [ "$CURRENT_API_KEY" != "your_very_long_random_secret_key_here_64_chars_minimum" ]; then
    echo "   Текущий: ${CURRENT_API_KEY:0:16}..."
    echo ""
    read -p "   Сгенерировать новый ключ? (y/N): " -r api_response
    api_response=$(echo "$api_response" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    
    if [[ "$api_response" == "y" ]]; then
        # Используем api_key.sh если доступен, иначе встроенную функцию
        if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
            bash "$SCRIPT_DIR/api_key.sh" generate
        else
            echo "   🔄 Генерация нового API ключа..."
            NEW_API_KEY=$(generate_api_key)
            set_api_key "$NEW_API_KEY" ".env"
            echo "   ✅ Новый ключ сгенерирован: ${NEW_API_KEY:0:16}..."
            echo ""
            echo "   📋 Скопируйте этот ключ в ApiAi:"
            echo "      $NEW_API_KEY"
            echo ""
        fi
    else
        echo "   ✅ Оставляем текущий ключ"
        # Показываем информацию через api_key.sh если доступен
        if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
            bash "$SCRIPT_DIR/api_key.sh" show
        else
            echo "   🔑 Текущий ключ: $CURRENT_API_KEY"
            echo ""
        fi
    fi
else
    echo "   ⚠️  Ключ не настроен, генерируем новый..."
    # Используем api_key.sh если доступен, иначе встроенную функцию
    if [ -f "$SCRIPT_DIR/api_key.sh" ]; then
        bash "$SCRIPT_DIR/api_key.sh" generate
    else
        NEW_API_KEY=$(generate_api_key)
        set_api_key "$NEW_API_KEY" ".env"
        echo "   ✅ Новый ключ сгенерирован: ${NEW_API_KEY:0:16}..."
        echo ""
        echo "   📋 Скопируйте этот ключ в ApiAi:"
        echo "      $NEW_API_KEY"
        echo ""
    fi
fi

# Запускаем новый контейнер
echo "🚀 Запускаем новый контейнер..."

# Проверяем, используется ли Docker Compose
if [ "$DOCKER_AVAILABLE" = true ]; then
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Запускаем через Docker Compose"
        
        # Используем современную команду docker compose
        $COMPOSE_CMD up -d
    else
        # Используем обычную Docker команду
        $DOCKER_CMD run -d \
            --name "$CONTAINER_NAME" \
            --env-file "$ENV_FILE" \
            --restart unless-stopped \
            "$IMAGE_NAME"
    fi
    
    # Держим секреты недоступными для посторонних пользователей системы
    if [ -f ".env" ]; then
        chmod 600 .env
        echo "✅ Права на .env установлены (600)"
    fi
    
    # Устанавливаем права на app_keys.json чтобы контейнер мог сохранять ключи
    if [ -f "app_keys.json" ]; then
        chmod 600 app_keys.json
        echo "✅ Права на app_keys.json установлены (600)"
    elif [ ! -f "app_keys.json" ]; then
        # Создаем файл если его нет
        echo '{"app_keys": {}, "default": {}}' > app_keys.json
        chmod 600 app_keys.json
        echo "✅ Создан app_keys.json с правами 600"
    fi
fi

# Проверяем статус запуска
echo ""
echo "📊 Проверяем статус запуска..."
sleep 5

# Определяем реальное имя контейнера
# В compose.yaml задано container_name: telegram-helper-lite
ACTUAL_CONTAINER_NAME="$CONTAINER_NAME"

echo "🔍 Ищем контейнер: $ACTUAL_CONTAINER_NAME"

if [ "$DOCKER_AVAILABLE" = true ] && $DOCKER_CMD ps -q -f name="$ACTUAL_CONTAINER_NAME" | grep -q .; then
    echo "✅ Контейнер успешно запущен"
    
    # Показываем информацию о контейнере
    echo ""
    echo "📋 Информация о контейнере:"
    $DOCKER_CMD ps | grep "$ACTUAL_CONTAINER_NAME"
    
    # Проверяем переменные окружения
    echo ""
    echo "🔍 Проверяем переменные окружения:"
    if $DOCKER_CMD exec "$ACTUAL_CONTAINER_NAME" env | grep -q "BOT_TOKEN"; then
        echo "✅ BOT_TOKEN найден в контейнере"
        echo "Токен: $($DOCKER_CMD exec "$ACTUAL_CONTAINER_NAME" env | grep BOT_TOKEN | cut -d'=' -f2 | cut -c1-10)..."
    else
        echo "❌ BOT_TOKEN не найден в контейнере"
    fi
    
    
    # Показываем последние логи
    echo ""
    echo "📝 Последние логи контейнера:"
    $DOCKER_CMD logs --tail 10 "$ACTUAL_CONTAINER_NAME"
    
else
    echo "❌ Ошибка: контейнер не запустился"
    echo ""
    echo "📝 Логи ошибки:"
    $DOCKER_CMD logs "$ACTUAL_CONTAINER_NAME" 2>/dev/null || echo "Логи недоступны"
    
    echo ""
    echo "🔄 Восстанавливаем из резервной копии..."
    if [ "$ENV_FILE" = "/etc/telegramhelper.env" ]; then
        sudo cp "$BACKUP_FILE" "$ENV_FILE"
    else
        cp "$BACKUP_FILE" "$ENV_FILE"
    fi
    
    echo "🔄 Перезапускаем старый контейнер..."
    
    # Проверяем, используется ли Docker Compose
    if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
        echo "🐳 Восстанавливаем через Docker Compose"
        
        # Используем современную команду docker compose
        $COMPOSE_CMD up -d
    else
        # Используем обычную Docker команду
        $DOCKER_CMD run -d \
            --name "$CONTAINER_NAME" \
            --env-file "$ENV_FILE" \
            --restart unless-stopped \
            "$IMAGE_NAME"
    fi
    
    exit 1
fi

echo ""

# Финальное сообщение в зависимости от режима
if [ "$RESTART_ONLY" = true ]; then
    echo "🎉 Контейнер успешно перезапущен!"
    echo ""
    echo "📋 Что было сделано:"
    echo "   • Создана резервная копия: $BACKUP_FILE"
    echo "   • Токен НЕ изменялся"
    echo "   • Настройки ИИ обновлены (если были изменены)"
    echo "   • Пересобран Docker образ БЕЗ кэша"
    echo "   • Перезапущен контейнер: $CONTAINER_NAME"
else
    echo "🎉 Токен успешно обновлен!"
    echo ""
    echo "📋 Что было сделано:"
    echo "   • Создана резервная копия: $BACKUP_FILE"
    echo "   • Обновлен токен в: $ENV_FILE"
    echo "   • Настройки ИИ обновлены (если были изменены)"
    echo "   • Пересобран Docker образ БЕЗ кэша"
    echo "   • Перезапущен контейнер: $CONTAINER_NAME"
fi
echo ""
echo "🔍 Для проверки работы бота:"
echo "   • Отправьте /start боту в Telegram"
if [ -f "compose.yaml" ] || [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
    echo "   • Проверьте логи: $COMPOSE_CMD logs -f"
    echo "   • Проверьте статус: $DOCKER_CMD ps"
else
    echo "   • Проверьте логи: $DOCKER_CMD logs -f $CONTAINER_NAME"
    echo "   • Проверьте статус: $DOCKER_CMD ps | grep $CONTAINER_NAME"
fi
echo ""
echo "🤖 Для работы команды /ai:"
# Проверяем текущие настройки ИИ
if grep -q "DEFAULT_AI_PROVIDER=" "$ENV_FILE"; then
    CURRENT_PROVIDER=$(grep "DEFAULT_AI_PROVIDER=" "$ENV_FILE" | cut -d'=' -f2)
    echo "   ✅ ИИ настроен:"
    echo "      • Провайдер: $CURRENT_PROVIDER"
    
    if grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
        echo "      • ANTHROPIC_API_KEY настроен"
    else
        echo "      • ⚠️  ANTHROPIC_API_KEY не настроен"
    fi
    
    if grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
        echo "      • OPENAI_API_KEY настроен"
    else
        echo "      • ⚠️  OPENAI_API_KEY не настроен"
    fi
    
    if [[ "$CURRENT_PROVIDER" == "anthropic" ]] && grep -q "ANTHROPIC_API_KEY=" "$ENV_FILE" && ! grep -q "ANTHROPIC_API_KEY=your_anthropic_api_key" "$ENV_FILE"; then
        echo "   🚀 Команда /ai должна работать с Claude!"
    elif [[ "$CURRENT_PROVIDER" == "openai" ]] && grep -q "OPENAI_API_KEY=" "$ENV_FILE" && ! grep -q "OPENAI_API_KEY=your_openai_api_key" "$ENV_FILE"; then
        echo "   🚀 Команда /ai должна работать с GPT!"
    else
        echo "   ⚠️  Для работы /ai нужен API ключ для провайдера $CURRENT_PROVIDER"
    fi
else
    echo "   ❌ DEFAULT_AI_PROVIDER не найден в $ENV_FILE"
    echo "   ⚠️  Настройте ИИ вручную для работы команды /ai"
fi
echo ""
echo "🛡️ VLESS-Reality / Xray:"
# Проверяем статус Xray на хосте
if command -v xray &> /dev/null; then
    XRAY_VERSION=$(xray version 2>&1 | head -1)
    echo "   ✅ Xray установлен: $XRAY_VERSION"
    
    # Проверяем статус службы
    if systemctl is-active --quiet xray 2>/dev/null; then
        echo "   ✅ Xray запущен"
    else
        echo "   ⚠️  Xray не запущен"
        echo "   Для запуска: systemctl start xray"
    fi
    
    # Проверяем порт 443
    if ss -tlnp 2>/dev/null | grep -q ":443 "; then
        echo "   ✅ Порт 443 слушается"
    else
        echo "   ⚠️  Порт 443 не слушается"
    fi
else
    echo "   ❌ Xray не установлен"
    echo "   Для установки:"
    echo "   bash -c \"\\\$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install"
fi
echo ""
echo "   📋 Настройка VLESS-Reality:"
echo "      1. /vless_sync         - сгенерировать ключи"
echo "      2. /vless_export       - получить '🖥️ Xray Server Config'"
echo "      3. SSH: nano /usr/local/etc/xray/config.json"
echo "         (вставить JSON, Ctrl+O, Ctrl+X)"
echo "      4. SSH: systemctl restart xray"
echo "      5. /vless_test         - проверить подключение"
echo ""
echo "🔄 Для отката изменений:"
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
echo "🔒 Рекомендации по безопасности:"
echo "   • 🥇 ИНТЕРАКТИВНЫЙ ВВОД (самый безопасный): просто запустите ./scripts/change_token.sh"
echo "   • 🥈 ВРЕМЕННЫЙ ФАЙЛ: echo \"токен\" > /tmp/new_bot_token.txt && ./scripts/change_token.sh"
echo "   • 🥉 Переменная окружения: export NEW_BOT_TOKEN=\"токен\" && ./scripts/change_token.sh"
echo "   • 🥉 Аргумент командной строки: ./scripts/change_token.sh токен (НЕ РЕКОМЕНДУЕТСЯ!)"
echo ""
echo "🔍 Проверка безопасности:"
echo "   • Проверьте историю команд: history | grep -i token"
echo "   • При необходимости очистите историю: history -d <номер_команды>"
echo "   • Очистите переменные: unset NEW_BOT_TOKEN"
