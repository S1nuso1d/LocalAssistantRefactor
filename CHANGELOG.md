# Changelog

Формат: версии от новых к старым. Версия приложения хранится в файле `VERSION`.

## 2026.11.24-r24.0 — Release hardening

**Защита экрана (приоритет)**
- Исправлен пробел в `CaptureGuard`: кэш защищённых окон был привязан к номеру HWND, а Windows переиспользует номера после уничтожения окна. Новое окно (подсказка, меню, всплывающий список) с «переработанным» HWND могло пропустить защиту. Теперь событие создания нативного окна (`WinIdChange`) всегда повторно применяет защиту; снимки состояний ограничены, а привязки активных HWND очищаются при уничтожении соответствующего QWidget.
- Решение о вызове Win32 вынесено в чистую функцию `apply_protection` с тестом, воспроизводящим сценарий переиспользования HWND (и тестом, показывающим, что прежняя логика его пропускала).
- Выбор документов для базы знаний больше не использует встроенный диалог Windows (отдельное окно ОС, недоступное защите): пока защита включена, открывается диалог Qt, который защищается.
- «Открыть папку данных» запускает Проводник — окно другой программы, которое защита скрыть не может. Пока защита включена, приложение предупреждает об этом и спрашивает подтверждение (меню и Центр диагностики).
- «Скрыть окно» (горячая клавиша, трей, кнопка) убирает с экрана и открытые диалоги — настройки, историю, базу знаний, диагностику. Обычные диалоги закрываются через свои обработчики; занятое окно базы знаний временно скрывается и восстанавливается из трея, не прерывая импорт и индексацию.

**Прочее**
- Ctrl+C в консольном запуске: добавлен лёгкий таймер, дающий интерпретатору возможность обработать сигнал, пока Qt находится в C++-цикле событий. В оконных сборках без консоли таймер не создаётся.
- README переписан как руководство пользователя; история изменений перенесена сюда без потерь. Добавлены `docs/MANUAL_QA.md` (ручная проверка на Windows перед релизом) и установщик Inno Setup; его сборка теперь является обязательной проверкой CI. Деинсталлятор удаляет фактическое значение автозапуска HKCU, в том числе включённое позднее из настроек приложения.
- Тесты: +19 (кэш HWND, жизненный цикл окон, скрытие диалогов, политика выбора файлов, определение консоли).

## 2026.11.24-r23.11

- Audio and Whisper worker joins now use short polling intervals and emit a single warning when shutdown exceeds five seconds, identifying the worker that has not exited.
- The application keeps waiting for blocked workers rather than detaching, daemonizing or force-killing threads that may still be inside native audio/ML code.
- During a delayed user-requested exit, the main window is shown again with a clear status message; Windows logoff/shutdown keeps the window hidden.
- Added pure unit tests for fast shutdown, delayed shutdown reporting, continued joining and invalid timing values.

**По-русски:**

- завершение аудиопотоков стало наблюдаемым: если аудиодрайвер или Whisper задерживает остановку более пяти секунд, приложение показывает предупреждение и продолжает безопасно ожидать поток. Во время обычного выхода окно возвращается с понятным статусом вместо ощущения, что приложение зависло.

## 2026.11.24-r23.10

- Made the Ollama model-discovery QThread parentless and registry-managed. Closing the settings dialog no longer relies on a fixed 3-second wait before destroying a potentially running QThread.
- Settings updates are persisted as a candidate snapshot before becoming visible to running components; failed writes leave the previous in-memory and on-disk values intact.
- Mutable list settings are returned as defensive copies to prevent callers from changing live values outside the settings lock.
- Document import checks file size with stat() before read_bytes(), while retaining a post-read guard against files that grow during reading.
- Added regression tests for model-worker lifetime, failed settings persistence, defensive copies and oversized-file preflight.

**По-русски:**

- поток обнаружения моделей Ollama безопасно переживает закрытие окна настроек; настройки публикуются в памяти только после успешной записи JSON на диск; большие документы отклоняются до чтения содержимого в память.

## 2026.11.24-r23.9 — Transient-noise rejection

- Добавлена оценка кратковременной активности сигнала по 20-миллисекундным кадрам внутри каждого 500-мс аудиоблока.
- Одиночные щелчки, удары по микрофону и короткие импульсные шумы больше не должны запускать распознавание только из-за высокого среднего RMS блока; для начала речи требуется не менее 12% активных кадров.
- Сохранена прежняя логика вызовов PhraseSegmenter без нового параметра, а для штатного аудиопотока включена дополнительная проверка активности.
- Добавлены регрессионные тесты на короткий щелчок, короткое голосовое высказывание, пустые/некорректные аудиоданные и обратную совместимость.

**По-русски:**

- дополнительная проверка речи по 20-мс кадрам подавляет одиночные щелчки и импульсные шумы до передачи фразы в Whisper.

## 2026.11.24-r23.8 — Audio discovery isolation

- Импорт SoundCard перенесён внутрь фоновой операции: тестирование и открытие модуля настроек больше не зависят от системной библиотеки PulseAudio/Linux audio backend.
- Аудиоперечисление получает модуль SoundCard через внедрение зависимости, поэтому тесты обходятся без настоящих аудиодрайверов и native-библиотек.
- После завершения фонового потока ссылка на QThread очищается, предотвращая обращение к уже удалённому Qt-объекту при следующем обновлении.
- CI больше не устанавливает SoundCard в матричное окружение unit-тестов, где аудиооборудование не требуется; Windows package job продолжает устанавливать полный production requirements.

**По-русски:**

- SoundCard загружается только при фоновом обновлении списка аудио устройств, тесты не зависят от PulseAudio или реального аудиооборудования, жизненный цикл потока обновления очищается после завершения.

## 2026.11.24-r23.7 — Responsive audio-device discovery

- Перечисление аудиоустройств вынесено из GUI-потока в отдельный QThread: медленный или временно зависающий backend SoundCard больше не блокирует окно настроек во время обновления списка.
- Результаты применяются в GUI-потоке; текущий несохранённый выбор сохраняется даже если пользователь меняет его, пока список обновляется.
- Кнопка обновления блокируется на время запроса, повторные параллельные запросы не запускаются; при закрытии диалога поток корректно завершается.
- Добавлены изолированные тесты worker-потока для успешного обнаружения и независимых ошибок устройств вывода/микрофона.

**По-русски:**

- обнаружение аудиоустройств вынесено в фоновый поток, чтобы медленное перечисление Bluetooth/USB endpoint не подвешивало настройки. Результаты безопасно применяются в интерфейсе, несохранённый выбор сохраняется; закрытие окна не ожидает зависший backend.

## 2026.11.24-r23.6

- Audio settings now provide an explicit refresh action, so newly connected USB, Bluetooth and 2.4 GHz endpoints can be discovered without closing the settings dialog.
- Refreshing audio devices preserves the user's current unsaved selections and retains a missing pinned device as a visible placeholder.
- Duplicate endpoint names are disambiguated with a short backend-ID suffix; the full ID remains available as a tooltip.
- Added pure unit tests for stable, case-insensitive device labels and duplicate-ID collision handling.

**По-русски:**

- В настройках голоса появилась кнопка обновления устройств: подключённые после запуска USB, Bluetooth и 2.4 GHz устройства можно обнаружить без повторного открытия окна.
- Обновление сохраняет ещё не сохранённый выбор пользователя и показывает отсутствующее закреплённое устройство отдельной строкой.
- Устройства с одинаковыми именами различаются по короткому фрагменту backend ID; полный ID доступен в подсказке.

## 2026.11.24-r23.5

- The Diagnostics Center now marks a missing pinned audio device that is temporarily replaced by the system default as a warning instead of reporting the source as healthy.
- Unknown audio-source states now fail safe to a warning; only an explicit connected state is reported as healthy.
- Added deterministic coverage for device polling, default-device changes, pinned-device removal/restoration, transient enumeration failures and retry backoff.

**По-русски:**

- Центр диагностики теперь показывает предупреждение, когда выбранное устройство временно недоступно и захват работает через системное устройство по умолчанию.
- Неизвестное состояние аудиоисточника больше не отображается как полностью исправное: для статуса «в норме» требуется подтверждённое подключение.
- Добавлены тесты переключения устройств по умолчанию, отключения и возврата выбранного устройства, временной ошибки перечисления и экспоненциальных повторов.

## 2026.11.24-r23.4

- Centralized the displayed application version in the root VERSION file; source and frozen onedir builds now resolve the same metadata.
- Removed duplicate push + pull_request workflow runs and added cancellation of stale runs for the same ref.
- Windows packaging now checks the executable, PyInstaller internal directory and packaged VERSION before uploading the artifact.
- The uploaded Windows artifact name is derived from VERSION, preventing stale release labels.

**По-русски:**

- Версия приложения теперь читается из единого файла `VERSION`, поэтому заголовок окна, исходная копия и собранный Windows-пакет показывают одну и ту же версию.
- GitHub Actions запускает одну проверку на push вместо дублирования одинакового полного Windows build при одновременных событиях push и pull request; устаревшие запуски для той же ветки отменяются.
- Перед публикацией артефакта CI проверяет наличие исполняемого файла, внутренней директории PyInstaller и совпадение упакованного файла `VERSION` с исходной версией.
- Имя Windows-артефакта формируется из версии проекта, а не из вручную прописанной старой строки.

## 2026.11.24-r23.3

- Win32 capture-affinity failures now log the affected HWND and the error codes returned by both the modern exclusion mode and the black-rectangle fallback.
- The user32 bindings now use ctypes error capture correctly, making Windows API refusals diagnosable instead of reporting only a generic failure.
- CaptureGuard avoids redundant affinity calls after a window has already been protected successfully and contains ordinary Qt object-lifecycle errors inside its application-wide event filter.
- Ctrl+C in a console-launched session now schedules the same graceful shutdown path as the tray Exit command; repeated interrupts are coalesced while workers stop.
- Added regression tests for Win32 error formatting and idempotent Ctrl+C shutdown scheduling.

**По-русски:**

- Ошибки Windows Display Affinity теперь записывают HWND и коды ошибок обоих режимов защиты от захвата.
- Защита захвата не повторяет успешные вызовы Win32 API для уже защищённых окон; обработчик событий Qt изолирует ошибки времени жизни виджетов.
- Ctrl+C в консольном запуске переводит приложение в штатный путь завершения вместо выбрасывания KeyboardInterrupt внутри Qt event loop.

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

**По-русски:**

- Добавлен асинхронный «Центр диагностики»: доступность Ollama и список установленных моделей, соответствие настроенных ролей, состояния системного звука/микрофона, настройки Whisper/TTS, целостность SQLite-баз и размер локального журнала.
- Проверка выполняется в отдельном QThread и не блокирует окно чата. Закрытие окна диагностики откладывается до завершения текущей проверки.
- Отчёт можно скопировать для поддержки. Он намеренно не включает текст диалогов, распознанную речь, документы, записи памяти и содержимое логов; учётные данные, query string и fragment URL Ollama скрываются.
- История диалогов теперь подгружается страницами по 100 записей. Поиск сбрасывает страницу; кнопка «Загрузить ещё» получает следующую порцию без загрузки всей истории в память.
- Ошибка чтения истории показывается явно, а не маскируется под пустой список.
- Добавлены регрессионные тесты для пагинации, проверки SQLite только на чтение, редактирования endpoint в отчёте и отсутствия текста переписки в диагностике.

## 2026.11.24-r22 — Local Knowledge & Memory

- Добавлена доступная из меню «База знаний и долговременная память…» локальная библиотека документов и ручных заметок с отдельной вкладкой проверки поиска.
- Импортируются PDF с текстовым слоем, DOCX, TXT и Markdown. Для PDF и DOCX используются `pypdf` и `python-docx`; сканированные PDF без OCR не индексируются.
- Документы извлекаются локально, разбиваются на фрагменты с указанием страницы/абзаца/таблицы и индексируются в отдельной `knowledge.sqlite3`. Полнотекстовый поиск использует SQLite FTS5, а при отсутствии FTS5 — безопасный LIKE fallback.
- Добавлена явная долговременная память с категориями и тегами. Записи создаются, редактируются и удаляются пользователем; количество записей, добавляемых в контекст ответа, настраивается отдельно. Автоматическое сохранение личных сведений из переписки не выполняется.
- Опциональный семантический поиск через отдельную Ollama embedding-модель. Без неё полнотекстовый поиск продолжает работать; векторный индекс можно создать/обновить для выбранного документа. До отправки query embedding приложение проверяет, есть ли для выбранной модели хотя бы один индексированный документ, поэтому пустая база не вызывает лишний запрос Ollama.
- Индексация embeddings использует staging-таблицу и атомарную публикацию: при ошибке сети существующий векторный индекс сохраняется.
- Embedding-запросы используют общий сериализованный клиент Ollama, поэтому не конкурируют с генерацией, классификацией и прогревом; отмена активной задачи закрывает именно принадлежащий ей HTTP-ответ. Отмена, пока embedding ожидает общую очередь, не прерывает текущий чат. Отмена индексации проверяется между пакетами и перед атомарной публикацией; предыдущий индекс сохраняется.
- Документные фрагменты и записи памяти добавляются только в текущий запрос, не записываются обратно в историю как текст пользователя и помечаются как недоверенные справочные данные. Дополнительная политика недоверенных источников закрепляется в системном сообщении, а не только в пользовательском блоке. Перед добавлением материалов учитывается оставшийся бюджет контекста.
- Семантическое ранжирование использует top-k heap: во время сканирования загружаются только ID и векторы, а текст извлекается из SQLite лишь для лучших результатов. Скан ограничен 20 000 кандидатов и адаптивным бюджетом до 64 МиБ сырых векторов.
- Добавлена миграция схемы базы знаний (`PRAGMA user_version`) и индекса памяти с поддержкой поиска по тегам.

## r21 — Audio Intelligence Foundation

- Системный звук и микрофон теперь являются независимыми аудиоисточниками. Их можно включать отдельно или одновременно в Настройки → Голос. Микрофон по умолчанию выключен для сохранения прежнего поведения и приватности.
- Каждый источник имеет собственный поток захвата, сегментатор, ограниченную очередь и политику переподключения. Один общий Whisper worker обрабатывает фразы последовательно, чтобы не вызывать один экземпляр модели конкурентно.
- В списках устройств сохраняются backend ID SoundCard, а не только отображаемые имена. Для старых настроек сохранён переход по имени; если выбранное устройство исчезло, оно остаётся выбранным и будет повторно обнаруживаться, а захват временно использует системное устройство по умолчанию.
- У каждого источника отдельная оценка уровня и адаптивный энергетический порог. Rolling-percentile noise estimator подстраивает порог к фоновому шуму; гистерезис между началом и окончанием речи снижает дробление тихих слогов. Встроенный VAD faster-whisper продолжает выполнять дополнительную проверку.
- Пауза, закрытие приложения и смена устройства отдельно инвалидируют события соответствующего аудиопотока и отменяют ожидание Whisper между сегментами. Очереди системного звука и микрофона ограничены независимо и обрабатываются по очереди, чтобы один источник не вытеснял другой; фразы старше 30 секунд отбрасываются как неактуальные.
- Источник распознанной фразы показывается в предпросмотре. Микрофон приглушается во время TTS по умолчанию, чтобы ассистент не распознавал собственную озвучку. При использовании наушников можно явно разрешить микрофону оставаться активным; акустическое эхоподавление не реализовано.
- Добавлены независимые настройки выбора устройства, источников аудио и чувствительности. Старый `audio_device` по имени мигрирует без потери выбранного значения.

## r20 — Reliability Foundation

- Поток прогрева не запускается, если приложение завершает работу во время проверки Ollama. Создание и запуск background-потока синхронизированы, чтобы исключить гонку между стартом потока и Stop.

- Кнопка «Стоп» теперь отменяет всю операцию, включая ожидание локального классификатора, а не только поток генерации ответа.
- Запрос классификатора выполняется с отменяемым HTTP-ответом; отмена не показывается пользователю как ошибка Ollama.
- Фоновый прогрев модели прекращается при поступлении интерактивного сообщения. Прогрев, проверка Ollama, классификация и генерация сериализованы, чтобы не создавать конкурирующие запросы через общий HTTP-сеанс и к одному model runner.
- Повторная проверка Ollama проходит через очередь процессора, а не через отдельный неуправляемый поток.
- Изменение аудиоустройства больше не блокирует интерфейс ожиданием до пяти секунд. Новый AudioWorker создаётся после фактического завершения старого QThread.
- Поток Whisper не является daemon-потоком: приложение дожидается завершения текущего native inference, прежде чем владеющий QThread завершится. При этом обработчик прекращает потреблять новые сегменты сразу после сигнала остановки и не публикует результат закрываемого потока.
- Повторная генерация удаляет только ответ, относящийся к последнему сообщению пользователя. Ответ на более ранний вопрос не удаляется, если последнее сообщение ещё осталось без ответа.
- Добавлены регрессионные тесты отмены запросов и безопасной повторной генерации.

## r19 — Release preparation

- CI запускает тесты на Ubuntu и Windows с Python 3.12 и установленным PySide6, поэтому тесты MessageProcessor не должны незаметно пропускаться из-за отсутствия Qt.
- Отдельная задача Windows CI собирает приложение через PyInstaller и публикует папку `LocalAssistant` как артефакт сборки.
- `build_exe.bat` использует только `.venv`, а не случайный системный Python.
- `update.bat` выполняет безопасное fast-forward обновление `master` и не перезаписывает локальные изменения.

Проверка реальной смены Bluetooth/USB/2.4 GHz устройств, поведения трея, автозапуска и защиты экрана всё равно требует ручного запуска на Windows. Успешная сборка CI не является подтверждением работы аудиоустройств на каждом компьютере.

## r18 — Audio recovery

- Переоткрытие loopback-потока при смене системного устройства вывода.
- Отслеживание вручную выбранного устройства: если оно временно отключилось, используется системное устройство по умолчанию; когда выбранное устройство возвращается, приложение автоматически переключается обратно.
- Изменение выбора устройства в настройках применяется без перезапуска приложения.
- Очередь и находящиеся в обработке фразы помечаются поколением аудиопотока. После переключения устройства или паузы результат распознавания от старого потока отбрасывается.
- После нескольких последовательных ошибок захвата применяется экспоненциальная задержка повторного подключения от 1 до 15 секунд; после восстановления она сбрасывается.
- Остановка и пауза не пропускают старые фразы в следующий активный сеанс.
