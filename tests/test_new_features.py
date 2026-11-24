import tempfile
import threading
import time
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



class ProcessorEventQueueTests(unittest.TestCase):
    def test_voice_limit_drops_oldest_but_preserves_commands(self):
        from assistant.event_queue import ProcessorEventQueue

        events = ProcessorEventQueue(max_voice_phrases=2)
        events.put_voice("old")
        events.put(("SWITCH", 7))
        events.put_voice("middle")
        dropped = events.put_voice("new")

        self.assertEqual(dropped, "old")
        self.assertEqual(
            events.snapshot(),
            [("SWITCH", 7), ("VOICE", "middle"), ("VOICE", "new")],
        )

    def test_clear_voice_removes_stale_markers_and_keeps_switch_position(self):
        from assistant.event_queue import ProcessorEventQueue

        events = ProcessorEventQueue(max_voice_phrases=2)
        events.put_voice("old phrase")
        events.put(("SWITCH", 123))
        self.assertEqual(events.clear_voice(), ["old phrase"])
        events.put_voice("new phrase")
        self.assertEqual(
            events.snapshot(), [("SWITCH", 123), ("VOICE", "new phrase")]
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

    def test_regenerate_does_not_remove_answer_to_previous_user_message(self):
        c = self.store.create_conversation()
        self.store.add_message(c, "user", "Первый вопрос")
        first_answer = self.store.add_message(c, "assistant", "Ответ на первый вопрос")
        self.store.add_message(c, "user", "Второй вопрос без ответа")

        self.assertIsNone(self.store.pop_last_assistant_message(c))
        messages = self.store.get_messages(c)
        self.assertEqual([m["id"] for m in messages], [
            messages[0]["id"], first_answer, messages[2]["id"]
        ])
        self.assertEqual(messages[1]["content"], "Ответ на первый вопрос")

        self.store.add_message(c, "assistant", "Ответ на второй вопрос")
        removed = self.store.pop_last_assistant_message(c)
        self.assertEqual(removed["content"], "Ответ на второй вопрос")
        self.assertEqual(
            [m["content"] for m in self.store.get_messages(c)],
            ["Первый вопрос", "Ответ на первый вопрос", "Второй вопрос без ответа"],
        )


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

    def test_replacement_worker_is_invalidated_when_speaks_overlap(self):
        import threading as th
        from assistant.tts import SpeechEngine

        first_entered = th.Event()
        release_first = th.Event()
        call_lock = th.Lock()
        call_count = [0]
        created = {}

        def popen(cmd, **kwargs):
            with call_lock:
                call_id = call_count[0]
                call_count[0] += 1
            if call_id == 0:
                first_entered.set()
                release_first.wait(2)
            proc = FakeProc(th.Event())
            created[call_id] = proc
            return proc

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        settings = SettingsStore(Path(d.name) / "s.json")
        engine = SpeechEngine(settings, popen=popen, platform="win32")
        first = th.Thread(target=lambda: engine.speak("Первый"))
        first.start()
        self.assertTrue(first_entered.wait(1))

        # The second utterance reserves a newer generation and is allowed to
        # start before the first Popen call returns.
        engine.speak("Второй")
        self.assertTrue(self.wait_for(lambda: engine.speaking and 1 in created))
        release_first.set()
        first.join(timeout=2)
        self.assertTrue(self.wait_for(lambda: 0 in created))
        self.assertTrue(created[0].killed)  # stale process was rejected
        self.assertFalse(created[1].killed)  # current process remains active
        engine.stop()

    def test_stop_during_process_creation_cannot_leave_stale_speaking_state(self):
        import threading as th
        from assistant.tts import SpeechEngine

        holder = {}
        procs = []

        def popen(cmd, **kwargs):
            holder["engine"].stop()
            proc = FakeProc(th.Event())
            procs.append(proc)
            return proc

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        settings = SettingsStore(Path(d.name) / "s.json")
        states = []
        engine = SpeechEngine(
            settings, on_state=states.append, popen=popen, platform="win32"
        )
        holder["engine"] = engine
        engine.speak("Проверка остановки")
        self.assertTrue(self.wait_for(lambda: bool(procs)))
        self.assertTrue(self.wait_for(lambda: procs[0].killed))
        self.assertFalse(engine.speaking)
        self.assertEqual(states, [])

    def test_popen_failure_does_not_leave_speaking_enabled(self):
        import time
        from assistant.tts import SpeechEngine

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        settings = SettingsStore(Path(d.name) / "s.json")
        states = []

        def fail_popen(*args, **kwargs):
            raise OSError("PowerShell unavailable")

        engine = SpeechEngine(
            settings, on_state=states.append, popen=fail_popen, platform="win32"
        )
        engine.speak("Проверка ошибки")
        deadline = time.time() + 2.0
        while time.time() < deadline and engine._generation == 1:
            time.sleep(0.01)
        self.assertFalse(engine.speaking)
        self.assertEqual(states, [])

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
            def post(self, url, json, stream, timeout):
                seen.update(url=url, **json)
                seen["stream_argument"] = stream
                return FakeResp(200, payload={"done": True})
        self.make_client(S()).warm_up()
        self.assertTrue(seen["url"].endswith("/api/generate"))
        self.assertEqual(seen["prompt"], "")
        # Ollama itself is asked for a non-streaming warm-up response, while
        # requests streams the HTTP body so Stop can close it during loading.
        self.assertFalse(seen["stream"])
        self.assertTrue(seen["stream_argument"])
        self.assertEqual(seen["model"], "qwen3-coder:30b")
        self.assertEqual(seen["keep_alive"], "10m")

    def test_warm_up_missing_model_gives_pull_hint(self):
        class S:
            def post(self, url, json, stream, timeout):
                return FakeResp(404, payload={"error": "model not found"})
        with self.assertRaises(OllamaError) as ctx:
            self.make_client(S()).warm_up()
        self.assertIn("ollama pull", str(ctx.exception))

    def test_warm_up_connection_error(self):
        import requests

        class S:
            def post(self, url, json, stream, timeout):
                raise requests.ConnectionError("refused")
        with self.assertRaises(OllamaError):
            self.make_client(S()).warm_up()

    def test_warm_up_is_cancellable_after_response_headers(self):
        import requests
        from assistant.ollama_client import OllamaCancelled

        entered = threading.Event()

        class BlockingResponse(FakeResp):
            def json(self):
                entered.set()
                deadline = time.monotonic() + 2
                while not self.closed and time.monotonic() < deadline:
                    time.sleep(0.005)
                if self.closed:
                    raise requests.ConnectionError("response closed")
                return {"done": True}

        class S:
            def post(self, url, json, stream, timeout):
                return response

        response = BlockingResponse()
        client = self.make_client(S())
        cancel_event = threading.Event()
        errors = []

        def run():
            try:
                client.warm_up(cancel_event=cancel_event)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(1))
        cancel_event.set()
        client.cancel_active_request()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OllamaCancelled)
        self.assertTrue(response.closed)

    def test_classifier_request_is_cancellable_after_response_headers(self):
        import requests
        from assistant.ollama_client import OllamaCancelled

        entered = threading.Event()

        class BlockingResponse(FakeResp):
            def json(self):
                entered.set()
                deadline = time.monotonic() + 2
                while not self.closed and time.monotonic() < deadline:
                    time.sleep(0.005)
                if self.closed:
                    raise requests.ConnectionError("response closed")
                return {"message": {"content": '{"is_request": true}'}}

        response = BlockingResponse()

        class S:
            def post(self, url, json, stream, timeout):
                return response

        client = self.make_client(S())
        cancel_event = threading.Event()
        errors = []

        def run():
            try:
                client.classify_ambiguous(
                    "Как он туда попал без вопросительного знака?",
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(1))
        cancel_event.set()
        client.cancel_active_request()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OllamaCancelled)
        self.assertTrue(response.closed)

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


class DeviceWatcherTests(unittest.TestCase):
    def test_due_is_rate_limited(self):
        from assistant.devicewatch import DeviceWatcher
        now = [100.0]
        w = DeviceWatcher(4.0, clock=lambda: now[0])
        self.assertFalse(w.due())
        now[0] = 103.9
        self.assertFalse(w.due())
        now[0] = 104.0
        self.assertTrue(w.due())
        self.assertFalse(w.due())  # just reset
        now[0] = 108.5
        self.assertTrue(w.due())

    def test_default_change_rules(self):
        from assistant.devicewatch import DeviceWatcher as W
        self.assertTrue(W.default_changed(True, "Speakers", "Headphones"))
        self.assertFalse(W.default_changed(True, "Speakers", "Speakers"))
        self.assertFalse(W.default_changed(False, "Speakers", "Headphones"))  # pinned device
        self.assertFalse(W.default_changed(True, "Speakers", None))  # query failed
        self.assertFalse(W.default_changed(True, "Speakers", ""))

    def test_selected_device_loss_and_return_are_detected(self):
        from assistant.devicewatch import DeviceWatcher as W

        self.assertTrue(W.should_reopen(
            opened_name="USB Headset", follows_default=False,
            current_default_name="Speakers", opened_selection="USB Headset",
            current_selection="USB Headset", requested_available_at_open=True,
            requested_available_now=False,
        ))
        self.assertTrue(W.should_reopen(
            opened_name="Speakers", follows_default=True,
            current_default_name="Speakers", opened_selection="USB Headset",
            current_selection="USB Headset", requested_available_at_open=False,
            requested_available_now=True,
        ))

    def test_settings_device_change_is_detected(self):
        from assistant.devicewatch import DeviceWatcher as W

        self.assertTrue(W.should_reopen(
            opened_name="Speakers", follows_default=True,
            current_default_name="Speakers", opened_selection="По умолчанию",
            current_selection="Bluetooth Headphones",
            requested_available_at_open=True, requested_available_now=True,
        ))

    def test_pinned_device_ignores_default_change(self):
        from assistant.devicewatch import DeviceWatcher as W

        self.assertFalse(W.should_reopen(
            opened_name="USB Headset", follows_default=False,
            current_default_name="Bluetooth Headphones",
            opened_selection="USB Headset", current_selection="USB Headset",
            requested_available_at_open=True, requested_available_now=True,
        ))

    def test_device_presence_is_case_insensitive_and_unknown_on_query_failure(self):
        from assistant.devicewatch import DeviceWatcher as W

        self.assertTrue(W.selected_device_available("USB Headset", ["usb headset"]))
        self.assertFalse(W.selected_device_available("USB Headset", ["Speakers"]))
        self.assertIsNone(W.selected_device_available("USB Headset", None))


class RetryBackoffTests(unittest.TestCase):
    def test_exponential_delay_is_capped_and_resettable(self):
        from assistant.devicewatch import RetryBackoff

        retry = RetryBackoff(initial_seconds=1, maximum_seconds=8, multiplier=2)
        self.assertEqual(
            [retry.next_delay() for _ in range(6)], [1, 2, 4, 8, 8, 8]
        )
        retry.reset()
        self.assertEqual(retry.next_delay(), 1)

    def test_multiplier_one_never_grows(self):
        from assistant.devicewatch import RetryBackoff

        retry = RetryBackoff(initial_seconds=2, maximum_seconds=10, multiplier=1)
        self.assertEqual([retry.next_delay() for _ in range(4)], [2, 2, 2, 2])


class FakeUser32:
    """Mimics user32 affinity calls; ``modern`` False emulates old Windows."""

    def __init__(self, modern=True, fail_all=False):
        self.modern, self.fail_all = modern, fail_all
        self.affinity = 0
        self.calls = []

    def SetWindowDisplayAffinity(self, hwnd, value):
        self.calls.append(value)
        if self.fail_all:
            return 0
        if value == 0x11 and not self.modern:
            return 0
        self.affinity = value
        return 1

    def GetWindowDisplayAffinity(self, hwnd, ref):
        if self.fail_all:
            return 0
        ref._obj.value = self.affinity
        return 1


class CaptureAffinityTests(unittest.TestCase):
    def test_modern_windows_hides_completely(self):
        from assistant import winintegration as w
        api = FakeUser32()
        self.assertEqual(w.set_capture_hidden(1, True, api, "win32"), w.HIDDEN)
        self.assertEqual(api.affinity, 0x11)
        self.assertEqual(w.get_capture_state(1, api, "win32"), w.HIDDEN)

    def test_old_windows_falls_back_to_black_rectangle(self):
        from assistant import winintegration as w
        api = FakeUser32(modern=False)
        self.assertEqual(w.set_capture_hidden(1, True, api, "win32"), w.BLACK)
        self.assertEqual(api.calls, [0x11, 0x01])
        self.assertEqual(w.get_capture_state(1, api, "win32"), w.BLACK)

    def test_can_be_switched_off(self):
        from assistant import winintegration as w
        api = FakeUser32()
        w.set_capture_hidden(1, True, api, "win32")
        self.assertEqual(w.set_capture_hidden(1, False, api, "win32"), w.VISIBLE)
        self.assertEqual(w.get_capture_state(1, api, "win32"), w.VISIBLE)

    def test_refusal_is_reported_not_hidden_behind_success(self):
        from assistant import winintegration as w
        api = FakeUser32(fail_all=True)
        self.assertEqual(w.set_capture_hidden(1, True, api, "win32"), w.FAILED)
        self.assertEqual(w.get_capture_state(1, api, "win32"), w.FAILED)

    def test_other_platforms(self):
        from assistant import winintegration as w
        self.assertEqual(w.set_capture_hidden(1, True, None, "linux"), w.UNSUPPORTED)
        self.assertEqual(w.get_capture_state(1, None, "linux"), w.UNSUPPORTED)
        for state in (w.HIDDEN, w.BLACK, w.VISIBLE, w.UNSUPPORTED, w.FAILED):
            self.assertIn(state, w.STATE_TEXT)

    def test_exception_in_api_is_contained(self):
        from assistant import winintegration as w

        class Broken:
            def SetWindowDisplayAffinity(self, *a):
                raise OSError("boom")
        self.assertEqual(w.set_capture_hidden(1, True, Broken(), "win32"), w.FAILED)


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved, access):
        outer = self

        class Key:
            def __enter__(self_inner):
                return outer.values
            def __exit__(self_inner, *a):
                return False
        return Key()

    def QueryValueEx(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        return key[name], 1

    def SetValueEx(self, key, name, reserved, kind, value):
        key[name] = value

    def DeleteValue(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        del key[name]


class AutostartTests(unittest.TestCase):
    def test_roundtrip(self):
        from assistant import autostart
        reg = FakeWinreg()
        self.assertFalse(autostart.is_enabled(reg))
        self.assertTrue(autostart.set_enabled(True, reg, command='"app.exe" --tray'))
        self.assertTrue(autostart.is_enabled(reg))
        self.assertEqual(reg.values[autostart.VALUE_NAME], '"app.exe" --tray')
        self.assertTrue(autostart.set_enabled(False, reg))
        self.assertFalse(autostart.is_enabled(reg))
        self.assertTrue(autostart.set_enabled(False, reg))  # removing twice is fine

    def test_legacy_liveassistant_entry_is_detected_and_migrated(self):
        from assistant import autostart
        reg = FakeWinreg()
        reg.values["LiveAssistant"] = '"old.exe" --tray'
        self.assertTrue(autostart.is_enabled(reg))
        self.assertTrue(autostart.set_enabled(True, reg, command='"new.exe" --tray'))
        self.assertEqual(reg.values[autostart.VALUE_NAME], '"new.exe" --tray')
        self.assertNotIn("LiveAssistant", reg.values)
        self.assertTrue(autostart.set_enabled(False, reg))
        self.assertFalse(autostart.is_enabled(reg))

    def test_launch_command_variants(self):
        from assistant import autostart
        self.assertEqual(
            autostart.launch_command(frozen=True, executable="C:/A/LocalAssistant.exe"),
            '"C:/A/LocalAssistant.exe" --tray',
        )
        cmd = autostart.launch_command(
            frozen=False, executable="C:/Py/python.exe", script="C:/App/main.py"
        )
        self.assertTrue(cmd.endswith('"C:/App/main.py" --tray'))
        self.assertTrue(cmd.startswith('"'))

    def test_not_windows(self):
        from assistant import autostart
        import sys as _sys
        if _sys.platform != "win32":
            self.assertFalse(autostart.is_enabled())
            self.assertFalse(autostart.set_enabled(True))


class TrayDefaultsTests(unittest.TestCase):
    def test_new_defaults_and_validation(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        st = SettingsStore(Path(d.name) / "s.json")
        self.assertTrue(st.get("hide_from_capture"))      # protection on by default
        self.assertTrue(st.get("close_to_tray"))
        self.assertFalse(st.get("start_in_tray"))
        self.assertTrue(st.get("tray_icon_visible"))
        self.assertEqual(st.get("toggle_window_hotkey"), "ctrl+alt+h")
        self.assertNotEqual(st.get("toggle_window_hotkey"), st.get("hotkey"))
        st.update({"close_to_tray": "false", "toggle_window_hotkey": "  "})
        self.assertFalse(st.get("close_to_tray"))
        self.assertEqual(st.get("toggle_window_hotkey"), "ctrl+alt+h")  # junk -> default


class IconAssetTests(unittest.TestCase):
    def test_generated_icon_is_a_valid_multisize_ico(self):
        import struct
        import sys as _sys
        tools = Path(__file__).resolve().parent.parent / "tools"
        _sys.path.insert(0, str(tools))
        try:
            import make_icon
        finally:
            _sys.path.remove(str(tools))
        data = make_icon.build_ico()
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, kind, count), (0, 1, len(make_icon.SIZES)))
        for i, size in enumerate(make_icon.SIZES):
            w, h, _c, _r, _p, bpp, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            self.assertEqual(w or 256, size)
            self.assertEqual(bpp, 32)
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")

    def test_shipped_icon_exists(self):
        icon = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
        self.assertTrue(icon.exists() and icon.stat().st_size > 1000)


if __name__ == "__main__":
    unittest.main()
