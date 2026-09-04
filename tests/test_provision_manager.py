import unittest
from unittest.mock import patch

import provision_manager


class ProvisionManagerTests(unittest.TestCase):
    def test_provision_user_uses_provided_enabled_protocols(self):
        with (
            patch.object(provision_manager, "list_enabled_protocols", return_value=[]),
            patch.object(provision_manager, "_xui_enabled", return_value=True),
            patch("xui_manager.provision_named_client", return_value=(True, "created", "vless://example")),
        ):
            result = provision_manager.provision_user(
                1290265278,
                enabled_protocols=["vless"],
            )

        self.assertIn("vless", result)
        self.assertTrue(result["vless"]["ok"])
        self.assertEqual(result["vless"]["client_name"], "Vless_ID12_78")


if __name__ == "__main__":
    unittest.main()
