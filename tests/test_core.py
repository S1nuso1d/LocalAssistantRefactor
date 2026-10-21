import json
import tempfile
import threading
import unittest
from pathlib import Path

from assistant.conversation_store import ConversationStore
from assistant.settings_store import SettingsStore
from assistant.speech_filter import DuplicateFilter, classify_by_rules
from assistant.ollama_client import OllamaClient


class CoreTests(unittest.TestCase):
    def test_settings_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.update({"answer_model": "gemma:2b", "background_alpha": 210})
            reloaded = SettingsStore(path)
            self.assertEqual(reloaded.get("answer_model"), "gemma:2b")
            self.assertEqual(reloaded.get("background_alpha"), 210)

    def test_conversation_persist_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "history.sqlite3")
            conversation_id = store.create_conversation()
            store.add_message(conversation_id, "user", "Объясни цикл Python")
            store.add_message(conversation_id, "assistant", "Цикл повторяет блок кода.", model="gemma:2b", duration_ms=750)
            messages = store.get_messages(conversation_id)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1]["model"], "gemma:2b")
            self.assertEqual(len(store.list_conversations("цикл")), 1)
            self.assertEqual(len(store.context_messages(conversation_id, 2)), 2)
            store.set_pinned(conversation_id, True)
            self.assertEqual(store.list_conversations()[0]["is_pinned"], 1)
            store.add_message(conversation_id, "user", "Старый вопрос")
            store.add_message(conversation_id, "assistant", "Старый ответ")
            self.assertTrue(store.edit_last_user_message(conversation_id, "Исправленный вопрос"))
            messages = store.get_messages(conversation_id)
            self.assertEqual(messages[-1]["role"], "user")
            self.assertEqual(messages[-1]["content"], "Исправленный вопрос")

    def test_rules_and_duplicate_filter(self):
        self.assertEqual(classify_by_rules("Объясни, что делает цикл"), "accept")
        self.assertEqual(classify_by_rules("ага"), "reject")
        self.assertEqual(classify_by_rules("Вчера смотрел новый фильм"), "ambiguous")
        duplicates = DuplicateFilter()
        self.assertFalse(duplicates.is_duplicate("Объясни список Python"))
        self.assertTrue(duplicates.is_duplicate("Объясни список Python"))

    def test_ollama_stream_parses_ndjson_chunks(self):
        class FakeResponse:
            def __init__(self):
                self.closed = False
            def raise_for_status(self):
                return None
            def iter_lines(self, decode_unicode=True):
                yield json.dumps({"message": {"content": "Привет"}, "done": False}).encode()
                yield json.dumps({"message": {"content": " мир!"}, "done": False}).encode()
                yield json.dumps({"message": {"content": ""}, "done": True}).encode()
            def close(self):
                self.closed = True

        class FakeSession:
            def __init__(self):
                self.response = FakeResponse()
                self.payload = None
            def post(self, url, json, stream, timeout):
                self.payload = json
                return self.response

        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(Path(directory) / "settings.json")
            client = OllamaClient(settings)
            client.session = FakeSession()
            pieces = list(client.stream_chat(
                [{"role": "user", "content": "Привет"}],
                threading.Event(),
                model="gemma:2b",
            ))
            self.assertEqual("".join(pieces), "Привет мир!")
            self.assertTrue(client.session.payload["stream"])
            self.assertEqual(client.session.payload["model"], "gemma:2b")
            self.assertTrue(client.session.response.closed)


if __name__ == "__main__":
    unittest.main()
