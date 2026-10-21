"""Regression cases for natural Russian programming requests."""
import unittest

from assistant.coding_mode import detect_programming_intent


class ExtendedCodingIntentTests(unittest.TestCase):
    def test_short_python_exercise_request(self):
        self.assertEqual(
            detect_programming_intent("Дай задачку по Python про циклы"),
            "exercise",
        )

    def test_debug_request_without_word_error(self):
        self.assertEqual(
            detect_programming_intent("Посмотри этот код, почему он не работает"),
            "debug",
        )

    def test_explain_request(self):
        self.assertEqual(
            detect_programming_intent("Объясни, что означает эта строка"),
            "explain",
        )

    def test_continue_request(self):
        self.assertEqual(
            detect_programming_intent("Продолжи предыдущий код"),
            "continue",
        )

    def test_code_example_request(self):
        self.assertEqual(
            detect_programming_intent("Покажи код на Python для чтения файла"),
            "write_code",
        )


if __name__ == "__main__":
    unittest.main()
