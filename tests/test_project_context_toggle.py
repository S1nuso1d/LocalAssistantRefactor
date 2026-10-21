"""Tests for the explicit project-context setting."""
import tempfile
import unittest
from pathlib import Path

from assistant.conversation_store import ConversationStore
from assistant.processor import MessageProcessor
from assistant.settings_store import SettingsStore


class ProjectContextToggleTests(unittest.TestCase):
    def test_project_files_are_only_added_when_context_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "example.py").write_text(
                "def hello():\n    return 'project context marker'\n",
                encoding="utf-8",
            )

            settings = SettingsStore(root / "settings.json")
            settings.update({
                "project_root": str(project),
                "project_context_enabled": False,
            })
            store = ConversationStore(root / "history.sqlite3")
            processor = MessageProcessor(settings, store)

            disabled_context = processor._make_context()[0]["content"]
            self.assertNotIn("FILE: example.py", disabled_context)
            self.assertNotIn("project context marker", disabled_context)

            settings.update({"project_context_enabled": True})
            enabled_context = processor._make_context()[0]["content"]
            self.assertIn("FILE: example.py", enabled_context)
            self.assertIn("project context marker", enabled_context)


if __name__ == "__main__":
    unittest.main()
