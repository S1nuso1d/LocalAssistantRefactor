"""Prompt profiles and lightweight intent detection for programming requests."""
from __future__ import annotations

import re

_PATTERNS = {
    "debug": re.compile(
        r"\b(traceback|exception|stack trace|error|bug|nameerror|typeerror|syntaxerror)\b"
        r"|ошибк|не работает|не запускается|падает|почини|исправь.{0,12}(код|ошибк|файл)"
        r"|посмотри.{0,18}(код|ошибк)|разберись с ошибк",
        re.IGNORECASE,
    ),
    "exercise": re.compile(
        r"(?:дай|предложи|придумай).{0,24}(?:задачк?[ауи]|упражнен)"
        r"|задач[ауи].{0,28}(?:python|код|программ|цикл|список)"
        r"|потренир|практическ.{0,12}задан|задачк?у? по python",
        re.IGNORECASE,
    ),
    "explain": re.compile(
        r"объясни|разбер[иь]|что делает|что означает|как работает"
        r"|почему.{0,20}(код|ошибк|программ)|расскажи про"
        r"|объясни.{0,20}(код|ошибк|функц)",
        re.IGNORECASE,
    ),
    "write_code": re.compile(
        r"напиши|сгенерируй|реализуй"
        r"|сделай.{0,15}(функц|класс|код|скрипт|пример)"
        r"|создай.{0,15}(функц|класс|код|скрипт|файл)"
        r"|пример кода|написать код|покажи код",
        re.IGNORECASE,
    ),
    "continue": re.compile(
        r"продолжи|продолжай|дополни|с того места|на основе предыдущего"
        r"|продолжить работу с кодом|давай дальше|продолжи предыдущий",
        re.IGNORECASE,
    ),
}

_INTENT_INSTRUCTIONS = {
    "debug": (
        "Пользователь разбирает ошибку. Сначала назови вероятную причину, затем предложи "
        "конкретное исправление и объясни, как проверить результат. Не выдумывай отсутствующие строки traceback."
    ),
    "exercise": (
        "Пользователь хочет практическое задание. Дай условие, входные/выходные примеры и критерии проверки. "
        "Не показывай решение сразу, если пользователь прямо его не просил."
    ),
    "explain": (
        "Пользователь просит объяснение. Объясняй по шагам простым языком, добавь короткий пример "
        "и типичную ошибку, если это уместно."
    ),
    "write_code": (
        "Пользователь просит код. Дай цельный, запускаемый пример с нужными импортами, разумными именами "
        "и обработкой ошибок. Затем кратко объясни способ запуска."
    ),
    "continue": (
        "Продолжи работу с фрагментом, который уже обсуждался в этой переписке. Сохрани выбранный язык, "
        "имена, архитектуру и контекст. Не переписывай весь проект заново без необходимости; покажи именно продолжение."
    ),
}


def detect_programming_intent(text: str) -> str | None:
    """Classify common programming intents without calling a model."""
    text = (text or "").strip()
    if not text:
        return None
    for intent in ("debug", "exercise", "explain", "write_code", "continue"):
        if _PATTERNS[intent].search(text):
            return intent
    return None


def build_coding_prompt(text: str, mode: str = "coding") -> str:
    """Return an additional system instruction for the selected assistant profile."""
    mode = (mode or "coding").strip().lower()
    intent = detect_programming_intent(text)
    if mode not in {"general", "coding", "tutor"}:
        mode = "coding"
    if mode == "general" and intent is None:
        return ""
    if mode == "tutor":
        profile = (
            "Профиль: наставник по программированию. Объясняй простым языком, задавай наводящие вопросы "
            "и не выдавай полное решение учебной задачи без просьбы."
        )
    elif mode == "coding":
        profile = (
            "Профиль: помощник-программист. Помогай с Python, FastAPI, SQL, Kotlin и другими языками. "
            "При изменении кода предпочитай цельные файлы, сохраняй архитектуру и указывай, какие проверки выполнены."
        )
    else:
        profile = "Профиль: универсальный ассистент."
    if intent:
        return f"{profile}\nТип запроса: {intent}. {_INTENT_INSTRUCTIONS[intent]}"
    return profile
