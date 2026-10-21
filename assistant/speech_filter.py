
"""Rule-first intent filtering and duplicate detection."""

import re
import threading
import time
from collections import deque

from .config import (
    ASSISTANT_NAMES,
    DUPLICATE_WINDOW_SECONDS,
    MAX_RECENT_PHRASES,
    REQUIRE_ASSISTANT_NAME,
)

QUESTION_STARTERS = (
    "кто", "что", "где", "куда", "откуда", "когда", "почему", "зачем",
    "как", "какой", "какая", "какие", "какое", "сколько", "чем",
    "можно ли", "правда ли", "верно ли", "есть ли", "является ли",
    "будет ли", "стоит ли", "нужно ли", "надо ли",
)

REQUEST_STARTERS = (
    "объясни", "расскажи", "покажи", "сравни", "найди", "проверь",
    "реши", "напиши", "перепиши", "исправь", "составь", "подскажи",
    "помоги", "ответь", "проанализируй", "переведи", "посчитай",
    "вычисли", "предложи", "придумай", "сделай", "создай", "прочитай",
    "повтори", "уточни", "перечисли", "сформулируй", "сгенерируй",
)

SHORT_REACTIONS = {
    "ага", "угу", "да", "нет", "ну да", "ну нет", "понятно", "ясно",
    "хорошо", "ладно", "ок", "окей", "спасибо", "пожалуйста", "все",
    "так", "вот", "ну", "ээ", "эм", "точно", "конечно", "не знаю",
}

# Частые начала продолжения разговора. Такие фразы могут быть ответом
# на предыдущий вопрос ассистента, даже если в них нет вопросительного
# знака или явной команды.
CONTEXTUAL_STARTERS = (
    "конечно", "давай", "давайте", "например", "допустим", "смотри",
    "смотрите", "у меня", "мне нужно", "мне надо", "я хочу",
    "я думаю", "я считаю", "получается", "тогда", "значит",
    "в общем", "дело в том", "проблема в том", "задача такая",
    "задание такое", "есть список", "у нас есть",
)

BACKGROUND_REGEXES = [
    re.compile(r"^(субтитры|продолжение следует)$"),
    re.compile(r"^(до свидания|всем пока|до встречи)$"),
    re.compile(r"^(раз два|раз два три)$"),
]


def normalize_text(text: str) -> str:
    text = text.lower().strip().replace("ё", "е")
    text = re.sub(r"[^\w\s?!.%-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_assistant_name(text: str, assistant_names=None) -> bool:
    normalized = normalize_text(text)
    names = assistant_names or ASSISTANT_NAMES

    for name in names:
        normalized_name = normalize_text(name)
        if normalized_name and re.search(
            rf"(?<!\w){re.escape(normalized_name)}(?!\w)",
            normalized,
        ):
            return True

    return False


def starts_with_any(text: str, starters: tuple[str, ...]) -> bool:
    normalized = normalize_text(text)
    return any(
        normalized == word or normalized.startswith(word + " ")
        for word in starters
    )


def looks_like_question(text: str) -> bool:
    normalized = normalize_text(text)

    if "?" in text or starts_with_any(normalized, QUESTION_STARTERS):
        return True

    return bool(
        re.match(
            r"^(ты|вы|можешь|можете|сможешь|сможете)\s+(мне\s+)?"
            r"(объяснить|рассказать|показать|помочь|подсказать|"
            r"проверить|найти|сравнить|решить)\b",
            normalized,
        )
    )


def looks_like_request(text: str) -> bool:
    normalized = normalize_text(text)

    if starts_with_any(normalized, REQUEST_STARTERS):
        return True

    return bool(
        re.match(
            r"^(можешь|можете|сможешь|сможете)\s+(мне\s+)?"
            r"(объяснить|рассказать|показать|найти|проверить|решить|"
            r"написать|исправить|составить|посчитать|перевести|"
            r"сравнить|создать|подсказать|помочь)\b",
            normalized,
        )
    )


def is_obvious_background(text: str) -> bool:
    normalized = normalize_text(text)

    if not normalized or normalized in SHORT_REACTIONS:
        return True

    if len(normalized.split()) <= 2 and len(normalized) < 12:
        if (
            not has_assistant_name(normalized)
            and not looks_like_question(text)
            and not looks_like_request(text)
        ):
            return True

    return any(
        pattern.fullmatch(normalized)
        for pattern in BACKGROUND_REGEXES
    )


def is_substantive_utterance(text: str) -> bool:
    """Return True for a meaningful phrase, including conversational follow-ups."""
    normalized = normalize_text(text)

    if not normalized or is_obvious_background(normalized):
        return False

    words = normalized.split()

    # Long, coherent utterances are often follow-ups to the assistant.
    # They should not be silently discarded just because they lack
    # an imperative verb or a question mark.
    if len(words) >= 7 and len(normalized) >= 35:
        return True

    # Also recognize common conversational follow-up openings.
    if starts_with_any(normalized, CONTEXTUAL_STARTERS):
        return len(words) >= 4

    return False


def classify_by_rules(
    text: str,
    assistant_names=None,
    require_assistant_name: bool | None = None,
) -> str:
    """Return accept, reject, or ambiguous."""
    required = (
        REQUIRE_ASSISTANT_NAME
        if require_assistant_name is None
        else require_assistant_name
    )

    normalized = normalize_text(text)

    if not normalized:
        return "reject"

    if has_assistant_name(text, assistant_names):
        return "accept"

    if looks_like_question(text) or looks_like_request(text):
        return "ambiguous" if required else "accept"

    if is_obvious_background(text):
        return "reject"

    # A substantive phrase may be a continuation of the current dialogue.
    # When the assistant name is not mandatory, accept it without asking
    # a classifier that sees the phrase out of context.
    if not required and is_substantive_utterance(text):
        return "accept"

    return "ambiguous"


class DuplicateFilter:
    def __init__(self) -> None:
        self.items: deque[tuple[str, float]] = deque()
        self.lock = threading.Lock()

    def is_duplicate(self, text: str) -> bool:
        normalized = normalize_text(text)

        if not normalized:
            return True

        now = time.monotonic()

        with self.lock:
            while (
                self.items
                and now - self.items[0][1] > DUPLICATE_WINDOW_SECONDS
            ):
                self.items.popleft()

            for previous, _ in self.items:
                if normalized == previous:
                    return True

                if len(normalized) >= 18 and len(previous) >= 18:
                    shorter, longer = sorted(
                        (normalized, previous), key=len
                    )
                    # Whisper may return an overlapping phrase with a few
                    # extra words on the next chunk. Suppress near-identical
                    # duplicates, but keep genuine continuations that add
                    # substantial new information.
                    if shorter in longer and len(shorter) / len(longer) >= 0.80:
                        return True

            self.items.append((normalized, now))

            while len(self.items) > MAX_RECENT_PHRASES:
                self.items.popleft()

        return False
