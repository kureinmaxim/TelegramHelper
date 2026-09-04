# -*- coding: utf-8 -*-
"""
Модуль шифрования для TelegramSimple API
Реализует Application-Level Encryption (AES-256-GCM).
Совместим с форматом compatible AES-256-GCM clients.
"""
import os
import json
import hashlib
import logging
from typing import Union, Dict, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

class EncryptionError(Exception):
    """Базовый класс для ошибок шифрования"""
    pass

class SecureMessenger:
    """
    Класс для безопасного обмена сообщениями.
    
    Особенности:
    - Алгоритм: AES-256-GCM
    - Формат пакета: [Nonce(12B)][Ciphertext + Tag]
    - Совместим с compatible AES-256-GCM clients
    """
    
    # Размер Nonce для AES-GCM (12 байт - стандарт NIST)
    NONCE_SIZE = 12

    def __init__(self, key: str):
        """
        Инициализация мессенджера.
        
        Args:
            key: Ключ шифрования (hex строка или обычная строка)
        """
        if not key:
            raise EncryptionError("Encryption key is required")
        
        # Преобразуем hex ключ в bytes
        try:
            self.key = bytes.fromhex(key)
        except ValueError:
            # Если не hex, используем как строку и хешируем
            self.key = hashlib.sha256(key.encode()).digest()
        
        # Инициализируем AES-GCM с 32-байтным ключом (AES-256)
        self._aesgcm = AESGCM(self.key[:32])
        
        logger.info("SecureMessenger initialized with AES-256-GCM (compatible AES-256-GCM clients compatible format)")

    def encrypt(self, data: Union[dict, str, bytes]) -> bytes:
        """
        Шифрует данные.
        
        Args:
            data: Данные (dict, str или bytes)
            
        Returns:
            bytes: Зашифрованный пакет в формате nonce + ciphertext
        """
        try:
            # 1. Подготовка данных
            if isinstance(data, dict):
                plaintext = json.dumps(data, ensure_ascii=False).encode('utf-8')
            elif isinstance(data, str):
                plaintext = data.encode('utf-8')
            else:
                plaintext = data
                
            # 2. Генерация Nonce
            nonce = os.urandom(self.NONCE_SIZE)
            
            # 3. Шифрование (AESGCM.encrypt возвращает ciphertext + tag)
            ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
            
            # 4. Формирование пакета: nonce + ciphertext
            return nonce + ciphertext
            
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {str(e)}")

    def decrypt(self, data: bytes) -> bytes:
        """
        Расшифровывает пакет.
        
        Args:
            data: Зашифрованный пакет
            
        Returns:
            bytes: Расшифрованные данные
        """
        try:
            # 1. Извлекаем nonce и ciphertext
            nonce = data[:self.NONCE_SIZE]
            ciphertext = data[self.NONCE_SIZE:]
            
            # 2. Расшифровка
            return self._aesgcm.decrypt(nonce, ciphertext, None)
                
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise EncryptionError(f"Failed to decrypt data: {str(e)}")
    
    def encrypt_json(self, data: dict) -> str:
        """
        Шифрует данные и возвращает base64 строку.
        
        Args:
            data: Словарь для шифрования
            
        Returns:
            Base64-encoded зашифрованные данные
        """
        import base64
        encrypted = self.encrypt(data)
        return base64.b64encode(encrypted).decode('utf-8')
    
    def decrypt_json(self, data: str) -> dict:
        """
        Расшифровывает base64 строку и возвращает словарь.
        
        Args:
            data: Base64-encoded зашифрованные данные
            
        Returns:
            Расшифрованный словарь
        """
        import base64
        encrypted = base64.b64decode(data)
        decrypted = self.decrypt(encrypted)
        return json.loads(decrypted.decode('utf-8'))
