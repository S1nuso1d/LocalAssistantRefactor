"""Tests for the single application-version source."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant.version import UNKNOWN_VERSION, read_app_version


class ApplicationVersionTests(unittest.TestCase):
    def test_reads_version_from_project_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "VERSION").write_text("2026.11.24-r23.4\n", encoding="utf-8")
            self.assertEqual(read_app_version(root), "2026.11.24-r23.4")

    def test_strips_utf8_bom_and_surrounding_whitespace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "VERSION").write_text(
                "\ufeff 2026.11.24-r23.4 \n", encoding="utf-8"
            )
            self.assertEqual(read_app_version(root), "2026.11.24-r23.4")

    def test_missing_or_empty_version_uses_safe_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(read_app_version(root), UNKNOWN_VERSION)
            (root / "VERSION").write_text("  \n", encoding="utf-8")
            self.assertEqual(read_app_version(root), UNKNOWN_VERSION)


if __name__ == "__main__":
    unittest.main()
