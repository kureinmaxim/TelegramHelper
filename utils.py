"""
Utility functions for the Telegram bot (Lite version).
"""

# Annotations are not evaluated at import: without this, `-> Optional[OpenAI]`
# raises NameError on machines without the openai package, even though
# graceful degradation via OPENAI_AVAILABLE is in place.
from __future__ import annotations

import os
import logging
import time
from typing import Optional, Tuple, List, Dict, Any
from telegram import User, Chat, MessageEntity
from datetime import datetime
import json

logger = logging.getLogger(__name__)

# Conditional imports for AI packages
try:
    import openai
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI package not available. Install with: pip install openai")

# Conditional import for anthropic
try:
    import anthropic
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("Anthropic package not available. Install with: pip install anthropic")

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
    logger.warning("deep_translator package not available. Install with: pip install deep-translator")

try:
    import langid  # type: ignore
    LANGID_AVAILABLE = True
    try:
        langid.set_languages(["ru", "en", "fr", "de"])
    except Exception:
        pass
except ImportError:
    LANGID_AVAILABLE = False

def escape_markdown(text: str) -> str:
    """
    Escape special characters for Markdown V2 format.
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text safe for Markdown V2
    """
    if not text:
        return ""
    
    # Characters that need to be escaped in Markdown V2
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    escaped_text = text
    for char in escape_chars:
        escaped_text = escaped_text.replace(char, f'\\{char}')
    
    return escaped_text

def format_user_info(user: User, chat: Chat) -> str:
    """
    Format user information for display.
    
    Args:
        user: Telegram User object
        chat: Telegram Chat object
        
    Returns:
        Formatted user information string
    """
    try:
        info_parts = []
        
        # User ID
        info_parts.append(f"🆔 *User ID:* `{user.id}`")
        
        # Username
        if user.username:
            info_parts.append(f"👤 *Username:* @{escape_markdown(user.username)}")
        
        # First name
        if user.first_name:
            info_parts.append(f"📝 *First Name:* {escape_markdown(user.first_name)}")
        
        # Last name
        if user.last_name:
            info_parts.append(f"📝 *Last Name:* {escape_markdown(user.last_name)}")
        
        # Language code
        if user.language_code:
            info_parts.append(f"🌐 *Language:* `{user.language_code}`")
        
        # Chat information
        info_parts.append(f"💬 *Chat Type:* `{chat.type}`")
        if chat.title:
            info_parts.append(f"💬 *Chat Title:* {escape_markdown(chat.title)}")
        
        # Bot flag (role is added by the caller)
        if user.is_bot:
            info_parts.append("🤖 *Type:* Bot")
        
        # Premium status
        if hasattr(user, 'is_premium') and user.is_premium:
            info_parts.append("⭐ *Premium:* Yes")
        
        return "\n".join(info_parts)
        
    except Exception as e:
        logger.error(f"Error formatting user info: {e}")
        return "❌ Unable to format user information"

def log_user_action(user: User, action: str, details: Optional[str] = None):
    """
    Log user actions for monitoring and debugging.
    
    Args:
        user: Telegram User object
        action: Action performed
        details: Additional details about the action
    """
    try:
        username = user.username or "no_username"
        user_info = f"User {user.id} (@{username})"
        
        if details:
            logger.info(f"{user_info} performed action: {action} - {details}")
        else:
            logger.info(f"{user_info} performed action: {action}")
            
    except Exception as e:
        logger.error(f"Error logging user action: {e}")

def format_timestamp(timestamp: Optional[datetime] = None) -> str:
    """
    Format timestamp for display.
    
    Args:
        timestamp: Datetime object, defaults to current time
        
    Returns:
        Formatted timestamp string
    """
    if not timestamp:
        timestamp = datetime.now()
    
    return timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

def validate_command_args(args: list, expected_count: int) -> bool:
    """
    Validate command arguments count.
    
    Args:
        args: List of command arguments
        expected_count: Expected number of arguments
        
    Returns:
        True if valid, False otherwise
    """
    return len(args) == expected_count

def clean_text(text: str, max_length: int = 4096) -> str:
    """
    Clean and truncate text for Telegram message limits.
    
    Args:
        text: Text to clean
        max_length: Maximum length allowed
        
    Returns:
        Cleaned and truncated text
    """
    if not text:
        return ""
    
    # Remove excessive whitespace
    cleaned = " ".join(text.split())
    
    # Truncate if too long
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length - 3] + "..."
    
    return cleaned

# --- Prompt templates helpers (for API) ---

_DEFAULT_PROMPT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "science": {
        "title": "Scientific query",
        "template": (
            "You are a scientific assistant. Write a structured answer to the query.\n"
            "Query: {input}\n"
            "Requirements: concise, by sections (Background, Key facts, Sources/leads to verify)."
        ),
    },
    "fiction": {
        "title": "Fiction",
        "template": (
            "You are a literary author. Write a fragment in the chosen genre.\n"
            "Theme/setup: {input}\n"
            "Preferences: imagery, expressive language, a hook at the end."
        ),
    },
    "programming": {
        "title": "Programming",
        "template": (
            "You are an experienced developer. Explain and/or propose a solution.\n"
            "Task/context: {input}\n"
            "Requirements: clear steps, code examples, tests."
        ),
    },
    "creativity": {
        "title": "Creativity/Brainstorm",
        "template": (
            "Generate 10 ideas on the topic: {input}\n"
            "Add brief notes and possible first steps."
        ),
    },
    "debunk": {
        "title": "Debunking fakes",
        "template": (
            "Structure a fact-check of the claim.\n"
            "Claim: {input}\n"
            "Structure: Claim → Facts/sources → Counterarguments → Conclusion."
        ),
    },
}

_PROMPT_TEMPLATES_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def load_prompt_templates(path: str = "prompt_templates.json") -> Dict[str, Dict[str, str]]:
    global _PROMPT_TEMPLATES_CACHE
    if _PROMPT_TEMPLATES_CACHE is not None:
        return _PROMPT_TEMPLATES_CACHE
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _PROMPT_TEMPLATES_CACHE = data
                    return data
        _PROMPT_TEMPLATES_CACHE = _DEFAULT_PROMPT_TEMPLATES
        return _PROMPT_TEMPLATES_CACHE
    except Exception as e:
        logger.warning(f"Failed to load prompt templates, using defaults: {e}")
        _PROMPT_TEMPLATES_CACHE = _DEFAULT_PROMPT_TEMPLATES
        return _PROMPT_TEMPLATES_CACHE


def get_prompt_categories() -> List[str]:
    templates = load_prompt_templates()
    return list(templates.keys())


def render_prompt(category: str, input_text: str) -> Optional[str]:
    templates = load_prompt_templates()
    cat = templates.get(category)
    if not cat:
        return None
    template = cat.get("template", "{input}")
    try:
        return template.format(input=input_text)
    except Exception as e:
        logger.warning(f"Prompt render failed for {category}: {e}")
        return template.replace("{input}", input_text)


# --- OpenAI API helpers ---

_openai_client = None

def get_openai_client() -> Optional[OpenAI]:
    """Get or create OpenAI client."""
    if not OPENAI_AVAILABLE:
        logger.error("OpenAI package not installed")
        return None
        
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set")
        return None
    
    try:
        _openai_client = OpenAI(api_key=api_key)
        return _openai_client
    except Exception as e:
        logger.error(f"Failed to create OpenAI client: {e}")
        return None

def get_ai_completion(prompt: str, max_tokens: int = 1000) -> Optional[str]:
    """Get completion from AI API based on DEFAULT_AI_PROVIDER."""
    provider = os.getenv("DEFAULT_AI_PROVIDER", "openai").lower()
    
    if provider == "anthropic":
        if not ANTHROPIC_AVAILABLE:
            logger.warning("Anthropic requested but not available, falling back to OpenAI")
            if not OPENAI_AVAILABLE:
                logger.error("Neither Anthropic nor OpenAI are available")
                return "Error: AI services not available. Please install required packages."
            return get_openai_completion(prompt, max_tokens)
        return get_anthropic_completion(prompt, max_tokens)
    else:
        if not OPENAI_AVAILABLE:
            logger.error("OpenAI not available")
            if ANTHROPIC_AVAILABLE:
                logger.warning("Falling back to Anthropic")
                return get_anthropic_completion(prompt, max_tokens)
            return "Error: AI services not available. Please install required packages."
        return get_openai_completion(prompt, max_tokens)

# --- Global Model State ---
_CURRENT_OPENAI_MODEL = None
_CURRENT_ANTHROPIC_MODEL = None
_MODEL_CACHE = {}
_MODEL_CACHE_TTL = 3600  # 1 hour in seconds

def _read_model_from_env_file(key: str, default: str) -> str:
    """Read the model name directly from the .env file (container sync)."""
    try:
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{key}="):
                        return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return os.getenv(key, default)

def get_current_model(provider: str) -> str:
    """Get the currently selected model for a provider.
    
    Priority:
    1. In-memory variable (set via /ch_model)
    2. Model from .env (container sync)
    3. Default value
    """
    if provider == "openai":
        if _CURRENT_OPENAI_MODEL:
            return _CURRENT_OPENAI_MODEL
        return _read_model_from_env_file("OPENAI_MODEL", "gpt-4o")
    elif provider == "anthropic":
        if _CURRENT_ANTHROPIC_MODEL:
            return _CURRENT_ANTHROPIC_MODEL
        return _read_model_from_env_file("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    return "unknown"

def set_current_model(provider: str, model: str) -> bool:
    """Set the current model for a provider and save to .env for container sync."""
    global _CURRENT_OPENAI_MODEL, _CURRENT_ANTHROPIC_MODEL
    
    env_key = None
    if provider == "openai":
        _CURRENT_OPENAI_MODEL = model
        env_key = "OPENAI_MODEL"
    elif provider == "anthropic":
        _CURRENT_ANTHROPIC_MODEL = model
        env_key = "ANTHROPIC_MODEL"
    else:
        return False
    
    # Persist to .env so containers stay in sync
    try:
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Update or append
            found = False
            new_lines = []
            for line in lines:
                if line.startswith(f"{env_key}="):
                    new_lines.append(f"{env_key}={model}\n")
                    found = True
                else:
                    new_lines.append(line)
            
            if not found:
                new_lines.append(f"{env_key}={model}\n")
            
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            
            # Also update the current process environment
            os.environ[env_key] = model
            logger.info(f"Model {model} saved to .env for {provider}")
    except Exception as e:
        logger.warning(f"Could not save model to .env: {e}")
    
    return True  # Model set successfully

def get_available_models(provider: str) -> List[str]:
    """Get list of available models for a provider with caching and filtering."""
    global _MODEL_CACHE
    
    now = time.time()
    cache_key = f"models_{provider}"
    
    # Check cache
    if cache_key in _MODEL_CACHE:
        timestamp, models = _MODEL_CACHE[cache_key]
        if now - timestamp < _MODEL_CACHE_TTL:
            return models
            
    models = []
    
    try:
        if provider == "openai":
            client = get_openai_client()
            if client:
                # Fetch models from API
                api_models = client.models.list()
                # Sort by creation date (newest first)
                sorted_models = sorted(api_models.data, key=lambda m: m.created, reverse=True)
                
                # Filter and take top 5
                count = 0
                for m in sorted_models:
                    # Filter for chat models (gpt-*) and exclude instruct/audio/etc if needed
                    # Keeping it simple: must start with gpt- and not be an instruct model
                    if m.id.startswith("gpt-") and "instruct" not in m.id:
                        models.append(m.id)
                        count += 1
                        if count >= 5:
                            break
                            
        elif provider == "anthropic":
            client = get_anthropic_client()
            if client and hasattr(client, 'models') and hasattr(client.models, 'list'):
                # Fetch models from API
                api_models = client.models.list()
                # Sort by creation date (newest first) - Anthropic uses created_at (datetime)
                # We need to handle that created_at might be a datetime object
                sorted_models = sorted(api_models, key=lambda m: m.created_at, reverse=True)
                
                # Take top 5
                count = 0
                for m in sorted_models:
                    if m.type == "model":
                        models.append(m.id)
                        count += 1
                        if count >= 5:
                            break
                            
    except Exception as e:
        logger.error(f"Error fetching models for {provider}: {e}")
        # Fallback to hardcoded list if API fails
        if provider == "openai":
            models = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
        elif provider == "anthropic":
            models = ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"]
            
    # If we got results (or fallback), cache them
    if models:
        _MODEL_CACHE[cache_key] = (now, models)
        
    return models or []

def get_openai_completion(
    prompt: str, 
    max_tokens: int = 1000,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:
    """
    Get completion from OpenAI API.
    
    Args:
        prompt: Prompt text
        max_tokens: Max tokens in the response
        conversation_history: History as [{"role": "user|assistant", "content": "..."}]
    """
    client = get_openai_client()
    if not client:
        return None
    
    model = get_current_model("openai")
    
    # Build message list
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Answer concisely and to the point."}
    ]
    
    # Append conversation history if present
    if conversation_history:
        messages.extend(conversation_history)
    
    # Append the current prompt
    messages.append({"role": "user", "content": prompt})
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None

# --- Anthropic API helpers ---

_anthropic_client = None


def get_anthropic_client() -> Optional[object]:
    """Get or create Anthropic client (Messages API)."""
    if not ANTHROPIC_AVAILABLE:
        logger.error("Anthropic package not installed")
        return None

    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set")
        return None

    try:
        # Log SDK version for diagnostics
        try:
            import anthropic as _anth
            logger.info(f"Anthropic library version: {_anth.__version__}")
        except Exception:
            pass

        # Create standard client; do not pass advanced kwargs
        _anthropic_client = Anthropic(api_key=api_key)
        return _anthropic_client
    except Exception as e:
        logger.error(f"Failed to create Anthropic client: {e}")
        return None


def get_anthropic_completion(
    prompt: str, 
    max_tokens: int = 1000,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:
    """
    Get completion from Anthropic Claude via Messages API.
    
    Args:
        prompt: Prompt text
        max_tokens: Max tokens in the response
        conversation_history: History as [{"role": "user|assistant", "content": "..."}]
    """
    if not ANTHROPIC_AVAILABLE:
        logger.error("Anthropic package not available")
        return "Error: Anthropic library is not installed"

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set")
        return "Error: Anthropic API key is not configured"

    model = get_current_model("anthropic")
    logger.info(f"Using Anthropic model: {model}")

    client = get_anthropic_client()
    if client is None:
        return "Error initializing Anthropic client. Check settings."

    # Build message list
    messages = []
    
    # Append conversation history if present
    if conversation_history:
        messages.extend(conversation_history)
    
    # Append the current prompt
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )

        # New SDK returns list of content blocks
        content = getattr(response, "content", None)
        if isinstance(content, list) and content:
            block = content[0]
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                return text
        # Fallback: if SDK returns a raw string for some reason
        if isinstance(content, str) and content.strip():
            return content

        return "Could not extract text from Anthropic response"
    except Exception as e:
        err_text = str(e)
        logger.error(f"Anthropic API error: {err_text}")
        # Helpful hint for common httpx 0.28+ incompatibility
        if "unexpected keyword argument 'proxies'" in err_text:
            return (
                "Anthropic error: httpx/anthropic version mismatch. "
                "Pin httpx<0.28 (e.g. 0.27.2) and reinstall dependencies."
            )
        return f"Error using Anthropic API: {err_text}"

def get_app_version() -> Dict[str, str]:
    """
    Get application version and metadata from pyproject.toml.
    
    Returns:
        Dictionary with version, name, description, release_date, developer, and last_updated
    """
    try:
        import tomllib
    except ImportError:
        # Python < 3.11 compatibility
        try:
            import tomli as tomllib
        except ImportError:
            try:
                import tomllib
            except ImportError:
                logger.warning("tomllib/tomli not available, cannot read pyproject.toml")
            return {
                "version": "Unknown",
                "name": "TelegramHelper",
                "description": "",
                "release_date": "Unknown",
                "developer": "Unknown",
                "last_updated": "Unknown",
            }
    
    try:
        pyproject_path = os.path.join(os.getcwd(), "pyproject.toml")
        if not os.path.exists(pyproject_path):
            # Try to find pyproject.toml in parent directories
            current_dir = os.getcwd()
            for _ in range(3):  # Look up to 3 levels up
                parent_dir = os.path.dirname(current_dir)
                pyproject_path = os.path.join(parent_dir, "pyproject.toml")
                if os.path.exists(pyproject_path):
                    break
                current_dir = parent_dir
            else:
                logger.warning("pyproject.toml not found")
                return {
                    "version": "Unknown",
                    "name": "TelegramHelper",
                    "description": "",
                    "release_date": "Unknown",
                    "developer": "Unknown",
                    "last_updated": "Unknown",
                }
        
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        
        project_data = data.get("project", {})
        metadata = data.get("tool", {}).get("telegramhelper", {}).get("metadata", {})
        
        return {
            "version": project_data.get("version", "Unknown"),
            "name": project_data.get("name", "TelegramHelper"),
            "description": project_data.get("description", ""),
            "release_date": metadata.get("release_date", "Unknown"),
            "developer": metadata.get("developer", "Unknown"),
            "last_updated": metadata.get("last_updated", "Unknown"),
        }
        
    except Exception as e:
        logger.error(f"Error reading pyproject.toml: {e}")
        return {
            "version": "Read error",
            "name": "TelegramHelper",
            "description": "",
            "release_date": "Read error",
            "developer": "Read error",
            "last_updated": "Read error",
        }

def split_long_text(text: str, max_length: int = 4000) -> List[str]:
    """Split long text into chunks that fit Telegram message limits."""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    current_chunk = ""
    
    # Split by lines so questions are not cut mid-word
    lines = text.split('\n')
    
    for line in lines:
        # Adding this line would exceed the limit
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = line
            else:
                # Single line too long: split it
                if len(line) > max_length:
                    # Prefer a break at punctuation (. , : ; ! ?)
                    for i in range(max_length, max(0, max_length - 100), -1):
                        if line[i] in '.!?:;':
                            chunks.append(line[:i+1])
                            current_chunk = line[i+1:]
                            break
                    else:
                        # No good break: split on words
                        words = line.split()
                        temp_chunk = ""
                        for word in words:
                            if len(temp_chunk) + len(word) + 1 > max_length:
                                if temp_chunk:
                                    chunks.append(temp_chunk.strip())
                                    temp_chunk = word
                                else:
                                    # One word too long: split by characters
                                    chunks.append(word[:max_length])
                                    temp_chunk = word[max_length:]
                            else:
                                temp_chunk += " " + word if temp_chunk else word
                        if temp_chunk:
                            current_chunk = temp_chunk
                else:
                    current_chunk = line
        else:
            current_chunk += "\n" + line if current_chunk else line
    
    # Append the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks

def extract_spoiler_entities(text_with_tg_spoiler: str) -> Tuple[str, List[MessageEntity]]:
    """
    Convert text containing <tg-spoiler>...</tg-spoiler> into
    plain text + Telegram MessageEntity(type='spoiler') offsets.
    """
    if not text_with_tg_spoiler:
        return "", []
    plain_parts: List[str] = []
    entities: List[MessageEntity] = []
    i = 0
    n = len(text_with_tg_spoiler)
    while i < n:
        if text_with_tg_spoiler.startswith('<tg-spoiler>', i):
            # start of spoiler
            i += len('<tg-spoiler>')
            start_offset = sum(len(p) for p in plain_parts)
            # collect until close
            close_idx = text_with_tg_spoiler.find('</tg-spoiler>', i)
            if close_idx == -1:
                # no closing tag, treat as plain text
                spoiler_text = text_with_tg_spoiler[i:]
                plain_parts.append(spoiler_text)
                entities.append(MessageEntity(type='spoiler', offset=start_offset, length=len(spoiler_text)))
                break
            else:
                spoiler_text = text_with_tg_spoiler[i:close_idx]
                plain_parts.append(spoiler_text)
                entities.append(MessageEntity(type='spoiler', offset=start_offset, length=len(spoiler_text)))
                i = close_idx + len('</tg-spoiler>')
        else:
            plain_parts.append(text_with_tg_spoiler[i])
            i += 1
    plain_text = ''.join(plain_parts)
    return plain_text, entities

async def safe_send_markdown(update_or_message, text: str, reply_markup=None, **kwargs):
    """
    Safely send a message with Markdown V2, falling back to plain text if parsing fails.
    
    Args:
        update_or_message: Update object or Message object to reply to
        text: Message text with Markdown V2 formatting
        reply_markup: Optional reply markup
        **kwargs: Additional arguments for send_message/reply_text
    
    Returns:
        Sent message object
    """
    from telegram.constants import ParseMode
    
    # Determine if we have an Update or Message object
    if hasattr(update_or_message, 'message'):
        # It's an Update object
        message_obj = update_or_message.message
    else:
        # It's a Message object
        message_obj = update_or_message
    
    try:
        # Try to send with Markdown V2
        return await message_obj.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
            **kwargs
        )
    except Exception as e:
        if "Can't parse entities" in str(e):
            # Fallback: remove Markdown formatting and send as plain text
            import re
            # Remove Markdown V2 formatting
            plain_text = re.sub(r'\\(.)', r'\1', text)  # Unescape characters
            plain_text = re.sub(r'\*\*(.*?)\*\*', r'\1', plain_text)  # Remove bold
            plain_text = re.sub(r'\*(.*?)\*', r'\1', plain_text)  # Remove italic
            plain_text = re.sub(r'`(.*?)`', r'\1', plain_text)  # Remove code
            plain_text = re.sub(r'__(.*?)__', r'\1', plain_text)  # Remove underline
            
            return await message_obj.reply_text(
                plain_text,
                reply_markup=reply_markup,
                **kwargs
            )
        else:
            # Re-raise other exceptions
            raise


def detect_language(text: str) -> str:
    """Return ru/en/fr/de (or en as fallback)."""
    if not text or not text.strip():
        return "en"
    if LANGID_AVAILABLE:
        try:
            code, _conf = langid.classify(text[:500])
            if code in ("ru", "en", "fr", "de"):
                return code
        except Exception:
            pass
    cyrillic = sum(1 for ch in text.lower() if "\u0430" <= ch <= "\u044f" or ch == "\u0451")
    if cyrillic >= max(3, len(text) // 8):
        return "ru"
    return "en"


def get_language_name(lang_code: str) -> str:
    return {
        "ru": "Russian",
        "en": "English",
        "fr": "French",
        "de": "German",
    }.get(lang_code, lang_code)


def translate_text(
    text: str, target_lang: str, source_lang: Optional[str] = None
) -> Tuple[str, str, str]:
    """Translate with GoogleTranslator. Returns (translated, source, target)."""
    if not text or not target_lang:
        return text, "unknown", target_lang
    if not source_lang:
        source_lang = detect_language(text)
    if source_lang == target_lang:
        return text, source_lang, target_lang
    if not TRANSLATOR_AVAILABLE:
        return "[Translator unavailable: pip install deep-translator]", source_lang, target_lang
    try:
        translated = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        if translated and translated.strip():
            return translated, source_lang, target_lang
        translated = GoogleTranslator(source="auto", target=target_lang).translate(text)
        return (translated or text), source_lang, target_lang
    except Exception as exc:
        logger.error("translate_text failed: %s", exc)
        return f"[Translation error: {exc}]", source_lang, target_lang


def get_ai_translation(
    text: str, target_lang: str = "ru", source_lang: Optional[str] = None
) -> Optional[str]:
    """Context-aware translation via the configured AI provider."""
    if not OPENAI_AVAILABLE and not ANTHROPIC_AVAILABLE:
        return None
    if not source_lang:
        source_lang = detect_language(text)
    source_name = get_language_name(source_lang)
    target_name = get_language_name(target_lang)
    prompt = (
        f"Translate the following text from {source_name} to {target_name}. "
        "Keep meaning, tone, and proper names. Return only the translation.\n\n"
        f"{text}"
    )
    result = get_ai_completion(prompt, max_tokens=1500)
    if not result:
        return None
    result = result.strip()
    if (result.startswith('"') and result.endswith('"')) or (
        result.startswith("'") and result.endswith("'")
    ):
        result = result[1:-1]
    return result
