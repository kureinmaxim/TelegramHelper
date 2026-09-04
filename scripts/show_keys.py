#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для просмотра активных API и ключей шифрования на сервере.

Использование:
    python3 scripts/show_keys.py
    python3 scripts/show_keys.py --app-id example-app
    python3 scripts/show_keys.py --all
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Импортируем ALLOWED_APPS из security.py
try:
    from security import ALLOWED_APPS
except ImportError:
    # Fallback если security.py недоступен
    ALLOWED_APPS = {
        "example-app": {
            "name": "BOM Categorizer Modern Edition v5",
            "version": "5.x",
        },
        "apiai-v3": {
            "name": "ApiAi Tauri Edition v3",
            "version": "3.x",
        },
        "test-client": {
            "name": "Test Client (Development)",
            "version": "dev",
        }
    }

# Функции для работы с ключами без импорта app_keys
def _load_keys_from_file():
    """Загрузить ключи из файла напрямую"""
    keys_file = project_root / "app_keys.json"
    if not keys_file.exists():
        return {"app_keys": {}, "default": {}}
    try:
        with open(keys_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"app_keys": {}, "default": {}}
    except Exception as e:
        print(f"⚠️  Ошибка чтения app_keys.json: {e}")
        return {"app_keys": {}, "default": {}}

def get_api_key_from_data(app_id: str = None, data: dict = None):
    """Получить API ключ из данных"""
    if data is None:
        data = _load_keys_from_file()
    
    # Если указан app_id и есть индивидуальный ключ
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            api_key = app_keys[app_id].get("api_key")
            if api_key:
                return api_key
    
    # Fallback на дефолтный ключ из app_keys.json
    default_key = data.get("default", {}).get("api_key")
    if default_key:
        return default_key
    
    # Последний fallback - из переменных окружения или .env
    env_key = os.getenv("API_SECRET_KEY")
    if env_key:
        return env_key
    
    # Пытаемся прочитать из .env файла
    env_file = project_root / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("API_SECRET_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    
    return None

def get_encryption_key_from_data(app_id: str = None, data: dict = None):
    """Получить ключ шифрования из данных"""
    if data is None:
        data = _load_keys_from_file()
    
    # Если указан app_id и есть индивидуальный ключ
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            enc_key = app_keys[app_id].get("encryption_key")
            if enc_key:
                return enc_key
    
    # Fallback на дефолтный ключ из app_keys.json
    default_key = data.get("default", {}).get("encryption_key")
    if default_key:
        return default_key
    
    # Последний fallback - из переменных окружения или .env
    env_key = os.getenv("ENCRYPTION_KEY")
    if env_key:
        return env_key
    
    # Пытаемся прочитать из .env файла
    env_file = project_root / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("ENCRYPTION_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    
    # Последний fallback - API_SECRET_KEY
    return get_api_key_from_data(app_id, data)

def list_app_ids_from_data(data: dict = None):
    """Получить список всех app_id с ключами"""
    if data is None:
        data = _load_keys_from_file()
    app_keys = data.get("app_keys", {})
    return list(app_keys.keys())


def allow_full_secret_output() -> bool:
    """Разрешён ли явный full reveal из локального CLI."""
    return os.getenv("TELEGRAMHELPER_ALLOW_SECRET_REVEAL", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def print_key_info(app_id: str = None, show_full: bool = False, data: dict = None):
    """Вывести информацию о ключах для app_id"""
    
    # Загружаем данные если не переданы
    if data is None:
        data = _load_keys_from_file()
    
    # Получаем ключи
    api_key = get_api_key_from_data(app_id, data)
    enc_key = get_encryption_key_from_data(app_id, data)
    
    # Определяем источник ключей
    has_individual = False
    
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            has_individual = True
            app_info = app_keys[app_id]
            created = app_info.get("created_at", "N/A")
            updated = app_info.get("updated_at", "N/A")
    
    # Заголовок
    if app_id:
        app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
        print(f"\n🔑 Ключи для: {app_id} ({app_name})")
        if has_individual:
            print(f"   📅 Создан: {created}")
            print(f"   🔄 Обновлен: {updated}")
            print(f"   ✅ Индивидуальные ключи")
        else:
            print(f"   ⚠️  Используются дефолтные ключи из .env")
    else:
        print(f"\n🔑 Дефолтные ключи (из .env)")
    
    print("-" * 60)
    
    # API ключ
    if api_key:
        if show_full:
            print(f"🔐 API Key: {api_key}")
        else:
            masked = api_key[:8] + "..." + api_key[-8:] if len(api_key) > 16 else "***"
            print(f"🔐 API Key: {masked}")
    else:
        print(f"🔐 API Key: ❌ Не установлен")
    
    # Ключ шифрования
    if enc_key:
        if show_full:
            print(f"🔒 Encryption Key: {enc_key}")
        else:
            masked = enc_key[:8] + "..." + enc_key[-8:] if len(enc_key) > 16 else "***"
            print(f"🔒 Encryption Key: {masked}")
    else:
        print(f"🔒 Encryption Key: ❌ Не установлен")
    
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Просмотр активных API и ключей шифрования",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python3 scripts/show_keys.py                    # Дефолтные ключи
  python3 scripts/show_keys.py --all               # Все ключи
  python3 scripts/show_keys.py --app-id example-app
  python3 scripts/show_keys.py --app-id example-app --full
        """
    )
    
    parser.add_argument(
        "--app-id",
        type=str,
        help="ID приложения (например, example-app)"
    )
    
    parser.add_argument(
        "--all",
        action="store_true",
        help="Показать все ключи для всех app_id"
    )
    
    parser.add_argument(
        "--full",
        action="store_true",
        help="Показать полные ключи (без маскировки)"
    )
    
    args = parser.parse_args()

    if args.full and not allow_full_secret_output():
        print("⛔ Полный вывод секретов отключён по умолчанию.")
        print("Временно включите `TELEGRAMHELPER_ALLOW_SECRET_REVEAL=true` только на локальном сервере, если это действительно нужно.")
        sys.exit(1)
    
    print("=" * 60)
    print("🔍 Просмотр активных API ключей")
    print("=" * 60)
    
    # Проверяем наличие app_keys.json
    keys_file = project_root / "app_keys.json"
    if keys_file.exists():
        print(f"✅ Файл ключей найден: {keys_file}")
    else:
        print(f"⚠️  Файл ключей не найден: {keys_file}")
        print("   Используются только ключи из .env")
    
    print()
    
    if args.all:
        # Загружаем данные один раз
        data = _load_keys_from_file()
        
        # Показываем все ключи
        print("📋 Дефолтные ключи:")
        print_key_info(None, args.full, data)
        
        # Получаем список всех app_id с ключами
        app_ids_with_keys = list_app_ids_from_data(data)
        
        # Также показываем все разрешенные app_id
        print("📋 Индивидуальные ключи:")
        for app_id in ALLOWED_APPS.keys():
            if app_id in app_ids_with_keys:
                print_key_info(app_id, args.full, data)
            else:
                app_name = ALLOWED_APPS.get(app_id, {}).get("name", app_id)
                print(f"\n⚠️  {app_id} ({app_name}): нет индивидуальных ключей")
                print("   Используются дефолтные ключи из .env\n")
        
    elif args.app_id:
        # Проверяем, что app_id разрешен
        if args.app_id not in ALLOWED_APPS:
            print(f"❌ Ошибка: app_id '{args.app_id}' не найден в ALLOWED_APPS")
            print(f"\nРазрешенные app_id:")
            for aid, info in ALLOWED_APPS.items():
                print(f"  - {aid}: {info.get('name', 'N/A')}")
            sys.exit(1)
        
        print_key_info(args.app_id, args.full)
    else:
        # Показываем только дефолтные ключи
        print_key_info(None, args.full)
        print("\n💡 Используйте --all для просмотра всех ключей")
        print("💡 Используйте --app-id <app_id> для конкретного сервиса")


if __name__ == "__main__":
    main()

