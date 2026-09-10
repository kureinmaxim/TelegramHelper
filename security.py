# -*- coding: utf-8 -*-
"""
Security module for TelegramHelper API.

Implements:
- HMAC request signatures
- Timestamp checks (replay protection)
- Nonce checks (request uniqueness)
- APP_ID whitelist
- Rate limiting

Author: TelegramHelper contributors
Date: 24.11.2025
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

# === CONFIGURATION ===

# Whitelist of allowed applications
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

# Timestamp lifetime (seconds)
TIMESTAMP_TOLERANCE = int(os.getenv("TIMESTAMP_TOLERANCE", "300"))  # 5 minutes

# Used-nonce store (use Redis in production)
_used_nonces: Set[str] = set()
_nonce_timestamps: Dict[str, float] = {}

# Rate limiting (use Redis in production)
_rate_limits: Dict[str, list] = defaultdict(list)


class SecurityConfig(BaseModel):
    """Security configuration."""
    enable_signature_check: bool = True
    enable_timestamp_check: bool = True
    enable_nonce_check: bool = True
    enable_rate_limiting: bool = True
    enable_app_whitelist: bool = True


# Load config from environment variables
SECURITY_CONFIG = SecurityConfig(
    enable_signature_check=os.getenv("ENABLE_SIGNATURE_CHECK", "true").lower() == "true",
    enable_timestamp_check=os.getenv("ENABLE_TIMESTAMP_CHECK", "true").lower() == "true",
    enable_nonce_check=os.getenv("ENABLE_NONCE_CHECK", "true").lower() == "true",
    enable_rate_limiting=os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true",
    enable_app_whitelist=os.getenv("ENABLE_APP_WHITELIST", "true").lower() == "true"
)


def get_hmac_secret() -> str:
    """Return HMAC secret from environment variables."""
    secret = os.getenv("HMAC_SECRET")
    if not secret:
        # Fallback to API_SECRET_KEY if HMAC_SECRET is unset
        secret = os.getenv("API_SECRET_KEY")
    if not secret:
        logger.warning("HMAC_SECRET not configured! Using default (INSECURE)")
        secret = "default_insecure_secret_change_me"
    return secret


def get_encryption_key(app_id: Optional[str] = None) -> str:
    """
    Get encryption key with per-app_id keys.
    If ENCRYPTION_KEY is unset, API_SECRET_KEY is used.

    Args:
        app_id: Application ID (optional)
    """
    # Try per-app key for app_id
    if app_id:
        try:
            from app_keys import get_encryption_key as get_app_enc_key
            # IMPORTANT: force_reload=True so keys are always read fresh from file
            key = get_app_enc_key(app_id, force_reload=True)
            if key:
                return key
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback to default keys from env
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        key = os.getenv("API_SECRET_KEY")
    if not key:
        raise ValueError("Neither ENCRYPTION_KEY nor API_SECRET_KEY is set")
    return key


def verify_api_key(x_api_key: str = Header(None), x_app_id: str = Header(None)) -> str:
    """
    Basic API key check with per-app_id keys.

    Args:
        x_api_key: API key from X-API-KEY header
        x_app_id: Application ID from X-APP-ID header (optional)

    Returns:
        Valid API key

    Raises:
        HTTPException: If the key is invalid
    """
    if not x_api_key:
        raise HTTPException(
            status_code=401, 
            detail="Missing API key. Provide X-API-KEY header."
        )
    
    # Try per-app key for app_id
    expected_key = None
    if x_app_id:
        try:
            from app_keys import get_api_key
            # IMPORTANT: force_reload=True to always read fresh keys from file
            expected_key = get_api_key(x_app_id, force_reload=True)
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback to default key from env
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
    Verify API key from decrypted payload (encrypted requests).

    Used when api_key is sent inside encrypted data, not HTTP headers,
    to protect it from interception.

    Args:
        api_key: API key from decrypted payload
        app_id: Application ID from decrypted payload

    Returns:
        Valid API key

    Raises:
        HTTPException: If the key is invalid or missing
    """
    if not api_key:
        raise HTTPException(
            status_code=401, 
            detail="Missing API key in encrypted payload"
        )
    
    # Per-app key for app_id
    expected_key = None
    if app_id:
        try:
            from app_keys import get_api_key
            # IMPORTANT: force_reload=True to always read fresh keys from file
            expected_key = get_api_key(app_id, force_reload=True)
        except ImportError:
            logger.warning("app_keys module not available, using default")
    
    # Fallback to default key from env
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
    Verify application identifier.

    Args:
        x_app_id: Application ID from X-APP-ID header

    Returns:
        Application config from the whitelist

    Raises:
        HTTPException: If the app is not on the whitelist
    """
    if not SECURITY_CONFIG.enable_app_whitelist:
        return {"name": "unknown", "rate_limit_per_minute": 60}
    
    if not x_app_id:
        # Backward compatible: allow requests without APP_ID
        # but with a tighter rate limit
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
    Verify request timestamp (replay protection).

    Args:
        x_timestamp: Unix timestamp from X-Timestamp header

    Returns:
        Valid timestamp

    Raises:
        HTTPException: If the timestamp is stale or invalid
    """
    if not SECURITY_CONFIG.enable_timestamp_check:
        return int(time.time())
    
    if not x_timestamp:
        # Backward compatible
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
    Verify nonce uniqueness (replay protection).

    Args:
        x_nonce: Unique request ID from X-Nonce

    Returns:
        Valid nonce

    Raises:
        HTTPException: If the nonce was already used
    """
    if not SECURITY_CONFIG.enable_nonce_check:
        return ""
    
    if not x_nonce:
        # Backward compatible
        return ""
    
    # Drop old nonces (older than TIMESTAMP_TOLERANCE)
    _cleanup_old_nonces()
    
    if x_nonce in _used_nonces:
        logger.warning(f"Duplicate nonce detected: {x_nonce[:8]}...")
        raise HTTPException(
            status_code=401,
            detail="Nonce already used (possible replay attack)"
        )
    
    # Store nonce
    _used_nonces.add(x_nonce)
    _nonce_timestamps[x_nonce] = time.time()
    
    return x_nonce


def _cleanup_old_nonces():
    """Drop expired nonces."""
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
    Verify HMAC request signature.

    Args:
        payload: Request body
        x_timestamp: Timestamp from header
        x_nonce: Nonce from header
        x_signature: Signature from X-Signature header

    Returns:
        True if the signature is valid

    Raises:
        HTTPException: If the signature is invalid
    """
    if not SECURITY_CONFIG.enable_signature_check:
        return True
    
    if not x_signature:
        # Backward compatible
        logger.warning("Request without signature")
        return True
    
    secret = get_hmac_secret()
    
    # Build string to sign
    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sign_string = f"{x_timestamp or ''}:{x_nonce or ''}:{payload_json}"
    
    # Expected signature
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time compare (timing-attack protection)
    if not hmac.compare_digest(x_signature, expected_signature):
        logger.warning("Invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    return True


def check_rate_limit(app_id: str, app_config: dict) -> bool:
    """
    Check rate limit for an application.

    Args:
        app_id: Application identifier
        app_config: Application config

    Returns:
        True if the limit is not exceeded

    Raises:
        HTTPException: If the limit is exceeded
    """
    if not SECURITY_CONFIG.enable_rate_limiting:
        return True
    
    current_time = time.time()
    rate_limit = app_config.get("rate_limit_per_minute", 60)
    
    # Drop entries older than 1 minute
    _rate_limits[app_id] = [
        ts for ts in _rate_limits[app_id]
        if current_time - ts < 60
    ]
    
    # Check limit
    if len(_rate_limits[app_id]) >= rate_limit:
        logger.warning(f"Rate limit exceeded for {app_id}")
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({rate_limit} requests/minute)"
        )
    
    # Record this request
    _rate_limits[app_id].append(current_time)
    
    return True


# === CLIENT HELPERS (for compatible AES-256-GCM clients) ===

def create_signed_headers(
    payload: dict,
    api_key: str,
    hmac_secret: str,
    app_id: str = "example-app"
) -> dict:
    """
    Build signed headers for a secure request.

    Used by compatible AES-256-GCM clients to form requests.

    Args:
        payload: Request body
        api_key: API key
        hmac_secret: Secret for HMAC signature
        app_id: Application identifier

    Returns:
        HTTP headers dict
    """
    import uuid
    
    # Generate timestamp and nonce
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    
    # Build string to sign
    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sign_string = f"{timestamp}:{nonce}:{payload_json}"
    
    # HMAC-SHA256
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


# === DECORATOR FOR PROTECTED ENDPOINTS ===

def secure_endpoint(func):
    """
    Decorator to protect an endpoint.

    Runs all security checks:
    - API key
    - APP_ID
    - Timestamp
    - Nonce
    - Rate limit
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Checks run via FastAPI Depends
        return await func(*args, **kwargs)
    return wrapper


# === UTILITIES ===

def get_client_ip(request: Request) -> str:
    """Return client IP address."""
    # Proxy headers
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
    """Audit-log a request."""
    client_ip = get_client_ip(request)
    logger.info(
        f"API Request | IP: {client_ip} | App: {app_id} | "
        f"Endpoint: {endpoint} | Status: {status}"
    )


# === INITIALIZATION ===

def init_security():
    """Initialize the security module."""
    logger.info("Security module initialized")
    logger.info(f"Signature check: {SECURITY_CONFIG.enable_signature_check}")
    logger.info(f"Timestamp check: {SECURITY_CONFIG.enable_timestamp_check}")
    logger.info(f"Nonce check: {SECURITY_CONFIG.enable_nonce_check}")
    logger.info(f"Rate limiting: {SECURITY_CONFIG.enable_rate_limiting}")
    logger.info(f"App whitelist: {SECURITY_CONFIG.enable_app_whitelist}")
    logger.info(f"Allowed apps: {list(ALLOWED_APPS.keys())}")

