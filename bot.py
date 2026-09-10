# -*- coding: utf-8 -*-
"""
Lightweight Telegram bot with VLESS-Reality support.

This module is the slim bot with commands for:
- Basics: start, help, info, clear
- Admin: ver, dockhand, headscale, API keys, encryption, users
- VLESS-Reality: full VLESS configuration management
"""

import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from handlers import BotHandlersLite
from config import Config
from telegram_bot_menu import setup_bot_commands
from telegram_text_normalize import normalize_pasted_command_update

logger = logging.getLogger(__name__)


class TelegramBotLite:
    """Lightweight Telegram bot with VLESS-Reality support."""
    
    def __init__(self, config: Config):
        """Initialize the bot with configuration."""
        self.config = config
        self.handlers = BotHandlersLite(config=self.config)
        self.application = None
    
    async def start(self, blocking: bool = True):
        """Start the bot."""
        try:
            # Build the Application
            self.application = (
                Application.builder()
                .token(self.config.bot_token)
                .connect_timeout(self.config.bot_connect_timeout)
                .read_timeout(self.config.bot_read_timeout)
                .write_timeout(self.config.bot_write_timeout)
                .pool_timeout(self.config.bot_pool_timeout)
                .build()
            )
            
            # Register handlers
            self._register_handlers()
            
            # Start the bot
            logger.info("Bot Lite is starting...")
            await self.application.initialize()
            await self.application.start()
            await setup_bot_commands(self.application.bot, self.config)

            # Start polling
            logger.info("Bot Lite is now polling for updates...")
            await self.application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                timeout=self.config.poll_timeout,
                poll_interval=self.config.poll_interval,
            )
            
            if not blocking:
                return
            
            # Keep the bot running until interrupted
            import signal
            stop_signals = (signal.SIGINT, signal.SIGTERM)
            loop = asyncio.get_running_loop()
            stop_future = loop.create_future()
            
            def signal_handler(signum, frame):
                logger.info(f"Received signal {signum}")
                if not stop_future.done():
                    stop_future.set_result(signum)
            
            for sig in stop_signals:
                signal.signal(sig, signal_handler)
            
            try:
                await stop_future
            except Exception as e:
                logger.error(f"Error while waiting: {e}")
            finally:
                logger.info("Stopping bot...")
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            await self._cleanup_after_failed_start()
            raise
        finally:
            if blocking and self.application:
                await self.application.stop()
                await self.application.shutdown()

    async def start_with_retry(self, blocking: bool = True):
        """Start the Telegram bot with retry; do not take down the whole process on network errors."""
        attempt = 0
        max_attempts = int(getattr(self.config, "bot_start_max_attempts", 0) or 0)
        retry_interval = max(
            1,
            int(getattr(self.config, "bot_start_retry_interval", 30) or 30),
        )

        while True:
            attempt += 1
            try:
                await self.start(blocking=blocking)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if max_attempts and attempt >= max_attempts:
                    logger.error(
                        "Telegram bot failed to start after %s attempts: %s",
                        attempt,
                        exc,
                    )
                    raise
                logger.warning(
                    "Telegram bot start attempt %s failed: %s. Retrying in %ss",
                    attempt,
                    exc,
                    retry_interval,
                )
                await asyncio.sleep(retry_interval)

    async def _cleanup_after_failed_start(self):
        """Best-effort cleanup after initialize/start failed."""
        app = self.application
        if not app:
            return
        try:
            updater = getattr(app, "updater", None)
            if updater and getattr(updater, "running", False):
                await updater.stop()
        except Exception:
            logger.debug("Ignoring updater cleanup failure", exc_info=True)
        try:
            if getattr(app, "running", False):
                await app.stop()
        except Exception:
            logger.debug("Ignoring application stop failure", exc_info=True)
        try:
            await app.shutdown()
        except Exception:
            logger.debug("Ignoring application shutdown failure", exc_info=True)
        self.application = None
    
    async def stop(self):
        """Stop the bot."""
        if self.application:
            logger.info("Stopping bot application...")
            await self._cleanup_after_failed_start()
    
    def _register_handlers(self):
        """Register all command handlers."""
        try:
            # iOS / paste: without BOT_COMMAND and with invisible chars, CommandHandler never fires
            self.application.add_handler(
                MessageHandler(filters.TEXT, normalize_pasted_command_update),
                group=-1,
            )

            # === BASIC COMMANDS ===
            self.application.add_handler(
                CommandHandler("start", self.handlers.start_command)
            )
            self.application.add_handler(
                CommandHandler("help", self.handlers.help_command)
            )
            self.application.add_handler(
                CommandHandler("info", self.handlers.info_command)
            )
            self.application.add_handler(
                CommandHandler("clear", self.handlers.clear_chat)
            )
            self.application.add_handler(
                CommandHandler("settings", self.handlers.settings_command)
            )
            self.application.add_handler(
                CommandHandler("id", self.handlers.id_command)
            )
            self.application.add_handler(
                CommandHandler("ai", self.handlers.ai_command)
            )
            self.application.add_handler(
                CommandHandler("tr", self.handlers.translate_command)
            )
            self.application.add_handler(
                CommandHandler("tr_ai", self.handlers.translate_ai_command)
            )
            self.application.add_handler(
                CommandHandler("ru", self.handlers.translate_ru)
            )
            self.application.add_handler(
                CommandHandler("en", self.handlers.translate_en)
            )
            self.application.add_handler(
                CommandHandler("fr", self.handlers.translate_fr)
            )
            self.application.add_handler(
                CommandHandler("kb_on", self.handlers.kb_on)
            )
            self.application.add_handler(
                CommandHandler("kb_off", self.handlers.kb_off)
            )
            self.application.add_handler(
                CommandHandler("kb_translate", self.handlers.kb_translate)
            )
            self.application.add_handler(
                CommandHandler("kb_hide", self.handlers.kb_hide)
            )
            self.application.add_handler(
                CommandHandler("prompt", self.handlers.prompt_command)
            )
            self.application.add_handler(
                CommandHandler("prompt_show", self.handlers.prompt_show)
            )
            self.application.add_handler(
                CommandHandler("help_prompt", self.handlers.help_prompt_command)
            )
            
            # === SYSTEM AND INFO (admin) ===
            self.application.add_handler(
                CommandHandler("ver", self.handlers.version_command)
            )
            self.application.add_handler(
                CommandHandler("dockhand", self.handlers.dockhand_command)
            )
            # VPS transport diagnostics — available to everyone,
            # no secrets; live checks of ports/processes/containers.
            self.application.add_handler(
                CommandHandler("diag", self.handlers.diag_command)
            )
            self.application.add_handler(
                CommandHandler("api", self.handlers.api_command)
            )
            self.application.add_handler(
                CommandHandler("gen_api_key", self.handlers.gen_api_key_command)
            )
            self.application.add_handler(
                CommandHandler("del_api_key", self.handlers.del_api_key_command)
            )
            self.application.add_handler(
                CommandHandler("encryption_key", self.handlers.encryption_key_command)
            )
            self.application.add_handler(
                CommandHandler("gen_encryption_key", self.handlers.gen_encryption_key_command)
            )
            self.application.add_handler(
                CommandHandler("del_encryption_key", self.handlers.del_encryption_key_command)
            )
            self.application.add_handler(
                CommandHandler("gen_chacha_key", self.handlers.gen_chacha_key_command)
            )
            self.application.add_handler(
                CommandHandler("gen_pqc_key", self.handlers.gen_pqc_key_command)
            )

            backup_commands = {
                "rclone": self.handlers.rclone_command,
                "backup_status": self.handlers.backup_status,
                "backup_test": self.handlers.backup_test,
                "backup_now": self.handlers.backup_now,
                "backup_list": self.handlers.backup_list,
            }
            for cmd_name, cmd_handler in backup_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, cmd_handler))
            
            # === USER MANAGEMENT (admin) ===
            self.application.add_handler(
                CommandHandler("list_users", self.handlers.admin_list_users)
            )
            self.application.add_handler(
                CommandHandler("setcity", self.handlers.admin_setcity)
            )
            self.application.add_handler(
                CommandHandler("setgreeting", self.handlers.admin_setgreeting)
            )
            self.application.add_handler(
                CommandHandler("special_add", self.handlers.admin_special_add)
            )
            self.application.add_handler(
                CommandHandler("admin_list", self.handlers.admin_list)
            )
            self.application.add_handler(
                CommandHandler("admin_add", self.handlers.admin_add)
            )
            self.application.add_handler(
                CommandHandler("admin_remove", self.handlers.admin_remove)
            )
            self.application.add_handler(
                CommandHandler("special_remove", self.handlers.admin_special_remove)
            )
            # first_seen / last_seen log (admin + special)
            self.application.add_handler(
                CommandHandler("users_log", self.handlers.users_log_command)
            )
            # Current user's profiles (admin + special)
            self.application.add_handler(
                CommandHandler("my_profile", self.handlers.my_profile_command)
            )
            # User card by TG ID: profile menu for all protocols
            # (create / delete / rotate / QR). Admin only.
            self.application.add_handler(
                CommandHandler("user", self.handlers.user_card_command)
            )
            
            # === AI SETTINGS (admin) ===
            self.application.add_handler(
                CommandHandler("ai_provider", self.handlers.ai_set_provider)
            )
            self.application.add_handler(
                CommandHandler("ch_model", self.handlers.ch_model_command)
            )
            
            # === VLESS-REALITY COMMANDS (admin) ===
            self.application.add_handler(
                CommandHandler("vless_status", self.handlers.vless_status)
            )
            self.application.add_handler(
                CommandHandler("vless_on", self.handlers.vless_on)
            )
            self.application.add_handler(
                CommandHandler("vless_off", self.handlers.vless_off)
            )
            self.application.add_handler(
                CommandHandler("vless_config", self.handlers.vless_config)
            )
            self.application.add_handler(
                CommandHandler("vless_set_server", self.handlers.vless_set_server)
            )
            self.application.add_handler(
                CommandHandler("vless_set_port", self.handlers.vless_set_port)
            )
            self.application.add_handler(
                CommandHandler("vless_add_client", self.handlers.vless_add_client)
            )
            self.application.add_handler(
                CommandHandler("vless_qr", self.handlers.vless_qr)
            )
            self.application.add_handler(
                CommandHandler("vless_list_clients", self.handlers.vless_list_clients)
            )
            self.application.add_handler(
                CommandHandler("vless_del_client", self.handlers.vless_del_client)
            )
            self.application.add_handler(
                CommandHandler("vless_set_uuid", self.handlers.vless_set_uuid)
            )
            self.application.add_handler(
                CommandHandler("vless_set_key", self.handlers.vless_set_key)
            )
            self.application.add_handler(
                CommandHandler("vless_set_shortid", self.handlers.vless_set_shortid)
            )
            self.application.add_handler(
                CommandHandler("vless_set_sni", self.handlers.vless_set_sni)
            )
            self.application.add_handler(
                CommandHandler("vless_set_fingerprint", self.handlers.vless_set_fingerprint)
            )
            self.application.add_handler(
                CommandHandler("vless_gen_keys", self.handlers.vless_gen_keys)
            )
            self.application.add_handler(
                CommandHandler("vless_test", self.handlers.vless_test)
            )
            self.application.add_handler(
                CommandHandler("vless_export", self.handlers.vless_export)
            )
            self.application.add_handler(
                CommandHandler("vless_sync", self.handlers.vless_sync)
            )
            self.application.add_handler(
                CommandHandler("vless_reset", self.handlers.vless_reset)
            )
            
            # === XRAY MANAGEMENT COMMANDS (admin) ===
            self.application.add_handler(
                CommandHandler("xray_status", self.handlers.xray_status)
            )
            self.application.add_handler(
                CommandHandler("xray_config", self.handlers.xray_config)
            )
            self.application.add_handler(
                CommandHandler("xray_install", self.handlers.xray_install)
            )
            self.application.add_handler(
                CommandHandler("xray_apply", self.handlers.xray_apply)
            )
            self.application.add_handler(
                CommandHandler("xray_start", self.handlers.xray_start)
            )
            self.application.add_handler(
                CommandHandler("xray_stop", self.handlers.xray_stop)
            )
            self.application.add_handler(
                CommandHandler("xray_restart", self.handlers.xray_restart)
            )
            self.application.add_handler(
                CommandHandler("xray_logs", self.handlers.xray_logs)
            )

            # === NGINX SNI ROUTING COMMANDS ===
            nginx_commands = {
                "nginx_status": self.handlers.nginx_status,
                "nginx_enable": self.handlers.nginx_enable,
                "nginx_disable": self.handlers.nginx_disable,
                "nginx_set_domain": self.handlers.nginx_set_domain,
                "nginx_config": self.handlers.nginx_config,
            }
            for cmd_name, cmd_handler in nginx_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, cmd_handler))

            # === HEADSCALE COMMANDS ===
            headscale_commands = {
                "headscale": self.handlers.headscale_host_tailscale_command,
                "headscale_status": self.handlers.headscale_status,
                "headscale_enable": self.handlers.headscale_enable,
                "headscale_disable": self.handlers.headscale_disable,
                "headscale_set_url": self.handlers.headscale_set_url,
                "headscale_gen": self.handlers.headscale_gen,
                "headscale_revoke": self.handlers.headscale_revoke,
                "headscale_list_nodes": self.handlers.headscale_list_nodes,
                "headscale_create_user": self.handlers.headscale_create_user,
                "exit_node": self.handlers.exit_node_command,
                "exit_node_on": self.handlers.exit_node_on_command,
                "exit_node_off": self.handlers.exit_node_off_command,
            }
            for cmd_name, cmd_handler in headscale_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, cmd_handler))

            # === HYSTERIA2 COMMANDS ===
            hy2_commands = {
                "hy2": self.handlers.hy2_command,
                "hy2_status": self.handlers.hy2_status,
                "hy2_on": self.handlers.hy2_on,
                "hy2_off": self.handlers.hy2_off,
                "hy2_config": self.handlers.hy2_config,
                "hy2_set_server": self.handlers.hy2_set_server,
                "hy2_set_port": self.handlers.hy2_set_port,
                "hy2_set_password": self.handlers.hy2_set_password,
                "hy2_set_obfs": self.handlers.hy2_set_obfs,
                "hy2_set_sni": self.handlers.hy2_set_sni,
                "hy2_set_speed": self.handlers.hy2_set_speed,
                "hy2_set_masquerade": self.handlers.hy2_set_masquerade,
                "hy2_set_insecure": self.handlers.hy2_set_insecure,
                "hy2_set_quic_safe": self.handlers.hy2_set_quic_safe,
                "hy2_set_quic": self.handlers.hy2_set_quic,
                "hy2_gen_password": self.handlers.hy2_gen_password,
                "hy2_gen_cert": self.handlers.hy2_gen_cert,
                "hy2_gen_all": self.handlers.hy2_gen_all,
                "hy2_add_client": self.handlers.hy2_add_client,
                "hy2_qr": self.handlers.hy2_qr,
                "hy2_del_client": self.handlers.hy2_del_client,
                "hy2_list_clients": self.handlers.hy2_list_clients,
                "hy2_install": self.handlers.hy2_install,
                "hy2_apply": self.handlers.hy2_apply,
                "hy2_start": self.handlers.hy2_start,
                "hy2_stop": self.handlers.hy2_stop,
                "hy2_restart": self.handlers.hy2_restart,
                "hy2_logs": self.handlers.hy2_logs,
                "hy2_export": self.handlers.hy2_export,
            }
            for cmd_name, handler_func in hy2_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === RETICULUM / HA-STACK COMMANDS ===
            reticulum_commands = {
                "reticulum_status": self.handlers.reticulum_status,
                "reticulum_restart": self.handlers.reticulum_restart,
                "reticulum_hash": self.handlers.reticulum_hash,
                "reticulum_i2p": self.handlers.reticulum_i2p,
                "reticulum_health": self.handlers.reticulum_health,
            }
            for cmd_name, handler_func in reticulum_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === NAIVEPROXY COMMANDS ===
            naive_commands = {
                "naive_status": self.handlers.naive_status,
                "naive_on": self.handlers.naive_on,
                "naive_off": self.handlers.naive_off,
                "naive_config": self.handlers.naive_config,
                "naive_set_domain": self.handlers.naive_set_domain,
                "naive_set_port": self.handlers.naive_set_port,
                "naive_set_user": self.handlers.naive_set_user,
                "naive_set_password": self.handlers.naive_set_password,
                "naive_set_dpi": self.handlers.naive_set_dpi,
                "naive_gen_creds": self.handlers.naive_gen_creds,
                "naive_install": self.handlers.naive_install,
                "naive_uri": self.handlers.naive_uri,
                "naive_apply": self.handlers.naive_apply,
                "naive_export": self.handlers.naive_export,
            }
            for cmd_name, handler_func in naive_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === TUIC COMMANDS ===
            tuic_commands = {
                "tuic_status": self.handlers.tuic_status,
                "tuic_on": self.handlers.tuic_on,
                "tuic_off": self.handlers.tuic_off,
                "tuic_config": self.handlers.tuic_config,
                "tuic_set_server": self.handlers.tuic_set_server,
                "tuic_set_port": self.handlers.tuic_set_port,
                "tuic_set_cc": self.handlers.tuic_set_cc,
                "tuic_gen_cert": self.handlers.tuic_gen_cert,
                "tuic_gen_all": self.handlers.tuic_gen_all,
                "tuic_add": self.handlers.tuic_add_client,
                "tuic_qr": self.handlers.tuic_qr,
                "tuic_del": self.handlers.tuic_del_client,
                "tuic_list": self.handlers.tuic_list_clients,
                "tuic_apply": self.handlers.tuic_apply,
                "tuic_start": self.handlers.tuic_start,
                "tuic_stop": self.handlers.tuic_stop,
                "tuic_restart": self.handlers.tuic_restart,
                "tuic_logs": self.handlers.tuic_logs,
                "tuic_export": self.handlers.tuic_export,
            }
            for cmd_name, handler_func in tuic_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === ANYTLS COMMANDS ===
            anytls_commands = {
                "anytls_status": self.handlers.anytls_status,
                "anytls_on": self.handlers.anytls_on,
                "anytls_off": self.handlers.anytls_off,
                "anytls_config": self.handlers.anytls_config,
                "anytls_set_server": self.handlers.anytls_set_server,
                "anytls_set_port": self.handlers.anytls_set_port,
                "anytls_gen_cert": self.handlers.anytls_gen_cert,
                "anytls_gen_all": self.handlers.anytls_gen_all,
                "anytls_add": self.handlers.anytls_add_client,
                "anytls_qr": self.handlers.anytls_qr,
                "anytls_del": self.handlers.anytls_del_client,
                "anytls_list": self.handlers.anytls_list_clients,
                "anytls_apply": self.handlers.anytls_apply,
                "anytls_start": self.handlers.anytls_start,
                "anytls_stop": self.handlers.anytls_stop,
                "anytls_restart": self.handlers.anytls_restart,
                "anytls_logs": self.handlers.anytls_logs,
                "anytls_export": self.handlers.anytls_export,
            }
            for cmd_name, handler_func in anytls_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === XHTTP COMMANDS ===
            xhttp_commands = {
                "xhttp_status": self.handlers.xhttp_status,
                "xhttp_on": self.handlers.xhttp_on,
                "xhttp_off": self.handlers.xhttp_off,
                "xhttp_config": self.handlers.xhttp_config,
                "xhttp_set_server": self.handlers.xhttp_set_server,
                "xhttp_set_port": self.handlers.xhttp_set_port,
                "xhttp_set_path": self.handlers.xhttp_set_path,
                "xhttp_set_host": self.handlers.xhttp_set_host,
                "xhttp_set_mode": self.handlers.xhttp_set_mode,
                "xhttp_gen_cert": self.handlers.xhttp_gen_cert,
                "xhttp_gen_all": self.handlers.xhttp_gen_all,
                "xhttp_add": self.handlers.xhttp_add_client,
                "xhttp_qr": self.handlers.xhttp_qr,
                "xhttp_del": self.handlers.xhttp_del_client,
                "xhttp_list": self.handlers.xhttp_list_clients,
                "xhttp_apply": self.handlers.xhttp_apply,
                "xhttp_start": self.handlers.xhttp_start,
                "xhttp_stop": self.handlers.xhttp_stop,
                "xhttp_restart": self.handlers.xhttp_restart,
                "xhttp_logs": self.handlers.xhttp_logs,
                "xhttp_export": self.handlers.xhttp_export,
            }
            for cmd_name, handler_func in xhttp_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === MIERU COMMANDS ===
            mieru_commands = {
                "mieru_status": self.handlers.mieru_status,
                "mieru_config": self.handlers.mieru_config,
                "mieru_set_server": self.handlers.mieru_set_server,
                "mieru_set_port": self.handlers.mieru_set_port,
                "mieru_set_mtu": self.handlers.mieru_set_mtu,
                "mieru_set_multiplexing": self.handlers.mieru_set_multiplexing,
                "mieru_set_handshake": self.handlers.mieru_set_handshake,
                "mieru_set_socks5_port": self.handlers.mieru_set_socks5_port,
                "mieru_gen_password": self.handlers.mieru_gen_password,
                "mieru_add_client": self.handlers.mieru_add_client,
                "mieru_list_clients": self.handlers.mieru_list_clients,
                "mieru_del_client": self.handlers.mieru_del_client,
                "mieru_install": self.handlers.mieru_install,
                "mieru_apply": self.handlers.mieru_apply,
                "mieru_start": self.handlers.mieru_start,
                "mieru_stop": self.handlers.mieru_stop,
                "mieru_restart": self.handlers.mieru_restart,
                "mieru_logs": self.handlers.mieru_logs,
                "mieru_export": self.handlers.mieru_export,
                "mieru_set_dpi": self.handlers.mieru_set_dpi,
            }
            for cmd_name, handler_func in mieru_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === MTPROTO PROXY COMMANDS ===
            mt_commands = {
                "mt_status": self.handlers.mt_status,
                "mt_on": self.handlers.mt_on,
                "mt_off": self.handlers.mt_off,
                "mt_config": self.handlers.mt_config,
                "mt_set_server": self.handlers.mt_set_server,
                "mt_set_port": self.handlers.mt_set_port,
                "mt_set_mode": self.handlers.mt_set_mode,
                "mt_set_domain": self.handlers.mt_set_domain,
                "mt_set_tag": self.handlers.mt_set_tag,
                "mt_set_workers": self.handlers.mt_set_workers,
                "mt_gen_secret": self.handlers.mt_gen_secret,
                "mt_gen_all": self.handlers.mt_gen_all,
                "mt_add_client": self.handlers.mt_add_client,
                "mt_qr": self.handlers.mt_qr,
                "mt_del_client": self.handlers.mt_del_client,
                "mt_list_clients": self.handlers.mt_list_clients,
                "mt_install": self.handlers.mt_install,
                "mt_apply": self.handlers.mt_apply,
                "mt_start": self.handlers.mt_start,
                "mt_stop": self.handlers.mt_stop,
                "mt_restart": self.handlers.mt_restart,
                "mt_logs": self.handlers.mt_logs,
                "mt_fetch_config": self.handlers.mt_fetch_config,
                "mt_export": self.handlers.mt_export,
            }
            for cmd_name, handler_func in mt_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === 3X-UI INTEGRATION ===
            # ConversationHandler for step-by-step /xui_setup. Registered
            # BEFORE the shared CallbackQueryHandler so our `xui_vfy_*` /
            # `xui_ib*` callbacks in an active dialog land here,
            # not in the catch-all router. PTB skips the Conversation if
            # the user has no active state, so ordinary clicks
            # on a /user card are unaffected.
            xui_conv = ConversationHandler(
                entry_points=[
                    CommandHandler("xui_setup", self.handlers.xui_setup_start),
                ],
                states={
                    self.handlers.XUI_URL: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self.handlers.xui_setup_url,
                        )
                    ],
                    self.handlers.XUI_USER: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self.handlers.xui_setup_user,
                        )
                    ],
                    self.handlers.XUI_PWD: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self.handlers.xui_setup_pwd,
                        )
                    ],
                    self.handlers.XUI_VERIFY: [
                        CallbackQueryHandler(
                            self.handlers.xui_setup_verify,
                            pattern=r"^xui_vfy_",
                        )
                    ],
                    self.handlers.XUI_INBOUND: [
                        CallbackQueryHandler(
                            self.handlers.xui_setup_inbound,
                            pattern=r"^xui_ib",
                        )
                    ],
                },
                fallbacks=[
                    CommandHandler("xui_cancel", self.handlers.xui_setup_cancel),
                    CommandHandler("cancel", self.handlers.xui_setup_cancel),
                ],
                name="xui_setup_conversation",
                allow_reentry=True,
            )
            self.application.add_handler(xui_conv)

            xui_commands = {
                "xui_status": self.handlers.xui_status_command,
                "xui_list": self.handlers.xui_list_command,
                "xui_set_inbound": self.handlers.xui_set_inbound_command,
                "xui_enable": self.handlers.xui_enable_command,
                "xui_disable": self.handlers.xui_disable_command,
                "xui_clear": self.handlers.xui_clear_command,
            }
            for cmd_name, handler_func in xui_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === Bot-managed provisioning ===
            # Auto-create clients with canonical names in bot-managed inbounds
            # for every enabled protocol (see provision_manager.py).
            provision_commands = {
                "provision": self.handlers.provision_command,
                "provision_all": self.handlers.provision_all_command,
                "profiles": self.handlers.profiles_command,
                "clean_user": self.handlers.clean_user_command,
                "setemail": self.handlers.setemail_command,
                "email_profile": self.handlers.email_profile_command,
            }
            for cmd_name, handler_func in provision_commands.items():
                self.application.add_handler(CommandHandler(cmd_name, handler_func))

            # === RAW TG ID FROM ADMIN ===
            # An admin can send a bare number (TG ID) — that opens the user
            # card. Register after all CommandHandlers so /user stays
            # preferred; the digits-only filter excludes commands and normal chat.
            self.application.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(r"^\d{4,15}$"),
                    self.handlers.admin_raw_id_message,
                )
            )

            # Remember last free-text message for /tr and RU/EN/FR keyboard.
            # After the raw-id handler so a numeric Telegram ID still opens a card.
            self.application.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self.handlers.remember_chat_text,
                )
            )

            # === Inline-picker callbacks for /provision /profiles /clean_user ===
            # Registered BEFORE the catch-all — pattern is specific.
            self.application.add_handler(
                CallbackQueryHandler(
                    self.handlers.provision_picker_callback,
                    pattern=r"^(prov_pick|prof_pick|clean_pick):",
                )
            )

            # === CALLBACK QUERY HANDLER ===
            self.application.add_handler(
                CallbackQueryHandler(self.handlers.callback_query_handler)
            )
            
            # === ERROR HANDLER ===
            self.application.add_error_handler(self.handlers.error_handler)
            
            logger.info("All handlers registered successfully")
            
        except Exception as e:
            logger.error(f"Error registering handlers: {e}")
            raise
