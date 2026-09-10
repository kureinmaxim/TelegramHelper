# -*- coding: utf-8 -*-
"""
Conversation history storage for AI chats.

Two modes:
- simple: single request/response, no history
- chat: multi-turn chat with persisted history
"""

import json
import os
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Thread safety
_history_lock = threading.Lock()

# History file path
_HISTORY_STORE_PATH = os.getenv("CONVERSATION_HISTORY_PATH", 
                                os.path.join(os.getcwd(), "conversation_history.json"))

# Max messages kept per conversation
MAX_HISTORY_MESSAGES = 50

# Conversation TTL (days); older conversations are deleted
CONVERSATION_TTL_DAYS = 7

# Data shape:
# {
#   "conversations": {
#       "conversation_id": {
#           "created_at": "2025-01-01T12:00:00",
#           "last_activity": "2025-01-01T12:00:00",
#           "messages": [
#               {"role": "user", "content": "Hello"},
#               {"role": "assistant", "content": "Hello!"}
#           ]
#       }
#   }
# }


def _load_history() -> Dict:
    """Load conversation history from file."""
    with _history_lock:
        if not os.path.exists(_HISTORY_STORE_PATH):
            return {"conversations": {}}
        try:
            with open(_HISTORY_STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"conversations": {}}
        except Exception as e:
            logger.error(f"Error loading conversation history: {e}")
            return {"conversations": {}}


def _save_history(data: Dict) -> None:
    """Save conversation history to file."""
    with _history_lock:
        try:
            directory = os.path.dirname(_HISTORY_STORE_PATH) or "."
            os.makedirs(directory, exist_ok=True)
            
            # Atomic write
            temp_file = _HISTORY_STORE_PATH + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, _HISTORY_STORE_PATH)
        except Exception as e:
            logger.error(f"Error saving conversation history: {e}")


def _cleanup_old_conversations(data: Dict) -> Dict:
    """Drop conversations older than TTL."""
    cutoff_date = datetime.now() - timedelta(days=CONVERSATION_TTL_DAYS)
    cutoff_str = cutoff_date.isoformat()
    
    conversations = data.get("conversations", {})
    active_conversations = {}
    
    for conv_id, conv_data in conversations.items():
        last_activity = conv_data.get("last_activity", "")
        if last_activity >= cutoff_str:
            active_conversations[conv_id] = conv_data
        else:
            logger.info(f"Removing old conversation: {conv_id}")
    
    data["conversations"] = active_conversations
    return data


def get_conversation_history(conversation_id: str) -> List[Dict[str, str]]:
    """
    Return conversation history.

    Args:
        conversation_id: Conversation ID

    Returns:
        Messages as [{"role": "user|assistant", "content": "..."}]
    """
    data = _load_history()
    conversations = data.get("conversations", {})
    
    if conversation_id in conversations:
        messages = conversations[conversation_id].get("messages", [])
        # Cap message count
        return messages[-MAX_HISTORY_MESSAGES:]
    
    return []


def add_message_to_history(
    conversation_id: str,
    role: str,
    content: str
) -> None:
    """
    Append a message to conversation history.

    Args:
        conversation_id: Conversation ID
        role: "user" or "assistant"
        content: Message text
    """
    data = _load_history()
    conversations = data.setdefault("conversations", {})
    
    now = datetime.now().isoformat()
    
    if conversation_id not in conversations:
        conversations[conversation_id] = {
            "created_at": now,
            "last_activity": now,
            "messages": []
        }
    
    conversation = conversations[conversation_id]
    conversation["last_activity"] = now
    
    # Append message
    conversation["messages"].append({
        "role": role,
        "content": content
    })
    
    # Cap message count
    if len(conversation["messages"]) > MAX_HISTORY_MESSAGES:
        conversation["messages"] = conversation["messages"][-MAX_HISTORY_MESSAGES:]
    
    # Drop expired conversations
    data = _cleanup_old_conversations(data)
    
    _save_history(data)


def clear_conversation_history(conversation_id: str) -> bool:
    """
    Clear history for one conversation.

    Args:
        conversation_id: Conversation ID

    Returns:
        True if the conversation existed and was deleted
    """
    data = _load_history()
    conversations = data.get("conversations", {})
    
    if conversation_id in conversations:
        del conversations[conversation_id]
        _save_history(data)
        return True
    
    return False


def generate_conversation_id(app_id: str, user_id: Optional[str] = None) -> str:
    """
    Generate a unique conversation ID.

    Args:
        app_id: Application ID (e.g. example-app)
        user_id: Optional user ID

    Returns:
        Unique conversation ID
    """
    import uuid
    import hashlib
    
    # Deterministic ID when user_id is present
    if user_id:
        seed = f"{app_id}:{user_id}"
        conv_id = hashlib.md5(seed.encode()).hexdigest()[:16]
        return f"{app_id}-{conv_id}"
    
    # Otherwise random
    unique_id = uuid.uuid4().hex[:16]
    return f"{app_id}-{unique_id}"

