"""Regression tests for voice intent filtering and classifier fallbacks."""
import unittest
from unittest.mock import patch

from assistant.ollama_client import OllamaClient
from assistant.speech_filter import classify_by_rules, is_substantive_utterance


FOLLOW_UP = (
    "Конечно, давай простую. Задание у тебя есть список ростов человеков "
    "сантиметрах, например. Твоя задача написать цикл, который посчитает "
    "средний рост, а потом выведет."
)


class FakeSettings:
    def __init__(self, **values):
        self.values = {
            "require_assistant_name": False,
            "assistant_names": None,
            "classifier_model": "test-model",
            **values,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class SpeechFilterRegressionTests(unittest.TestCase):
    def test_long_follow_up_is_substantive(self):
        self.assertTrue(is_substantive_utterance(FOLLOW_UP))

    def test_long_follow_up_is_accepted_without_assistant_name(self):
        self.assertEqual(
            classify_by_rules(FOLLOW_UP, require_assistant_name=False),
            "accept",
        )

    def test_negative_model_classification_does_not_drop_substantive_follow_up(self):
        client = OllamaClient(FakeSettings())
        with (
            patch("assistant.ollama_client.classify_by_rules", return_value="ambiguous"),
            patch.object(client, "classify_ambiguous", return_value=False),
        ):
            accepted, reason = client.should_answer(FOLLOW_UP)

        self.assertTrue(accepted)
        self.assertEqual(reason, "принято резервным правилом")

    def test_strict_name_setting_keeps_classifier_decision(self):
        client = OllamaClient(FakeSettings(require_assistant_name=True))
        with (
            patch("assistant.ollama_client.classify_by_rules", return_value="ambiguous"),
            patch.object(client, "classify_ambiguous", return_value=False),
        ):
            accepted, reason = client.should_answer(FOLLOW_UP)

        self.assertFalse(accepted)
        self.assertEqual(reason, "отсеяно локальной моделью")

    def test_short_background_reaction_is_rejected_by_rules(self):
        self.assertEqual(
            classify_by_rules("Ага", require_assistant_name=False),
            "reject",
        )


if __name__ == "__main__":
    unittest.main()
