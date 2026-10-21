"""Regression test for stopping the message processor while its queue is being read."""
import queue
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

# PySide6 is not required by the headless CI suite. Provide only the tiny
# QThread/Signal surface needed for this deterministic direct-call unit test.
try:
    from PySide6.QtCore import QThread as _QThread
except ModuleNotFoundError as exc:
    if exc.name not in {"PySide6", "PySide6.QtCore"}:
        raise

    class _TestSignal:
        def emit(self, *_args, **_kwargs):
            pass

    class _TestQThread:
        def __init__(self, *_args, **_kwargs):
            pass

    _qtcore = types.ModuleType("PySide6.QtCore")
    _qtcore.QThread = _TestQThread
    _qtcore.Signal = lambda *_args, **_kwargs: _TestSignal()
    _pyside = types.ModuleType("PySide6")
    _pyside.QtCore = _qtcore
    sys.modules.setdefault("PySide6", _pyside)
    sys.modules["PySide6.QtCore"] = _qtcore

from assistant.conversation_store import ConversationStore
from assistant.processor import MessageProcessor
from assistant.settings_store import SettingsStore


class StopDuringGetQueue(queue.Queue):
    """Simulate stop() winning just after get() has obtained a pending message."""

    def __init__(self, processor):
        super().__init__()
        self.processor = processor
        self.triggered = False

    def get(self, block=True, timeout=None):
        item = super().get(block=block, timeout=timeout)
        if item is not None and not self.triggered:
            self.triggered = True
            self.processor.stop()
        return item


class ProcessorShutdownTests(unittest.TestCase):
    def test_pending_message_is_not_started_if_stop_races_with_queue_get(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            store = ConversationStore(root / "history.sqlite3")
            processor = MessageProcessor(settings, store)

            stop_race_queue = StopDuringGetQueue(processor)
            processor._queue = stop_race_queue
            processor._queue.put(("MESSAGE", ("Do not start this request", True)))
            processor._run_message = Mock()

            # Call QThread.run synchronously to make the race deterministic and
            # avoid starting a real worker thread in this unit test.
            processor.run()

            processor._run_message.assert_not_called()
            self.assertFalse(processor._running)
            processor._client.cancel_active_request()


if __name__ == "__main__":
    unittest.main()
