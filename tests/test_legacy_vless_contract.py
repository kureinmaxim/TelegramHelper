import unittest
from unittest.mock import patch

import vless_manager


class LegacyVlessContractTests(unittest.TestCase):
    def test_build_legacy_vless_contract_keeps_required_fields(self):
        config = {
            "server": "203.0.113.10",
            "port": 443,
            "uuid": "11111111-2222-3333-4444-555555555555",
            "public_key": "examplePublicKeyValue",
            "short_id": "6ba85179e30d4fc2",
            "sni": "www.microsoft.com",
            "fingerprint": "chrome",
            "flow": "xtls-rprx-vision",
            "private_key": "server-only-secret",
        }

        legacy = vless_manager.build_legacy_vless_contract(config)
        is_valid, missing = vless_manager.validate_legacy_vless_contract(legacy)

        self.assertTrue(is_valid, f"missing fields: {missing}")
        self.assertEqual(
            tuple(legacy.keys()),
            vless_manager.LEGACY_VLESS_REQUIRED_FIELDS,
        )
        self.assertNotIn("private_key", legacy)

    def test_validate_legacy_vless_contract_reports_missing_fields(self):
        legacy = {
            "server": "203.0.113.10",
            "port": 443,
            "uuid": "",
            "public_key": "examplePublicKeyValue",
            "short_id": "",
            "sni": "www.microsoft.com",
            "fingerprint": "chrome",
            "flow": "xtls-rprx-vision",
        }

        is_valid, missing = vless_manager.validate_legacy_vless_contract(legacy)

        self.assertFalse(is_valid)
        self.assertEqual(missing, ["uuid", "short_id"])

    def test_server_xray_config_forces_ipv4_without_blackhole(self):
        # Regression guard: a blind `::/0 -> blackhole` rule used to also kill
        # Reality's dest relay (Reality dials serverNames[0]:443 for every
        # connection; when that dest resolved to IPv6 the dial hit the blackhole
        # and Xray rejected EVERY client with "REALITY: processed invalid
        # connection"). No-IPv6 egress must instead be handled by freedom/UseIPv4
        # plus inbound sniffing destOverride (which re-resolves IPv6-literal
        # client destinations to the sniffed domain).
        config = {
            "enabled": True,
            "server": "203.0.113.10",
            "port": 443,
            "uuid": "11111111-2222-3333-4444-555555555555",
            "public_key": "examplePublicKeyValue",
            "private_key": "examplePrivateKeyValue",
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
            xray_config = vless_manager.export_xray_config(is_server=True)

        # No blind IPv6 blackhole and no ::/0 routing rule.
        self.assertNotIn(
            {"protocol": "blackhole", "tag": "block-ipv6"},
            xray_config["outbounds"],
        )
        self.assertNotIn("routing", xray_config)

        # IPv6 egress is suppressed via freedom/UseIPv4 instead.
        freedom = next(
            o for o in xray_config["outbounds"] if o.get("protocol") == "freedom"
        )
        self.assertEqual(freedom["settings"]["domainStrategy"], "UseIPv4")

        # Inbound sniffing rewrites IPv6-literal destinations to the SNI domain.
        inbound = next(
            i for i in xray_config["inbounds"] if i.get("protocol") == "vless"
        )
        self.assertTrue(inbound["sniffing"]["enabled"])
        self.assertIn("tls", inbound["sniffing"]["destOverride"])


if __name__ == "__main__":
    unittest.main()
