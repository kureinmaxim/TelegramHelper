#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Encryption compatibility test between compatible AES-256-GCM clients and TelegramHelper.
This script simulates sending a request from compatible AES-256-GCM clients.
"""

import sys
import os
from pathlib import Path

# Add the encryption module path from compatible AES-256-GCM clients
project_root = Path(__file__).resolve().parents[1]
bom_path = Path(os.environ.get("BOMCATEGORIZER_PATH", project_root.parent / "compatible AES-256-GCM clients"))
sys.path.insert(0, str(bom_path))

from bom_categorizer.encryption import SecureMessenger as BOMSecureMessenger

# Add the encryption module path from TelegramHelper
telegram_path = project_root
sys.path.insert(0, str(telegram_path))

from encryption import SecureMessenger as TelegramSecureMessenger

import json
import base64

def test_cross_compatibility():
    """Cross-compatibility encryption test"""
    
    print("=" * 70)
    print("🔄 Encryption compatibility test: compatible AES-256-GCM clients ↔ TelegramHelper")
    print("=" * 70)
    
    # One key for both modules
    test_key = "a" * 64  # 256-bit hex key
    
    # Test data (same as in compatible AES-256-GCM clients)
    test_data = {
        "prompt": "Classify the component: Resistor C2-23",
        "provider": "anthropic",
        "max_tokens": 1000
    }
    
    print(f"\n📦 Test data:")
    print(f"   {json.dumps(test_data, ensure_ascii=False)}")
    
    # === TEST 1: compatible AES-256-GCM clients -> TelegramHelper ===
    print(f"\n" + "─" * 70)
    print("1️⃣ Test: compatible AES-256-GCM clients encrypts → TelegramHelper decrypts")
    print("─" * 70)
    
    try:
        # Initialization
        bom_messenger = BOMSecureMessenger(test_key)
        telegram_messenger = TelegramSecureMessenger(test_key)
        
        # Encrypt in compatible AES-256-GCM clients
        encrypted_by_bom = bom_messenger.encrypt(test_data)
        print(f"   ✓ compatible AES-256-GCM clients encrypted: {len(encrypted_by_bom)} bytes")
        print(f"   ✓ Format: nonce({encrypted_by_bom[:12].hex()[:20]}...) + ciphertext")
        
        # Decrypt in TelegramHelper
        decrypted_by_telegram = telegram_messenger.decrypt(encrypted_by_bom)
        result = json.loads(decrypted_by_telegram.decode('utf-8'))
        print(f"   ✓ TelegramHelper decrypted: {json.dumps(result, ensure_ascii=False)}")
        
        if result == test_data:
            print(f"   ✅ SUCCESS: Data matches!")
        else:
            print(f"   ❌ ERROR: Data does not match!")
            return False
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # === TEST 2: TelegramHelper -> compatible AES-256-GCM clients ===
    print(f"\n" + "─" * 70)
    print("2️⃣ Test: TelegramHelper encrypts → compatible AES-256-GCM clients decrypts")
    print("─" * 70)
    
    try:
        # Sample API response
        response_data = {
            "response": "Category: resistors, Confidence: high",
            "provider": "anthropic",
            "status": "success"
        }
        
        # Encrypt in TelegramHelper
        encrypted_by_telegram = telegram_messenger.encrypt(response_data)
        print(f"   ✓ TelegramHelper encrypted: {len(encrypted_by_telegram)} bytes")
        
        # Decrypt in compatible AES-256-GCM clients
        decrypted_by_bom = bom_messenger.decrypt(encrypted_by_telegram)
        result = json.loads(decrypted_by_bom.decode('utf-8'))
        print(f"   ✓ compatible AES-256-GCM clients decrypted: {json.dumps(result, ensure_ascii=False)[:60]}...")
        
        if result == response_data:
            print(f"   ✅ SUCCESS: Data matches!")
        else:
            print(f"   ❌ ERROR: Data does not match!")
            return False
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # === TEST 3: Base64 format (as in the API) ===
    print(f"\n" + "─" * 70)
    print("3️⃣ Test: Base64 format (full round-trip via API)")
    print("─" * 70)
    
    try:
        # compatible AES-256-GCM clients encrypts and encodes as Base64
        encrypted = bom_messenger.encrypt(test_data)
        b64_request = base64.b64encode(encrypted).decode('utf-8')
        print(f"   ✓ compatible AES-256-GCM clients sends: {b64_request[:50]}...")
        
        # TelegramHelper receives and decrypts
        received = base64.b64decode(b64_request)
        decrypted = telegram_messenger.decrypt(received)
        parsed = json.loads(decrypted.decode('utf-8'))
        print(f"   ✓ TelegramHelper received: {json.dumps(parsed, ensure_ascii=False)[:50]}...")
        
        # TelegramHelper encrypts the response and encodes as Base64
        encrypted_response = telegram_messenger.encrypt(response_data)
        b64_response = base64.b64encode(encrypted_response).decode('utf-8')
        print(f"   ✓ TelegramHelper sends: {b64_response[:50]}...")
        
        # compatible AES-256-GCM clients receives and decrypts
        received_response = base64.b64decode(b64_response)
        decrypted_response = bom_messenger.decrypt(received_response)
        final_result = json.loads(decrypted_response.decode('utf-8'))
        print(f"   ✓ compatible AES-256-GCM clients received: {json.dumps(final_result, ensure_ascii=False)[:50]}...")
        
        if final_result == response_data:
            print(f"   ✅ SUCCESS: Full round-trip works!")
        else:
            print(f"   ❌ ERROR: Data does not match after the full round-trip!")
            return False
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print(f"\n" + "=" * 70)
    print("✨ ALL TESTS PASSED!")
    print("🎯 Encryption is fully compatible between the projects")
    print("=" * 70)
    
    return True

if __name__ == "__main__":
    success = test_cross_compatibility()
    sys.exit(0 if success else 1)
