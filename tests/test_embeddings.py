"""Tests for embedding response validation and API compatibility."""
from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

import requests

from assistant.embeddings import EmbeddingCancelled, EmbeddingError, _validate_vectors, embed_texts
from assistant.ollama_client import OllamaCancelled, OllamaClient
from assistant.settings_store import SettingsStore


class FakeResponse:
    def __init__(self, payload=None, status_code=200, error=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class EmbeddingValidationTests(unittest.TestCase):
    def test_valid_vectors_are_normalized(self):
        result = _validate_vectors([[3.0, 4.0], [0.0, 2.0]], 2)
        self.assertAlmostEqual(result[0][0], 0.6)
        self.assertAlmostEqual(result[0][1], 0.8)
        self.assertEqual(result[1], [0.0, 1.0])

    def test_rejects_wrong_count_dimensions_zero_and_non_finite(self):
        for vectors, count in (([], 1), ([[1.0], [1.0, 2.0]], 2), ([[0.0, 0.0]], 1), ([[float("inf")]], 1)):
            with self.subTest(vectors=vectors):
                with self.assertRaises(EmbeddingError):
                    _validate_vectors(vectors, count)

    def test_current_embed_endpoint_closes_response(self):
        response = FakeResponse({"embeddings": [[1.0, 2.0]]})
        session = FakeSession([response])
        result = embed_texts(
            ["query"], model="test", ollama_url="http://localhost:11434/api/chat", session=session
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(response.closed)
        self.assertEqual(session.calls[0][0], "http://localhost:11434/api/embed")
        self.assertTrue(session.calls[0][1]["stream"])

    def test_legacy_endpoint_fallback_is_sequential_and_closes_responses(self):
        not_found = FakeResponse(status_code=404)
        legacy = FakeResponse({"embedding": [0.5, 0.5]})
        session = FakeSession([not_found, legacy])
        result = embed_texts(
            ["query"], model="test", ollama_url="http://localhost:11434/api/chat", session=session
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(not_found.closed)
        self.assertTrue(legacy.closed)
        self.assertEqual(session.calls[1][0], "http://localhost:11434/api/embeddings")

    def test_embedding_input_and_batch_limits_are_enforced_before_http(self):
        session = FakeSession([])
        with self.assertRaises(EmbeddingError):
            embed_texts(
                ["x" * 16001], model="test", ollama_url="http://localhost:11434/api/chat", session=session
            )
        with self.assertRaises(EmbeddingError):
            embed_texts(
                ["x"] * 65, model="test", ollama_url="http://localhost:11434/api/chat", session=session
            )
        self.assertEqual(session.calls, [])

    def test_cancelled_before_request_does_not_call_server(self):
        event = threading.Event()
        event.set()
        session = FakeSession([])
        with self.assertRaises(EmbeddingCancelled):
            embed_texts(
                ["query"], model="test", ollama_url="http://localhost:11434/api/chat",
                session=session, cancel_event=event,
            )
        self.assertEqual(session.calls, [])

    def test_cancel_closes_active_response_and_raises_cancelled(self):
        event = threading.Event()
        response = FakeResponse({"embeddings": [[1.0, 0.0]]})
        session = FakeSession([response])

        def set_active(active):
            if active is not None:
                event.set()

        with self.assertRaises(EmbeddingCancelled):
            embed_texts(
                ["query"], model="test", ollama_url="http://localhost:11434/api/chat",
                session=session, cancel_event=event, set_active=set_active,
            )
        self.assertTrue(response.closed)


class OllamaClientEmbeddingTests(unittest.TestCase):
    def test_ollama_client_uses_shared_session_and_clears_active_response(self):
        response = FakeResponse({"embeddings": [[3.0, 4.0]]})
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder) / "settings.json")
            client = OllamaClient(settings)
            client.session = FakeSession([response])
            vectors = client.embed_texts(["hello"], model="embedding-model")
        self.assertTrue(response.closed)
        self.assertIsNone(client._active_response)
        self.assertAlmostEqual(vectors[0][0], 0.6)
        self.assertEqual(len(client.session.calls), 1)

    def test_ollama_client_rejects_cancelled_work_before_http(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder) / "settings.json")
            client = OllamaClient(settings)
            client.session = FakeSession([])
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(OllamaCancelled):
                client.embed_texts(["hello"], model="embedding-model", cancel_event=cancelled)
            self.assertEqual(client.session.calls, [])

    def test_ollama_client_embedding_waiting_for_shared_lock_is_cancellable(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder) / "settings.json")
            client = OllamaClient(settings)
            client.session = FakeSession([])
            client._request_lock = threading.Lock()
            client._request_lock.acquire()
            active_chat_response = FakeResponse({"message": {"content": "chat"}})
            client._set_active(active_chat_response)
            cancelled = threading.Event()
            errors = []

            def worker():
                try:
                    client.embed_texts(["hello"], model="embedding-model", cancel_event=cancelled)
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=worker)
            thread.start()
            cancelled.set()
            thread.join(timeout=2)
            client._request_lock.release()
            self.assertFalse(thread.is_alive())
            self.assertTrue(errors and isinstance(errors[0], OllamaCancelled))
            self.assertFalse(active_chat_response.closed, "cancel while waiting must not cancel the current chat")
            self.assertEqual(client.session.calls, [])

    def test_ollama_client_cancels_only_its_active_embedding_response(self):
        class BlockingResponse(FakeResponse):
            def __init__(self):
                super().__init__({})
                self.closed_event = threading.Event()

            def json(self):
                if not self.closed_event.wait(3):
                    raise requests.Timeout("response was not closed")
                raise requests.ConnectionError("closed by cancellation")

            def close(self):
                self.closed = True
                self.closed_event.set()

        class BlockingSession:
            def __init__(self, response):
                self.response = response
                self.started = threading.Event()
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                self.started.set()
                return self.response

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsStore(Path(folder) / "settings.json")
            client = OllamaClient(settings)
            response = BlockingResponse()
            session = BlockingSession(response)
            client.session = session
            cancelled = threading.Event()
            errors = []

            def worker():
                try:
                    client.embed_texts(["hello"], model="embedding-model", cancel_event=cancelled)
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(session.started.wait(1))
            cancelled.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(response.closed)
            self.assertTrue(errors and isinstance(errors[0], OllamaCancelled))
            self.assertIsNone(client._active_response)


if __name__ == "__main__":
    unittest.main()
