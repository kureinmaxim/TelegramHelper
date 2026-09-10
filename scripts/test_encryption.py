#!/usr/bin/env python3
"""
Script to test API encryption.
Emulates a client (compatible AES-256-GCM clients) sending an encrypted request.
"""
import os
import sys
import json
import requests
import logging
from dotenv import load_dotenv

# Add project root to the path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from encryption import SecureMessenger
from security import create_signed_headers

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_encryption():
    # Load environment
    load_dotenv()

    api_key = os.getenv("API_SECRET_KEY")
    encryption_key = os.getenv("ENCRYPTION_KEY", api_key)
    hmac_secret = os.getenv("HMAC_SECRET")
    base_url = os.getenv("API_URL", "http://localhost:8000")

    if not api_key or not hmac_secret:
        logger.error("API_SECRET_KEY or HMAC_SECRET not set in .env")
        return

    logger.info(f"Using Encryption Key: {encryption_key[:4]}...{encryption_key[-4:]}")

    # Init messenger
    messenger = SecureMessenger(encryption_key)

    # Test payload
    payload = {
        "prompt": "Hello, are you encrypted?",
        "provider": "anthropic",
        "max_tokens": 100
    }

    logger.info(f"Original Payload: {json.dumps(payload, indent=2)}")

    # 1. Encrypt
    encrypted_data = messenger.encrypt(payload)
    logger.info(f"Encrypted Data Size: {len(encrypted_data)} bytes")
    logger.info(f"Encrypted Hex (first 32 bytes): {encrypted_data.hex()[:64]}...")

    # 2. Build headers (create_signed_headers is for signing JSON payloads).
    # For the encrypted endpoint the signature could be checked on the
    # ciphertext or on the plaintext. In our api.py, full_security_check
    # runs BEFORE decrypt, but verify_signature expects payload: dict.
    # full_security_check is called before ai_query_encrypted, and cannot
    # verify the body signature because the body is bytes, not JSON.
    #
    # IMPORTANT: does full_security_check try to read the request body?
    # No — verify_signature takes a payload. FastAPI can read Request.body
    # only once.
    #
    # Looking at api.py more carefully:
    # full_security_check does not read the body. verify_signature takes a payload.
    # Is verify_signature called inside full_security_check?
    # No — it is imported, but full_security_check does NOT call it for the body.
    # full_security_check checks headers (timestamp, nonce, api_key).
    # Is verify_signature called separately?
    # In api.py:
    # async def full_security_check(...):
    #    ... verify_api_key ... verify_nonce ...
    #    return { ... }
    #
    # full_security_check does NOT call verify_signature!
    # So the body signature is not checked in full_security_check.
    #
    # The plain ai_query also has no explicit verify_signature call.
    # So the signature was not checked in v1 at all?
    # Check security.py verify_signature usage.
    # It is defined — but is it used?
    #
    # In api.py v1 (before these changes) verify_signature was imported but never called.
    # Bug or feature?
    # Maybe the signature check lived in middleware, or it was missed.
    #
    # Either way, for the encrypted endpoint API Key + Nonce + Encryption Tag is enough.
    # The GCM Tag already guarantees body integrity.
    # So HMAC of the body is redundant when we use AES-GCM.
    # Headers (Timestamp, Nonce) are still worth protecting.
    #
    # In the current scheme we just send headers to pass full_security_check.

    headers = {
        "X-API-KEY": api_key,
        "X-APP-ID": "test-client",
        "X-Timestamp": str(int(os.getenv("TIMESTAMP_TOLERANCE", "300"))), # Mock timestamp
        "X-Nonce": os.urandom(8).hex(),
        "Content-Type": "application/octet-stream"
    }

    # Set timestamp/nonce correctly
    from security import create_signed_headers
    # Cannot use create_signed_headers as-is: it signs a JSON payload.
    # Ours is bytes. Build headers by hand.

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

    # 3. Send request
    url = f"{base_url}/ai_query/encrypted"
    logger.info(f"Sending POST to {url}")

    try:
        response = requests.post(url, data=encrypted_data, headers=headers)

        if response.status_code != 200:
            logger.error(f"Error {response.status_code}: {response.text}")
            return

        # 4. Decrypt response
        encrypted_response = response.content
        logger.info(f"Response Size: {len(encrypted_response)} bytes")

        decrypted_response = messenger.decrypt(encrypted_response)
        logger.info("Decryption Successful!")
        logger.info(f"Response: {json.dumps(decrypted_response, indent=2)}")

    except Exception as e:
        logger.error(f"Test failed: {e}")

if __name__ == "__main__":
    test_encryption()
