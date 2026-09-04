import requests
import os
import json

BASE_URL = "http://localhost:8000"

def test_root():
    """Тест корневого endpoint"""
    print("\n" + "="*50)
    print("📡 Тест: GET /")
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
    """Тест получения шаблонов промптов"""
    print("\n" + "="*50)
    print("📋 Тест: GET /prompt_templates")
    print("="*50)
    try:
        response = requests.get(f"{BASE_URL}/prompt_templates", timeout=5)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Найдено шаблонов: {data['count']}")
            for t in data['templates']:
                print(f"  - {t['category']}: {t['title']}")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_prompt_categories():
    """Тест получения категорий"""
    print("\n" + "="*50)
    print("📂 Тест: GET /prompt_categories")
    print("="*50)
    try:
        response = requests.get(f"{BASE_URL}/prompt_categories", timeout=5)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Категории: {', '.join(data['categories'])}")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_ai_query_direct():
    """Тест AI запроса с прямым промптом"""
    print("\n" + "="*50)
    print("🤖 Тест: POST /ai_query (прямой промпт)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    payload = {
        "prompt": "Скажи 'Привет' одним словом",
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
    """Тест AI запроса с использованием шаблона"""
    print("\n" + "="*50)
    print("🎯 Тест: POST /ai_query (с шаблоном)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    payload = {
        "template_category": "science",
        "input_text": "Что такое квантовая запутанность?",
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
            print(f"Response (первые 200 символов): {data['response'][:200]}...")
        else:
            print("Error:", response.text)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

def test_bom_categorizer_style():
    """Тест в стиле compatible AES-256-GCM clients - поиск информации о компоненте"""
    print("\n" + "="*50)
    print("🔧 Тест: стиль compatible AES-256-GCM clients (компонент)")
    print("="*50)
    
    api_key = os.getenv("API_SECRET_KEY", "secret_key")
    
    # Промпт как в compatible AES-256-GCM clients
    component_name = "STM32F103C8T6"
    prompt = f"""Найди информацию об электронном компоненте: {component_name}

Пожалуйста, предоставь следующую информацию в структурированном виде:

1. Полное название и производитель
2. Тип компонента (микросхема, резистор, конденсатор и т.д.)
3. Основные характеристики (напряжение, ток, частота, корпус и т.д.)
4. Краткое описание назначения
5. Типичные примеры использования (2-3 примера)

Формат ответа: JSON
{{
    "found": true/false,
    "full_name": "полное название",
    "manufacturer": "производитель",
    "type": "тип компонента",
    "description": "описание"
}}"""

    payload = {
        "prompt": prompt,
        "provider": "anthropic",
        "max_tokens": 1000
    }
    
    headers = {"X-API-KEY": api_key}  # Как в compatible AES-256-GCM clients
    
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
    
    # Тесты без авторизации
    results.append(("GET /", test_root()))
    results.append(("GET /prompt_templates", test_prompt_templates()))
    results.append(("GET /prompt_categories", test_prompt_categories()))
    
    # Тесты с авторизацией (требуют API_SECRET_KEY и Anthropic/OpenAI ключ)
    api_key = os.getenv("API_SECRET_KEY")
    if api_key:
        results.append(("POST /ai_query (direct)", test_ai_query_direct()))
        results.append(("POST /ai_query (template)", test_ai_query_with_template()))
        results.append(("compatible AES-256-GCM clients style", test_bom_categorizer_style()))
    else:
        print("\n⚠️  API_SECRET_KEY не установлен - пропускаем тесты с авторизацией")
    
    # Итоги
    print("\n" + "="*50)
    print("📊 РЕЗУЛЬТАТЫ:")
    print("="*50)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {name}")
    print(f"\n  Итого: {passed}/{total} тестов пройдено")

if __name__ == "__main__":
    main()
