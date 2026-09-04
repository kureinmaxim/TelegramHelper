import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import xui_manager


class XuiConfigPathTests(unittest.TestCase):
    def test_load_config_reads_directory_fallback_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "xui_config.json"
            config_dir.mkdir()
            payload = {
                "enabled": True,
                "base_url": "https://127.0.0.1:8081/mxmurl",
                "username": "admin",
                "password_enc_b64": "encrypted",
                "default_inbound_id": 7,
            }
            (config_dir / "config.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            with patch.object(xui_manager, "CONFIG_PATH", str(config_dir)):
                cfg = xui_manager.load_config()

        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["base_url"], "https://127.0.0.1:8081/mxmurl")
        self.assertEqual(cfg["default_inbound_id"], 7)

    def test_save_config_writes_directory_fallback_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "xui_config.json"
            config_dir.mkdir()
            cfg = xui_manager._empty_config()
            cfg.update({
                "enabled": True,
                "base_url": "https://127.0.0.1:8081/mxmurl",
                "username": "admin",
                "password_enc_b64": "encrypted",
                "default_inbound_id": 9,
            })

            with patch.object(xui_manager, "CONFIG_PATH", str(config_dir)):
                ok, msg = xui_manager.save_config(cfg)
                saved = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(ok, msg)
        self.assertEqual(saved["base_url"], "https://127.0.0.1:8081/mxmurl")
        self.assertEqual(saved["default_inbound_id"], 9)

    def test_to_loopback_base_url_rewrites_mesh_ipv4(self):
        self.assertEqual(
            xui_manager.to_loopback_base_url("https://100.64.0.6:8081/mxmurl"),
            "https://127.0.0.1:8081/mxmurl",
        )
        self.assertIsNone(
            xui_manager.to_loopback_base_url("https://127.0.0.1:8081/mxmurl")
        )
        self.assertIsNone(
            xui_manager.to_loopback_base_url("https://panel.example.com:8081/x")
        )

    def test_is_transient_network_error(self):
        self.assertTrue(
            xui_manager._is_transient_network_error(
                "network: HTTPSConnectionPool ConnectTimeoutError timed out"
            )
        )
        self.assertFalse(
            xui_manager._is_transient_network_error("wrong username or password")
        )


if __name__ == "__main__":
    unittest.main()
