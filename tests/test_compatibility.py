#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест совместимости шифрования между compatible AES-256-GCM clients и TelegramHelper.
Этот скрипт имитирует отправку запроса из compatible AES-256-GCM clients.
"""

import sys
import os
from pathlib import Path

# Добавляем путь к модулю encryption из compatible AES-256-GCM clients
project_root = Path(__file__).resolve().parents[1]
bom_path = Path(os.environ.get("BOMCATEGORIZER_PATH", project_root.parent / "compatible AES-256-GCM clients"))
sys.path.insert(0, str(bom_path))

from bom_categorizer.encryption import SecureMessenger as BOMSecureMessenger

# Добавляем путь к модулю encryption из TelegramHelper
telegram_path = project_root
sys.path.insert(0, str(telegram_path))

from encryption import SecureMessenger as TelegramSecureMessenger

import json
import base64

def test_cross_compatibility():
    """Тест кросс-совместимости шифрования"""
    
    print("=" * 70)
    print("🔄 Тест совместимости шифрования compatible AES-256-GCM clients ↔ TelegramHelper")
    print("=" * 70)
    
    # Один ключ для обоих модулей
    test_key = "a" * 64  # 256-bit hex key
    
    # Тестовые данные (как в compatible AES-256-GCM clients)
    test_data = {
        "prompt": "Классифицируй компонент: Резистор С2-23",
        "provider": "anthropic",
        "max_tokens": 1000
    }
    
    print(f"\n📦 Тестовые данные:")
    print(f"   {json.dumps(test_data, ensure_ascii=False)}")
    
    # === ТЕСТ 1: compatible AES-256-GCM clients -> TelegramHelper ===
    print(f"\n" + "─" * 70)
    print("1️⃣ Тест: compatible AES-256-GCM clients шифрует → TelegramHelper расшифровывает")
    print("─" * 70)
    
    try:
        # Инициализация
        bom_messenger = BOMSecureMessenger(test_key)
        telegram_messenger = TelegramSecureMessenger(test_key)
        
        # Шифруем в compatible AES-256-GCM clients
        encrypted_by_bom = bom_messenger.encrypt(test_data)
        print(f"   ✓ compatible AES-256-GCM clients зашифровал: {len(encrypted_by_bom)} байт")
        print(f"   ✓ Формат: nonce({encrypted_by_bom[:12].hex()[:20]}...) + ciphertext")
        
        # Расшифровываем в TelegramHelper
        decrypted_by_telegram = telegram_messenger.decrypt(encrypted_by_bom)
        result = json.loads(decrypted_by_telegram.decode('utf-8'))
        print(f"   ✓ TelegramHelper расшифровал: {json.dumps(result, ensure_ascii=False)}")
        
        if result == test_data:
            print(f"   ✅ УСПЕХ: Данные совпадают!")
        else:
            print(f"   ❌ ОШИБКА: Данные не совпадают!")
            return False
            
    except Exception as e:
        print(f"   ❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # === ТЕСТ 2: TelegramHelper -> compatible AES-256-GCM clients ===
    print(f"\n" + "─" * 70)
    print("2️⃣ Тест: TelegramHelper шифрует → compatible AES-256-GCM clients расшифровывает")
    print("─" * 70)
    
    try:
        # Тестовый ответ от API
        response_data = {
            "response": "Категория: resistors, Уверенность: high",
            "provider": "anthropic",
            "status": "success"
        }
        
        # Шифруем в TelegramHelper
        encrypted_by_telegram = telegram_messenger.encrypt(response_data)
        print(f"   ✓ TelegramHelper зашифровал: {len(encrypted_by_telegram)} байт")
        
        # Расшифровываем в compatible AES-256-GCM clients
        decrypted_by_bom = bom_messenger.decrypt(encrypted_by_telegram)
        result = json.loads(decrypted_by_bom.decode('utf-8'))
        print(f"   ✓ compatible AES-256-GCM clients расшифровал: {json.dumps(result, ensure_ascii=False)[:60]}...")
        
        if result == response_data:
            print(f"   ✅ УСПЕХ: Данные совпадают!")
        else:
            print(f"   ❌ ОШИБКА: Данные не совпадают!")
            return False
            
    except Exception as e:
        print(f"   ❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # === ТЕСТ 3: Base64 формат (как в API) ===
    print(f"\n" + "─" * 70)
    print("3️⃣ Тест: Base64 формат (полный цикл через API)")
    print("─" * 70)
    
    try:
        # compatible AES-256-GCM clients шифрует и кодирует в Base64
        encrypted = bom_messenger.encrypt(test_data)
        b64_request = base64.b64encode(encrypted).decode('utf-8')
        print(f"   ✓ compatible AES-256-GCM clients отправляет: {b64_request[:50]}...")
        
        # TelegramHelper получает и расшифровывает
        received = base64.b64decode(b64_request)
        decrypted = telegram_messenger.decrypt(received)
        parsed = json.loads(decrypted.decode('utf-8'))
        print(f"   ✓ TelegramHelper получил: {json.dumps(parsed, ensure_ascii=False)[:50]}...")
        
        # TelegramHelper шифрует ответ и кодирует в Base64
        encrypted_response = telegram_messenger.encrypt(response_data)
        b64_response = base64.b64encode(encrypted_response).decode('utf-8')
        print(f"   ✓ TelegramHelper отправляет: {b64_response[:50]}...")
        
        # compatible AES-256-GCM clients получает и расшифровывает
        received_response = base64.b64decode(b64_response)
        decrypted_response = bom_messenger.decrypt(received_response)
        final_result = json.loads(decrypted_response.decode('utf-8'))
        print(f"   ✓ compatible AES-256-GCM clients получил: {json.dumps(final_result, ensure_ascii=False)[:50]}...")
        
        if final_result == response_data:
            print(f"   ✅ УСПЕХ: Полный цикл работает!")
        else:
            print(f"   ❌ ОШИБКА: Данные не совпадают после полного цикла!")
            return False
            
    except Exception as e:
        print(f"   ❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print(f"\n" + "=" * 70)
    print("✨ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print("🎯 Шифрование полностью совместимо между проектами")
    print("=" * 70)
    
    return True

if __name__ == "__main__":
    success = test_cross_compatibility()
    sys.exit(0 if success else 1)
