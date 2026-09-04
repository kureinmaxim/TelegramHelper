#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для просмотра версии проекта на сервере.

Использование:
    python3 scripts/show_version.py
"""

import os
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_version_from_pyproject():
    """Получить версию из pyproject.toml"""
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("version"):
                        # version = "3.1.1"
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception as e:
            return f"Ошибка: {e}"
    return "Не найден"


def get_git_info():
    """Получить информацию о git"""
    import subprocess
    
    info = {}
    
    try:
        # Текущая ветка
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        info["branch"] = result.stdout.strip() if result.returncode == 0 else "N/A"
        
        # Последний коммит
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit"] = result.stdout.strip() if result.returncode == 0 else "N/A"
        
        # Дата последнего коммита
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit_date"] = result.stdout.strip() if result.returncode == 0 else "N/A"
        
        # Сообщение последнего коммита
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True, text=True, cwd=project_root
        )
        info["commit_message"] = result.stdout.strip() if result.returncode == 0 else "N/A"
        
        # Статус (есть ли незакоммиченные изменения)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_root
        )
        if result.returncode == 0:
            info["dirty"] = bool(result.stdout.strip())
        else:
            info["dirty"] = None
            
    except FileNotFoundError:
        info["error"] = "git не установлен"
    except Exception as e:
        info["error"] = str(e)
    
    return info


def get_allowed_apps():
    """Получить список разрешённых приложений (парсинг без импорта)"""
    # Читаем security.py напрямую, чтобы не требовать fastapi и другие зависимости
    security_file = project_root / "security.py"
    
    if not security_file.exists():
        return "Файл security.py не найден"
    
    try:
        with open(security_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Ищем ALLOWED_APPS = { ... }
        import re
        
        # Паттерн для поиска app_id в кавычках (не закомментированных)
        # Ищем строки вида: "app-id": {
        apps = []
        in_allowed_apps = False
        brace_count = 0
        
        for line in content.split('\n'):
            stripped = line.strip()
            
            # Пропускаем комментарии
            if stripped.startswith('#'):
                continue
            
            # Начало ALLOWED_APPS
            if 'ALLOWED_APPS' in line and '=' in line and '{' in line:
                in_allowed_apps = True
                brace_count = line.count('{') - line.count('}')
                continue
            
            if in_allowed_apps:
                brace_count += line.count('{') - line.count('}')
                
                # Ищем app_id (строка в кавычках перед двоеточием)
                match = re.match(r'\s*["\']([a-zA-Z0-9_-]+)["\']\s*:\s*\{', line)
                if match:
                    apps.append(match.group(1))
                
                # Конец ALLOWED_APPS
                if brace_count <= 0:
                    break
        
        return apps if apps else "Не найдено ни одного приложения"
        
    except Exception as e:
        return f"Ошибка парсинга: {e}"


def get_docker_info():
    """Проверить статус Docker контейнера."""
    # 1. Проверяем, внутри ли мы контейнера
    if os.path.exists("/.dockerenv"):
        return "Внутри контейнера (✅ Active)"
    
    try:
        with open("/proc/1/cgroup", "r") as f:
            if "docker" in f.read():
                return "Внутри контейнера (✅ Active)"
    except:
        pass
    
    # 2. Если мы на хосте, проверяем запущен ли контейнер
    try:
        import subprocess
        # Ищем контейнеры проекта (telegram и dockhand)
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            all_containers = result.stdout.strip().split('\n')
            # Фильтруем только наши
            containers = [c for c in all_containers if "telegram" in c or "dockhand" in c]
            
            if containers:
                return f"Контейнеры запущены: {', '.join(containers)} ✅"
            else:
                return "Контейнер НЕ запущен ❌ (но Docker установлен)"
        else:
            return "Ошибка проверки Docker (возможно, нет прав)"
    except FileNotFoundError:
        return "Docker не установлен ❌"
    except Exception as e:
        return f"Ошибка: {e}"


def main():
    print("=" * 60)
    print("📦 Информация о версии TelegramHelper")
    print("=" * 60)
    
    # Версия из pyproject.toml
    version = get_version_from_pyproject()
    print(f"\n🏷️  Версия: {version}")
    
    # Путь к проекту
    print(f"📁 Путь: {project_root}")
    
    # Docker
    docker_info = get_docker_info()
    print(f"🐳 Docker: {docker_info}")
    
    # Git информация
    print("\n" + "-" * 60)
    print("📊 Git информация:")
    print("-" * 60)
    
    git_info = get_git_info()
    
    if "error" in git_info:
        print(f"❌ {git_info['error']}")
    elif git_info.get('branch') == 'N/A':
        print("⚠️  Git репозиторий не найден")
        print("   (папка .git не синхронизируется через rsync)")
        print("   Это нормально для production-сервера.")
    else:
        print(f"🌿 Ветка: {git_info.get('branch', 'N/A')}")
        print(f"🔖 Коммит: {git_info.get('commit', 'N/A')}")
        print(f"📅 Дата: {git_info.get('commit_date', 'N/A')}")
        print(f"💬 Сообщение: {git_info.get('commit_message', 'N/A')}")
        
        if git_info.get("dirty"):
            print("⚠️  Есть незакоммиченные изменения!")
        elif git_info.get("dirty") is False:
            print("✅ Рабочая директория чистая")
    
    # Разрешённые приложения
    print("\n" + "-" * 60)
    print("🔐 Разрешённые приложения (ALLOWED_APPS):")
    print("-" * 60)
    
    apps = get_allowed_apps()
    if isinstance(apps, list):
        for app_id in apps:
            print(f"  • {app_id}")
    else:
        print(f"❌ {apps}")
    
    # Python версия
    print("\n" + "-" * 60)
    print("🐍 Python информация:")
    print("-" * 60)
    print(f"  Версия: {sys.version}")
    print(f"  Путь: {sys.executable}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
