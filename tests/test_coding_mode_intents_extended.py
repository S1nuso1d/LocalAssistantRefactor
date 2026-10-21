"""Regression cases for natural Russian programming requests."""
import unittest

from assistant.coding_mode import build_coding_prompt, detect_programming_intent


class ExtendedCodingIntentTests(unittest.TestCase):
    def test_short_python_exercise_request(self):
        self.assertEqual(
            detect_programming_intent("Дай задачку по Python про циклы"),
            "exercise",
        )

    def test_submitted_assignment_is_not_misclassified_as_exercise_request(self):
        task = (
            "У тебя есть список ростов человека в сантиметрах. "
            "Твоя задача: написать цикл, который посчитает средний рост, "
            "а потом выведет среднее значение."
        )
        self.assertEqual(detect_programming_intent(task), "solve_exercise")

    def test_submitted_assignment_gets_direct_solution_in_coding_mode(self):
        task = (
            "Твоя задача: написать цикл, который посчитает средний рост, "
            "а потом выведет среднее значение."
        )
        prompt = build_coding_prompt(task, "coding")
        self.assertIn("цельный рабочий код", prompt)
        self.assertIn("1–3 коротких предложениях", prompt)
        self.assertIn("Не пересказывай условие", prompt)

    def test_tutor_mode_keeps_hints_instead_of_full_solution(self):
        task = "Твоя задача: написать цикл, который посчитает средний рост."
        prompt = build_coding_prompt(task, "tutor")
        self.assertIn("одну конкретную подсказку", prompt)
        self.assertIn("Полное решение покажи", prompt)

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
