#!/bin/bash
# Скрипт для деплоя бота на сервер

# Настройки по умолчанию
SERVER_USER="root"
SERVER_HOST="YOUR_SERVER_IP"
SERVER_PATH="/opt/TelegramHelper"
SSH_PORT="YOUR_SSH_PORT"  # Укажите ваш SSH порт

# Проверяем аргументы
if [ "$#" -lt 1 ]; then
  echo "Использование: $0 <server_user@server_host> [server_path] [ssh_port]"
  echo "Пример: $0 root@YOUR_SERVER_IP /opt/TelegramSimple YOUR_SSH_PORT"
  echo ""
  echo "Или без аргументов для использования значений по умолчанию:"
  echo "  Сервер: $SERVER_USER@$SERVER_HOST:$SSH_PORT"
  echo "  Путь: $SERVER_PATH"
  echo ""
  read -p "Использовать значения по умолчанию? (Y/n): " use_defaults
  if [ "$use_defaults" = "n" ] || [ "$use_defaults" = "N" ]; then
    exit 1
  fi
fi

# Парсим аргументы
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
echo "🚀 Деплой TelegramSimple"
echo "========================================"
echo "📌 Сервер: $SERVER_USER@$SERVER_HOST:$SSH_PORT"
echo "📁 Путь: $SERVER_PATH"
echo ""

# Проверяем доступность сервера
echo "🔍 Проверяем соединение с сервером..."
ssh $SSH_OPTS -q $SERVER_USER@$SERVER_HOST exit
if [ $? -ne 0 ]; then
  echo "❌ Не удалось подключиться к серверу"
  exit 1
fi
echo "✅ Соединение установлено"

# Создаем директорию на сервере, если не существует
echo "📂 Создаем директорию на сервере..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "mkdir -p $SERVER_PATH"

# Копируем файлы проекта на сервер
echo "📤 Копируем файлы на сервер..."
rsync -avz --delete \
  --exclude 'venv' --exclude '.env' --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'bot.log' \
  --exclude 'app_keys.json' --exclude 'users.json' --exclude 'vless_config.json' \
  -e "ssh $SSH_OPTS" \
  ./ $SERVER_USER@$SERVER_HOST:$SERVER_PATH/

# Проверяем наличие .env на сервере
echo "⚙️ Проверяем настройки на сервере..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "if [ ! -f $SERVER_PATH/.env ]; then cp $SERVER_PATH/example.env $SERVER_PATH/.env; echo '⚠️ Создан файл .env из примера. Пожалуйста, отредактируйте его!'; fi"

# Проверяем наличие Docker на сервере
echo "🐳 Проверяем Docker на сервере..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "command -v docker >/dev/null 2>&1 || { echo '❌ Docker не установлен'; exit 1; }"

# Создаём файлы данных и перезапускаем контейнер
echo "🔄 Перезапускаем контейнер..."
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "cd $SERVER_PATH && \
  if [ ! -f app_keys.json ]; then echo '{\"app_keys\": {}, \"default\": {}}' > app_keys.json; fi && \
  if [ ! -f users.json ]; then echo '{}' > users.json; fi && \
  if [ ! -f vless_config.json ]; then echo '{}' > vless_config.json; fi && \
  chmod 600 app_keys.json users.json vless_config.json 2>/dev/null; \
  docker compose down && docker compose up -d --build"

# Ждём запуска и показываем версию
echo ""
echo "⏳ Ожидаем запуска контейнера..."
sleep 3

echo ""
echo "========================================"
echo "📊 Проверка версии на сервере"
echo "========================================"
ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST "cd $SERVER_PATH && python3 scripts/show_version.py"

echo ""
echo "========================================"
echo "✅ Деплой завершён!"
echo "========================================"
echo ""
echo "Полезные команды:"
echo "  📋 Логи: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && docker compose logs -f\""
echo "  🔄 Рестарт: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && docker compose restart\""
echo "  📊 Версия: ssh $SSH_OPTS $SERVER_USER@$SERVER_HOST \"cd $SERVER_PATH && python3 scripts/show_version.py\""

