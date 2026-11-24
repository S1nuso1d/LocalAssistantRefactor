"""Tests for the standalone Ollama model-discovery worker."""

from __future__ import annotations

import unittest

from assistant.model_discovery import ModelsWorker


class ModelsWorkerTests(unittest.TestCase):
    def _run(self, fetch_models):
        worker = ModelsWorker(
            "http://localhost:11434/api/chat", fetch_models=fetch_models
        )
        self.assertIsNone(worker.parent())
        emitted = []
        worker.loaded.connect(lambda models: emitted.append(models))
        worker.run()
        self.assertEqual(len(emitted), 1)
        return emitted[0]

    def test_emits_sorted_unique_model_names(self):
        result = self._run(
            lambda _url: ["qwen3:latest", "gemma:2b", "qwen3:latest", "  "]
        )
        self.assertEqual(result, ["gemma:2b", "qwen3:latest"])

    def test_discovery_failure_emits_empty_list(self):
        def fail(_url):
            raise OSError("Ollama is offline")

        self.assertEqual(self._run(fail), [])

    def test_invalid_response_shape_emits_empty_list(self):
        self.assertEqual(self._run(lambda _url: {"models": []}), [])


if __name__ == "__main__":
    unittest.main()
