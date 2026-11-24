# Changelog

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
