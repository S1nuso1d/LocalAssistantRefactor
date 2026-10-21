import tempfile
import unittest
from pathlib import Path

from assistant.coding_mode import build_coding_prompt, detect_programming_intent
from assistant.project_context import build_project_context, collect_project_files
from assistant.speech_output import plain_text_for_speech


class CodingModeTests(unittest.TestCase):
    def test_detects_common_programming_intents(self):
        self.assertEqual(detect_programming_intent("Напиши функцию сортировки на Python"), "write_code")
        self.assertEqual(detect_programming_intent("Почему возникает traceback и как исправить ошибку?"), "debug")
        self.assertEqual(detect_programming_intent("Дай задачку по циклам"), "exercise")
        self.assertEqual(detect_programming_intent("Объясни, что делает декоратор"), "explain")
        self.assertEqual(detect_programming_intent("Продолжи код с предыдущего ответа"), "continue")
        self.assertIsNone(detect_programming_intent("Привет, как дела?"))

    def test_general_mode_only_adds_prompt_for_programming_requests(self):
        self.assertEqual(build_coding_prompt("Привет", "general"), "")
        self.assertIn("Профиль", build_coding_prompt("Напиши код на Python", "general"))

    def test_project_context_skips_secret_and_build_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print('hello')", encoding="utf-8")
            (root / ".env").write_text("API_TOKEN=secret", encoding="utf-8")
            (root / "secrets.json").write_text('{"token":"secret"}', encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "dependency.js").write_text("unsafe()", encoding="utf-8")
            files = collect_project_files(root)
            self.assertEqual([name for name, _ in files], ["main.py"])
            context = build_project_context(root)
            self.assertIn("main.py", context)
            self.assertNotIn("API_TOKEN", context)
            self.assertNotIn("unsafe()", context)

    def test_project_context_limits_file_count_and_total_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(5):
                (root / f"file_{index}.py").write_text("x" * 500, encoding="utf-8")
            files = collect_project_files(root, max_files=2, max_file_chars=500, max_total_chars=3000)
            self.assertLessEqual(len(files), 2)
            self.assertTrue(all(len(content) <= 500 for _, content in files))

    def test_speech_text_skips_code(self):
        fence = chr(96) * 3
        text = plain_text_for_speech("Результат:\n" + fence + "python\nprint('test')\n" + fence + "\nГотово!")
        self.assertIn("Результат", text)
        self.assertIn("Блок кода пропущен", text)
        self.assertNotIn("print", text)


if __name__ == "__main__":
    unittest.main()
