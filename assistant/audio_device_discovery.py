"""Background audio endpoint enumeration, isolated from the Qt Widgets UI."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("live_assistant.settings")


class AudioDevicesWorker(QThread):
    """Enumerate audio endpoints without blocking Qt or importing native APIs at module load."""

    loaded = Signal(list, list, list)

    def __init__(self, soundcard_module=None) -> None:
        super().__init__()
        self._soundcard_module = soundcard_module

    def run(self) -> None:
        speakers: list[tuple[str, str]] = []
        microphones: list[tuple[str, str]] = []
        errors: list[str] = []
        soundcard_module = self._soundcard_module
        if soundcard_module is None:
            try:
                import soundcard as soundcard_module
            except Exception as exc:
                logger.exception("Аудиобиблиотека недоступна.")
                self.loaded.emit(
                    speakers, microphones,
                    [f"аудиобиблиотека: {type(exc).__name__}"],
                )
                return

        try:
            speakers = [
                (str(getattr(item, "id", "") or "").strip(), str(item.name).strip())
                for item in soundcard_module.all_speakers()
                if str(getattr(item, "name", "") or "").strip()
            ]
        except Exception as exc:
            logger.exception("Не удалось обновить список устройств вывода.")
            errors.append(f"вывод: {type(exc).__name__}")

        try:
            microphones = [
                (str(getattr(item, "id", "") or "").strip(), str(item.name).strip())
                for item in soundcard_module.all_microphones(include_loopback=False)
                if str(getattr(item, "name", "") or "").strip()
            ]
        except Exception as exc:
            logger.exception("Не удалось обновить список микрофонов.")
            errors.append(f"микрофон: {type(exc).__name__}")

        self.loaded.emit(speakers, microphones, errors)
