import requests
import os
import json

BASE_URL = "http://localhost:8000"

def test_root():
    """Test the root endpoint"""
    print("\n" + "="*50)
    print("📡 Test: GET /")
    print("="*50)
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        print(f"Status Code: {response.status_code}")
        print("Response:", json.dumps(response.json(), indent=2, ensure_ascii=False))
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_prompt_templates():
    """Test fetching prompt templates"""
    print("\n" + "="*50)
    print("📋 Test: GET /prompt_templates")
    print("="*50)
    try:
        response = requests.get(f"{BASE_URL}/prompt_templates", timeout=5)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Templates found: {data['count']}")
            for t in data['templates']:
                print(f"  - {t['category']}: {t['title']}")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_prompt_categories():
    """Test fetching categories"""
    print("\n" + "="*50)
    print("📂 Test: GET /prompt_categories")
    print("="*50)
    try:
        response = requests.get(f"{BASE_URL}/prompt_categories", timeout=5)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Categories: {', '.join(data['categories'])}")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_ai_query_direct():
    """Test an AI request with a direct prompt"""
    print("\n" + "="*50)
    print("🤖 Test: POST /ai_query (direct prompt)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    payload = {
        "prompt": "Say 'Hello' in one word",
        "provider": "anthropic",
        "max_tokens": 50
    }
    
    headers = {"x-api-key": api_key}
    
    try:
        response = requests.post(f"{BASE_URL}/ai_query", json=payload, headers=headers, timeout=30)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Provider: {data['provider']}")
            print(f"Response: {data['response'][:100]}...")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_ai_query_with_template():
    """Test an AI request using a template"""
    print("\n" + "="*50)
    print("🎯 Test: POST /ai_query (with template)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    payload = {
        "template_category": "science",
        "input_text": "What is quantum entanglement?",
        "provider": "anthropic",
        "max_tokens": 500
    }
    
    headers = {"x-api-key": api_key}
    
    try:
        response = requests.post(f"{BASE_URL}/ai_query", json=payload, headers=headers, timeout=60)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Provider: {data['provider']}")
            print(f"Template used: {data.get('template_used', 'None')}")
            print(f"Response (first 200 characters): {data['response'][:200]}...")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_bom_categorizer_style():
    """Test in compatible AES-256-GCM clients style — look up a component"""
    print("\n" + "="*50)
    print("🔧 Test: compatible AES-256-GCM clients style (component)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    # Prompt as in compatible AES-256-GCM clients
    component_name = "STM32F103C8T6"
    prompt = f"""Find information about the electronic component: {component_name}

Please provide the following information in a structured form:

1. Full name and manufacturer
2. Component type (IC, resistor, capacitor, etc.)
3. Key specs (voltage, current, frequency, package, etc.)
4. Brief description of purpose
5. Typical use cases (2-3 examples)

Response format: JSON
{{
    "found": true/false,
    "full_name": "full name",
    "manufacturer": "manufacturer",
    "type": "component type",
    "description": "description"
}}"""

    payload = {
        "prompt": prompt,
        "provider": "anthropic",
        "max_tokens": 1000
    }
    
    headers = {"X-API-KEY": api_key}  # As in compatible AES-256-GCM clients
    
    try:
        response = requests.post(f"{BASE_URL}/ai_query", json=payload, headers=headers, timeout=60)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Provider: {data['provider']}")
            print(f"Response: {data['response'][:300]}...")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def main():
    print("\n" + "🚀 TelegramHelper API Test Suite")
    print("="*50)
    
    results = []
    
    # Tests without auth
    results.append(("GET /", test_root()))
    results.append(("GET /prompt_templates", test_prompt_templates()))
    results.append(("GET /prompt_categories", test_prompt_categories()))
    
    # Tests with auth (need API_SECRET_KEY and an Anthropic/OpenAI key)
    api_key = os.getenv("API_SECRET_KEY")
    if api_key:
        results.append(("POST /ai_query (direct)", test_ai_query_direct()))
        results.append(("POST /ai_query (template)", test_ai_query_with_template()))
        results.append(("compatible AES-256-GCM clients style", test_bom_categorizer_style()))
    else:
        print("\n⚠️  API_SECRET_KEY is not set — skipping authenticated tests")
    
    # Summary
    print("\n" + "="*50)
    print("📊 RESULTS:")
    print("="*50)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {name}")
    print(f"\n  Total: {passed}/{total} tests passed")

if __name__ == "__main__":
    main()
