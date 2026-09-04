#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔐 Secure Config Transfer — Безопасная передача VLESS конфигурации

Шифрует vless_client_config.json для безопасной передачи через
небезопасные каналы (email, мессенджеры, cloud storage).

Использует AES-256-GCM (совместим с SecureMessenger из encryption.py).

Использование:
    # Шифрование
    python3 secure_config_transfer.py encrypt config.json
    python3 secure_config_transfer.py encrypt config.json --output encrypted.bin
    
    # Расшифровка  
    python3 secure_config_transfer.py decrypt encrypted.bin
    python3 secure_config_transfer.py decrypt encrypted.bin --output config.json
    
    # С заданным паролем
    python3 secure_config_transfer.py encrypt config.json --password "mypass"
    
    # Генерация пароля
    python3 secure_config_transfer.py generate-password
"""

import os
import sys
import json
import base64
import hashlib
import argparse
import getpass
from pathlib import Path
from typing import Tuple, Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("❌ Требуется библиотека cryptography")
    print("   pip install cryptography")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# Константы
# ═══════════════════════════════════════════════════════════════

NONCE_SIZE = 12  # 96 бит для AES-GCM (стандарт NIST)
SALT_SIZE = 16   # Для PBKDF2
ITERATIONS = 100_000  # PBKDF2 итерации

# Магические байты для идентификации формата
MAGIC_BYTES = b'VLESS_ENC_V1'


# ═══════════════════════════════════════════════════════════════
# Криптографические функции
# ═══════════════════════════════════════════════════════════════

def derive_key(password: str, salt: bytes) -> bytes:
    """
    Получить 256-битный ключ из пароля используя PBKDF2-SHA256.
    
    Args:
        password: Пароль пользователя
        salt: Случайная соль (16 байт)
    
    Returns:
        32-байтный ключ для AES-256
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    
    return kdf.derive(password.encode('utf-8'))


def encrypt_data(data: bytes, password: str) -> bytes:
    """
    Шифрует данные с помощью AES-256-GCM.
    
    Формат выходных данных:
    [MAGIC_BYTES(12)][SALT(16)][NONCE(12)][CIPHERTEXT+TAG]
    
    Args:
        data: Данные для шифрования
        password: Пароль
    
    Returns:
        Зашифрованный пакет
    """
    # Генерируем соль и nonce
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    
    # Получаем ключ из пароля
    key = derive_key(password, salt)
    
    # Шифруем
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    
    # Формируем пакет
    return MAGIC_BYTES + salt + nonce + ciphertext


def decrypt_data(encrypted: bytes, password: str) -> bytes:
    """
    Расшифровывает данные AES-256-GCM.
    
    Args:
        encrypted: Зашифрованный пакет
        password: Пароль
    
    Returns:
        Расшифрованные данные
    
    Raises:
        ValueError: При неверном формате или пароле
    """
    # Проверяем магические байты
    if not encrypted.startswith(MAGIC_BYTES):
        raise ValueError("Неверный формат файла (не VLESS_ENC_V1)")
    
    # Извлекаем компоненты
    offset = len(MAGIC_BYTES)
    salt = encrypted[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = encrypted[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext = encrypted[offset:]
    
    # Получаем ключ из пароля
    key = derive_key(password, salt)
    
    # Расшифровываем
    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError("Неверный пароль или повреждённые данные") from e


def generate_password(length: int = 24) -> str:
    """
    Генерирует криптографически стойкий пароль.
    
    Args:
        length: Длина пароля (по умолчанию 24)
    
    Returns:
        Безопасный пароль
    """
    import secrets
    import string
    
    # Используем буквы, цифры и некоторые спецсимволы
    alphabet = string.ascii_letters + string.digits + "-_!@#"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# ═══════════════════════════════════════════════════════════════
# Команды CLI
# ═══════════════════════════════════════════════════════════════

def cmd_encrypt(args):
    """Команда шифрования"""
    input_path = Path(args.input)
    
    if not input_path.exists():
        print(f"❌ Файл не найден: {input_path}")
        return 1
    
    # Определяем выходной файл
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.enc')
    
    # Получаем пароль
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("🔑 Введите пароль для шифрования: ")
        password2 = getpass.getpass("🔑 Подтвердите пароль: ")
        
        if password != password2:
            print("❌ Пароли не совпадают!")
            return 1
    
    if len(password) < 8:
        print("⚠️  Предупреждение: пароль слишком короткий (рекомендуется 12+ символов)")
    
    # Читаем и шифруем
    print(f"📄 Читаю: {input_path}")
    data = input_path.read_bytes()
    
    print("🔐 Шифрую...")
    encrypted = encrypt_data(data, password)
    
    # Записываем
    output_path.write_bytes(encrypted)
    
    # Также создаём base64 версию для удобства передачи
    b64_path = output_path.with_suffix('.enc.txt')
    b64_data = base64.b64encode(encrypted).decode('utf-8')
    b64_path.write_text(b64_data)
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   ✅ Файл зашифрован!")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📦 Бинарный:  {output_path} ({len(encrypted)} байт)")
    print(f"📝 Base64:    {b64_path} ({len(b64_data)} символов)")
    print()
    print("💡 Для расшифровки:")
    print(f"   python3 {sys.argv[0]} decrypt {output_path}")
    print()
    print("⚠️  ВАЖНО: Передайте пароль ОТДЕЛЬНО от файла!")
    print("   Например: пароль по телефону, файл через email")
    
    return 0


def cmd_decrypt(args):
    """Команда расшифровки"""
    input_path = Path(args.input)
    
    if not input_path.exists():
        print(f"❌ Файл не найден: {input_path}")
        return 1
    
    # Определяем выходной файл
    if args.output:
        output_path = Path(args.output)
    else:
        # Убираем .enc или .enc.txt
        name = input_path.stem
        if name.endswith('.enc'):
            name = name[:-4]
        output_path = input_path.parent / f"{name}_decrypted.json"
    
    # Получаем пароль
    if args.password:
        password = args.password
    else:
        password = getpass.getpass("🔑 Введите пароль для расшифровки: ")
    
    # Читаем файл
    print(f"📄 Читаю: {input_path}")
    data = input_path.read_bytes()
    
    # Проверяем, не base64 ли это
    if not data.startswith(MAGIC_BYTES):
        try:
            # Пробуем декодировать base64
            data = base64.b64decode(data)
        except Exception:
            pass
    
    # Расшифровываем
    print("🔓 Расшифровываю...")
    try:
        decrypted = decrypt_data(data, password)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    
    # Записываем
    output_path.write_bytes(decrypted)
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   ✅ Файл расшифрован!")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📄 Сохранено: {output_path}")
    
    # Показываем содержимое если это JSON
    try:
        config = json.loads(decrypted)
        print()
        print("📋 Содержимое конфигурации:")
        print("───────────────────────────────────────────────────────────────")
        
        if 'vless_link' in config:
            print(f"🔗 VLESS Link: {config['vless_link'][:50]}...")
        if 'server' in config:
            print(f"📍 Server: {config['server']}:{config.get('port', 443)}")
        if 'uuid' in config:
            uuid = config['uuid']
            print(f"🆔 UUID: {uuid[:8]}...{uuid[-4:]}")
            
    except json.JSONDecodeError:
        pass
    
    return 0


def cmd_generate_password(args):
    """Команда генерации пароля"""
    password = generate_password(args.length)
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   🔑 Сгенерирован безопасный пароль")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"   {password}")
    print()
    print("💡 Используйте с --password при шифровании:")
    print(f"   python3 {sys.argv[0]} encrypt config.json --password '{password}'")
    
    return 0


def cmd_info(args):
    """Показать информацию о зашифрованном файле"""
    input_path = Path(args.input)
    
    if not input_path.exists():
        print(f"❌ Файл не найден: {input_path}")
        return 1
    
    data = input_path.read_bytes()
    
    # Проверяем base64
    is_base64 = False
    if not data.startswith(MAGIC_BYTES):
        try:
            data = base64.b64decode(data)
            is_base64 = True
        except Exception:
            print("❌ Файл не является зашифрованным конфигом VLESS")
            return 1
    
    if not data.startswith(MAGIC_BYTES):
        print("❌ Файл не является зашифрованным конфигом VLESS")
        return 1
    
    # Извлекаем метаданные
    offset = len(MAGIC_BYTES)
    salt = data[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = data[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext_len = len(data) - offset
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   📦 Информация о зашифрованном файле")
    print("═══════════════════════════════════════════════════════════════")
    print()
    print(f"📄 Файл: {input_path}")
    print(f"📊 Размер: {len(data)} байт")
    print(f"🏷️  Формат: VLESS_ENC_V1")
    print(f"🔤 Base64: {'Да' if is_base64 else 'Нет'}")
    print()
    print("🔐 Криптография:")
    print(f"   • Алгоритм: AES-256-GCM")
    print(f"   • KDF: PBKDF2-SHA256 ({ITERATIONS:,} итераций)")
    print(f"   • Salt: {SALT_SIZE} байт")
    print(f"   • Nonce: {NONCE_SIZE} байт")
    print(f"   • Шифротекст: {ciphertext_len} байт")
    
    return 0


# ═══════════════════════════════════════════════════════════════
# Точка входа
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🔐 Безопасная передача VLESS конфигурации",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s encrypt vless_config.json
  %(prog)s decrypt vless_config.enc
  %(prog)s generate-password
  %(prog)s info vless_config.enc
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Команда')
    
    # encrypt
    enc_parser = subparsers.add_parser('encrypt', help='Зашифровать файл конфигурации')
    enc_parser.add_argument('input', help='Входной JSON файл')
    enc_parser.add_argument('--output', '-o', help='Выходной файл (по умолчанию: input.enc)')
    enc_parser.add_argument('--password', '-p', help='Пароль (или будет запрошен)')
    
    # decrypt
    dec_parser = subparsers.add_parser('decrypt', help='Расшифровать файл')
    dec_parser.add_argument('input', help='Зашифрованный файл (.enc или .enc.txt)')
    dec_parser.add_argument('--output', '-o', help='Выходной файл')
    dec_parser.add_argument('--password', '-p', help='Пароль (или будет запрошен)')
    
    # generate-password
    gen_parser = subparsers.add_parser('generate-password', help='Сгенерировать безопасный пароль')
    gen_parser.add_argument('--length', '-l', type=int, default=24, help='Длина пароля (по умолчанию: 24)')
    
    # info
    info_parser = subparsers.add_parser('info', help='Информация о зашифрованном файле')
    info_parser.add_argument('input', help='Зашифрованный файл')
    
    args = parser.parse_args()
    
    if args.command == 'encrypt':
        return cmd_encrypt(args)
    elif args.command == 'decrypt':
        return cmd_decrypt(args)
    elif args.command == 'generate-password':
        return cmd_generate_password(args)
    elif args.command == 'info':
        return cmd_info(args)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())

