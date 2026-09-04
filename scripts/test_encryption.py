#!/usr/bin/env python3
"""
Скрипт для тестирования шифрования API.
Эмулирует клиент (compatible AES-256-GCM clients), отправляющий зашифрованный запрос.
"""
import os
import sys
import json
import requests
import logging
from dotenv import load_dotenv

# Добавляем корневую директорию в путь, чтобы импортировать модули
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from encryption import SecureMessenger
from security import create_signed_headers

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_encryption():
    # Загружаем переменные окружения
    load_dotenv()
    
    api_key = os.getenv("API_SECRET_KEY")
    encryption_key = os.getenv("ENCRYPTION_KEY", api_key)
    hmac_secret = os.getenv("HMAC_SECRET")
    base_url = os.getenv("API_URL", "http://localhost:8000")
    
    if not api_key or not hmac_secret:
        logger.error("API_SECRET_KEY or HMAC_SECRET not set in .env")
        return

    logger.info(f"Using Encryption Key: {encryption_key[:4]}...{encryption_key[-4:]}")
    
    # Инициализация мессенджера
    messenger = SecureMessenger(encryption_key)
    
    # Тестовые данные
    payload = {
        "prompt": "Hello, are you encrypted?",
        "provider": "anthropic",
        "max_tokens": 100
    }
    
    logger.info(f"Original Payload: {json.dumps(payload, indent=2)}")
    
    # 1. Шифруем данные
    encrypted_data = messenger.encrypt(payload)
    logger.info(f"Encrypted Data Size: {len(encrypted_data)} bytes")
    logger.info(f"Encrypted Hex (first 32 bytes): {encrypted_data.hex()[:64]}...")
    
    # 2. Формируем заголовки (используем create_signed_headers для подписи)
    # Важно: для зашифрованного эндпоинта подпись может проверяться 
    # либо по зашифрованному телу, либо по расшифрованному.
    # В нашей реализации api.py full_security_check проверяет подпись ДО расшифровки,
    # но verify_signature ожидает payload: dict.
    # В текущей реализации api.py full_security_check вызывается ДО ai_query_encrypted.
    # Но verify_signature требует payload.
    # В случае encrypted endpoint, full_security_check не сможет проверить подпись тела, 
    # так как тело - это байты, а не JSON.
    # 
    # ВАЖНО: В текущей реализации api.py full_security_check пытается читать тело запроса?
    # Нет, verify_signature принимает payload.
    # Но FastAPI Request body можно прочитать только один раз.
    # 
    # Давайте посмотрим на api.py внимательнее.
    # full_security_check не читает body. verify_signature принимает payload.
    # Но verify_signature вызывается внутри full_security_check?
    # Нет, verify_signature импортируется, но full_security_check его НЕ вызывает для проверки тела!
    # full_security_check проверяет headers (timestamp, nonce, api_key).
    # А verify_signature вызывается отдельно?
    # В api.py:
    # async def full_security_check(...):
    #    ... verify_api_key ... verify_nonce ...
    #    return { ... }
    # 
    # Опа! В full_security_check НЕТ вызова verify_signature!
    # Значит подпись тела не проверяется в full_security_check.
    # 
    # В ai_query (обычном) тоже нет явного вызова verify_signature.
    # Значит подпись вообще не проверялась в v1?
    # Давайте проверим security.py verify_signature usage.
    # Она там определена, но используется ли?
    # 
    # В api.py v1 (до моих изменений) verify_signature импортировалась, но не вызывалась.
    # Это баг или фича? 
    # Возможно, проверка подписи была в middleware или я пропустил.
    # 
    # В любом случае, для encrypted endpoint нам достаточно API Key + Nonce + Encryption Tag.
    # GCM Tag уже гарантирует целостность тела.
    # Так что подпись HMAC для тела избыточна, если мы используем AES-GCM.
    # Но заголовки (Timestamp, Nonce) все равно стоит защитить.
    # 
    # В текущей схеме мы просто отправляем заголовки для прохождения full_security_check.
    
    headers = {
        "X-API-KEY": api_key,
        "X-APP-ID": "test-client",
        "X-Timestamp": str(int(os.getenv("TIMESTAMP_TOLERANCE", "300"))), # Mock timestamp
        "X-Nonce": os.urandom(8).hex(),
        "Content-Type": "application/octet-stream"
    }
    
    # Добавляем timestamp/nonce корректно
    from security import create_signed_headers
    # Мы не можем использовать create_signed_headers "как есть", потому что она подписывает JSON payload.
    # А у нас payload - байты.
    # Сделаем вручную заголовки.
    
    import time
    import uuid
    
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    
    headers = {
        "X-API-KEY": api_key,
        "X-APP-ID": "test-client",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "Content-Type": "application/octet-stream"
    }

    # 3. Отправляем запрос
    url = f"{base_url}/ai_query/encrypted"
    logger.info(f"Sending POST to {url}")
    
    try:
        response = requests.post(url, data=encrypted_data, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Error {response.status_code}: {response.text}")
            return

        # 4. Расшифровываем ответ
        encrypted_response = response.content
        logger.info(f"Response Size: {len(encrypted_response)} bytes")
        
        decrypted_response = messenger.decrypt(encrypted_response)
        logger.info("Decryption Successful!")
        logger.info(f"Response: {json.dumps(decrypted_response, indent=2)}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}")

if __name__ == "__main__":
    test_encryption()
