#!/usr/bin/env python3
"""
Sync Xray Config
Generates Xray config using vless_manager.py and writes it to /usr/local/etc/xray/config.json
This allows the TelegramHelper bot to control the actual Xray server.
"""

import sys
import os
import json
import logging

# Add parent directory to path to import vless_manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from vless_manager import export_xray_config, get_vless_status, generate_all_keys
except ImportError as e:
    print(f"Error importing vless_manager: {e}")
    sys.exit(1)


def _reality_private_key(cfg):
    """Reality privateKey из xray-конфига ('' если нет). Его пустота = xray не стартует."""
    try:
        return cfg["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] or ""
    except (KeyError, IndexError, TypeError):
        return ""

XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"

def sync_config():
    print("Generating Xray configuration...")

    # Get server config from vless_manager
    xray_config = export_xray_config(is_server=True)
    status = get_vless_status()

    # Свежая установка: Reality privateKey пуст → xray падает с
    # 'Failed to build REALITY config: empty "privateKey"'. Генерируем ключи
    # (uuid + x25519 + short_id + default-клиент) и пере-экспортируем конфиг,
    # чтобы VLESS стартовал из коробки. Кастомизация — потом через бота (/vless_*).
    if not _reality_private_key(xray_config):
        print("Reality privateKey пуст — генерирую ключи VLESS (uuid + x25519 + default client)...")
        ok, _keys, msg = generate_all_keys()
        print(f"  {msg}")
        xray_config = export_xray_config(is_server=True)
        status = get_vless_status()
        if not _reality_private_key(xray_config):
            print("⚠️ Не удалось сгенерировать Reality-ключи (нет xray x25519 и cryptography?). "
                  "Сгенерируй через бота: /vless_gen_keys, затем systemctl restart xray.")

    if not status['configured']:
        print("Warning: VLESS is not fully configured in vless_manager. Using partial config.")
    
    # Write to Xray config file
    try:
        os.makedirs(os.path.dirname(XRAY_CONFIG_PATH), exist_ok=True)
        
        with open(XRAY_CONFIG_PATH, 'w') as f:
            json.dump(xray_config, f, indent=2)
            
        print(f"Successfully wrote Xray config to {XRAY_CONFIG_PATH}")
        print(f"Server Port: {status.get('port', 443)}")
        # UUID/SNI печатаем только если VLESS уже сконфигурирован. На свежей
        # установке клиентов/serverNames ещё нет — это НЕ ошибка (конфиг записан
        # выше, xray стартует), поэтому не индексируем пустые списки вслепую.
        try:
            inbound = xray_config.get("inbounds", [{}])[0]
            clients = inbound.get("settings", {}).get("clients", []) or []
            snis = (inbound.get("streamSettings", {})
                    .get("realitySettings", {}).get("serverNames", []) or [])
            if clients:
                print(f"UUID: {clients[0].get('id', '')}")
            if snis:
                print(f"SNI: {snis[0]}")
            if not clients:
                print("VLESS ещё не настроен (нет клиентов). Конфиг записан, xray "
                      "запущен — добавь клиента через бота (/vless_*), затем "
                      "перезапусти: systemctl restart xray.")
        except (KeyError, IndexError, TypeError):
            pass  # диагностический вывод не должен ронять синк

    except PermissionError:
        print(f"Error: Permission denied writing to {XRAY_CONFIG_PATH}. Run as root.")
        sys.exit(1)
    except Exception as e:
        print(f"Error writing config: {e}")
        sys.exit(1)

if __name__ == "__main__":
    sync_config()
