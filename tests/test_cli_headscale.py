# -*- coding: utf-8 -*-
"""
Тесты SSH CLI Headscale-команд и инфраструктуры prompt (без запущенного сервера).

Покрывает чистую логику:
  - разбор аргументов /headscale_gen (срок vs user);
  - редакцию истории (секретный аргумент /headscale_revoke не сохраняется);
  - маршрутизацию AdminCLI на headscale_manager.

Запуск: python -m unittest tests.test_cli_headscale
"""

import unittest
from unittest.mock import patch

import headscale_manager
import cli_prompt
from admin_cli import AdminCLI


class ParseUserExpirationTests(unittest.TestCase):
    def test_only_duration(self):
        self.assertEqual(headscale_manager.parse_user_expiration(["720h"]), (None, "720h"))

    def test_only_user(self):
        self.assertEqual(headscale_manager.parse_user_expiration(["alice"]), ("alice", None))

    def test_user_and_duration_any_order(self):
        self.assertEqual(
            headscale_manager.parse_user_expiration(["alice", "30m"]), ("alice", "30m"))
        self.assertEqual(
            headscale_manager.parse_user_expiration(["7d", "alice"]), ("alice", "7d"))

    def test_empty(self):
        self.assertEqual(headscale_manager.parse_user_expiration([]), (None, None))

    def test_duration_units(self):
        for tok in ("30s", "15m", "24h", "7d"):
            self.assertEqual(headscale_manager.parse_user_expiration([tok]), (None, tok))


class HistoryRedactionTests(unittest.TestCase):
    def test_revoke_strips_secret_key(self):
        self.assertEqual(
            cli_prompt.redact_for_history("/headscale_revoke deadbeefKEY123456"),
            "/headscale_revoke",
        )

    def test_revoke_without_args_kept(self):
        self.assertEqual(
            cli_prompt.redact_for_history("/headscale_revoke"), "/headscale_revoke")

    def test_revoke_case_insensitive(self):
        self.assertEqual(
            cli_prompt.redact_for_history("/HEADSCALE_REVOKE SECRET"), "/headscale_revoke")

    def test_non_secret_command_kept_with_args(self):
        self.assertEqual(
            cli_prompt.redact_for_history("/headscale_gen alice 720h"),
            "/headscale_gen alice 720h",
        )

    def test_blank_skipped(self):
        self.assertIsNone(cli_prompt.redact_for_history("   "))


@unittest.skipUnless(cli_prompt.HAVE_PTK, "prompt_toolkit не установлен")
class CommandCompleterTests(unittest.TestCase):
    def _complete(self, text, commands, arg_hints=None):
        from prompt_toolkit.document import Document

        comp = cli_prompt._CommandCompleter(commands, arg_hints or {})
        doc = Document(text, len(text))
        return [c.text for c in comp.get_completions(doc, None)]

    def test_slash_prefix_completes_commands(self):
        cmds = ["/ver", "/headscale_status", "/headscale_gen", "/help"]
        out = self._complete("/head", cmds)
        self.assertIn("/headscale_status", out)
        self.assertIn("/headscale_gen", out)
        self.assertNotIn("/ver", out)

    def test_exact_command_still_offered(self):
        self.assertEqual(self._complete("/ver", ["/ver", "/help"]), ["/ver"])

    def test_arg_hints_after_space(self):
        out = self._complete("/headscale_gen 7", ["/headscale_gen"],
                             {"/headscale_gen": ["24h", "720h", "7d"]})
        self.assertEqual(sorted(out), ["720h", "7d"])

    def test_arg_hints_empty_fragment_shows_all(self):
        out = self._complete("/headscale_gen ", ["/headscale_gen"],
                             {"/headscale_gen": ["24h", "720h", "7d"]})
        self.assertEqual(sorted(out), ["24h", "720h", "7d"])


class AdminCliRoutingTests(unittest.TestCase):
    def test_status_routes_to_manager(self):
        fake = {
            "enabled": True, "server_url": "https://hs.example.com",
            "container_name": "headscale", "container_running": True,
            "node_count": 2, "user_count": 1,
            "headplane": {"container_running": False, "browser_url": "", "tunnel_hint": ""},
        }
        with patch.object(headscale_manager, "get_status", return_value=fake):
            ok, text = AdminCLI().execute("/headscale_status")
        self.assertTrue(ok)
        self.assertIn("hs.example.com", text)

    def test_list_nodes_routes_to_manager(self):
        with patch.object(headscale_manager, "list_nodes",
                          return_value=(True, "ok", [])):
            ok, text = AdminCLI().execute("/headscale_list_nodes")
        self.assertTrue(ok)

    def test_gen_passes_parsed_args(self):
        with patch.object(headscale_manager, "create_preauth_key",
                          return_value=(True, "ok", "KEY")) as gen, \
             patch.object(headscale_manager, "export_client_instructions",
                          return_value="instructions"):
            ok, text = AdminCLI().execute("/headscale_gen", ["alice", "720h"])
        self.assertTrue(ok)
        gen.assert_called_once_with(user="alice", expiration="720h")

    def test_revoke_without_args_lists_keys(self):
        with patch.object(headscale_manager, "list_preauth_keys",
                          return_value=(True, "ok", [])) as lst:
            ok, text = AdminCLI().execute("/headscale_revoke")
        self.assertTrue(ok)
        lst.assert_called_once()


if __name__ == "__main__":
    unittest.main()
