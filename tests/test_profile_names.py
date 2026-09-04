import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import hysteria2_manager
import anytls_manager
import mieru_manager
import mtproto_manager
import naiveproxy_manager
import profile_names
import tuic_manager
import vless_manager
import xhttp_manager


class ProfileNameTests(unittest.TestCase):
    def test_server_marker_uses_last_four_ipv4_digits(self):
        self.assertEqual(profile_names.server_marker("YOUR_VPS_IP"), "7173")
        self.assertEqual(profile_names.server_marker("203.0.113.10"), "1310")

    def test_visible_profile_name_keeps_protocol_client_and_server(self):
        self.assertEqual(
            profile_names.visible_profile_name(
                "Hysteria2",
                "YOUR_VPS_IP",
                "Hys_ID12_78",
            ),
            "Hysteria2-7173-Hys_ID12_78",
        )

    def test_regular_vless_link_does_not_use_happ_prefix(self):
        config = {
            "server": "YOUR_VPS_IP",
            "port": 443,
            "public_key": "examplePublicKeyValue",
            "short_id": "6ba85179e30d4fc2",
            "sni": "www.microsoft.com",
            "fingerprint": "chrome",
            "flow": "xtls-rprx-vision",
            "clients": [{
                "name": "Vless_ID12_78",
                "uuid": "11111111-2222-3333-4444-555555555555",
            }],
        }

        with patch.object(vless_manager, "_load_config", return_value=config):
            ok, _msg, link = vless_manager.generate_client_link("Vless_ID12_78")

        self.assertTrue(ok)
        self.assertEqual(urlsplit(link).fragment, "VLESS-7173-Vless_ID12_78")

    def test_hysteria2_uri_uses_visible_profile_name(self):
        config = {
            "server": "YOUR_VPS_IP",
            "port": 443,
            "sni": "www.microsoft.com",
            "clients": [{
                "name": "Hys_ID12_78",
                "password": "example-password",
            }],
        }

        with patch.object(hysteria2_manager, "_load_config", return_value=config):
            ok, _msg, uri = hysteria2_manager.generate_client_uri("Hys_ID12_78")

        self.assertTrue(ok)
        self.assertEqual(urlsplit(uri).fragment, "Hysteria2-7173-Hys_ID12_78")

    def test_naive_uri_and_profile_use_server_marker_without_happ(self):
        config = {
            "domain": "YOUR_VPS_IP",
            "server": "YOUR_VPS_IP",
            "port": 443,
            "username": "naive-user",
            "password": "naive-password",
            "scheme": "https",
            "local_socks_port": 10808,
            "padding": True,
        }

        with patch.object(naiveproxy_manager, "_load_config", return_value=config):
            uri = naiveproxy_manager.build_client_uri()
            profile = json.loads(naiveproxy_manager.export_aping_profile())

        self.assertEqual(urlsplit(uri).fragment, "NaiveProxy-7173")
        self.assertEqual(profile["profile"]["name"], "NaiveProxy-7173")

    def test_tuic_anytls_xhttp_uri_fragments_use_visible_profile_names(self):
        tuic_config = {
            "server": "YOUR_VPS_IP",
            "port": 443,
            "clients": [{
                "name": "Tuic_ID12_78",
                "uuid": "11111111-2222-3333-4444-555555555555",
                "password": "tuic-password",
            }],
        }
        anytls_config = {
            "server": "YOUR_VPS_IP",
            "port": 443,
            "clients": [{
                "name": "Any_ID12_78",
                "password": "anytls-password",
            }],
        }
        xhttp_config = {
            "server": "YOUR_VPS_IP",
            "port": 443,
            "clients": [{
                "name": "Xh_ID12_78",
                "uuid": "11111111-2222-3333-4444-555555555555",
            }],
        }

        with patch.object(tuic_manager, "_load_config", return_value=tuic_config):
            ok, _msg, tuic_uri = tuic_manager.generate_client_uri("Tuic_ID12_78")
        with patch.object(anytls_manager, "_load_config", return_value=anytls_config):
            ok_any, _msg, anytls_uri = anytls_manager.generate_client_uri("Any_ID12_78")
        with patch.object(xhttp_manager, "_load_config", return_value=xhttp_config):
            ok_xh, _msg, xhttp_uri = xhttp_manager.generate_client_uri("Xh_ID12_78")

        self.assertTrue(ok)
        self.assertTrue(ok_any)
        self.assertTrue(ok_xh)
        self.assertEqual(urlsplit(tuic_uri).fragment, "TUIC-7173-Tuic_ID12_78")
        self.assertEqual(urlsplit(anytls_uri).fragment, "AnyTLS-7173-Any_ID12_78")
        self.assertEqual(urlsplit(xhttp_uri).fragment, "XHTTP-7173-Xh_ID12_78")

    def test_mieru_uri_and_profile_exports_use_visible_profile_name(self):
        config = {
            "server": "YOUR_VPS_IP",
            "port_bindings": [{"port": 29999, "protocol": "TCP"}],
            "clients": [{
                "name": "Mieru_ID12_78",
                "password": "mieru-password",
            }],
        }

        with patch.object(mieru_manager, "_load_config", return_value=config):
            uri = mieru_manager.build_simple_uri("Mieru_ID12_78")
            client_config = json.loads(mieru_manager.export_client_config("Mieru_ID12_78"))
            aping_profile = json.loads(mieru_manager.export_aping_profile("Mieru_ID12_78"))

        self.assertEqual(urlsplit(uri).fragment, "Mieru-7173-Mieru_ID12_78")
        self.assertEqual(client_config["profiles"][0]["profileName"], "Mieru-7173-Mieru_ID12_78")
        self.assertEqual(aping_profile["profile"]["name"], "Mieru-7173-Mieru_ID12_78")

if __name__ == "__main__":
    unittest.main()
