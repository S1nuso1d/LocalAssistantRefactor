import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from assistant.conversation_store import ConversationStore, escape_like
from assistant.ollama_client import OllamaClient, OllamaError
from assistant.segmenter import PhraseSegmenter, segment_is_reliable
from assistant.settings_store import DEFAULT_SETTINGS, SettingsStore
from assistant.speech_filter import classify_by_rules, is_whisper_hallucination
from assistant.text_utils import (
    format_time, relative_day, safe_filename, split_thinking, strip_thinking,
)


class ThinkingTests(unittest.TestCase):
    def test_closed_block(self):
        thinking, answer, still = split_thinking("<think>hmm</think>\n\nОтвет")
        self.assertEqual((thinking, answer, still), ("hmm", "Ответ", False))

    def test_unclosed_block_while_streaming(self):
        thinking, answer, still = split_thinking("Привет <think>размышляю")
        self.assertEqual(answer, "Привет")
        self.assertTrue(still)
        self.assertEqual(thinking, "размышляю")

    def test_only_closing_tag(self):
        self.assertEqual(strip_thinking("рассуждение</think>итог"), "итог")

    def test_plain_text_untouched(self):
        self.assertEqual(split_thinking("просто текст"), ("", "просто текст", False))


class TextUtilTests(unittest.TestCase):
    def test_relative_day(self):
        now = datetime(2026, 11, 11, 15, 0)
        today = datetime(2026, 11, 11, 9, 5).timestamp()
        yesterday = datetime(2026, 11, 10, 22, 30).timestamp()
        self.assertEqual(relative_day(today, now), "Сегодня, 09:05")
        self.assertEqual(relative_day(yesterday, now), "Вчера, 22:30")
        self.assertTrue(relative_day(datetime(2026, 1, 3, 8, 0).timestamp(), now).startswith("03.01"))

    def test_format_time_and_filename(self):
        self.assertEqual(format_time(None), "")
        self.assertRegex(format_time(1_700_000_000), r"^\d\d:\d\d$")
        self.assertEqual(safe_filename('a/b:c*?"'), "a b c")
        self.assertEqual(safe_filename("///"), "dialog")


class SegmenterTests(unittest.TestCase):
    def chunk(self, level, n=800):
        return np.full(n, level, dtype=np.float32)

    def run_stream(self, levels, **kwargs):
        seg = PhraseSegmenter(0.5, 0.45, 12.0, kwargs.get("preroll", 1))
        phrases = []
        for level in levels:
            out = seg.feed(self.chunk(level), abs(level), 0.01, 0.9)
            if out is not None:
                phrases.append(out)
        return phrases

    def test_phrase_with_preroll(self):
        phrases = self.run_stream([0.0, 0.0, 0.1, 0.1, 0.0, 0.0])
        self.assertEqual(len(phrases), 1)
        # 1 preroll + 2 speech + 2 trailing silence chunks of 800 samples
        self.assertEqual(len(phrases[0]), 800 * 5)

    def test_no_preroll(self):
        phrases = self.run_stream([0.0, 0.1, 0.0, 0.0], preroll=0)
        self.assertEqual(len(phrases[0]), 800 * 3)

    def test_silence_only_yields_nothing(self):
        self.assertEqual(self.run_stream([0.0] * 20), [])

    def test_too_long_phrase_is_cut(self):
        phrases = self.run_stream([0.1] * 30)
        self.assertGreaterEqual(len(phrases), 1)

    def test_reliability(self):
        self.assertTrue(segment_is_reliable(0.1, -0.3, 1.2))
        self.assertFalse(segment_is_reliable(0.9, -1.4, 1.2))
        self.assertFalse(segment_is_reliable(0.1, -0.3, 3.0))
        self.assertFalse(segment_is_reliable(None, -2.5, None))
        self.assertTrue(segment_is_reliable(None, None, None))


class HallucinationTests(unittest.TestCase):
    def test_known_phrases(self):
        for text in (
            "Субтитры сделал DimaTorzok",
            "Редактор субтитров А.Семкин Корректор А.Егорова",
            "Спасибо за просмотр!",
            "[Музыка]",
        ):
            self.assertEqual(classify_by_rules(text), "reject", text)

    def test_real_requests_survive(self):
        self.assertFalse(is_whisper_hallucination("Объясни, как работает субтитр в плеере"))
        self.assertEqual(classify_by_rules("Объясни, что такое цикл"), "accept")


class SettingsValidationTests(unittest.TestCase):
    def test_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text(
                '{"num_ctx": "abc", "temperature": 99, "background_alpha": "300",'
                ' "compact_mode": "yes", "whisper_device": "tpu",'
                ' "assistant_names": "oops", "window_geometry": [1, 2]}',
                encoding="utf-8",
            )
            s = SettingsStore(path)
            self.assertEqual(s.get("num_ctx"), DEFAULT_SETTINGS["num_ctx"])
            self.assertEqual(s.get("temperature"), 2.0)
            self.assertEqual(s.get("background_alpha"), 250)
            self.assertEqual(s.get("compact_mode"), False)
            self.assertEqual(s.get("whisper_device"), "cpu")
            self.assertEqual(s.get("assistant_names"), DEFAULT_SETTINGS["assistant_names"])
            self.assertIsNone(s.get("window_geometry"))

    def test_corrupt_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            s = SettingsStore(path)
            self.assertEqual(s.get("answer_model"), DEFAULT_SETTINGS["answer_model"])
            self.assertTrue((Path(d) / "settings.json.bad").exists())

    def test_update_coerces_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            s = SettingsStore(Path(d) / "s.json")
            s.update({"num_ctx": "4096", "window_geometry": [10, 20, 500, 600]})
            self.assertEqual(s.get("num_ctx"), 4096)
            self.assertEqual(s.get("window_geometry"), [10, 20, 500, 600])
            s.reset()
            self.assertEqual(s.get("num_ctx"), 8192)
            s.get("assistant_names").append("x")  # must not mutate defaults
            self.assertNotIn("x", DEFAULT_SETTINGS["assistant_names"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self._dir.name) / "h.sqlite3")

    def tearDown(self):
        self._dir.cleanup()

    def test_search_treats_wildcards_literally(self):
        a = self.store.create_conversation("Скидка 50%")
        b = self.store.create_conversation("Другое")
        self.store.add_message(b, "user", "snake_case и обычный текст")
        self.assertEqual([c["id"] for c in self.store.list_conversations("50%")], [a])
        self.assertEqual(len(self.store.list_conversations("%")), 1)
        self.assertEqual([c["id"] for c in self.store.list_conversations("snake_case")], [b])
        self.assertEqual(self.store.list_conversations("snakeXcase"), [])
        self.assertEqual(escape_like("a%b_c\\"), "a\\%b\\_c\\\\")

    def test_pop_last_assistant_and_export(self):
        c = self.store.create_conversation("Тест")
        self.store.add_message(c, "user", "Привет")
        self.store.add_message(c, "assistant", "Здравствуй", model="m", duration_ms=5)
        text = self.store.export_markdown(c)
        self.assertIn("# Тест", text)
        self.assertIn("Здравствуй", text)
        popped = self.store.pop_last_assistant_message(c)
        self.assertEqual(popped["content"], "Здравствуй")
        self.assertEqual(popped["model"], "m")
        self.assertEqual(len(self.store.get_messages(c)), 1)
        self.assertIsNone(self.store.pop_last_assistant_message(c))


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} Error")

    def iter_lines(self, decode_unicode=True):
        return iter(())

    def close(self):
        self.closed = True


class OllamaClientTests(unittest.TestCase):
    def make_client(self, session):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        client = OllamaClient(SettingsStore(Path(d.name) / "s.json"))
        client.session = session
        return client

    def test_tags_url(self):
        self.assertEqual(
            OllamaClient.tags_url("http://localhost:11434/api/chat/"),
            "http://localhost:11434/api/tags",
        )

    def test_check_ready_reports_missing_model(self):
        class S:
            def get(self, url, timeout):
                return FakeResp(payload={"models": [{"name": "llama3:latest"}]})
        ok, msg = self.make_client(S()).check_ready()
        self.assertFalse(ok)
        self.assertIn("qwen3-coder:30b", msg)

    def test_check_ready_ok_with_implicit_latest(self):
        class S:
            def get(self, url, timeout):
                return FakeResp(payload={"models": [{"name": "qwen3-coder:30b"}]})
        self.assertTrue(self.make_client(S()).check_ready()[0])

    def test_check_ready_offline(self):
        import requests

        class S:
            def get(self, url, timeout):
                raise requests.ConnectionError()
        ok, msg = self.make_client(S()).check_ready()
        self.assertFalse(ok)
        self.assertIn("Ollama", msg)

    def test_404_gives_pull_hint_and_options_sent(self):
        seen = {}

        class S:
            def post(self, url, json, stream, timeout):
                seen.update(json)
                return FakeResp(404, payload={"error": "model 'x' not found"})
        client = self.make_client(S())
        with self.assertRaises(OllamaError) as ctx:
            list(client.stream_chat([{"role": "user", "content": "hi"}], threading.Event(), model="x"))
        self.assertIn("ollama pull x", str(ctx.exception))
        self.assertEqual(seen["options"]["num_ctx"], 8192)
        self.assertEqual(seen["keep_alive"], "10m")


if __name__ == "__main__":
    unittest.main()
