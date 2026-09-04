import sys
import os
import json
import base64
import asyncio
from fastapi.testclient import TestClient

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import app, secure_messenger
from encryption import SecureMessenger

# Mock environment variables if needed
os.environ["ENCRYPTION_KEY"] = "test_key_32_bytes_long_exactly_!!"

def test_obfuscation_flow():
    """
    Test the full obfuscation flow:
    1. Client encrypts data
    2. Client encodes to Base64
    3. Client sends JSON to /ai_query/secure
    4. Server decodes Base64 -> decrypts -> processes -> encrypts -> encodes Base64
    5. Client receives JSON -> decodes Base64 -> decrypts
    """
    
    # Initialize TestClient
    client = TestClient(app)
    
    # Ensure secure_messenger is initialized (it might need startup event)
    # For testing, we can manually initialize it if the startup event doesn't run in TestClient context easily
    # But api.py initializes it in startup_event. TestClient runs startup events by default.
    
    # However, we need to make sure we use the SAME key as the server
    # In api.py, it loads from env. We set env above.
    # But we also need a client-side messenger with the same key.
    
    key = os.environ["ENCRYPTION_KEY"]
    client_messenger = SecureMessenger(key)
    
    # Manually initialize server-side messenger for testing
    import api
    api.secure_messenger = SecureMessenger(key)
    
    # Prepare request data
    request_payload = {
        "query": "Test query",
        "provider": "openai",
        "model": "gpt-4"
    }
    
    # 1. Client Encrypts
    encrypted_bytes = client_messenger.encrypt(request_payload)
    
    # 2. Client Encodes to Base64
    b64_data = base64.b64encode(encrypted_bytes).decode('utf-8')
    
    # 3. Client Sends Request
    # We need to mock the security headers/checks or bypass them.
    # The endpoint uses `full_security_check`.
    # We can generate valid headers.
    
    timestamp = str(int(asyncio.get_event_loop().time() if hasattr(asyncio, 'get_event_loop') else 0)) # Mock timestamp
    # Actually, let's just use time.time()
    import time
    timestamp = str(int(time.time()))
    nonce = os.urandom(8).hex()
    
    # We need to sign the request if signature check is enabled.
    # Assuming ENABLE_SIGNATURE_CHECK might be true.
    # Let's try to bypass or provide valid signature if we can import security utils.
    
    # For simplicity, let's assume we can just hit the endpoint if we provide the headers expected by `security.py`
    # But `full_security_check` validates headers.
    
    # Let's try to mock `full_security_check` dependency override
    from api import full_security_check
    app.dependency_overrides[full_security_check] = lambda: {"client_id": "test_client"}
    
    response = client.post(
        "/ai_query/secure",
        json={"data": b64_data}
    )
    
    assert response.status_code == 200, f"Request failed: {response.text}"
    
    response_json = response.json()
    assert "data" in response_json, "Response missing 'data' field"
    
    # 4. Client Decodes Response
    b64_response = response_json["data"]
    encrypted_response_bytes = base64.b64decode(b64_response)
    
    # 5. Client Decrypts
    decrypted_response = client_messenger.decrypt(encrypted_response_bytes)
    
    # Check response content
    # Since we didn't mock `process_ai_request`, it might fail or return a mock response depending on implementation.
    # In `api.py`, `process_ai_request` calls AI providers. We should probably mock that too.
    
    print("Decrypted Response:", decrypted_response)
    
    # If `process_ai_request` actually runs, it might fail due to missing API keys for OpenAI/Anthropic.
    # But we just want to verify the encryption/obfuscation layer.
    # If the server returns 500 inside `process_ai_request`, we'll see it.
    
if __name__ == "__main__":
    # We need to mock `process_ai_request` to avoid actual API calls
    from unittest.mock import AsyncMock, patch
    from api import AIQueryResponse
    
    mock_response = AIQueryResponse(
        response="This is a mocked response",
        provider="openai",
        model="gpt-4",
        status="success"
    )
    
    with patch("api.process_ai_request", new=AsyncMock(return_value=mock_response)):
        try:
            test_obfuscation_flow()
            print("✅ Obfuscation Test Passed!")
        except Exception as e:
            print(f"❌ Obfuscation Test Failed: {e}")
            import traceback
            traceback.print_exc()
