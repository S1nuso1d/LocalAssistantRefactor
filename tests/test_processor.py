"""Behavioural tests for MessageProcessor.

Needs PySide6 (signals); skipped automatically where it is not installed, so
the Qt-free CI job stays green. Run locally with the project's venv:
    python -m unittest tests.test_processor -v
"""
import tempfile
import unittest
from pathlib import Path

try:
    from assistant.processor import MessageProcessor
    HAVE_QT = True
except Exception:  # PySide6 / audio stack not importable here
    HAVE_QT = False

from assistant.conversation_store import ConversationStore
from assistant.ollama_client import OllamaError
from assistant.settings_store import SettingsStore


class FakeClient:
    def __init__(self):
        self.mode = "answer"  # answer | reject | error

    def should_answer(self, text, context=None):
        if self.mode == "error":
            raise OllamaError("down")
        return self.mode == "answer", "тест"

    def stream_chat(self, messages, cancel_event, model=None):
        yield "Ответ"

    def cancel_active_request(self):
        pass


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = Path(self._dir.name)
        self.p = MessageProcessor(
            SettingsStore(root / "s.json"), ConversationStore(root / "h.sqlite3")
        )
        self.p._client = FakeClient()
        self.decisions, self.finished, self.errors = [], [], []
        self.p.phrase_decision.connect(lambda *a: self.decisions.append(a))
        self.p.answer_finished.connect(lambda *a: self.finished.append(a))
        self.p.error_message.connect(lambda *a: self.errors.append(a))

    def test_accepted_voice_phrase_is_answered_and_logged(self):
        self.p._run_message("Как работает цикл?", False)
        self.assertEqual(self.decisions[-1], ("Как работает цикл?", True, "тест"))
        self.assertEqual(self.finished[-1], ("Ответ", True))

    def test_rejected_phrase_gets_no_answer(self):
        self.p._client.mode = "reject"
        self.p._run_message("Просто болтовня людей", False)
        self.assertEqual(self.decisions[-1][1:], (False, "тест"))
        self.assertEqual(self.finished, [])

    def test_duplicate_is_skipped(self):
        self.p._client.mode = "reject"
        self.p._run_message("Просто болтовня людей", False)
        self.p._run_message("Просто болтовня людей", False)
        self.assertEqual(self.decisions[-1][2], "повтор недавней фразы")

    def test_classifier_failure_is_logged_not_shown_as_chat_error(self):
        self.p._client.mode = "error"
        self.p._run_message("Совсем другая фраза номер два", False)
        self.assertIn("классификатор недоступен", self.decisions[-1][2])
        self.assertEqual(self.errors, [])

    def test_typed_message_bypasses_filter(self):
        self.p._client.mode = "reject"
        self.p._run_message("напечатано", True)
        self.assertEqual(self.decisions, [])
        self.assertEqual(self.finished[-1], ("Ответ", False))

    def test_voice_queue_overflow_and_stop(self):
        for text in ("a1", "b2", "c3"):
            self.p.enqueue_text(text, False)
        self.assertEqual([d[0] for d in self.decisions], ["a1"])
        self.assertEqual(list(self.p._voice_texts), ["b2", "c3"])
        self.decisions.clear()
        self.p.cancel_current()
        self.assertEqual([d[0] for d in self.decisions], ["b2", "c3"])
        self.assertFalse(self.p._voice_texts)


    def test_long_history_is_trimmed_by_token_budget(self):
        self.p.settings.update({"num_ctx": 2048, "max_history_messages": 100})
        conv = self.p._conversation_id
        for i in range(12):
            self.p.store.add_message(conv, "user", f"вопрос {i} " + "я" * 900)
            self.p.store.add_message(conv, "assistant", f"ответ {i} " + "ю" * 900)
        self.p.store.add_message(conv, "user", "последний вопрос")
        context = self.p._make_context()
        self.assertEqual(context[0]["role"], "system")
        self.assertEqual(context[-1]["content"], "последний вопрос")
        self.assertEqual(context[1]["role"], "user")
        self.assertGreater(self.p._trimmed, 10)
        self.assertLess(self.p._prompt_estimate, 2048)

    def test_status_reports_context_after_answer(self):
        statuses = []
        self.p.status_changed.connect(statuses.append)
        self.p._run_message("Как работает цикл?", True)
        self.assertTrue(any(s.startswith("Ответ готов") and "контекст" in s for s in statuses))


    def _wait(self, cond, timeout=2.0):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_startup_warms_up_model_when_ollama_ready(self):
        statuses = []
        self.p.status_changed.connect(statuses.append)
        client = self.p._client
        client.check_ready = lambda: (True, "Ollama готова")
        warmed = []
        client.warm_up = lambda: warmed.append(1)
        self.p._startup_check()
        self.assertTrue(self._wait(lambda: warmed))
        self.assertTrue(self._wait(lambda: "Модель загружена и готова" in statuses))

    def test_no_warm_up_when_ollama_down(self):
        client = self.p._client
        client.check_ready = lambda: (False, "Ollama не отвечает.")
        warmed = []
        client.warm_up = lambda: warmed.append(1)
        self.p._startup_check()
        self.assertEqual(self.errors[-1], ("Ollama не отвечает.",))
        self.assertEqual(warmed, [])


if __name__ == "__main__":
    unittest.main()
