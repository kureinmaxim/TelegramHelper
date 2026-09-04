# -*- coding: utf-8 -*-
"""
Модуль для хранения истории бесед с AI.

Поддерживает два режима:
- simple: простой запрос-ответ без истории
- chat: режим чата с сохранением истории беседы
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

# Путь к файлу хранения истории
_HISTORY_STORE_PATH = os.getenv("CONVERSATION_HISTORY_PATH", 
                                os.path.join(os.getcwd(), "conversation_history.json"))

# Максимальное количество сообщений в истории одной беседы
MAX_HISTORY_MESSAGES = 50

# Время жизни беседы (дни) - старые беседы удаляются
CONVERSATION_TTL_DAYS = 7

# Структура данных:
# {
#   "conversations": {
#       "conversation_id": {
#           "created_at": "2025-01-01T12:00:00",
#           "last_activity": "2025-01-01T12:00:00",
#           "messages": [
#               {"role": "user", "content": "Привет"},
#               {"role": "assistant", "content": "Здравствуйте!"}
#           ]
#       }
#   }
# }


def _load_history() -> Dict:
    """Загрузить историю бесед из файла"""
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
    """Сохранить историю бесед в файл"""
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
    """Удалить старые беседы (старше TTL)"""
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
    Получить историю беседы
    
    Args:
        conversation_id: ID беседы
        
    Returns:
        Список сообщений в формате [{"role": "user|assistant", "content": "..."}]
    """
    data = _load_history()
    conversations = data.get("conversations", {})
    
    if conversation_id in conversations:
        messages = conversations[conversation_id].get("messages", [])
        # Ограничиваем количество сообщений
        return messages[-MAX_HISTORY_MESSAGES:]
    
    return []


def add_message_to_history(
    conversation_id: str,
    role: str,
    content: str
) -> None:
    """
    Добавить сообщение в историю беседы
    
    Args:
        conversation_id: ID беседы
        role: "user" или "assistant"
        content: Текст сообщения
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
    
    # Добавляем сообщение
    conversation["messages"].append({
        "role": role,
        "content": content
    })
    
    # Ограничиваем количество сообщений
    if len(conversation["messages"]) > MAX_HISTORY_MESSAGES:
        conversation["messages"] = conversation["messages"][-MAX_HISTORY_MESSAGES:]
    
    # Очистка старых бесед
    data = _cleanup_old_conversations(data)
    
    _save_history(data)


def clear_conversation_history(conversation_id: str) -> bool:
    """
    Очистить историю конкретной беседы
    
    Args:
        conversation_id: ID беседы
        
    Returns:
        True если беседа была найдена и удалена
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
    Сгенерировать уникальный ID беседы
    
    Args:
        app_id: ID приложения (например, example-app)
        user_id: Опциональный ID пользователя
        
    Returns:
        Уникальный ID беседы
    """
    import uuid
    import hashlib
    
    # Если есть user_id, создаем детерминированный ID
    if user_id:
        seed = f"{app_id}:{user_id}"
        conv_id = hashlib.md5(seed.encode()).hexdigest()[:16]
        return f"{app_id}-{conv_id}"
    
    # Иначе генерируем случайный
    unique_id = uuid.uuid4().hex[:16]
    return f"{app_id}-{unique_id}"

