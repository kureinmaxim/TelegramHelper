# -*- coding: utf-8 -*-
"""
Модуль безопасности для TelegramHelper API

Реализует:
- HMAC подпись запросов
- Проверка timestamp (защита от replay attacks)
- Проверка nonce (уникальность запросов)
- Whitelist APP_ID
- Rate limiting

Автор: TelegramHelper contributors
Дата: 24.11.2025
"""

import os
import hmac
import hashlib
import time
import json
import logging
from typing import Optional, Dict, Set
from functools import wraps
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===

# Whitelist разрешённых приложений
ALLOWED_APPS: Dict[str, dict] = {
    # "example-app": {
    #     "name": "BOM Categorizer Modern Edition",
    #     "version": "4.x",
    #     "allowed_endpoints": ["/ai_query", "/prompt_templates", "/prompt_categories"],
    #     "rate_limit_per_minute": 60,
    #     "rate_limit_per_day": 1000
    # },
    "example-app": {
        "name": "BOM Categorizer Modern Edition v5",
        "version": "5.x",
        "allowed_endpoints": ["/ai_query", "/prompt_templates", "/prompt_categories"],
        "rate_limit_per_minute": 60,
        "rate_limit_per_day": 1000
    },
    #"apiai-v1": {
    #    "name": "ApiAi Experimental Rust version",
    #    "version": "1.x",
    #    "allowed_endpoints": ["/ai_query", "/prompt_templates", "/prompt_categories"],
    #    "rate_limit_per_minute": 60,
    #    "rate_limit_per_day": 1000
    #},
    "apiai-v2": {
        "name": "ApiAi Tauri Edition v2",
        "version": "2.x",
        "allowed_endpoints": ["/ai_query", "/ai_query/secure", "/echo", "/echo/secure", "/prompt_templates", "/prompt_categories"],
        "rate_limit_per_minute": 60,
        "rate_limit_per_day": 1000
    },
    "apiai-v3": {
        "name": "ApiAi Tauri Edition v3",
        "version": "3.x",
        "allowed_endpoints": ["/ai_query", "/ai_query/secure", "/echo", "/echo/secure", "/prompt_templates", "/prompt_categories"],
        "rate_limit_per_minute": 100,
        "rate_limit_per_day": 2000
    },
    # "example-app": {
    #     "name": "BOM Categorizer Modern Edition v6 (Reserved)",
    #     "version": "6.x",
    #     "allowed_endpoints": ["/ai_query", "/prompt_templates", "/prompt_categories"],
    #     "rate_limit_per_minute": 60,
    #     "rate_limit_per_day": 1000
    # },
    # "test-client": {
    #     "name": "Test Client (Development)",
    #     "version": "dev",
    #     "allowed_endpoints": ["/ai_query", "/prompt_templates", "/prompt_categories"],
    #     "rate_limit_per_minute": 10,
    #     "rate_limit_per_day": 100
    # },
    "apiai-ios-v0": {
        "name": "ApiAi iOS Edition v0",
        "version": "0.1.0",
        "allowed_endpoints": ["/ai_query", "/ai_query/secure", "/echo", "/echo/secure", "/admin_command", "/admin_command/secure"],
        "rate_limit_per_minute": 60,
        "rate_limit_per_day": 1000
    }
}

# Время жизни timestamp (секунды)
TIMESTAMP_TOLERANCE = int(os.getenv("TIMESTAMP_TOLERANCE", "300"))  # 5 минут

# Хранилище использованных nonce (в production использовать Redis)
_used_nonces: Set[str] = set()
_nonce_timestamps: Dict[str, float] = {}

# Rate limiting (в production использовать Redis)
_rate_limits: Dict[str, list] = defaultdict(list)


class SecurityConfig(BaseModel):
    """Конфигурация безопасности"""
    enable_signature_check: bool = True
    enable_timestamp_check: bool = True
    enable_nonce_check: bool = True
    enable_rate_limiting: bool = True
    enable_app_whitelist: bool = True


# Загружаем конфиг из переменных окружения
SECURITY_CONFIG = SecurityConfig(
    enable_signature_check=os.getenv("ENABLE_SIGNATURE_CHECK", "true").lower() == "true",
    enable_timestamp_check=os.getenv("ENABLE_TIMESTAMP_CHECK", "true").lower() == "true",
    enable_nonce_check=os.getenv("ENABLE_NONCE_CHECK", "true").lower() == "true",
    enable_rate_limiting=os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true",
    enable_app_whitelist=os.getenv("ENABLE_APP_WHITELIST", "true").lower() == "true"
)


def get_hmac_secret() -> str:
    """Получить HMAC секрет из переменных окружения"""
    secret = os.getenv("HMAC_SECRET")
    if not secret:
        # Fallback на API_SECRET_KEY если HMAC_SECRET не задан
        secret = os.getenv("API_SECRET_KEY")
    if not secret:
        logger.warning("HMAC_SECRET not configured! Using default (INSECURE)")
        secret = "default_insecure_secret_change_me"
    return secret


def get_encryption_key(app_id: Optional[str] = None) -> str:
    """
    Получить ключ шифрования с поддержкой индивидуальных ключей по app_id.
    Если ENCRYPTION_KEY не задан, используется API_SECRET_KEY.
    
    Args:
        app_id: ID приложения (опционально)
    """
    # Пытаемся получить индивидуальный ключ для app_id
    if app_id:
        try:
            from app_keys import get_encryption_key as get_app_enc_key
            # ВАЖНО: force_reload=True чтобы всегда читать свежие ключи из файла
            key = get_app_enc_key(app_id, force_reload=True)
            if key:
                return key
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback на дефолтные ключи из env
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        key = os.getenv("API_SECRET_KEY")
    if not key:
        raise ValueError("Neither ENCRYPTION_KEY nor API_SECRET_KEY is set")
    return key


def verify_api_key(x_api_key: str = Header(None), x_app_id: str = Header(None)) -> str:
    """
    Базовая проверка API ключа с поддержкой индивидуальных ключей по app_id
    
    Args:
        x_api_key: API ключ из заголовка X-API-KEY
        x_app_id: ID приложения из заголовка X-APP-ID (опционально)
        
    Returns:
        Валидный API ключ
        
    Raises:
        HTTPException: Если ключ невалидный
    """
    if not x_api_key:
        raise HTTPException(
            status_code=401, 
            detail="Missing API key. Provide X-API-KEY header."
        )
    
    # Пытаемся получить индивидуальный ключ для app_id
    expected_key = None
    if x_app_id:
        try:
            from app_keys import get_api_key
            # IMPORTANT: force_reload=True to always read fresh keys from file
            expected_key = get_api_key(x_app_id, force_reload=True)
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback на дефолтный ключ из env
    if not expected_key:
        expected_key = os.getenv("API_SECRET_KEY")
    
    if not expected_key:
        logger.error("API_SECRET_KEY not set in environment!")
        raise HTTPException(
            status_code=500, 
            detail="Server misconfiguration: API_SECRET_KEY not set"
        )
    
    if x_api_key != expected_key:
        logger.warning(f"Invalid API key attempt for app_id: {x_app_id or 'unknown'}")
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    return x_api_key


def verify_api_key_from_payload(api_key: str, app_id: str = None) -> str:
    """
    Проверка API ключа из расшифрованного payload (для encrypted requests).
    
    Используется когда api_key передаётся внутри зашифрованных данных,
    а не в HTTP заголовках, для защиты от перехвата.
    
    Args:
        api_key: API ключ из расшифрованного payload
        app_id: ID приложения из расшифрованного payload
        
    Returns:
        Валидный API ключ
        
    Raises:
        HTTPException: Если ключ невалидный или отсутствует
    """
    if not api_key:
        raise HTTPException(
            status_code=401, 
            detail="Missing API key in encrypted payload"
        )
    
    # Получаем индивидуальный ключ для app_id
    expected_key = None
    if app_id:
        try:
            from app_keys import get_api_key
            # IMPORTANT: force_reload=True to always read fresh keys from file
            expected_key = get_api_key(app_id, force_reload=True)
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback на дефолтный ключ из env
    if not expected_key:
        expected_key = os.getenv("API_SECRET_KEY")
    
    if not expected_key:
        logger.error("API_SECRET_KEY not set in environment!")
        raise HTTPException(
            status_code=500, 
            detail="Server misconfiguration: API_SECRET_KEY not set"
        )
    
    if api_key != expected_key:
        logger.warning(f"Invalid API key in payload for app_id: {app_id or 'unknown'}")
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    logger.info(f"API key verified from encrypted payload for app_id: {app_id or 'unknown'}")
    return api_key


def verify_app_id(x_app_id: str = Header(None)) -> dict:
    """
    Проверка идентификатора приложения
    
    Args:
        x_app_id: ID приложения из заголовка X-APP-ID
        
    Returns:
        Конфигурация приложения из whitelist
        
    Raises:
        HTTPException: Если приложение не в whitelist
    """
    if not SECURITY_CONFIG.enable_app_whitelist:
        return {"name": "unknown", "rate_limit_per_minute": 60}
    
    if not x_app_id:
        # Для обратной совместимости разрешаем запросы без APP_ID
        # но с ограниченным rate limit
        logger.warning("Request without X-APP-ID header")
        return {"name": "legacy", "rate_limit_per_minute": 10}
    
    if x_app_id not in ALLOWED_APPS:
        logger.warning(f"Unknown APP_ID: {x_app_id}")
        raise HTTPException(
            status_code=403,
            detail=f"Application '{x_app_id}' is not authorized"
        )
    
    return ALLOWED_APPS[x_app_id]


def verify_timestamp(x_timestamp: str = Header(None)) -> int:
    """
    Проверка timestamp запроса (защита от replay attacks)
    
    Args:
        x_timestamp: Unix timestamp из заголовка X-Timestamp
        
    Returns:
        Валидный timestamp
        
    Raises:
        HTTPException: Если timestamp устарел или невалидный
    """
    if not SECURITY_CONFIG.enable_timestamp_check:
        return int(time.time())
    
    if not x_timestamp:
        # Для обратной совместимости
        return int(time.time())
    
    try:
        request_time = int(x_timestamp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")
    
    current_time = int(time.time())
    time_diff = abs(current_time - request_time)
    
    if time_diff > TIMESTAMP_TOLERANCE:
        logger.warning(f"Expired timestamp: diff={time_diff}s, tolerance={TIMESTAMP_TOLERANCE}s")
        raise HTTPException(
            status_code=401,
            detail=f"Request timestamp expired (diff: {time_diff}s, max: {TIMESTAMP_TOLERANCE}s)"
        )
    
    return request_time


def verify_nonce(x_nonce: str = Header(None)) -> str:
    """
    Проверка уникальности nonce (защита от replay attacks)
    
    Args:
        x_nonce: Уникальный идентификатор запроса из X-Nonce
        
    Returns:
        Валидный nonce
        
    Raises:
        HTTPException: Если nonce уже использовался
    """
    if not SECURITY_CONFIG.enable_nonce_check:
        return ""
    
    if not x_nonce:
        # Для обратной совместимости
        return ""
    
    # Очистка старых nonce (старше TIMESTAMP_TOLERANCE)
    _cleanup_old_nonces()
    
    if x_nonce in _used_nonces:
        logger.warning(f"Duplicate nonce detected: {x_nonce[:8]}...")
        raise HTTPException(
            status_code=401,
            detail="Nonce already used (possible replay attack)"
        )
    
    # Сохраняем nonce
    _used_nonces.add(x_nonce)
    _nonce_timestamps[x_nonce] = time.time()
    
    return x_nonce


def _cleanup_old_nonces():
    """Очистка устаревших nonce"""
    current_time = time.time()
    expired_nonces = [
        nonce for nonce, ts in _nonce_timestamps.items()
        if current_time - ts > TIMESTAMP_TOLERANCE * 2
    ]
    for nonce in expired_nonces:
        _used_nonces.discard(nonce)
        _nonce_timestamps.pop(nonce, None)


def verify_signature(
    payload: dict,
    x_timestamp: str = Header(None),
    x_nonce: str = Header(None),
    x_signature: str = Header(None)
) -> bool:
    """
    Проверка HMAC подписи запроса
    
    Args:
        payload: Тело запроса
        x_timestamp: Timestamp из заголовка
        x_nonce: Nonce из заголовка
        x_signature: Подпись из заголовка X-Signature
        
    Returns:
        True если подпись валидна
        
    Raises:
        HTTPException: Если подпись невалидна
    """
    if not SECURITY_CONFIG.enable_signature_check:
        return True
    
    if not x_signature:
        # Для обратной совместимости
        logger.warning("Request without signature")
        return True
    
    secret = get_hmac_secret()
    
    # Формируем строку для подписи
    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sign_string = f"{x_timestamp or ''}:{x_nonce or ''}:{payload_json}"
    
    # Вычисляем ожидаемую подпись
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Безопасное сравнение (защита от timing attacks)
    if not hmac.compare_digest(x_signature, expected_signature):
        logger.warning("Invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    return True


def check_rate_limit(app_id: str, app_config: dict) -> bool:
    """
    Проверка rate limit для приложения
    
    Args:
        app_id: Идентификатор приложения
        app_config: Конфигурация приложения
        
    Returns:
        True если лимит не превышен
        
    Raises:
        HTTPException: Если лимит превышен
    """
    if not SECURITY_CONFIG.enable_rate_limiting:
        return True
    
    current_time = time.time()
    rate_limit = app_config.get("rate_limit_per_minute", 60)
    
    # Очищаем старые записи (старше 1 минуты)
    _rate_limits[app_id] = [
        ts for ts in _rate_limits[app_id]
        if current_time - ts < 60
    ]
    
    # Проверяем лимит
    if len(_rate_limits[app_id]) >= rate_limit:
        logger.warning(f"Rate limit exceeded for {app_id}")
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({rate_limit} requests/minute)"
        )
    
    # Добавляем текущий запрос
    _rate_limits[app_id].append(current_time)
    
    return True


# === КЛИЕНТСКИЕ ФУНКЦИИ (для compatible AES-256-GCM clients) ===

def create_signed_headers(
    payload: dict,
    api_key: str,
    hmac_secret: str,
    app_id: str = "example-app"
) -> dict:
    """
    Создание заголовков с подписью для безопасного запроса
    
    Используется в compatible AES-256-GCM clients для формирования запросов.
    
    Args:
        payload: Тело запроса
        api_key: API ключ
        hmac_secret: Секрет для HMAC подписи
        app_id: Идентификатор приложения
        
    Returns:
        Словарь заголовков для HTTP запроса
    """
    import uuid
    
    # Генерируем timestamp и nonce
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    
    # Формируем строку для подписи
    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sign_string = f"{timestamp}:{nonce}:{payload_json}"
    
    # Вычисляем HMAC-SHA256
    signature = hmac.new(
        hmac_secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "X-API-KEY": api_key,
        "X-APP-ID": app_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }


# === ДЕКОРАТОР ДЛЯ ЗАЩИЩЁННЫХ ENDPOINTS ===

def secure_endpoint(func):
    """
    Декоратор для защиты endpoint'а
    
    Выполняет все проверки безопасности:
    - API ключ
    - APP_ID
    - Timestamp
    - Nonce
    - Rate limit
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Проверки выполняются через Depends в FastAPI
        return await func(*args, **kwargs)
    return wrapper


# === УТИЛИТЫ ===

def get_client_ip(request: Request) -> str:
    """Получить IP адрес клиента"""
    # Проверяем заголовки от прокси
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    return request.client.host if request.client else "unknown"


def log_request(
    request: Request,
    app_id: str,
    endpoint: str,
    status: str = "success"
):
    """Логирование запроса для аудита"""
    client_ip = get_client_ip(request)
    logger.info(
        f"API Request | IP: {client_ip} | App: {app_id} | "
        f"Endpoint: {endpoint} | Status: {status}"
    )


# === ИНИЦИАЛИЗАЦИЯ ===

def init_security():
    """Инициализация модуля безопасности"""
    logger.info("Security module initialized")
    logger.info(f"Signature check: {SECURITY_CONFIG.enable_signature_check}")
    logger.info(f"Timestamp check: {SECURITY_CONFIG.enable_timestamp_check}")
    logger.info(f"Nonce check: {SECURITY_CONFIG.enable_nonce_check}")
    logger.info(f"Rate limiting: {SECURITY_CONFIG.enable_rate_limiting}")
    logger.info(f"App whitelist: {SECURITY_CONFIG.enable_app_whitelist}")
    logger.info(f"Allowed apps: {list(ALLOWED_APPS.keys())}")

