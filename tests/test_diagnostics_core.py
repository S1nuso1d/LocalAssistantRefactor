from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.diagnostics_core import (
    collect_diagnostics,
    display_endpoint,
    format_diagnostic_report,
    human_size,
    inspect_sqlite,
    probe_nvidia_gpu,
    probe_ollama,
)
from assistant.conversation_store import ConversationStore


class DiagnosticsCoreTests(unittest.TestCase):
    def test_display_endpoint_redacts_credentials_query_and_fragment(self):
        value = display_endpoint("https://alice:secret@example.test:11434/api/chat?token=abc#private")
        self.assertEqual(value, "https://example.test:11434")
        self.assertNotIn("alice", value)
        self.assertNotIn("secret", value)
        self.assertNotIn("abc", value)
        self.assertNotIn("tenant", value)

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 Б")
        self.assertEqual(human_size(1024), "1.0 КБ")

    def test_inspect_sqlite_is_read_only_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "existing.sqlite3"
            sqlite3.connect(db).close()
            result = inspect_sqlite(db)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(db.exists())
            missing = inspect_sqlite(Path(folder) / "missing.sqlite3")
            self.assertEqual(missing["status"], "not-created")
            self.assertFalse((Path(folder) / "missing.sqlite3").exists())

    def test_inspect_sqlite_reports_corrupt_database(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "bad.sqlite3"
            db.write_bytes(b"not a database")
            result = inspect_sqlite(db)
            self.assertEqual(result["status"], "error")

    def test_nvidia_probe_is_optional_and_never_raises(self):
        with patch("assistant.diagnostics_core.subprocess.run", side_effect=FileNotFoundError):
            result = probe_nvidia_gpu()
        self.assertEqual(result["status"], "info")
        self.assertIn("не найден", result["detail"])

    def test_probe_ollama_maps_configured_models(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def raise_for_status(self):
                return None
            def json(self):
                return {"models": [{"name": "qwen3-coder:30b"}, {"name": "nomic-embed-text:latest"}]}

        with patch("assistant.diagnostics_core.requests.get", return_value=Response()):
            result = probe_ollama({
                "ollama_url": "http://localhost:11434/api/chat",
                "answer_model": "qwen3-coder:30b",
                "classifier_model": "missing-model",
                "embedding_model": "nomic-embed-text",
            })
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["roles"]["ответы"], "установлена")
        self.assertEqual(result["roles"]["классификатор"], "не найдена")
        self.assertEqual(result["roles"]["embeddings"], "установлена")

    def test_report_excludes_conversation_and_log_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            conversation_db = root / "conversations.sqlite3"
            store = ConversationStore(conversation_db)
            conversation_id = store.create_conversation("Private title")
            store.add_message(conversation_id, "user", "Very private transcript phrase")
            knowledge_db = root / "knowledge.sqlite3"
            app_data = root / "data"
            app_data.mkdir()
            (app_data / "live_assistant.log").write_text(
                "Very private transcript phrase", encoding="utf-8"
            )
            with patch("assistant.diagnostics_core.probe_ollama", return_value={
                "status": "ok", "detail": "Доступна", "endpoint": "http://localhost:11434/api/tags",
                "models": ["qwen3-coder:30b"], "roles": {},
            }):
                report = collect_diagnostics(
                    settings={"answer_model": "qwen3-coder:30b", "system_audio_enabled": True},
                    audio_status={"system": "Подключён"}, listening=True, speaking=False,
                    conversation_db=conversation_db, knowledge_db=knowledge_db, app_data=app_data,
                )
            formatted = format_diagnostic_report(report, "test")
            self.assertNotIn("Very private transcript phrase", formatted)
            self.assertNotIn("Private title", formatted)
            self.assertIn("содержимое не читается", " ".join(row["detail"] for row in report["rows"]))


if __name__ == "__main__":
    unittest.main()
