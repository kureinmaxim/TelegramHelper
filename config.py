"""
Configuration management for the Telegram bot.
"""

import os
import logging

logger = logging.getLogger(__name__)

class Config:
    """Configuration class for bot settings."""
    
    def __init__(self):
        """Initialize configuration from environment variables."""
        self.bot_token = self._get_optional_env("BOT_TOKEN")
        # Multiple admins, comma-separated
        self.admin_user_ids = self._get_list_env("ADMIN_USER_IDS", [])
        self.debug_mode = self._get_bool_env("DEBUG_MODE", False)
        self.log_level = self._get_optional_env("LOG_LEVEL", "INFO")
        
        # New: polling tuning
        self.poll_timeout = self._get_int_env("POLL_TIMEOUT", 50)  # seconds (Telegram long polling max ~50)
        self.poll_interval = self._get_float_env("POLL_INTERVAL", 0.0)  # seconds between polls when idle
        self.bot_start_retry_interval = self._get_int_env("BOT_START_RETRY_INTERVAL", 30)
        self.bot_start_max_attempts = self._get_int_env("BOT_START_MAX_ATTEMPTS", 0)
        self.bot_connect_timeout = self._get_float_env("BOT_CONNECT_TIMEOUT", 30.0)
        self.bot_read_timeout = self._get_float_env("BOT_READ_TIMEOUT", 30.0)
        self.bot_write_timeout = self._get_float_env("BOT_WRITE_TIMEOUT", 30.0)
        self.bot_pool_timeout = self._get_float_env("BOT_POOL_TIMEOUT", 30.0)
        
        # New: list of special users who receive custom responses (comma-separated user IDs)
        self.special_user_ids = self._get_list_env("SPECIAL_USER_IDS", [])
        
        # New: per-user city mapping for weather in greetings, format: "12345:City A;67890:City B"
        self.user_city_map = self._get_user_city_map_env("USER_CITY_MAP")

        # Hysteria2 configuration
        self.hysteria2_config_path = self._get_optional_env("HYSTERIA2_CONFIG_PATH", "hysteria2_config.json")
        self.hysteria2_enabled = self._get_bool_env("HYSTERIA2_ENABLED", False)

        # MTProto proxy configuration
        self.mtproto_config_path = self._get_optional_env("MTPROTO_CONFIG_PATH", "mtproto_config.json")
        self.mtproto_enabled = self._get_bool_env("MTPROTO_ENABLED", False)

        # Set logging level based on configuration
        if self.debug_mode:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            level = getattr(logging, self.log_level.upper(), logging.INFO)
            logging.getLogger().setLevel(level)
        
        logger.info("Configuration loaded successfully")
        logger.info(f"Admin user IDs: {self.admin_user_ids}")
        logger.debug(f"Debug mode: {self.debug_mode}")
        logger.debug(f"Log level: {self.log_level}")
        logger.debug(f"Poll timeout: {self.poll_timeout}")
        logger.debug(f"Poll interval: {self.poll_interval}")
        logger.debug(f"Special user IDs: {self.special_user_ids}")
        logger.debug(f"User city map: {self.user_city_map}")
        
        if not self.bot_token:
            logger.warning("BOT_TOKEN is not set. Telegram bot will be disabled.")
    
    def _get_required_env(self, key: str) -> str:
        """Get a required environment variable."""
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Required environment variable {key} is not set")
        return value
    
    def _get_optional_env(self, key: str, default: str = None) -> str:
        """Get an optional environment variable with default value."""
        return os.getenv(key, default)
    
    def _get_bool_env(self, key: str, default: bool = False) -> bool:
        """Get a boolean environment variable."""
        value = os.getenv(key, str(default)).lower()
        return value in ('true', '1', 'yes', 'on')
    
    def _get_int_env(self, key: str, default: int) -> int:
        """Get an integer environment variable with default value."""
        try:
            return int(os.getenv(key, default))
        except (TypeError, ValueError):
            logger.warning(f"Invalid int for {key}, using default {default}")
            return default
    
    def _get_float_env(self, key: str, default: float) -> float:
        """Get a float environment variable with default value."""
        try:
            return float(os.getenv(key, default))
        except (TypeError, ValueError):
            logger.warning(f"Invalid float for {key}, using default {default}")
            return default
    
    def _get_list_env(self, key: str, default: list) -> list:
        """Get a comma-separated list of integers from environment variable."""
        raw_value = os.getenv(key)
        if raw_value is None or raw_value.strip() == "":
            return list(default) if isinstance(default, list) else []
        values = []
        for part in raw_value.split(","):
            token = part.strip()
            if token == "":
                continue
            try:
                values.append(int(token))
            except ValueError:
                logger.warning(f"Ignoring invalid user id in {key}: {token}")
        return values
    
    def _get_user_city_map_env(self, key: str) -> dict:
        """Parse mapping env var of the form '123:City A;456:City B' into {123: 'City A', 456: 'City B'}."""
        raw_value = os.getenv(key)
        mapping: dict[int, str] = {}
        if not raw_value:
            return mapping
        for item in raw_value.split(";"):
            if not item.strip():
                continue
            if ":" not in item:
                logger.warning(f"Ignoring malformed entry in {key}: {item}")
                continue
            id_part, city_part = item.split(":", 1)
            try:
                user_id = int(id_part.strip())
                city = city_part.strip()
                if city:
                    mapping[user_id] = city
            except ValueError:
                logger.warning(f"Ignoring invalid user id in {key}: {id_part}")
        return mapping
    
    def is_admin(self, user_id: int) -> bool:
        """Admin = primary (.env ADMIN_USER_IDS) OR dynamically assigned
        (storage users.json). Primary admins cannot be removed — see is_founder_admin."""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return False
        if uid in self.admin_user_ids:
            return True
        try:
            import storage
            return storage.is_dynamic_admin(uid)
        except Exception:
            return False

    def resolved_admin_user_ids(self) -> list[int]:
        """All admins for display: .env ADMIN_USER_IDS + users.json admin_user_ids."""
        try:
            import storage
            dynamic = storage.get_dynamic_admins()
        except Exception:
            dynamic = []
        primary = [int(x) for x in (self.admin_user_ids or [])]
        return sorted(set(primary) | set(dynamic))

    def is_founder_admin(self, user_id: int) -> bool:
        """Primary admin (from .env ADMIN_USER_IDS, set at install) —
        cannot be demoted by anyone, including themselves."""
        try:
            return int(user_id) in self.admin_user_ids
        except (ValueError, TypeError):
            return False

    def is_special_user(self, user_id: int) -> bool:
        """Check if a user is in the special users list."""
        try:
            return int(user_id) in self.special_user_ids
        except Exception:
            return False
    
    def get_city_for_user(self, user_id: int) -> str | None:
        """Get configured city name for a user if present."""
        try:
            return self.user_city_map.get(int(user_id))
        except Exception:
            return None
