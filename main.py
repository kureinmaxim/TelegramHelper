#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry point for the Telegram bot with VLESS-Reality support.

TelegramHelper combines:
1. Telegram bot — API keys, encryption, and VLESS-Reality management
2. REST API — protected service for integrating AI into third-party apps

Run:
    python main.py              # Bot + API
    python main.py --api-only   # API only
    python main.py --bot-only   # Bot only
"""

import asyncio
import logging
import sys
import os
import re
from pathlib import Path
from dotenv import load_dotenv

from bot import TelegramBotLite
from config import Config
from email_manager import validate_smtp_env


def load_environment():
    """Load environment variables from the .env file."""
    env_path = Path('.env')
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✅ Loaded environment variables from {env_path}")
    else:
        print("ℹ️ No .env file found, using system environment variables")


class SensitiveDataFilter(logging.Filter):
    """Redact sensitive data (including the Telegram bot token) from logs."""

    _BOT_TOKEN_IN_URL_RE = re.compile(r"/bot(\d+:[A-Za-z0-9_-]+)/")
    _BOT_TOKEN_RE = re.compile(r"\b(\d+:[A-Za-z0-9_-]{20,})\b")

    @classmethod
    def _sanitize(cls, text: str) -> str:
        sanitized = cls._BOT_TOKEN_IN_URL_RE.sub("/bot***REDACTED***/", text)
        sanitized = cls._BOT_TOKEN_RE.sub("***REDACTED***", sanitized)
        return sanitized

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            sanitized = self._sanitize(msg)
            if sanitized != msg:
                record.msg = sanitized
                record.args = ()
        except Exception:
            # Never break logging because of this filter.
            pass
        return True


def _configure_logging() -> logging.Logger:
    """Configure logging."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Log to stdout
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(stream_handler)
    
    # Try logging to a file
    try:
        file_handler = logging.FileHandler('bot.log')
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(file_handler)
    except Exception as file_error:
        root_logger.warning(
            "File logging disabled: %s. Using console logging only.",
            file_error,
        )

    # Reduce log noise:
    # - FastAPI/Uvicorn healthcheck access logs
    # - verbose httpx/httpcore INFO logs (including getUpdates)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)


logger = _configure_logging()


def print_banner():
    """Print the startup banner."""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🤖 TelegramHelper                                            ║
║   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                  ║
║                                                              ║
║   📡 API Management + 🛡️ VLESS-Reality Control               ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


async def main():
    """Initialize and start the bot and API."""
    try:
        print_banner()
        
        # Load environment variables
        load_environment()

        validate_smtp_env()

        # Initialize configuration
        config = Config()
        
        # Check bot token
        if not config.bot_token and not "--api-only" in sys.argv:
            logger.warning("⚠️  BOT_TOKEN is not set. Forcing API-only mode.")
            # No token: force API-only mode
            sys.argv.append("--api-only")
        
        # Parse command-line arguments
        api_only = "--api-only" in sys.argv
        bot_only = "--bot-only" in sys.argv
        
        bot = None
        bot_task = None
        
        if not api_only:
            # Initialize the bot
            bot = TelegramBotLite(config)
            
            if bot_only:
                # Start the bot only, in blocking mode
                logger.info("🤖 Starting Telegram bot (bot-only mode)...")
                await bot.start_with_retry(blocking=True)
                return
            else:
                # Start the bot in the background: Telegram timeouts must not take down the API/healthcheck.
                logger.info("🤖 Starting Telegram bot in background...")
                bot_task = asyncio.create_task(bot.start_with_retry(blocking=False))
        else:
            logger.info("ℹ️ Running in API-only mode (Telegram bot disabled)")
        
        # Start the API server
        import uvicorn
        from api import app
        
        api_port = int(os.getenv("API_PORT", "8000"))
        api_host = os.getenv("API_HOST", "0.0.0.0")
        
        uvicorn_config = uvicorn.Config(app, host=api_host, port=api_port, log_level="info")
        server = uvicorn.Server(uvicorn_config)
        
        logger.info(f"📡 Starting API server on {api_host}:{api_port}...")
        
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("API server cancelled")
        finally:
            logger.info("Shutting down...")
            if bot_task and not bot_task.done():
                bot_task.cancel()
                try:
                    await bot_task
                except asyncio.CancelledError:
                    pass
            if bot and not api_only:
                await bot.stop()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    print("\n🚀 Starting TelegramHelper...\n")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        sys.exit(1)
