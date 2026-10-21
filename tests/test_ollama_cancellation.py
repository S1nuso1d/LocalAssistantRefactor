"""Regression tests for cancellable Ollama streaming."""
import threading
import unittest
from unittest.mock import Mock

from assistant.ollama_client import OllamaClient


class FakeSettings:
    def get(self, key, default=None):
        values = {
            "answer_model": "test-model",
            "answer_timeout": 30,
            "ollama_url": "http://localhost:11434/api/chat",
        }
        return values.get(key, default)


class CancellationRaceTests(unittest.TestCase):
    def test_response_is_closed_if_cancelled_during_connection_setup(self):
        cancel_event = threading.Event()
        response = Mock()

        def post(*args, **kwargs):
            # Simulate cancellation after the HTTP request begins but before
            # requests.post returns the response to stream_chat.
            cancel_event.set()
            return response

        client = OllamaClient(FakeSettings())
        client.session = Mock()
        client.session.post.side_effect = post

        chunks = list(
            client.stream_chat(
                [{"role": "user", "content": "test"}],
                cancel_event,
                model="test-model",
            )
        )

        self.assertEqual(chunks, [])
        response.close.assert_called()
        response.iter_lines.assert_not_called()
        self.assertIsNone(client._active_response)


if __name__ == "__main__":
    unittest.main()
