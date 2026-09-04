import unittest

import hysteria2_manager as hy2


class Hysteria2VersionParseTests(unittest.TestCase):
    def test_parses_banner_with_version_line(self):
        raw = """░█░█░█░█░█▀▀░▀█▀░█▀▀░█▀▄░▀█▀░█▀█░░░▀▀▄
░█▀█░░█░░▀▀█░░█░░█▀▀░█▀▄░░█░░█▀█░░░▄▀░
░▀░▀░░▀░░▀▀▀░░▀░░▀▀▀░▀░▀░▀▀▀░▀░▀░░░▀▀▀

a powerful, lightning fast and censorship resistant proxy

Version:\tv2.11.0
BuildDate:\t2026-08-01T03:00:03Z
"""
        self.assertEqual(hy2._parse_hysteria_version(raw), "2.11.0")

    def test_strips_ansi(self):
        raw = "\x1b[32mVersion: 2.6.1\x1b[0m\n"
        self.assertEqual(hy2._parse_hysteria_version(raw), "2.6.1")

    def test_simple_version_line(self):
        self.assertEqual(
            hy2._parse_hysteria_version("hysteria version 2.6.0\n"),
            "2.6.0",
        )


if __name__ == "__main__":
    unittest.main()
