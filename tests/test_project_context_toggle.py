"""Tests for explicit opt-in to include selected project files in model context."""
import tempfile
import unittest
from pathlib import Path

from assistant.project_context import build_project_context_if_enabled


class ProjectContextToggleTests(unittest.TestCase):
    def test_project_files_are_only_added_when_context_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / "example.py").write_text(
                "def hello():\n    return 'project context marker'\n",
                encoding="utf-8",
            )

            disabled_context = build_project_context_if_enabled(project, False)
            self.assertEqual(disabled_context, "")

            enabled_context = build_project_context_if_enabled(project, True)
            self.assertIn("FILE: example.py", enabled_context)
            self.assertIn("project context marker", enabled_context)

    def test_empty_or_missing_root_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(build_project_context_if_enabled("", True), "")
            self.assertEqual(
                build_project_context_if_enabled(Path(directory) / "missing", True),
                "",
            )


if __name__ == "__main__":
    unittest.main()
