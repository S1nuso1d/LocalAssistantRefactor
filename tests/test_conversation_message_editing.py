"""Regression tests for editing an arbitrary user turn."""
import tempfile
import unittest
from pathlib import Path

from assistant.conversation_store import ConversationStore


class ConversationMessageEditingTests(unittest.TestCase):
    def test_editing_older_user_turn_removes_all_later_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "history.sqlite3")
            conversation_id = store.create_conversation("Основной диалог")
            first_id = store.add_message(conversation_id, "user", "Первый вопрос")
            store.add_message(conversation_id, "assistant", "Первый ответ")
            later_user_id = store.add_message(conversation_id, "user", "Второй вопрос")
            store.add_message(conversation_id, "assistant", "Второй ответ")

            self.assertTrue(
                store.edit_user_message(conversation_id, first_id, "Исправленный вопрос")
            )

            messages = store.get_messages(conversation_id)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["id"], first_id)
            self.assertEqual(messages[0]["content"], "Исправленный вопрос")
            self.assertNotIn(later_user_id, [item["id"] for item in messages])

    def test_editing_message_from_another_conversation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "history.sqlite3")
            first_conversation = store.create_conversation("Первый")
            second_conversation = store.create_conversation("Второй")
            foreign_id = store.add_message(first_conversation, "user", "Чужой вопрос")

            self.assertFalse(
                store.edit_user_message(second_conversation, foreign_id, "Подмена")
            )
            self.assertEqual(
                store.get_messages(first_conversation)[0]["content"], "Чужой вопрос"
            )

    def test_assistant_message_and_empty_text_cannot_be_edited(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "history.sqlite3")
            conversation_id = store.create_conversation()
            assistant_id = store.add_message(conversation_id, "assistant", "Ответ")
            user_id = store.add_message(conversation_id, "user", "Вопрос")

            self.assertFalse(
                store.edit_user_message(conversation_id, assistant_id, "Новый текст")
            )
            self.assertFalse(store.edit_user_message(conversation_id, user_id, "  "))
            self.assertEqual(
                [item["content"] for item in store.get_messages(conversation_id)],
                ["Ответ", "Вопрос"],
            )


if __name__ == "__main__":
    unittest.main()
