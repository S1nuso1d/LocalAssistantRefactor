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
    extract_code_blocks, format_time, relative_day, safe_filename,
    split_thinking, strip_thinking,
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


class FilterModeTests(unittest.TestCase):
    def test_balanced_defers_unmarked_questions(self):
        # Typical video line: starts like a question but has no "?" or imperative.
        self.assertEqual(classify_by_rules("Как он вообще туда попал вчера вечером"), "ambiguous")
        self.assertEqual(classify_by_rules("Как он вообще туда попал вчера вечером", mode="fast"), "accept")

    def test_balanced_still_accepts_clear_requests(self):
        self.assertEqual(classify_by_rules("Как работает цикл for?"), "accept")
        self.assertEqual(classify_by_rules("Объясни разницу между списком и кортежем"), "accept")
        self.assertEqual(classify_by_rules("Помощник, который час"), "accept")

    def test_substantive_chatter_goes_to_classifier_in_balanced(self):
        text = "Вчера мы долго гуляли по набережной и обсуждали планы на лето"
        self.assertEqual(classify_by_rules(text), "ambiguous")

    def test_hallucination_rejected_in_both_modes(self):
        for mode in ("fast", "balanced"):
            self.assertEqual(classify_by_rules("Спасибо за просмотр", mode=mode), "reject")


class ClassifierContextTests(unittest.TestCase):
    def test_format_dialogue_context(self):
        text = OllamaClient.format_dialogue_context([
            {"role": "user", "content": "Что такое цикл?"},
            {"role": "assistant", "content": "<think>x</think>Это повторение действий."},
            {"role": "user", "content": "лишнее старое"},
        ])
        self.assertNotIn("Что такое цикл", text)  # only the last two turns
        self.assertIn("Ассистент: Это повторение действий.", text)
        self.assertNotIn("think", text)
        self.assertEqual(OllamaClient.format_dialogue_context(None), "")

    def test_context_reaches_prompt(self):
        captured = {}

        class S:
            def post(self, url, json, timeout, stream=False):
                captured["prompt"] = json["messages"][-1]["content"]
                return FakeResp(payload={"message": {"content": '{"is_request": true}'}})
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        client = OllamaClient(SettingsStore(Path(d.name) / "s.json"))
        client.session = S()
        ok = client.classify_ambiguous("а подробнее", [{"role": "assistant", "content": "Цикл — это..."}])
        self.assertTrue(ok)
        self.assertIn("Цикл — это", captured["prompt"])
        self.assertIn("продолжение", captured["prompt"])


class QueueAndStreamTests(unittest.TestCase):
    def test_put_drop_oldest(self):
        import queue
        from assistant.segmenter import put_drop_oldest
        q = queue.Queue(maxsize=2)
        self.assertFalse(put_drop_oldest(q, 1))
        self.assertFalse(put_drop_oldest(q, 2))
        self.assertTrue(put_drop_oldest(q, 3))
        self.assertEqual([q.get(), q.get()], [2, 3])

    def test_stream_interval_grows_with_text(self):
        from assistant.text_utils import stream_interval_ms
        values = [stream_interval_ms(n) for n in (0, 2000, 5000, 20000)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 45)


class CodeBlockTests(unittest.TestCase):
    def test_extracts_closed_blocks_with_language(self):
        md = "Вот код:\n\n```python\nprint(1)\nprint(2)\n```\n\nи ещё\n\n~~~\nls -la\n~~~\n"
        self.assertEqual(
            extract_code_blocks(md),
            [("python", "print(1)\nprint(2)"), ("", "ls -la")],
        )

    def test_unfinished_fence_is_ignored_while_streaming(self):
        self.assertEqual(extract_code_blocks("текст\n```js\nconsole.log(1)"), [])

    def test_longer_fence_can_contain_shorter_one(self):
        md = "````md\n```\ninner\n```\n````"
        self.assertEqual(extract_code_blocks(md), [("md", "```\ninner\n```")])

    def test_inline_backticks_are_not_blocks(self):
        self.assertEqual(extract_code_blocks("используй `print()` и ```inline``` тут"), [])

    def test_language_with_symbols(self):
        self.assertEqual(extract_code_blocks("```c++\nint x;\n```")[0][0], "c++")


class SpeechCleanTests(unittest.TestCase):
    def test_markdown_is_made_speakable(self):
        from assistant.tts import clean_for_speech
        md = (
            "# Заголовок\n\n**Важно:** смотри [документацию](https://x.dev/a) "
            "и `print()`.\n\n- первый пункт\n- второй пункт\n\n"
            "```python\nprint(1)\n```\n\nГотово 🙂"
        )
        out = clean_for_speech(md)
        for bad in ("#", "**", "https", "```", "print(1)", "🙂", "]("):
            self.assertNotIn(bad, out)
        self.assertIn("документацию", out)
        self.assertIn("Пример кода показан на экране", out)
        self.assertIn("первый пункт. второй пункт", out)

    def test_thinking_and_empty(self):
        from assistant.tts import clean_for_speech
        self.assertEqual(clean_for_speech("<think>секрет</think>Привет"), "Привет")
        self.assertEqual(clean_for_speech("<think>только мысли"), "")
        self.assertEqual(clean_for_speech("```\nкод\n```").strip(), "Пример кода показан на экране.")

    def test_unclosed_code_fence_does_not_leak_code(self):
        from assistant.tts import clean_for_speech
        out = clean_for_speech("Вот пример:\n```js\nlet secret = 1")
        self.assertNotIn("secret", out)

    def test_long_text_is_cut_at_sentence(self):
        from assistant.tts import clean_for_speech
        text = " ".join(f"Предложение номер {i}." for i in range(400))
        out = clean_for_speech(text, limit=300)
        self.assertLessEqual(len(out), 300)
        self.assertTrue(out.endswith("."))

    def test_script_clamps_and_escapes(self):
        from assistant.tts import build_powershell_script
        script = build_powershell_script(99, -5, "O'Brien", "ru")
        self.assertIn("$s.Rate=10", script)
        self.assertIn("$s.Volume=0", script)
        self.assertIn("SelectVoice('O''Brien')", script)
        self.assertNotIn("-like", script)  # explicit voice wins over culture
        auto = build_powershell_script(0, 100, "", "ru")
        self.assertIn("-like 'ru*'", auto)
        # A hostile "culture" must never reach the script.
        evil = build_powershell_script(0, 100, "", "ru'; calc; '")
        self.assertNotIn("calc", evil)


class FakeProc:
    def __init__(self, release):
        self.release = release
        self.killed = False
        self.stdin_data = None

    def communicate(self, data, timeout=None):
        self.stdin_data = data
        self.release.wait(2)
        return b"", b""

    def kill(self):
        self.killed = True
        self.release.set()


class SpeechEngineTests(unittest.TestCase):
    def make(self, platform="win32"):
        import threading as th
        from assistant.tts import SpeechEngine
        states, procs = [], []

        def popen(cmd, **kwargs):
            proc = FakeProc(th.Event())
            proc.cmd = cmd
            procs.append(proc)
            return proc

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        settings = SettingsStore(Path(d.name) / "s.json")
        engine = SpeechEngine(settings, on_state=states.append, popen=popen, platform=platform)
        return engine, states, procs

    def wait_for(self, cond, timeout=2.0):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_unavailable_off_windows(self):
        engine, states, procs = self.make("linux")
        self.assertFalse(engine.available)
        self.assertFalse(engine.speak("Привет"))
        self.assertEqual(procs, [])

    def test_speak_then_finish(self):
        engine, states, procs = self.make()
        self.assertTrue(engine.speak("Привет, мир"))
        self.assertTrue(self.wait_for(lambda: procs and engine.speaking))
        self.assertEqual(procs[0].stdin_data.decode("utf-8"), "Привет, мир")
        self.assertEqual(procs[0].cmd[0], "powershell.exe")
        procs[0].release.set()  # utterance ends naturally
        self.assertTrue(self.wait_for(lambda: not engine.speaking))
        self.assertEqual(states, [True, False])

    def test_stop_kills_process(self):
        engine, states, procs = self.make()
        engine.speak("Длинный ответ")
        self.assertTrue(self.wait_for(lambda: engine.speaking))
        engine.stop()
        self.assertTrue(procs[0].killed)
        self.assertFalse(engine.speaking)
        self.assertEqual(states, [True, False])

    def test_new_utterance_replaces_current_without_flicker(self):
        engine, states, procs = self.make()
        engine.speak("Первый")
        self.assertTrue(self.wait_for(lambda: engine.speaking))
        engine.speak("Второй")
        self.assertTrue(self.wait_for(lambda: len(procs) == 2))
        self.assertTrue(procs[0].killed)
        self.assertTrue(self.wait_for(lambda: procs[1].stdin_data is not None))
        self.assertEqual(procs[1].stdin_data.decode("utf-8"), "Второй")
        self.assertTrue(engine.speaking)
        self.assertEqual(states, [True])  # never dropped to False in between
        engine.stop()
        self.assertEqual(states, [True, False])

    def test_nothing_speakable_is_ignored(self):
        engine, states, procs = self.make()
        self.assertFalse(engine.speak("<think>only thoughts</think>"))
        self.assertEqual(procs, [])

    def test_settings_validation(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        s = SettingsStore(Path(d.name) / "s.json")
        s.update({"tts_mode": "loud", "tts_rate": 50, "tts_volume": -3})
        self.assertEqual(s.get("tts_mode"), "off")
        self.assertEqual(s.get("tts_rate"), 10)
        self.assertEqual(s.get("tts_volume"), 0)


class PhraseLogTests(unittest.TestCase):
    def test_newest_first_stats_and_cap(self):
        from assistant.phrase_log import PhraseLog
        log = PhraseLog(maxlen=3)
        for i in range(5):
            log.add(f" фраза {i} ", i % 2 == 0, "причина", timestamp=100 + i)
        items = log.items()
        self.assertEqual([d.text for d in items], ["фраза 4", "фраза 3", "фраза 2"])
        self.assertEqual(log.stats(), (2, 1))
        log.clear()
        self.assertEqual(log.items(), [])
        self.assertEqual(log.stats(), (0, 0))


class ContextBudgetTests(unittest.TestCase):
    @staticmethod
    def msgs(*pairs):
        return [{"role": r, "content": c} for r, c in pairs]

    def test_keeps_newest_within_budget(self):
        from assistant.text_utils import estimate_tokens, trim_to_budget
        history = self.msgs(
            ("user", "а" * 300), ("assistant", "б" * 300),
            ("user", "в" * 300), ("assistant", "г" * 300), ("user", "д" * 30),
        )
        budget = estimate_tokens("а" * 300) * 2 + estimate_tokens("д" * 30)
        kept, dropped = trim_to_budget(history, budget)
        self.assertEqual(kept[-1]["content"], "д" * 30)
        self.assertEqual(kept[0]["role"], "user")  # no dangling assistant start
        self.assertEqual(dropped, len(history) - len(kept))
        self.assertGreater(dropped, 0)

    def test_newest_message_always_kept_even_if_huge(self):
        from assistant.text_utils import trim_to_budget
        kept, dropped = trim_to_budget(self.msgs(("user", "x" * 50000)), 100)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 0)

    def test_everything_fits(self):
        from assistant.text_utils import trim_to_budget
        history = self.msgs(("user", "привет"), ("assistant", "здравствуй"))
        self.assertEqual(trim_to_budget(history, 10000), (history, 0))

    def test_status_line(self):
        from assistant.text_utils import format_answer_status, format_tokens
        usage = {"prompt_eval_count": 800, "eval_count": 200, "eval_duration": 4_000_000_000}
        line = format_answer_status("m", 5.0, usage, 8192, 1500, 2)
        self.assertIn("50 ток/с", line)
        self.assertIn("контекст ≈1.5k/8.2k", line)  # estimate wins over cached count
        self.assertIn("не вошло: 2", line)
        self.assertEqual(format_answer_status("m", 1.0, None, 8192), "Ответ готов · m · 1.0 с")
        self.assertEqual(format_tokens(999), "999")

    def test_overflow_detection(self):
        from assistant.text_utils import context_is_overflowing
        self.assertTrue(context_is_overflowing({"prompt_eval_count": 8000}, 8192))
        self.assertFalse(context_is_overflowing({"prompt_eval_count": 3000}, 8192))
        self.assertFalse(context_is_overflowing(None, 8192))

    def test_client_captures_usage_from_final_chunk(self):
        import json as _json
        lines = [
            _json.dumps({"message": {"content": "При"}, "done": False}),
            _json.dumps({"message": {"content": "вет"}, "done": True,
                         "prompt_eval_count": 42, "eval_count": 7, "eval_duration": 700_000_000}),
        ]

        class Resp(FakeResp):
            def iter_lines(self, decode_unicode=True):
                return iter(lines)

        class S:
            def post(self, url, json, stream, timeout):
                return Resp(200)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        client = OllamaClient(SettingsStore(Path(d.name) / "s.json"))
        client.session = S()
        self.assertIsNone(client.last_usage)
        text = "".join(client.stream_chat([{"role": "user", "content": "x"}], threading.Event()))
        self.assertEqual(text, "Привет")
        self.assertEqual(client.last_usage, {
            "prompt_eval_count": 42, "eval_count": 7, "eval_duration": 700_000_000})


class WarmUpTests(unittest.TestCase):
    def make_client(self, session):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        client = OllamaClient(SettingsStore(Path(d.name) / "s.json"))
        client.session = session
        return client

    def test_generate_url(self):
        self.assertEqual(
            OllamaClient.generate_url("http://localhost:11434/api/chat"),
            "http://localhost:11434/api/generate",
        )

    def test_warm_up_sends_empty_prompt_without_streaming(self):
        seen = {}

        class S:
            def post(self, url, json, timeout):
                seen.update(url=url, **json)
                return FakeResp(200, payload={"done": True})
        self.make_client(S()).warm_up()
        self.assertTrue(seen["url"].endswith("/api/generate"))
        self.assertEqual(seen["prompt"], "")
        self.assertFalse(seen["stream"])
        self.assertEqual(seen["model"], "qwen3-coder:30b")
        self.assertEqual(seen["keep_alive"], "10m")

    def test_warm_up_missing_model_gives_pull_hint(self):
        class S:
            def post(self, url, json, timeout):
                return FakeResp(404, payload={"error": "model not found"})
        with self.assertRaises(OllamaError) as ctx:
            self.make_client(S()).warm_up()
        self.assertIn("ollama pull", str(ctx.exception))

    def test_warm_up_connection_error(self):
        import requests

        class S:
            def post(self, url, json, timeout):
                raise requests.ConnectionError("refused")
        with self.assertRaises(OllamaError):
            self.make_client(S()).warm_up()

    def test_setting_is_boolean_validated(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        s = SettingsStore(Path(d.name) / "s.json")
        self.assertTrue(s.get("warm_up_model"))
        s.update({"warm_up_model": "false"})
        self.assertFalse(s.get("warm_up_model"))
        s.update({"warm_up_model": 5})
        self.assertTrue(s.get("warm_up_model"))  # junk falls back to default


class PrivacyTests(unittest.TestCase):
    def test_text_is_hidden_unless_opted_in(self):
        from assistant.privacy import describe_text
        secret = "номер договора 12345"
        self.assertNotIn("12345", describe_text(secret, False))
        self.assertEqual(describe_text(secret, False), f"<{len(secret)} симв.>")
        self.assertEqual(describe_text(secret, True), secret)

    def test_default_is_off_and_validated(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        s = SettingsStore(Path(d.name) / "s.json")
        self.assertFalse(s.get("log_transcripts"))
        s.update({"log_transcripts": "true"})
        self.assertTrue(s.get("log_transcripts"))

    def test_no_log_call_formats_raw_speech_without_the_helper(self):
        """Guard: speech-bearing log calls must go through describe_text."""
        root = Path(__file__).resolve().parent.parent / "assistant"
        for name, needles in {
            "audio_worker.py": ("Распознано", "Ненадёжный сегмент"),
            "processor.py": ("Повтор голосовой фразы", "Решение фильтра"),
        }.items():
            source = (root / name).read_text(encoding="utf-8")
            for needle in needles:
                position = source.index(needle)
                call_start = source.rfind("logger.", 0, position)
                call = source[call_start:position + 220]
                self.assertIn("describe_text", call, needle)


class FlexibleSettingsTests(unittest.TestCase):
    def store(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return SettingsStore(Path(d.name) / "s.json")

    def test_language_accepts_any_code_and_auto(self):
        s = self.store()
        for code in ("de", "zh", "AUTO", " ja "):
            s.update({"language": code})
            self.assertEqual(s.get("language"), code.strip().lower())
        s.update({"language": "русский"})
        self.assertEqual(s.get("language"), "ru")  # junk -> default
        s.update({"language": "x1"})
        self.assertEqual(s.get("language"), "ru")

    def test_whisper_model_names_are_not_restricted(self):
        s = self.store()
        for name in ("large-v3-turbo", "distil-large-v3", "C:/models/my-whisper"):
            s.update({"whisper_model": name})
            self.assertEqual(s.get("whisper_model"), name)
        s.update({"whisper_model": "  "})
        self.assertEqual(s.get("whisper_model"), "base")

    def test_ollama_url_is_normalised(self):
        from assistant.settings_store import normalize_ollama_url as n
        full = "http://localhost:11434/api/chat"
        self.assertEqual(n("localhost:11434"), full)
        self.assertEqual(n("http://localhost:11434"), full)
        self.assertEqual(n("http://localhost:11434/"), full)
        self.assertEqual(n(full), full)
        self.assertEqual(n("  192.168.1.5:11434  "), "http://192.168.1.5:11434/api/chat")
        self.assertEqual(n("https://gpu.lan/ollama/api/chat"), "https://gpu.lan/ollama/api/chat")
        self.assertEqual(n("http://localhost:11434/api/generate"), full)
        self.assertEqual(n(""), "")
        self.assertEqual(n("http://"), "")

    def test_url_normalised_on_update_and_default_on_junk(self):
        s = self.store()
        s.update({"ollama_url": "localhost:11434"})
        self.assertEqual(s.get("ollama_url"), "http://localhost:11434/api/chat")
        s.update({"ollama_url": "   "})
        self.assertEqual(s.get("ollama_url"), "http://localhost:11434/api/chat")

    def test_derived_urls_work_after_normalisation(self):
        s = self.store()
        s.update({"ollama_url": "myhost:11434"})
        self.assertEqual(OllamaClient.tags_url(s.get("ollama_url")), "http://myhost:11434/api/tags")
        self.assertEqual(OllamaClient.generate_url(s.get("ollama_url")), "http://myhost:11434/api/generate")


if __name__ == "__main__":
    unittest.main()
