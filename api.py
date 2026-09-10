# -*- coding: utf-8 -*-
"""
TelegramHelper AI API

REST API for AI queries with support for:
- Anthropic Claude
- OpenAI GPT
- Prompt templates
- Multi-layer security

Author: TelegramHelper contributors
Date: 24.11.2025
"""

import os
import logging
import time
import uuid
import struct
import base64
import json
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Union
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from utils import (
    get_ai_completion, ANTHROPIC_AVAILABLE, OPENAI_AVAILABLE,
    load_prompt_templates, get_prompt_categories, render_prompt
)

# Import security module
try:
    from security import (
        verify_api_key,
        verify_app_id,
        verify_timestamp,
        verify_nonce,
        verify_signature,
        check_rate_limit,
        log_request,
        init_security,
        SECURITY_CONFIG,
        get_client_ip,
        get_encryption_key
    )
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
    logging.warning("Security module not available, using basic authentication only")

# Import encryption module
try:
    from encryption import SecureMessenger, EncryptionError
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    logging.warning("Encryption module not available")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="TelegramHelper AI API",
    description="Secure AI API for compatible AES-256-GCM clients integration",
    version="3.19.5"
)

# Add CORS middleware for Tauri app
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for desktop app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global SecureMessenger instance
secure_messenger: Optional[SecureMessenger] = None

# Initialize security on startup
@app.on_event("startup")
async def startup_event():
    global secure_messenger
    if SECURITY_AVAILABLE:
        init_security()
        
        # Initialize default keys from env
        try:
            from app_keys import init_default_keys
            init_default_keys()
        except ImportError:
            logger.warning("app_keys module not available")
        except Exception as e:
            logger.warning(f"Failed to init default keys: {e}")
        
        if ENCRYPTION_AVAILABLE:
            try:
                # Default SecureMessenger (compatibility)
                # Per-app keys are created dynamically in process_encrypted_request
                key = get_encryption_key()
                secure_messenger = SecureMessenger(key)
                logger.info("SecureMessenger initialized (default key)")
            except Exception as e:
                logger.error(f"Failed to initialize SecureMessenger: {e}")
    logger.info("TelegramHelper AI API started")
    logger.info(f"Security module: {'enabled' if SECURITY_AVAILABLE else 'disabled'}")
    logger.info(f"Anthropic: {'available' if ANTHROPIC_AVAILABLE else 'not available'}")
    logger.info(f"OpenAI: {'available' if OPENAI_AVAILABLE else 'not available'}")


# === MODELS ===

class AIQueryRequest(BaseModel):
    prompt: str = ""
    provider: Optional[str] = "anthropic"
    max_tokens: Optional[int] = 1000
    model: Optional[str] = None
    template_category: Optional[str] = None
    input_text: Optional[str] = None
    # Chat mode with history
    chat_mode: Optional[bool] = False  # True = chat with history, False = simple Q&A
    conversation_id: Optional[str] = None  # Conversation ID for chat mode


class AIQueryResponse(BaseModel):
    response: str
    provider: str
    model: Optional[str] = None
    status: str = "success"
    template_used: Optional[str] = None
    request_id: Optional[str] = None
    processing_time_ms: Optional[int] = None
    mode: Optional[str] = None  # plain/encrypted transfer mode
    conversation_id: Optional[str] = None  # Conversation ID (chat mode)
    chat_mode: Optional[bool] = False  # Mode (chat/simple)


class UniversalRequest(BaseModel):
    """Universal request model — supports both modes."""
    # Plain-request fields
    prompt: Optional[str] = None
    provider: Optional[str] = "anthropic"
    max_tokens: Optional[int] = 1000
    model: Optional[str] = None
    template_category: Optional[str] = None
    input_text: Optional[str] = None
    # Chat mode with history
    chat_mode: Optional[bool] = False  # True = chat with history, False = simple Q&A
    conversation_id: Optional[str] = None  # Conversation ID for chat mode
    # Encrypted request field (Base64)
    data: Optional[str] = None


class PromptTemplate(BaseModel):
    category: str
    title: str
    template: str


class PromptTemplatesResponse(BaseModel):
    templates: List[PromptTemplate]
    count: int


class HealthResponse(BaseModel):
    status: str
    version: str
    security: str
    encryption: str
    providers: Dict[str, bool]


class CancelRequest(BaseModel):
    """Model for cancel request"""
    request_id: str


# === ACTIVE REQUESTS TRACKING ===
# Dictionary to track active AI requests: {request_id: {status, cancel_requested, timestamp}}
active_requests: Dict[str, Dict[str, Any]] = {}


# === FALLBACK SECURITY (if the security module is unavailable) ===

def basic_verify_api_key(x_api_key: str = Header(None)):
    """Basic API key check (fallback)."""
    expected_key = os.getenv("API_SECRET_KEY")
    if not expected_key:
        logger.warning("API_SECRET_KEY not set!")
        raise HTTPException(status_code=500, detail="Server misconfiguration")
    
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    if x_api_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    return x_api_key


# === SECURITY DEPENDENCIES ===

async def full_security_check(
    request: Request,
    x_api_key: str = Header(None),
    x_app_id: str = Header(None),
    x_timestamp: str = Header(None),
    x_nonce: str = Header(None),
    x_signature: str = Header(None)
) -> Dict[str, Any]:
    """
    Full request security check.

    Returns:
        Dict with request info
    """
    start_time = time.time()
    request_id = f"req_{int(start_time)}_{uuid.uuid4().hex[:8]}"
    
    if SECURITY_AVAILABLE:
        # 1. API key (with per-app_id keys)
        verify_api_key(x_api_key, x_app_id)
        
        # 2. APP_ID
        app_config = verify_app_id(x_app_id)
        
        # 3. Timestamp
        verify_timestamp(x_timestamp)
        
        # 4. Nonce
        verify_nonce(x_nonce)
        
        # 5. Rate limiting
        check_rate_limit(x_app_id or "unknown", app_config)
        
        # Audit log
        log_request(request, x_app_id or "unknown", request.url.path)
        
        return {
            "request_id": request_id,
            "app_id": x_app_id,
            "app_config": app_config,
            "start_time": start_time,
            "client_ip": get_client_ip(request)
        }
    else:
        # Fallback to basic check
        basic_verify_api_key(x_api_key)
        return {
            "request_id": request_id,
            "app_id": "unknown",
            "app_config": {},
            "start_time": start_time,
            "client_ip": request.client.host if request.client else "unknown"
        }


async def encrypted_security_check(
    request: Request,
    x_timestamp: str = Header(None),
    x_nonce: str = Header(None)
) -> Dict[str, Any]:
    """
    Lightweight security check for encrypted requests.

    API key and app_id are NOT required in headers — they are
    extracted and verified from the decrypted payload.

    This keeps the API key out of network traffic.

    Returns:
        Dict with basic request info (no app_id)
    """
    start_time = time.time()
    request_id = f"req_{int(start_time)}_{uuid.uuid4().hex[:8]}"
    
    if SECURITY_AVAILABLE:
        # Timestamp and nonce only (replay protection)
        verify_timestamp(x_timestamp)
        verify_nonce(x_nonce)
        
        logger.info(f"[{request_id}] Encrypted request - API key will be verified from payload")
    
    return {
        "request_id": request_id,
        "app_id": None,  # Will be extracted from encrypted payload
        "app_config": {},
        "start_time": start_time,
        "client_ip": get_client_ip(request) if SECURITY_AVAILABLE else (request.client.host if request.client else "unknown"),
        "encrypted_mode": True  # Flag to indicate credentials are in payload
    }


# === ENDPOINTS ===

@app.get("/", response_model=HealthResponse)
async def root():
    """API status check."""
    return HealthResponse(
        status="running",
        version="3.19.5",
        security="full" if SECURITY_AVAILABLE else "basic",
        encryption="available" if (ENCRYPTION_AVAILABLE and secure_messenger) else "disabled",
        providers={
            "anthropic": ANTHROPIC_AVAILABLE,
            "openai": OPENAI_AVAILABLE
        }
    )


@app.get("/health")
async def health_check():
    """Service health endpoint."""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "security_module": SECURITY_AVAILABLE,
        "anthropic_available": ANTHROPIC_AVAILABLE,
        "openai_available": OPENAI_AVAILABLE
    }


# =====================================================================
# SECURITY: Bot management endpoints removed for security.
# All management operations are now SSH-only.
# Use scripts/ssh/*.py for management via SSH:
#   - scripts/ssh/info.py - server stats
#   - scripts/disable_bot.sh - disable bot
#   - scripts/change_token.sh - restore bot
# =====================================================================

@app.get("/prompt_templates", response_model=PromptTemplatesResponse)
async def get_templates():
    """
    List available prompt templates.
    Public endpoint — no auth required.
    """
    templates = load_prompt_templates()
    template_list = [
        PromptTemplate(
            category=cat,
            title=data.get("title", cat),
            template=data.get("template", "")
        )
        for cat, data in templates.items()
    ]
    return PromptTemplatesResponse(templates=template_list, count=len(template_list))


@app.get("/prompt_categories")
async def get_categories():
    """
    List template categories.
    Public endpoint — no auth required.
    """
    categories = get_prompt_categories()
    return {"categories": categories, "count": len(categories)}


@app.post("/cancel_request")
async def cancel_request(
    cancel_req: CancelRequest,
    security_info: Dict = Depends(full_security_check)
):
    """
    Cancel an active AI request.
    
    Headers:
    - X-API-KEY: required API key
    - X-APP-ID: application identifier
    """
    request_id = cancel_req.request_id
    app_id = security_info.get("app_id", "unknown")
    
    logger.info(f"Cancel request received from {app_id} for request_id: {request_id}")
    
    if request_id in active_requests:
        active_requests[request_id]["cancel_requested"] = True
        active_requests[request_id]["cancelled_at"] = time.time()
        logger.info(f"Request {request_id} marked for cancellation")
        return {
            "status": "cancelled",
            "request_id": request_id,
            "message": "Request marked for cancellation"
        }
    else:
        # Request might have already completed or doesn't exist
        logger.warning(f"Request {request_id} not found in active requests")
        return {
            "status": "not_found",
            "request_id": request_id,
            "message": "Request not found or already completed"
        }


@app.post("/echo")
async def echo_test(
    request_body: UniversalRequest,
    request: Request,
    security_info: Dict = Depends(full_security_check),
    x_app_id: str = Header(None, alias="X-APP-ID")
):
    """
    Echo endpoint for connectivity tests.
    Returns the sent message without calling an AI provider.
    Supports both plain and encrypted modes.
    
    Headers:
    - X-API-KEY: required API key
    - X-APP-ID: application identifier
    """
    request_id = security_info["request_id"]
    start_time = security_info["start_time"]
    app_id = security_info.get("app_id", "unknown")
    
    # Auto-detect mode
    if request_body.data:
        # Encrypted mode
        return await process_echo_encrypted(request_body.data, security_info, x_app_id)
    else:
        # Plain mode
        message = request_body.prompt or "Echo 123456789"
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"[{request_id}] Echo test from {app_id} | Time: {processing_time}ms | Message: {message}")
        
        response = AIQueryResponse(
            response=f"Echo: {message}",
            provider="echo-server",
            model="none",
            status="success",
            request_id=request_id,
            processing_time_ms=processing_time,
            mode="plain"
        )
        return response


async def process_echo_encrypted(
    data: str,
    security_info: Dict,
    header_app_id: str = None
) -> Dict:
    """
    Handle an encrypted echo request.

    SECURITY: API key and app_id are taken from the decrypted payload,
    not HTTP headers, to protect them from interception.
    The X-APP-ID header is used only to pick the encryption key.
    """
    if not ENCRYPTION_AVAILABLE:
        raise HTTPException(
            status_code=503, 
            detail="Encryption not available on server"
        )
    
    try:
        request_id = security_info.get("request_id", "unknown")
        start_time = security_info.get("start_time", time.time())
        
        # SecureMessenger with the per-app_id key (or default)
        from encryption import SecureMessenger
        from security import get_encryption_key, verify_api_key_from_payload, verify_app_id, check_rate_limit
        
        try:
            # X-APP-ID header selects the encryption key
            enc_key = get_encryption_key(header_app_id)
            messenger = SecureMessenger(enc_key)
            logger.info(f"[{request_id}] Using encryption key for app_id: {header_app_id or 'default'}")
        except Exception as e:
            logger.error(f"[{request_id}] Failed to create SecureMessenger: {e}")
            raise HTTPException(status_code=500, detail="Encryption key error")
        
        # 1. Decode Base64
        try:
            encrypted_bytes = base64.b64decode(data)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Base64 data")

        # 2. Decrypt
        try:
            decrypted_data = messenger.decrypt(encrypted_bytes)
        except Exception as e:
            logger.error(f"[{request_id}] Decryption failed: {e}")
            raise HTTPException(status_code=400, detail="Decryption failed")
            
        # 3. Parse JSON
        if isinstance(decrypted_data, bytes):
            try:
                query_data = json.loads(decrypted_data.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in encrypted payload")
        else:
            query_data = decrypted_data
        
        # 4. SECURITY: Extract and verify credentials from decrypted payload
        api_key = query_data.pop("api_key", None)
        app_id = query_data.pop("app_id", None)
        
        # Verify API key from payload (not from headers!)
        if SECURITY_AVAILABLE:
            verify_api_key_from_payload(api_key, app_id)
            
            # Verify app_id and check rate limits
            if app_id:
                app_config = verify_app_id(app_id)
                check_rate_limit(app_id, app_config)
                security_info["app_id"] = app_id
            
            logger.info(f"[{request_id}] Echo API key verified from encrypted payload for {app_id or 'unknown'}")
        else:
            # Fallback: basic API key check
            expected_key = os.getenv("API_SECRET_KEY")
            if not api_key or api_key != expected_key:
                raise HTTPException(status_code=403, detail="Invalid API key")
        
        # Get the message
        message = query_data.get("prompt", "Echo 123456789")
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"[{request_id}] Encrypted echo test from {app_id or 'unknown'} | Time: {processing_time}ms | Message: {message}")
        
        # Build the response
        response_dict = {
            "response": f"Echo: {message}",
            "provider": "echo-server",
            "model": "none",
            "status": "success",
            "request_id": request_id,
            "processing_time_ms": processing_time,
            "mode": "encrypted"
        }
        
        # 5. Encrypt response
        encrypted_response_bytes = messenger.encrypt(response_dict)
        
        # 6. Encode Response to Base64
        b64_response = base64.b64encode(encrypted_response_bytes).decode('utf-8')
        
        return {"data": b64_response, "mode": "encrypted"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Error in encrypted echo: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")



@app.post("/ai_query")
async def ai_query(
    request_body: UniversalRequest,
    request: Request,
    security_info: Dict = Depends(full_security_check),
    x_app_id: str = Header(None, alias="X-APP-ID")
):
    """
    Universal AI query with auto-detected transfer mode.

    Detects transfer mode automatically:
    - If "data" (Base64) is present → encrypted mode
    - If "prompt" is present → plain mode

    Supports:
    - Direct prompt (prompt)
    - Template (template_category + input_text)
    - Provider choice (anthropic/openai)
    - Encrypted payload (data as Base64)

    Headers:
    - X-API-KEY: required API key
    - X-APP-ID: application identifier (recommended)
    - X-Timestamp: request Unix timestamp
    - X-Nonce: unique request ID
    """
    # Auto-detect mode
    if request_body.data:
        # Encrypted mode (Base64)
        return await process_encrypted_request(request_body.data, security_info, x_app_id)
    else:
        # Plain mode
        query_request = AIQueryRequest(
            prompt=request_body.prompt or "",
            provider=request_body.provider,
            max_tokens=request_body.max_tokens,
            model=request_body.model,
            template_category=request_body.template_category,
            input_text=request_body.input_text,
            chat_mode=request_body.chat_mode,
            conversation_id=request_body.conversation_id
        )
        response = await process_ai_request(query_request, security_info)
        response.mode = "plain"
        return response


async def process_encrypted_request(
    data: str,
    security_info: Dict,
    header_app_id: str = None
) -> Union[Dict, AIQueryResponse]:
    """
    Handle an encrypted request (Base64 + AES-256-GCM).

    SECURITY: API key and app_id are taken from the decrypted payload,
    not HTTP headers, to protect them from interception.
    The X-APP-ID header is used only to pick the encryption key.

    Uses a per-app_id encryption key when configured.
    """
    if not ENCRYPTION_AVAILABLE:
        raise HTTPException(
            status_code=503, 
            detail="Encryption not available on server"
        )
    
    request_id = security_info.get("request_id", "unknown")
    
    try:
        # X-APP-ID header selects the encryption key
        from encryption import SecureMessenger
        from security import get_encryption_key, verify_api_key_from_payload, verify_app_id, check_rate_limit
        
        try:
            # Per-app_id key (or default)
            enc_key = get_encryption_key(header_app_id)
            messenger = SecureMessenger(enc_key)
            logger.info(f"[{request_id}] Using encryption key for app_id: {header_app_id or 'default'}")
        except Exception as e:
            logger.error(f"[{request_id}] Failed to create SecureMessenger: {e}")
            raise HTTPException(status_code=500, detail="Encryption key error")
        
        # 1. Decode Base64
        try:
            encrypted_bytes = base64.b64decode(data)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Base64 data")

        # 2. Decrypt
        try:
            decrypted_data = messenger.decrypt(encrypted_bytes)
        except Exception as e:
            logger.error(f"[{request_id}] Decryption failed: {e}")
            raise HTTPException(status_code=400, detail="Decryption failed")
            
        # 3. Parse JSON
        if isinstance(decrypted_data, bytes):
            try:
                query_data = json.loads(decrypted_data.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in encrypted payload")
        else:
            query_data = decrypted_data
        
        # 4. SECURITY: Extract and verify credentials from decrypted payload
        api_key = query_data.pop("api_key", None)
        app_id = query_data.pop("app_id", None)
        
        # Verify API key from payload (not from headers!)
        if SECURITY_AVAILABLE:
            verify_api_key_from_payload(api_key, app_id)
            
            # Verify app_id and check rate limits
            if app_id:
                app_config = verify_app_id(app_id)
                check_rate_limit(app_id, app_config)
                security_info["app_id"] = app_id
                security_info["app_config"] = app_config
            
            logger.info(f"[{request_id}] API key verified from encrypted payload for {app_id or 'unknown'}")
        else:
            # Fallback: basic API key check
            expected_key = os.getenv("API_SECRET_KEY")
            if not api_key or api_key != expected_key:
                raise HTTPException(status_code=403, detail="Invalid API key")
            
        # 5. Validate and create request (without api_key/app_id fields)
        try:
            query_request = AIQueryRequest(**query_data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid request format: {e}")
            
        # 6. Process request
        response = await process_ai_request(query_request, security_info)
        response.mode = "encrypted"
        
        # 7. Encrypt response (same messenger)
        response_dict = response.dict()
        encrypted_response_bytes = messenger.encrypt(response_dict)
        
        # 8. Encode Response to Base64
        b64_response = base64.b64encode(encrypted_response_bytes).decode('utf-8')
        
        return {"data": b64_response, "mode": "encrypted"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Error in encrypted request: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


async def process_ai_request(
    request_body: AIQueryRequest,
    security_info: Dict
) -> AIQueryResponse:
    """
    Internal AI request handler.
    Used by both the plain and encrypted endpoints.

    Two modes:
    - simple (chat_mode=False): single Q&A, no history
    - chat (chat_mode=True): chat mode with persisted history
    """
    request_id = security_info["request_id"]
    start_time = security_info["start_time"]
    app_id = security_info.get("app_id", "unknown")
    
    # Register request as active
    active_requests[request_id] = {
        "status": "processing",
        "cancel_requested": False,
        "started_at": start_time,
        "app_id": app_id
    }
    logger.info(f"[{request_id}] Registered as active request")
    
    # Chat mode
    chat_mode = request_body.chat_mode or False
    conversation_id = request_body.conversation_id
    logger.info(
        f"[{request_id}] AI query from {app_id} | "
        f"Provider: {request_body.provider} | "
        f"Mode: {'chat' if chat_mode else 'simple'} | "
        f"Conversation: {conversation_id or 'new'}"
    )
    
    # Resolve the final prompt
    final_prompt = request_body.prompt
    template_used = None
    
    # Use a template when provided
    if request_body.template_category and request_body.input_text:
        rendered = render_prompt(request_body.template_category, request_body.input_text)
        if rendered:
            final_prompt = rendered
            template_used = request_body.template_category
            logger.info(f"[{request_id}] Using template: {request_body.template_category}")
        else:
            logger.warning(f"[{request_id}] Template '{request_body.template_category}' not found")
    
    if not final_prompt:
        raise HTTPException(
            status_code=400,
            detail="Prompt is required (either direct or via template)"
        )
    
    # Conversation history (chat mode)
    conversation_history = None
    if chat_mode:
        try:
            from conversation_history import (
                get_conversation_history,
                add_message_to_history,
                generate_conversation_id
            )
            
            # Generate or reuse conversation_id
            if not conversation_id:
                conversation_id = generate_conversation_id(app_id)
                logger.info(f"[{request_id}] Generated new conversation_id: {conversation_id}")
            
            # Load conversation history
            conversation_history = get_conversation_history(conversation_id)
            logger.info(f"[{request_id}] Loaded {len(conversation_history)} messages from history")
            
        except ImportError:
            logger.warning(f"[{request_id}] conversation_history module not available, falling back to simple mode")
            chat_mode = False
        except Exception as e:
            logger.error(f"[{request_id}] Error loading conversation history: {e}")
            chat_mode = False
    
    # Resolve provider
    provider = request_body.provider.lower() if request_body.provider else "anthropic"
    
    # Provider availability with fallback
    if provider == "anthropic" and not ANTHROPIC_AVAILABLE:
        if OPENAI_AVAILABLE:
            logger.warning(f"[{request_id}] Anthropic not available, falling back to OpenAI")
            provider = "openai"
        else:
            raise HTTPException(status_code=503, detail="No AI providers available")
    
    if provider == "openai" and not OPENAI_AVAILABLE:
        if ANTHROPIC_AVAILABLE:
            logger.warning(f"[{request_id}] OpenAI not available, falling back to Anthropic")
            provider = "anthropic"
        else:
            raise HTTPException(status_code=503, detail="No AI providers available")
    
    # Check if cancel was requested before calling AI
    if active_requests.get(request_id, {}).get("cancel_requested", False):
        logger.info(f"[{request_id}] Request cancelled before AI call")
        # Cleanup
        if request_id in active_requests:
            del active_requests[request_id]
        raise HTTPException(status_code=499, detail="Request cancelled by client")
    
    # Call the AI provider
    try:
        response_text = None
        model_used = None
        
        if provider == "anthropic":
            from utils import get_anthropic_completion, get_current_model
            model_used = get_current_model("anthropic")
            response_text = get_anthropic_completion(
                final_prompt, 
                request_body.max_tokens,
                conversation_history=conversation_history
            )
        elif provider == "openai":
            from utils import get_openai_completion, get_current_model
            model_used = get_current_model("openai")
            response_text = get_openai_completion(
                final_prompt, 
                request_body.max_tokens,
                conversation_history=conversation_history
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
        
        if not response_text:
            raise HTTPException(status_code=500, detail="AI provider returned empty response")
        
        # Save to conversation history (chat mode)
        if chat_mode and conversation_id:
            try:
                from conversation_history import add_message_to_history
                # Save user prompt
                add_message_to_history(conversation_id, "user", final_prompt)
                # Save assistant reply
                add_message_to_history(conversation_id, "assistant", response_text)
                logger.info(f"[{request_id}] Saved messages to conversation history")
            except Exception as e:
                logger.error(f"[{request_id}] Error saving to conversation history: {e}")
        
        # Processing time
        processing_time = int((time.time() - start_time) * 1000)
        
        logger.info(f"[{request_id}] Success | Provider: {provider} | Model: {model_used} | Time: {processing_time}ms")
        
        return AIQueryResponse(
            response=response_text,
            provider=provider,
            model=model_used,
            status="success",
            template_used=template_used,
            request_id=request_id,
            processing_time_ms=processing_time,
            conversation_id=conversation_id if chat_mode else None,
            chat_mode=chat_mode
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup: remove from active requests
        if request_id in active_requests:
            del active_requests[request_id]
            logger.info(f"[{request_id}] Removed from active requests")


@app.post("/ai_query/encrypted")
async def ai_query_encrypted(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """
    Encrypted endpoint for AI queries (Binary Protocol).
    Expects raw binary body with custom packet format.
    """
    try:
        # Read raw binary body
        encrypted_body = await request.body()
        
        if not encrypted_body:
            raise HTTPException(status_code=400, detail="Empty request body")
            
        # Decrypt
        try:
            decrypted_data = secure_messenger.decrypt(encrypted_body)
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise HTTPException(status_code=400, detail="Decryption failed")
            
        # Parse JSON if needed (decrypt might return bytes or dict)
        if isinstance(decrypted_data, bytes):
            try:
                query_data = json.loads(decrypted_data.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in encrypted payload")
        else:
            query_data = decrypted_data
            
        # Validate schema
        try:
            query_request = AIQueryRequest(**query_data)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
            
        # Process request
        response = await process_ai_request(query_request, security_info)
        
        # Encrypt response
        response_dict = response.dict()
        encrypted_response = secure_messenger.encrypt(response_dict)
        
        # Return binary response
        return Response(content=encrypted_response, media_type="application/octet-stream")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in encrypted endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class ObfuscatedRequest(BaseModel):
    data: str  # Base64 encoded encrypted packet


@app.post("/ai_query/secure")
async def ai_query_secure(
    request: ObfuscatedRequest,
    security_info: Dict = Depends(encrypted_security_check),
    x_app_id: str = Header(None, alias="X-APP-ID")
):
    """
    Secure endpoint for AI queries with protected API key.
    
    SECURITY: API key and app_id are sent INSIDE the encrypted payload,
    not in HTTP headers. This protects credentials from interception.
    The X-APP-ID header is used only to pick the encryption key.
    
    Expects:
    - Encrypted payload with: api_key, app_id, prompt, provider, etc.
    - X-APP-ID header (optional, for encryption key selection)
    - NO X-API-KEY header required (credentials are encrypted)
    
    Headers (optional, for replay protection):
    - X-Timestamp: Unix timestamp
    - X-Nonce: Unique request ID
    """
    # Use the unified process_encrypted_request which handles API key verification
    return await process_encrypted_request(request.data, security_info, x_app_id)


@app.post("/echo/secure")
async def echo_secure(
    request: ObfuscatedRequest,
    security_info: Dict = Depends(encrypted_security_check),
    x_app_id: str = Header(None, alias="X-APP-ID")
):
    """
    Secure echo endpoint with protected API key.
    
    SECURITY: API key and app_id are sent INSIDE the encrypted payload,
    not in HTTP headers. This protects credentials from interception.
    The X-APP-ID header is used only to pick the encryption key.
    
    Expects:
    - Encrypted payload with: api_key, app_id, prompt
    - X-APP-ID header (optional, for encryption key selection)
    - NO X-API-KEY header required (credentials are encrypted)
    """
    return await process_echo_encrypted(request.data, security_info, x_app_id)


# === CONVERSATION MANAGEMENT ===

@app.post("/conversation/clear")
async def clear_conversation(
    conversation_id: str,
    security_info: Dict = Depends(full_security_check)
):
    """
    Clear conversation history.

    Args:
        conversation_id: Conversation ID to clear

    Returns:
        Operation status
    """
    try:
        from conversation_history import clear_conversation_history
        
        success = clear_conversation_history(conversation_id)
        
        if success:
            return {
                "status": "success",
                "message": f"Conversation {conversation_id} cleared",
                "conversation_id": conversation_id
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation {conversation_id} not found"
            )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Conversation history module not available"
        )
    except Exception as e:
        logger.error(f"Error clearing conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === ADMIN ENDPOINTS (require elevated rights) ===

@app.get("/admin/stats")
async def get_stats(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """
    Get API usage statistics.
    Requires authorization.
    """
    # In production this would come from Redis/PostgreSQL
    return {
        "status": "ok",
        "message": "Statistics endpoint (placeholder)",
        "request_id": security_info["request_id"]
    }


class AdminCommandRequest(BaseModel):
    """Model for admin command request"""
    command: str
    args: Optional[List[str]] = None


class AdminCommandResponse(BaseModel):
    """Model for admin command response"""
    success: bool
    response: str
    command: str
    request_id: Optional[str] = None


@app.post("/admin_command", response_model=AdminCommandResponse)
async def execute_admin_command(
    request_body: AdminCommandRequest,
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """
    Execute an admin command from an sing-box client.
    
    This endpoint allows executing bot commands (like /help, /vless_status)
    without Telegram, useful when the bot is disabled.
    
    Headers:
    - X-API-KEY: required API key
    - X-APP-ID: application identifier
    - X-Timestamp: Unix timestamp
    - X-Nonce: unique request ID
    
    Body:
    - command: Command to execute (e.g., "/help", "/vless_status")
    - args: Optional list of arguments
    
    Returns:
        AdminCommandResponse with success status and text response
    """
    request_id = security_info["request_id"]
    app_id = security_info.get("app_id", "unknown")
    
    command = request_body.command.strip()
    args = request_body.args or []
    
    logger.info(f"[{request_id}] Admin command from {app_id}: {command} {args}")
    
    try:
        from admin_cli import execute_admin_command as exec_cmd
        
        success, response = exec_cmd(command, args)
        
        logger.info(f"[{request_id}] Command {command} executed: success={success}")
        
        return AdminCommandResponse(
            success=success,
            response=response,
            command=command,
            request_id=request_id
        )
        
    except ImportError as e:
        logger.error(f"[{request_id}] admin_cli module not available: {e}")
        return AdminCommandResponse(
            success=False,
            response="❌ Admin CLI module not available on server",
            command=command,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"[{request_id}] Error executing admin command: {e}")
        return AdminCommandResponse(
            success=False,
            response=f"❌ Error: {str(e)}",
            command=command,
            request_id=request_id
        )


@app.post("/admin_command/secure")
async def execute_admin_command_secure(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """
    Execute an admin command with encrypted request/response.
    
    Body: Encrypted JSON containing {command, args}
    Response: Encrypted JSON with AdminCommandResponse
    
    This is the preferred endpoint - non-encrypted /admin_command is deprecated.
    """
    import base64
    from encryption import SecureMessenger
    from security import get_encryption_key
    
    request_id = security_info["request_id"]
    app_id = security_info.get("app_id", "apiai-v3")
    
    # Get encryption key for this app_id (same as echo/secure)
    try:
        encryption_key = get_encryption_key(app_id)
    except Exception as e:
        logger.error(f"[{request_id}] Failed to get encryption key for {app_id}: {e}")
        raise HTTPException(status_code=500, detail="Encryption not configured on server")
    
    messenger = SecureMessenger(encryption_key)
    
    # Read encrypted body (base64 encoded)
    encrypted_body = await request.body()
    encrypted_text = encrypted_body.decode('utf-8')
    
    if not encrypted_text:
        raise HTTPException(status_code=400, detail="Empty request body")
    
    # Decrypt request
    try:
        request_data = messenger.decrypt_json(encrypted_text)
    except Exception as e:
        logger.error(f"[{request_id}] Failed to decrypt admin command: {e}")
        raise HTTPException(status_code=400, detail="Invalid encrypted request")
    
    command = request_data.get("command", "").strip()
    args = request_data.get("args") or []
    
    if not command:
        raise HTTPException(status_code=400, detail="No command specified")
    
    logger.info(f"[{request_id}] Secure admin command from {app_id}: {command}")
    
    try:
        from admin_cli import execute_admin_command as exec_cmd
        
        success, response_text = exec_cmd(command, args)
        
        response_data = AdminCommandResponse(
            success=success,
            response=response_text,
            command=command,
            request_id=request_id
        )
        
        # Encrypt response (returns base64 string)
        encrypted_response = messenger.encrypt_json(response_data.model_dump())
        
        return Response(content=encrypted_response, media_type="text/plain")
        
    except ImportError as e:
        logger.error(f"[{request_id}] admin_cli module not available: {e}")
        error_response = AdminCommandResponse(
            success=False,
            response="❌ Admin CLI module not available on server",
            command=command,
            request_id=request_id
        )
        encrypted_error = messenger.encrypt_json(error_response.model_dump())
        return Response(content=encrypted_error, media_type="text/plain")
    except Exception as e:
        logger.error(f"[{request_id}] Error executing admin command: {e}")
        error_response = AdminCommandResponse(
            success=False,
            response=f"❌ Error: {str(e)}",
            command=command,
            request_id=request_id
        )
        encrypted_error = messenger.encrypt_json(error_response.model_dump())
        return Response(content=encrypted_error, media_type="text/plain")

# === ERROR HANDLERS ===

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP error handler with logging."""
    client_ip = request.client.host if request.client else "unknown"
    logger.warning(
        f"HTTP {exc.status_code} | IP: {client_ip} | "
        f"Path: {request.url.path} | Detail: {exc.detail}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
        "error": exc.detail,
        "status_code": exc.status_code
    }
    )


# === HEADSCALE ENDPOINTS ===

@app.get("/config/combined")
async def get_combined_config(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """Return combined VLESS + Headscale configuration."""
    import vless_manager
    import headscale_manager

    vless_config = vless_manager.export_client_config() if hasattr(vless_manager, 'export_client_config') else {}
    hs_config = headscale_manager.get_config()

    return {
        "vless": vless_config,
        "headscale": {
            "enabled": hs_config.get("enabled", False),
            "server_url": hs_config.get("server_url", ""),
        },
    }


@app.post("/headscale/gen_key")
async def generate_headscale_key(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """Generate a Headscale Pre-Auth key."""
    import headscale_manager

    if not headscale_manager.is_headscale_enabled():
        raise HTTPException(status_code=400, detail="Headscale is not enabled")

    success, message, key = headscale_manager.create_preauth_key()
    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {"success": True, "message": message, "key": key}


@app.get("/headscale/status")
async def headscale_status(
    request: Request,
    security_info: Dict = Depends(full_security_check)
):
    """Get Headscale status."""
    import headscale_manager
    return headscale_manager.get_status()


# === MAIN ===

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
