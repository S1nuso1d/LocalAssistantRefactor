"""Compatibility defaults; runtime values live in SettingsStore."""
from .settings_store import DEFAULT_SETTINGS

OLLAMA_URL = DEFAULT_SETTINGS["ollama_url"]
MODEL_NAME = DEFAULT_SETTINGS["answer_model"]
CLASSIFIER_MODEL_NAME = DEFAULT_SETTINGS["classifier_model"]
WHISPER_MODEL = DEFAULT_SETTINGS["whisper_model"]
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
LANGUAGE = DEFAULT_SETTINGS["language"]
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.5
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_DURATION)
SILENCE_THRESHOLD = DEFAULT_SETTINGS["silence_threshold"]
SILENCE_DURATION = DEFAULT_SETTINGS["silence_duration"]
MIN_SPEECH_DURATION = 0.45
MAX_PHRASE_DURATION = 40.0
FILTER_BACKGROUND_SPEECH = DEFAULT_SETTINGS["filter_background_speech"]
REQUIRE_ASSISTANT_NAME = DEFAULT_SETTINGS["require_assistant_name"]
ASSISTANT_NAMES = (
    "помощник", "ассистент", "чат gpt", "чатгпт", "эй помощник",
)
DUPLICATE_WINDOW_SECONDS = 20
MAX_RECENT_PHRASES = 100
CLASSIFIER_TIMEOUT = 90
ANSWER_TIMEOUT = 180
MAX_HISTORY_MESSAGES = DEFAULT_SETTINGS["max_history_messages"]
DEFAULT_BACKGROUND_ALPHA = DEFAULT_SETTINGS["background_alpha"]
DEFAULT_BACKGROUND_SATURATION = DEFAULT_SETTINGS["background_saturation"]
CLOSE_TO_TRAY = DEFAULT_SETTINGS["close_to_tray"]
SYSTEM_PROMPT = """
Ты — локальный русскоязычный ассистент.
Отвечай естественно, понятно и по существу, как в обычном чате.
Используй контекст диалога, не повторяй вопрос без необходимости.
По умолчанию отвечай кратко: сначала результат, затем только необходимое пояснение.
Не пересказывай условие своими словами и не разбирай каждое предложение отдельно, если об этом не попросили.
Если пользователь прислал программную задачу, помоги решить именно её, а не выдавай другое задание и не повторяй условие.
Не утверждай, что выполнил действия на компьютере, если ты их не выполнял.
""".strip()
