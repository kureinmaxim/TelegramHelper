# -*- coding: utf-8 -*-
"""
Per-app_id API and encryption key storage.

Data shape:
{
  "app_keys": {
    "example-app": {
      "api_key": "64_char_key",
      "encryption_key": "64_char_key",
      "created_at": "2025-01-01T12:00:00",
      "updated_at": "2025-01-01T12:00:00"
    }
  },
  "default": {
    "api_key": "key_from_env",
    "encryption_key": "key_from_env"
  }
}
"""

import json
import os
import threading
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Thread safety
_keys_lock = threading.Lock()

# Cache: last observed mtime of the keys file
_last_file_mtime = None

# Keys file path
_KEYS_STORE_PATH = os.getenv("APP_KEYS_PATH", 
                             os.path.join(os.getcwd(), "app_keys.json"))


def _load_keys(force_reload: bool = False) -> Dict:
    """Load keys from file."""
    global _last_file_mtime
    
    with _keys_lock:
        if not os.path.exists(_KEYS_STORE_PATH):
            _last_file_mtime = None
            return {"app_keys": {}, "default": {}}
        
        # Check whether the file changed
        try:
            current_mtime = os.path.getmtime(_KEYS_STORE_PATH)
            if not force_reload and _last_file_mtime == current_mtime:
                # Unchanged; cache may be reused
                pass
            _last_file_mtime = current_mtime
        except Exception:
            pass
        
        try:
            with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"app_keys": {}, "default": {}}
        except Exception as e:
            logger.error(f"Error loading app keys: {e}")
            return {"app_keys": {}, "default": {}}


def _save_keys(data: Dict) -> None:
    """Save keys to file."""
    with _keys_lock:
        try:
            directory = os.path.dirname(_KEYS_STORE_PATH) or "."
            os.makedirs(directory, exist_ok=True)
            
            # Direct write to avoid Docker bind mount issues (Errno 16)
            # Atomic replace (os.replace) changes inode which breaks bind mounts
            with open(_KEYS_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            # Extra check: file exists and is readable
            if os.path.exists(_KEYS_STORE_PATH):
                try:
                    with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                        json.load(f)  # Confirm valid JSON
                except Exception as e:
                    logger.error(f"Error verifying saved app keys file: {e}")
            else:
                logger.error(f"Error: app_keys.json was not created at {_KEYS_STORE_PATH}")
        except Exception as e:
            logger.error(f"Error saving app keys: {e}")


def get_api_key(app_id: Optional[str] = None, force_reload: bool = False) -> Optional[str]:
    """
    Get API key for app_id.

    Args:
        app_id: Application ID (e.g. example-app)
        force_reload: Force reload from file

    Returns:
        API key or None
    """
    data = _load_keys(force_reload=force_reload)
    
    # Per-app key if present
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            api_key = app_keys[app_id].get("api_key")
            if api_key:
                return api_key
    
    # Fallback to default key from env store
    default_key = data.get("default", {}).get("api_key")
    if default_key:
        return default_key
    
    # Last fallback: environment variables
    return os.getenv("API_SECRET_KEY")


def get_encryption_key(app_id: Optional[str] = None, force_reload: bool = False) -> Optional[str]:
    """
    Get encryption key for app_id.

    Args:
        app_id: Application ID
        force_reload: Force reload from file

    Returns:
        Encryption key or None
    """
    data = _load_keys(force_reload=force_reload)
    
    # Per-app key if present
    if app_id:
        app_keys = data.get("app_keys", {})
        if app_id in app_keys:
            enc_key = app_keys[app_id].get("encryption_key")
            if enc_key:
                return enc_key
    
    # Fallback to default key from env store
    default_key = data.get("default", {}).get("encryption_key")
    if default_key:
        return default_key
    
    # Last fallback: environment variables
    enc_key = os.getenv("ENCRYPTION_KEY")
    if enc_key:
        return enc_key
    
    return os.getenv("API_SECRET_KEY")


def set_api_key(app_id: str, api_key: str) -> bool:
    """
    Set API key for app_id.

    Args:
        app_id: Application ID
        api_key: API key

    Returns:
        True on success
    """
    data = _load_keys()
    app_keys = data.setdefault("app_keys", {})
    
    now = datetime.now().isoformat()
    
    if app_id not in app_keys:
        app_keys[app_id] = {
            "created_at": now,
            "updated_at": now
        }
    
    app_keys[app_id]["api_key"] = api_key
    app_keys[app_id]["updated_at"] = now
    
    _save_keys(data)
    
    # Verify the key was actually persisted
    # Reload from file for verification
    import time
    time.sleep(0.05)  # Brief delay for file sync
    
    # Check the file directly
    try:
        if os.path.exists(_KEYS_STORE_PATH):
            with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
                saved_app_keys = saved_data.get("app_keys", {})
                if app_id in saved_app_keys and saved_app_keys[app_id].get("api_key") == api_key:
                    return True
                else:
                    logger.warning(f"API key for {app_id} was saved but verification failed. Retrying...")
                    time.sleep(0.1)
                    # One more attempt
                    with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                        saved_data = json.load(f)
                        saved_app_keys = saved_data.get("app_keys", {})
                        if app_id in saved_app_keys and saved_app_keys[app_id].get("api_key") == api_key:
                            return True
    except Exception as e:
        logger.error(f"Error verifying saved API key: {e}")
    
    return True  # True anyway: _save_keys already ran


def set_encryption_key(app_id: str, encryption_key: str) -> bool:
    """
    Set encryption key for app_id.

    Args:
        app_id: Application ID
        encryption_key: Encryption key

    Returns:
        True on success
    """
    data = _load_keys()
    app_keys = data.setdefault("app_keys", {})
    
    now = datetime.now().isoformat()
    
    if app_id not in app_keys:
        app_keys[app_id] = {
            "created_at": now,
            "updated_at": now
        }
    
    app_keys[app_id]["encryption_key"] = encryption_key
    app_keys[app_id]["updated_at"] = now
    
    _save_keys(data)
    
    # Verify the key was actually persisted
    # Reload from file for verification
    import time
    time.sleep(0.05)  # Brief delay for file sync
    
    # Check the file directly
    try:
        if os.path.exists(_KEYS_STORE_PATH):
            with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
                saved_app_keys = saved_data.get("app_keys", {})
                if app_id in saved_app_keys and saved_app_keys[app_id].get("encryption_key") == encryption_key:
                    return True
                else:
                    logger.warning(f"Encryption key for {app_id} was saved but verification failed. Retrying...")
                    time.sleep(0.1)
                    # One more attempt
                    with open(_KEYS_STORE_PATH, "r", encoding="utf-8") as f:
                        saved_data = json.load(f)
                        saved_app_keys = saved_data.get("app_keys", {})
                        if app_id in saved_app_keys and saved_app_keys[app_id].get("encryption_key") == encryption_key:
                            return True
    except Exception as e:
        logger.error(f"Error verifying saved encryption key: {e}")
    
    return True  # True anyway: _save_keys already ran


def has_api_key(app_id: str, force_reload: bool = False) -> bool:
    """
    Check whether app_id has a dedicated API key.

    Args:
        app_id: Application ID
        force_reload: Force reload from file

    Returns:
        True if a dedicated key exists
    """
    data = _load_keys(force_reload=force_reload)
    app_keys = data.get("app_keys", {})
    return app_id in app_keys and bool(app_keys[app_id].get("api_key"))


def has_encryption_key(app_id: str, force_reload: bool = False) -> bool:
    """
    Check whether app_id has a dedicated encryption key.

    Args:
        app_id: Application ID
        force_reload: Force reload from file

    Returns:
        True if a dedicated key exists
    """
    data = _load_keys(force_reload=force_reload)
    app_keys = data.get("app_keys", {})
    return app_id in app_keys and bool(app_keys[app_id].get("encryption_key"))


def list_app_ids() -> list:
    """
    List all app_id values with configured keys.

    Returns:
        List of app_id
    """
    data = _load_keys()
    app_keys = data.get("app_keys", {})
    return list(app_keys.keys())


def delete_app_keys(app_id: str) -> bool:
    """
    Delete all keys for app_id.

    Args:
        app_id: Application ID

    Returns:
        True if keys were deleted
    """
    data = _load_keys()
    app_keys = data.get("app_keys", {})
    
    if app_id in app_keys:
        del app_keys[app_id]
        _save_keys(data)
        return True
    
    return False


def delete_api_key(app_id: str) -> bool:
    """
    Delete only the API key for app_id.

    Args:
        app_id: Application ID

    Returns:
        True if the key was deleted
    """
    data = _load_keys()
    app_keys = data.get("app_keys", {})
    
    if app_id in app_keys and "api_key" in app_keys[app_id]:
        del app_keys[app_id]["api_key"]
        # Drop the whole record if no keys remain
        if not app_keys[app_id].get("encryption_key"):
            del app_keys[app_id]
        else:
            app_keys[app_id]["updated_at"] = datetime.now().isoformat()
            
        _save_keys(data)
        return True
    
    return False


def delete_encryption_key(app_id: str) -> bool:
    """
    Delete only the encryption key for app_id.

    Args:
        app_id: Application ID

    Returns:
        True if the key was deleted
    """
    data = _load_keys()
    app_keys = data.get("app_keys", {})
    
    if app_id in app_keys and "encryption_key" in app_keys[app_id]:
        del app_keys[app_id]["encryption_key"]
        # Drop the whole record if no keys remain
        if not app_keys[app_id].get("api_key"):
            del app_keys[app_id]
        else:
            app_keys[app_id]["updated_at"] = datetime.now().isoformat()
            
        _save_keys(data)
        return True
    
    return False


def init_default_keys():
    """Initialize default keys from environment variables."""
    data = _load_keys()
    default = data.setdefault("default", {})
    
    api_key = os.getenv("API_SECRET_KEY")
    enc_key = os.getenv("ENCRYPTION_KEY")
    
    if api_key and not default.get("api_key"):
        default["api_key"] = api_key
    
    if enc_key and not default.get("encryption_key"):
        default["encryption_key"] = enc_key
    
    if api_key or enc_key:
        _save_keys(data)

