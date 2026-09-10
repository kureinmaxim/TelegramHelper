# -*- coding: utf-8 -*-
"""
Encryption module for TelegramSimple API.
Implements Application-Level Encryption (AES-256-GCM).
Compatible with AES-256-GCM client packet format.
"""
import os
import json
import hashlib
import logging
from typing import Union, Dict, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

class EncryptionError(Exception):
    """Base class for encryption errors."""
    pass

class SecureMessenger:
    """
    Secure message exchange.

    Notes:
    - Algorithm: AES-256-GCM
    - Packet format: [Nonce(12B)][Ciphertext + Tag]
    - Compatible with AES-256-GCM clients
    """
    
    # AES-GCM nonce size (12 bytes, NIST standard)
    NONCE_SIZE = 12

    def __init__(self, key: str):
        """
        Initialize the messenger.

        Args:
            key: Encryption key (hex string or plain string)
        """
        if not key:
            raise EncryptionError("Encryption key is required")
        
        # Convert hex key to bytes
        try:
            self.key = bytes.fromhex(key)
        except ValueError:
            # Not hex: treat as string and hash
            self.key = hashlib.sha256(key.encode()).digest()
        
        # AES-GCM with a 32-byte key (AES-256)
        self._aesgcm = AESGCM(self.key[:32])
        
        logger.info("SecureMessenger initialized with AES-256-GCM (compatible AES-256-GCM clients compatible format)")

    def encrypt(self, data: Union[dict, str, bytes]) -> bytes:
        """
        Encrypt data.

        Args:
            data: Payload (dict, str, or bytes)

        Returns:
            bytes: Encrypted packet in nonce + ciphertext format
        """
        try:
            # 1. Prepare plaintext
            if isinstance(data, dict):
                plaintext = json.dumps(data, ensure_ascii=False).encode('utf-8')
            elif isinstance(data, str):
                plaintext = data.encode('utf-8')
            else:
                plaintext = data
                
            # 2. Generate nonce
            nonce = os.urandom(self.NONCE_SIZE)
            
            # 3. Encrypt (AESGCM.encrypt returns ciphertext + tag)
            ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
            
            # 4. Packet: nonce + ciphertext
            return nonce + ciphertext
            
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt data: {str(e)}")

    def decrypt(self, data: bytes) -> bytes:
        """
        Decrypt a packet.

        Args:
            data: Encrypted packet

        Returns:
            bytes: Decrypted payload
        """
        try:
            # 1. Split nonce and ciphertext
            nonce = data[:self.NONCE_SIZE]
            ciphertext = data[self.NONCE_SIZE:]
            
            # 2. Decrypt
            return self._aesgcm.decrypt(nonce, ciphertext, None)
                
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise EncryptionError(f"Failed to decrypt data: {str(e)}")
    
    def encrypt_json(self, data: dict) -> str:
        """
        Encrypt data and return a base64 string.

        Args:
            data: Dict to encrypt

        Returns:
            Base64-encoded ciphertext
        """
        import base64
        encrypted = self.encrypt(data)
        return base64.b64encode(encrypted).decode('utf-8')
    
    def decrypt_json(self, data: str) -> dict:
        """
        Decrypt a base64 string and return a dict.

        Args:
            data: Base64-encoded ciphertext

        Returns:
            Decrypted dict
        """
        import base64
        encrypted = base64.b64decode(data)
        decrypted = self.decrypt(encrypted)
        return json.loads(decrypted.decode('utf-8'))
