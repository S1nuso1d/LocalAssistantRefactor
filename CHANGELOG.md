## 2026.11.24-r23.6

- Audio settings now provide an explicit refresh action, so newly connected USB, Bluetooth and 2.4 GHz endpoints can be discovered without closing the settings dialog.
- Refreshing audio devices preserves the user's current unsaved selections and retains a missing pinned device as a visible placeholder.
- Duplicate endpoint names are disambiguated with a short backend-ID suffix; the full ID remains available as a tooltip.
- Added pure unit tests for stable, case-insensitive device labels and duplicate-ID collision handling.

## 2026.11.24-r23.5

- The Diagnostics Center now marks a missing pinned audio device that is temporarily replaced by the system default as a warning instead of reporting the source as healthy.
- Unknown audio-source states now fail safe to a warning; only an explicit connected state is reported as healthy.
- Added deterministic coverage for device polling, default-device changes, pinned-device removal/restoration, transient enumeration failures and retry backoff.

## 2026.11.24-r23.4

- Centralized the displayed application version in the root VERSION file; source and frozen onedir builds now resolve the same metadata.
- Removed duplicate push + pull_request workflow runs and added cancellation of stale runs for the same ref.
- Windows packaging now checks the executable, PyInstaller internal directory and packaged VERSION before uploading the artifact.
- The uploaded Windows artifact name is derived from VERSION, preventing stale release labels.

## 2026.11.24-r23.3

- Win32 capture-affinity failures now log the affected HWND and the error codes returned by both the modern exclusion mode and the black-rectangle fallback.
- The user32 bindings now use ctypes error capture correctly, making Windows API refusals diagnosable instead of reporting only a generic failure.
- CaptureGuard avoids redundant affinity calls after a window has already been protected successfully and contains ordinary Qt object-lifecycle errors inside its application-wide event filter.
- Ctrl+C in a console-launched session now schedules the same graceful shutdown path as the tray Exit command; repeated interrupts are coalesced while workers stop.
- Added regression tests for Win32 error formatting and idempotent Ctrl+C shutdown scheduling.

# Changelog

## 2026.11.24 — r23.7 — Responsive audio-device discovery

- Перечисление аудиоустройств вынесено из GUI-потока в отдельный QThread: медленный или временно зависающий backend SoundCard больше не блокирует окно настроек во время обновления списка.
- Результаты применяются в GUI-потоке; текущий несохранённый выбор сохраняется даже если пользователь меняет его, пока список обновляется.
- Кнопка обновления блокируется на время запроса, повторные параллельные запросы не запускаются; при закрытии диалога поток корректно завершается.
- Добавлены изолированные тесты worker-потока для успешного обнаружения и независимых ошибок устройств вывода/микрофона.


## 2026.11.24-r23.2
- Исправлена проверка single-instance: теперь она запускает первый экземпляр в отдельном процессе ОС и проверяет реальную доставку IPC-команды в его работающий Qt event loop.
- Исключён ложный отказ теста, вызванный блокировкой event loop тестового процесса при синхронном запуске второго экземпляра.
- Обновлена версия и имя артефакта Windows CI.


## 2026.11.24-r23.1

- Fixed Qt test synchronization: worker-thread signal assertions now process the Qt event queue instead of racing queued signal delivery.
- Replaced QLocalServer-only instance exclusivity with a per-user QLockFile process lock; QLocalServer remains the activation channel.
- The lock is released explicitly on normal Qt shutdown; a second launch waits briefly for the active instance's IPC endpoint and then safely exits rather than starting a duplicate.
- Activation messages are buffered and line-framed so data arriving before the Qt connection callback or in partial chunks is handled correctly.
- Removed automatic deletion of an occupied IPC endpoint when its owner cannot be contacted; startup now fails closed rather than risking detaching a live instance.

## 2026.11.24-r23

- Added an asynchronous Diagnostics Center for Ollama, audio sources, Whisper configuration, SQLite integrity and local storage.
- Diagnostic reports intentionally exclude conversation content, document text, memory entries and log contents; Ollama endpoint credentials and URL paths/query secrets are redacted.
- Added paginated conversation history so the UI no longer stops at the first 100 conversations.
- History loading errors are now visible instead of being silently converted into an empty list.
- Added regression tests for database inspection, endpoint redaction, Ollama model-role checks, privacy of copied reports and history pagination.

## 2026.11.24-r22

- Added local document knowledge and explicit long-term memory.
