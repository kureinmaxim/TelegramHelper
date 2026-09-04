#!/bin/bash
# Скрипт для сборки и запуска Docker-контейнера с ботом

# Проверяем наличие .env файла
if [ ! -f .env ]; then
  echo "Файл .env не найден. Создаем из примера..."
  cp example.env .env
  echo "Пожалуйста, отредактируйте файл .env и укажите BOT_TOKEN"
  exit 1
fi

# Проверяем, что BOT_TOKEN установлен в .env
if ! grep -q "BOT_TOKEN=" .env || grep -q "BOT_TOKEN=$" .env || grep -q "BOT_TOKEN=your_token_here" .env; then
  echo "BOT_TOKEN не установлен в файле .env"
  echo "Пожалуйста, отредактируйте файл .env и укажите BOT_TOKEN"
  exit 1
fi

# Собираем Docker-образ
echo "Собираем Docker-образ..."
docker build -t telegram-helper:latest .

# Запускаем контейнер через Docker Compose
echo "Запускаем контейнер через Docker Compose..."
docker compose down
docker compose up -d

# Показываем логи
echo "Контейнер запущен. Показываем логи (Ctrl+C для выхода):"
docker compose logs -f | cat
