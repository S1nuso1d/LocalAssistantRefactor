"""Settings dialog: models, voice, window and assistant behaviour."""
from __future__ import annotations

import logging

import soundcard as sc
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import autostart
from .ollama_client import OllamaClient
from .settings_store import DEFAULT_SETTINGS, SettingsStore, normalize_ollama_url

logger = logging.getLogger("live_assistant.settings")

DIALOG_STYLE = """
QDialog { background: #171B29; color: #F3F5FF; font-family: 'Segoe UI'; }
QLabel { color: #E6EBFA; }
QLabel#hint { color: #8F98B2; font-size: 8pt; }
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox, QPlainTextEdit {
    background: #0D1120; color: #F5F7FF;
    border: 1px solid #414B68; border-radius: 8px; padding: 7px;
    selection-background-color: #667CE0;
}
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus,
QSpinBox:focus, QPlainTextEdit:focus { border-color: #8A9BFF; }
QComboBox QAbstractItemView {
    background: #0D1120; color: #F5F7FF; selection-background-color: #4A57A8;
    border: 1px solid #414B68;
}
QCheckBox { spacing: 8px; }
QPushButton {
    background: #3B4563; color: white; border: 1px solid #596685;
    border-radius: 8px; padding: 8px 12px;
}
QPushButton:hover { background: #46517A; }
QPushButton#save { background: #6B7CE4; border-color: #9FAEFF; }
QPushButton#save:hover { background: #7C8CF0; }
QPushButton#danger { background: transparent; border-color: #8A4A58; color: #FF9BAE; }
QPushButton#danger:hover { background: rgba(239, 95, 118, 30); }
QSlider::groove:horizontal { height: 5px; background: #414B68; border-radius: 2px; }
QSlider::handle:horizontal { background: #C5CEFF; width: 13px; margin: -5px 0; border-radius: 7px; }
QTabWidget::pane { border: 1px solid #2C3350; border-radius: 10px; top: -1px; }
QTabBar::tab {
    background: transparent; color: #AEB9D0; padding: 8px 16px;
    border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: white; border-bottom: 2px solid #8A9BFF; }
QTabBar::tab:hover { color: white; }
"""


class _ModelsWorker(QThread):
    """Query Ollama off the UI thread so the dialog opens instantly."""

    loaded = Signal(list)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        self.loaded.emit(OllamaClient.list_models(self._url))


def _form_tab() -> tuple[QWidget, QFormLayout]:
    page = QWidget()
    form = QFormLayout(page)
    form.setContentsMargins(14, 14, 14, 14)
    form.setSpacing(12)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return page, form


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None, capture_state=None):
        super().__init__(parent)
        self.settings = settings
        self._capture_state = capture_state
        self._worker: _ModelsWorker | None = None
        self.setWindowTitle("Настройки LocalAssistant")
        self.setMinimumWidth(520)
        self.setModal(True)
        self.setStyleSheet(DIALOG_STYLE)

        root = QVBoxLayout(self)
        title = QLabel("Настройки")
        title.setStyleSheet("font-size: 17pt; font-weight: 700; color: white;")
        subtitle = QLabel("Параметры сохраняются локально на этом компьютере.")
        subtitle.setObjectName("hint")
        root.addWidget(title)
        root.addWidget(subtitle)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        self._build_models_tab(tabs)
        self._build_voice_tab(tabs)
        self._build_speech_tab(tabs)
        self._build_window_tab(tabs)
        self._build_assistant_tab(tabs)
        self._build_knowledge_tab(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("save")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        reset = QPushButton("Сбросить всё")
        reset.setObjectName("danger")
        reset.clicked.connect(self._reset_all)

        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        root.addSpacing(6)
        root.addLayout(bottom)

        self._refresh_models()

    # ---- tabs -------------------------------------------------------
    def _build_models_tab(self, tabs: QTabWidget) -> None:
        page, form = _form_tab()
        s = self.settings

        self.ollama_url = QLineEdit(str(s.get("ollama_url")))
        form.addRow("Адрес Ollama", self.ollama_url)

        self.answer_model = QComboBox()
        self.answer_model.setEditable(True)
        self.answer_model.setCurrentText(str(s.get("answer_model")))
        form.addRow("Модель ответов", self.answer_model)

        self.classifier_model = QComboBox()
        self.classifier_model.setEditable(True)
        self.classifier_model.setCurrentText(str(s.get("classifier_model")))
        form.addRow("Модель классификации", self.classifier_model)

        self.embedding_model = QComboBox()
        self.embedding_model.setEditable(True)
        self.embedding_model.setCurrentText(str(s.get("embedding_model", "")))
        self.embedding_model.setToolTip(
            "Необязательная модель для семантического поиска по документам. "
            "Например: embeddinggemma или nomic-embed-text. "
            "Сначала установите её через ollama pull."
        )
        form.addRow("Модель эмбеддингов", self.embedding_model)

        status_row = QHBoxLayout()
        self.models_status = QLabel("Проверяю Ollama…")
        self.models_status.setObjectName("hint")
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self._refresh_models)
        status_row.addWidget(self.models_status, 1)
        status_row.addWidget(self.refresh_button)
        form.addRow("", status_row)

        self.warm_up = QCheckBox("Загружать модель ответов в память при запуске")
        self.warm_up.setChecked(bool(s.get("warm_up_model", True)))
        self.warm_up.setToolTip(
            "Первый ответ приходит быстрее, но модель занимает память "
            "видеокарты/ОЗУ, пока приложение открыто."
        )
        form.addRow("Прогрев", self.warm_up)

        self.num_ctx = QSpinBox()
        self.num_ctx.setRange(1024, 131072)
        self.num_ctx.setSingleStep(1024)
        self.num_ctx.setValue(int(s.get("num_ctx", 8192)))
        self.num_ctx.setToolTip(
            "Размер контекста в токенах. Ollama по умолчанию берёт 2–4 тыс., "
            "из-за чего длинные диалоги молча обрезаются."
        )
        form.addRow("Контекст (токены)", self.num_ctx)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setDecimals(2)
        self.temperature.setValue(float(s.get("temperature", 0.3)))
        form.addRow("Температура", self.temperature)

        hint = QLabel(
            "Совет: для классификатора подойдёт маленькая быстрая модель "
            "(например, 3–8B) — фильтр фраз заметно ускорится."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        tabs.addTab(page, "Модели")

    def _build_voice_tab(self, tabs: QTabWidget) -> None:
        page, form = _form_tab()
        s = self.settings

        self.language = QComboBox()
        for title, code in (
            ("Русский", "ru"), ("English", "en"), ("Українська", "uk"),
            ("Deutsch", "de"), ("Français", "fr"), ("Español", "es"),
            ("Italiano", "it"), ("Polski", "pl"), ("Türkçe", "tr"),
            ("中文", "zh"), ("日本語", "ja"), ("Автоопределение", "auto"),
        ):
            self.language.addItem(title, code)
        saved_language = str(s.get("language", "ru"))
        if self.language.findData(saved_language) < 0:
            self.language.addItem(saved_language, saved_language)
        self.language.setCurrentIndex(self.language.findData(saved_language))
        form.addRow("Язык распознавания", self.language)

        self.whisper_model = QComboBox()
        self.whisper_model.setEditable(True)
        self.whisper_model.addItems([
            "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo",
        ])
        self.whisper_model.setCurrentText(str(s.get("whisper_model", "base")))
        self.whisper_model.setToolTip(
            "Можно ввести любое имя модели faster-whisper или путь к папке. "
            "large-v3-turbo доступна в новых версиях faster-whisper."
        )
        form.addRow("Модель Whisper", self.whisper_model)

        self.whisper_device = QComboBox()
        self.whisper_device.addItem("Процессор (CPU)", "cpu")
        self.whisper_device.addItem("Видеокарта NVIDIA (CUDA)", "cuda")
        self.whisper_device.addItem("Автовыбор", "auto")
        self.whisper_device.setCurrentIndex(
            max(0, self.whisper_device.findData(str(s.get("whisper_device", "cpu"))))
        )
        form.addRow("Запуск Whisper", self.whisper_device)

        self.system_audio_enabled = QCheckBox("Захватывать системный звук")
        self.system_audio_enabled.setChecked(bool(s.get("system_audio_enabled", True)))
        form.addRow("Источник", self.system_audio_enabled)

        self.audio_device = QComboBox()
        form.addRow("Устройство вывода", self.audio_device)
        self.audio_device.setEnabled(self.system_audio_enabled.isChecked())
        self.system_audio_enabled.toggled.connect(self.audio_device.setEnabled)

        self.microphone_enabled = QCheckBox("Захватывать микрофон")
        self.microphone_enabled.setChecked(bool(s.get("microphone_enabled", False)))
        form.addRow("Дополнительный источник", self.microphone_enabled)

        self.microphone_device = QComboBox()
        form.addRow("Микрофон", self.microphone_device)
        self.microphone_device.setEnabled(self.microphone_enabled.isChecked())
        self.microphone_enabled.toggled.connect(self.microphone_device.setEnabled)

        device_actions = QHBoxLayout()
        self.refresh_audio_devices_button = QPushButton("Обновить список устройств")
        self.audio_devices_status = QLabel("Получаю список аудиоустройств…")
        self.audio_devices_status.setObjectName("hint")
        self.audio_devices_status.setWordWrap(True)
        device_actions.addWidget(self.refresh_audio_devices_button)
        device_actions.addWidget(self.audio_devices_status, 1)
        form.addRow("Обнаружение", device_actions)
        self.refresh_audio_devices_button.clicked.connect(
            lambda _checked=False: self._refresh_audio_devices(preserve_selection=True)
        )
        self._audio_devices_loaded = False
        self._refresh_audio_devices(preserve_selection=False)

        self.microphone_during_tts = QCheckBox(
            "Оставлять микрофон активным во время озвучки ассистента"
        )
        self.microphone_during_tts.setChecked(
            bool(s.get("microphone_during_tts", False))
        )
        self.microphone_during_tts.setEnabled(self.microphone_enabled.isChecked())
        self.microphone_enabled.toggled.connect(
            self.microphone_during_tts.setEnabled
        )
        self.microphone_during_tts.setToolTip(
            "По умолчанию микрофон приглушается во время TTS, чтобы ассистент "
            "не распознавал собственный голос. Включайте эту опцию только "
            "при использовании наушников. Акустического эхоподавления нет."
        )
        form.addRow("Во время озвучки", self.microphone_during_tts)

        mic_hint = QLabel(
            "Микрофон работает параллельно с системным звуком. Во время TTS "
            "он приглушён по умолчанию; его можно оставить активным для "
            "наушников. Акустическое эхоподавление пока не реализовано."
        )
        mic_hint.setObjectName("hint")
        mic_hint.setWordWrap(True)
        form.addRow("Важно", mic_hint)

        self.adaptive_vad = QCheckBox("Автоматически подстраивать чувствительность под фон")
        self.adaptive_vad.setChecked(bool(s.get("adaptive_vad_enabled", True)))
        self.adaptive_vad.setToolTip(
            "Адаптивный энергетический порог оценивает шумовой фон отдельно "
            "для каждого источника. Whisper VAD продолжает выполнять собственную проверку."
        )
        form.addRow("Обнаружение речи", self.adaptive_vad)

        self.vad_sensitivity = QDoubleSpinBox()
        self.vad_sensitivity.setRange(0.5, 2.0)
        self.vad_sensitivity.setSingleStep(0.1)
        self.vad_sensitivity.setDecimals(1)
        self.vad_sensitivity.setValue(float(s.get("vad_sensitivity", 1.0)))
        self.vad_sensitivity.setToolTip("Больше значение — выше чувствительность и ниже порог начала речи.")
        self.vad_sensitivity.setEnabled(self.adaptive_vad.isChecked())
        self.adaptive_vad.toggled.connect(self.vad_sensitivity.setEnabled)
        form.addRow("Чувствительность", self.vad_sensitivity)

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.001, 0.05)
        self.threshold.setSingleStep(0.001)
        self.threshold.setDecimals(3)
        self.threshold.setValue(float(s.get("silence_threshold", 0.008)))
        form.addRow("Базовый порог (энергия RMS)", self.threshold)
        self.threshold.setToolTip(
            "Используется как исходная точка адаптивного порога. "
            "При выключенной адаптации становится фиксированным порогом."
        )

        self.silence = QDoubleSpinBox()
        self.silence.setRange(0.4, 2.5)
        self.silence.setSingleStep(0.1)
        self.silence.setSuffix(" с")
        self.silence.setValue(float(s.get("silence_duration", 0.9)))
        form.addRow("Пауза для окончания фразы", self.silence)

        self.hotkey = QLineEdit(str(s.get("hotkey", "ctrl+shift+space")))
        self.hotkey.setPlaceholderText("ctrl+shift+space")
        form.addRow("Горячая клавиша", self.hotkey)

        self.filter_mode = QComboBox()
        self.filter_mode.addItem("Сбалансированный (точнее, чуть медленнее)", "balanced")
        self.filter_mode.addItem("Быстрый (отвечает на всё, похожее на вопрос)", "fast")
        self.filter_mode.setCurrentIndex(
            max(0, self.filter_mode.findData(str(s.get("filter_mode", "balanced"))))
        )
        form.addRow("Режим фильтра", self.filter_mode)

        self.filter_speech = QCheckBox("Фильтровать фоновую речь")
        self.filter_speech.setChecked(bool(s.get("filter_background_speech", True)))
        form.addRow("Фильтр", self.filter_speech)

        self.require_name = QCheckBox("Отвечать только после обращения к ассистенту")
        self.require_name.setChecked(bool(s.get("require_assistant_name", False)))
        form.addRow("Режим обращения", self.require_name)

        self.log_transcripts = QCheckBox("Писать распознанный текст в лог (для отладки)")
        self.log_transcripts.setChecked(bool(s.get("log_transcripts", False)))
        self.log_transcripts.setToolTip(
            "По умолчанию в лог попадает только длина фразы: системный звук "
            "может содержать закрытые разговоры."
        )
        form.addRow("Приватность лога", self.log_transcripts)

        self.assistant_names = QLineEdit(", ".join(s.get("assistant_names") or []))
        self.assistant_names.setPlaceholderText("помощник, ассистент")
        self.assistant_names.setToolTip("Имена через запятую, на которые откликается ассистент.")
        form.addRow("Имена ассистента", self.assistant_names)

        # The extended audio-source controls make this page taller than the
        # dialog on compact displays; keep every setting reachable by scrolling.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        tabs.addTab(scroll, "Голос")

    @staticmethod
    def _current_device_selection(combo: QComboBox) -> tuple[str, str]:
        index = combo.currentIndex()
        if index <= 0:
            return "", "По умолчанию"
        name = combo.itemData(index, Qt.ItemDataRole.UserRole + 1)
        return str(combo.currentData() or ""), str(name or combo.currentText())

    @staticmethod
    def _populate_audio_device_combo(
        combo: QComboBox,
        devices: list[tuple[str, str]],
        default_label: str,
        selected_id: str = "",
        selected_name: str = "По умолчанию",
    ) -> None:
        from .audio_device_labels import format_device_options

        previous_block = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(default_label, "")
            for display_name, device_id, original_name in format_device_options(devices):
                combo.addItem(display_name, device_id)
                index = combo.count() - 1
                combo.setItemData(
                    index, original_name, Qt.ItemDataRole.UserRole + 1
                )
                tooltip = f"Устройство: {original_name}"
                if device_id:
                    tooltip += f"\nID: {device_id}"
                combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)

            selected_index = combo.findData(selected_id) if selected_id else -1
            if selected_index < 0 and selected_name and selected_name != "По умолчанию":
                selected_index = next(
                    (
                        index for index in range(1, combo.count())
                        if str(
                            combo.itemData(index, Qt.ItemDataRole.UserRole + 1) or ""
                        ).casefold() == selected_name.casefold()
                    ),
                    -1,
                )

            if selected_index < 0 and (
                selected_id or (selected_name and selected_name != "По умолчанию")
            ):
                missing_name = (
                    selected_name
                    if selected_name and selected_name != "По умолчанию"
                    else f"Устройство …{selected_id[-8:]}"
                )
                combo.addItem(f"{missing_name} (не найдено)", selected_id)
                selected_index = combo.count() - 1
                combo.setItemData(
                    selected_index, missing_name, Qt.ItemDataRole.UserRole + 1
                )
                combo.setItemData(
                    selected_index,
                    "Сохранённое устройство пока не обнаружено. "
                    "Подключите его и обновите список.",
                    Qt.ItemDataRole.ToolTipRole,
                )
            combo.setCurrentIndex(max(0, selected_index))
        finally:
            combo.blockSignals(previous_block)

    def _refresh_audio_devices(self, preserve_selection: bool = True) -> None:
        """Refresh output and microphone lists without losing unsaved choices."""
        if preserve_selection and self._audio_devices_loaded:
            speaker_id, speaker_name = self._current_device_selection(self.audio_device)
            microphone_id, microphone_name = self._current_device_selection(
                self.microphone_device
            )
        else:
            speaker_id = str(self.settings.get("audio_device_id", "") or "")
            speaker_name = str(self.settings.get("audio_device", "По умолчанию"))
            microphone_id = str(self.settings.get("microphone_device_id", "") or "")
            microphone_name = str(
                self.settings.get("microphone_device", "По умолчанию")
            )

        errors: list[str] = []
        try:
            speakers = [
                (str(getattr(item, "id", "") or "").strip(), str(item.name).strip())
                for item in sc.all_speakers()
                if str(getattr(item, "name", "") or "").strip()
            ]
        except Exception as exc:
            logger.exception("Не удалось обновить список устройств вывода.")
            speakers = []
            errors.append(f"вывод: {type(exc).__name__}")

        try:
            microphones = [
                (str(getattr(item, "id", "") or "").strip(), str(item.name).strip())
                for item in sc.all_microphones(include_loopback=False)
                if str(getattr(item, "name", "") or "").strip()
            ]
        except Exception as exc:
            logger.exception("Не удалось обновить список микрофонов.")
            microphones = []
            errors.append(f"микрофон: {type(exc).__name__}")

        self._populate_audio_device_combo(
            self.audio_device,
            speakers,
            "Устройство по умолчанию",
            speaker_id,
            speaker_name,
        )
        self._populate_audio_device_combo(
            self.microphone_device,
            microphones,
            "Микрофон по умолчанию",
            microphone_id,
            microphone_name,
        )
        self._audio_devices_loaded = True

        summary = (
            f"Вывод: {len(speakers)} · микрофоны: {len(microphones)}"
        )
        if errors:
            summary += " · ошибки получения: " + ", ".join(errors)
        self.audio_devices_status.setText(summary)

    def _build_speech_tab(self, tabs: QTabWidget) -> None:
        page, form = _form_tab()
        s = self.settings

        self.tts_mode = QComboBox()
        self.tts_mode.addItem("Выключена", "off")
        self.tts_mode.addItem("Только ответы на голосовые вопросы", "voice")
        self.tts_mode.addItem("Все ответы", "always")
        self.tts_mode.setCurrentIndex(
            max(0, self.tts_mode.findData(str(s.get("tts_mode", "off"))))
        )
        form.addRow("Озвучка ответов", self.tts_mode)

        self.tts_rate = QSpinBox()
        self.tts_rate.setRange(-10, 10)
        self.tts_rate.setValue(int(s.get("tts_rate", 0)))
        self.tts_rate.setToolTip("−10 — медленно, 0 — обычно, 10 — быстро")
        form.addRow("Скорость речи", self.tts_rate)

        self.tts_volume = QSpinBox()
        self.tts_volume.setRange(0, 100)
        self.tts_volume.setSuffix(" %")
        self.tts_volume.setValue(int(s.get("tts_volume", 100)))
        form.addRow("Громкость", self.tts_volume)

        self.tts_voice = QLineEdit(str(s.get("tts_voice", "")))
        self.tts_voice.setPlaceholderText("по умолчанию — голос языка распознавания")
        form.addRow("Имя голоса Windows", self.tts_voice)

        hint = QLabel(
            "Используются встроенные голоса Windows (офлайн). Русский голос "
            "ставится в Параметры → Время и язык → Речь. Пока ассистент "
            "говорит, захват системного звука приглушается, чтобы он не "
            "слышал сам себя; прервать речь можно клавишей Esc."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        tabs.addTab(page, "Озвучка")

    def _build_window_tab(self, tabs: QTabWidget) -> None:
        page, form = _form_tab()
        s = self.settings

        opacity_layout = QHBoxLayout()
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(175, 250)
        self.opacity.setValue(int(s.get("background_alpha", 224)))
        self.opacity_label = QLabel(f"{self.opacity.value()} / 255")
        self.opacity.valueChanged.connect(lambda n: self.opacity_label.setText(f"{n} / 255"))
        opacity_layout.addWidget(self.opacity, 1)
        opacity_layout.addWidget(self.opacity_label)
        form.addRow("Плотность тёмного стекла", opacity_layout)

        self.compact_mode = QCheckBox("Запускать в компактном режиме")
        self.compact_mode.setChecked(bool(s.get("compact_mode", False)))
        form.addRow("Размер", self.compact_mode)

        self.always_on_top = QCheckBox("Закреплять окно поверх других окон")
        self.always_on_top.setChecked(bool(s.get("always_on_top", True)))
        form.addRow("Поведение", self.always_on_top)

        self.hide_capture = QCheckBox("Скрывать окно от записи и демонстрации экрана")
        self.hide_capture.setChecked(bool(s.get("hide_from_capture", True)))
        self.hide_capture.setToolTip(
            "Скрываются все окна приложения: главное, меню, настройки, подсказки. "
            "Работает для программной записи и демонстрации (OBS, Zoom, Teams, "
            "Discord, скриншоты Windows). Камеру, направленную на монитор, и "
            "аппаратную карту захвата это не скрывает."
        )
        form.addRow("Приватность", self.hide_capture)

        self.capture_status = QLabel(
            self._capture_state() if self._capture_state else "—"
        )
        self.capture_status.setObjectName("hint")
        self.capture_status.setWordWrap(True)
        form.addRow("Статус защиты", self.capture_status)

        privacy_note = QLabel(
            "Значок в трее и панель задач принадлежат Windows и видны при "
            "демонстрации всего экрана. Чтобы они не попадали в кадр, "
            "показывайте отдельное окно/вкладку или перетащите значок в "
            "скрытую область (стрелка ^), либо отключите показ значка ниже."
        )
        privacy_note.setObjectName("hint")
        privacy_note.setWordWrap(True)
        form.addRow("", privacy_note)

        self.close_to_tray = QCheckBox("Кнопка закрытия сворачивает в трей, а не выходит")
        self.close_to_tray.setChecked(bool(s.get("close_to_tray", True)))
        form.addRow("Трей", self.close_to_tray)

        self.start_in_tray = QCheckBox("Запускать свёрнутым в трей")
        self.start_in_tray.setChecked(bool(s.get("start_in_tray", False)))
        form.addRow("", self.start_in_tray)

        self.tray_icon_visible = QCheckBox("Показывать значок в трее")
        self.tray_icon_visible.setChecked(bool(s.get("tray_icon_visible", True)))
        self.tray_icon_visible.setToolTip(
            "Если выключить, окно можно вернуть только горячей клавишей. "
            "Если горячая клавиша не зарегистрировалась, значок всё равно "
            "будет показан, чтобы вы не потеряли окно."
        )
        form.addRow("", self.tray_icon_visible)

        self.window_hotkey = QLineEdit(str(s.get("toggle_window_hotkey", "ctrl+alt+h")))
        self.window_hotkey.setPlaceholderText("ctrl+alt+h")
        form.addRow("Показать/скрыть окно", self.window_hotkey)

        self.autostart = QCheckBox("Запускать вместе с Windows (в трее)")
        self.autostart.setChecked(autostart.is_enabled())
        form.addRow("Автозапуск", self.autostart)
        tabs.addTab(page, "Окно")

    def _build_assistant_tab(self, tabs: QTabWidget) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        s = self.settings

        layout.addWidget(QLabel("Системный промпт"))
        self.system_prompt = QPlainTextEdit(str(s.get("system_prompt")))
        self.system_prompt.setMinimumHeight(150)
        layout.addWidget(self.system_prompt, 1)

        reset_prompt = QPushButton("Вернуть промпт по умолчанию")
        reset_prompt.clicked.connect(
            lambda: self.system_prompt.setPlainText(DEFAULT_SETTINGS["system_prompt"])
        )
        layout.addWidget(reset_prompt, 0, Qt.AlignLeft)

        row = QHBoxLayout()
        row.addWidget(QLabel("Сообщений истории в контексте"))
        self.max_history = QSpinBox()
        self.max_history.setRange(2, 100)
        self.max_history.setValue(int(s.get("max_history_messages", 16)))
        row.addStretch(1)
        row.addWidget(self.max_history)
        layout.addLayout(row)
        tabs.addTab(page, "Ассистент")

    def _build_knowledge_tab(self, tabs: QTabWidget) -> None:
        page, form = _form_tab()
        s = self.settings

        self.knowledge_enabled = QCheckBox("Использовать импортированные документы при ответах")
        self.knowledge_enabled.setChecked(bool(s.get("knowledge_enabled", True)))
        form.addRow("База знаний", self.knowledge_enabled)

        self.memory_enabled = QCheckBox("Использовать записи долговременной памяти")
        self.memory_enabled.setChecked(bool(s.get("memory_enabled", True)))
        form.addRow("Память", self.memory_enabled)

        self.semantic_search_enabled = QCheckBox("Включить семантический поиск по embeddings")
        self.semantic_search_enabled.setChecked(bool(s.get("semantic_search_enabled", False)))
        self.semantic_search_enabled.setToolTip(
            "Требует установленную модель эмбеддингов, выбранную во вкладке «Модели». "
            "При выключении используется локальный полнотекстовый поиск SQLite FTS5."
        )
        form.addRow("Поиск", self.semantic_search_enabled)

        self.knowledge_max_chunks = QSpinBox()
        self.knowledge_max_chunks.setRange(1, 10)
        self.knowledge_max_chunks.setValue(int(s.get("knowledge_max_chunks", 5)))
        form.addRow("Максимум фрагментов документов", self.knowledge_max_chunks)

        self.knowledge_max_memories = QSpinBox()
        self.knowledge_max_memories.setRange(0, 10)
        self.knowledge_max_memories.setValue(int(s.get("knowledge_max_memories", 3)))
        self.knowledge_max_memories.setToolTip("0 — не добавлять записи памяти в контекст ответов.")
        form.addRow("Максимум записей памяти", self.knowledge_max_memories)

        self.knowledge_max_context_chars = QSpinBox()
        self.knowledge_max_context_chars.setRange(1000, 16000)
        self.knowledge_max_context_chars.setSingleStep(500)
        self.knowledge_max_context_chars.setValue(int(s.get("knowledge_max_context_chars", 7000)))
        self.knowledge_max_context_chars.setSuffix(" симв.")
        form.addRow("Бюджет справочного контекста", self.knowledge_max_context_chars)

        note = QLabel(
            "Документы обрабатываются и хранятся локально в knowledge.sqlite3. "
            "При включённом семантическом поиске их фрагменты и поисковые запросы "
            "отправляются на настроенный адрес Ollama для вычисления векторов. "
            "Если адрес Ollama удалённый, данные будут передаваться этому серверу. "
            "Полнотекстовый поиск работает без embedding-модели."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        form.addRow("Приватность", note)
        tabs.addTab(page, "Знания")

    # ---- models -----------------------------------------------------
    def _refresh_models(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.models_status.setText("Проверяю Ollama…")
        self.refresh_button.setEnabled(False)
        url = normalize_ollama_url(self.ollama_url.text()) or str(
            DEFAULT_SETTINGS["ollama_url"]
        )
        self._worker = _ModelsWorker(url, self)
        self._worker.loaded.connect(self._on_models_loaded)
        self._worker.start()

    def _on_models_loaded(self, models: list) -> None:
        self.refresh_button.setEnabled(True)
        for combo in (self.answer_model, self.classifier_model, self.embedding_model):
            current = combo.currentText()
            combo.clear()
            combo.addItems(models)
            combo.setCurrentText(current)
        if models:
            self.models_status.setText(f"Ollama отвечает · моделей: {len(models)}")
        else:
            self.models_status.setText(
                "Ollama не отвечает или моделей нет — имя можно ввести вручную."
            )

    def done(self, result: int) -> None:
        if self._worker is not None:
            self._worker.wait(3000)
        super().done(result)

    # ---- actions ----------------------------------------------------
    def _reset_all(self) -> None:
        answer = QMessageBox.question(
            self, "Сбросить настройки",
            "Вернуть все настройки к значениям по умолчанию?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.settings.reset()
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сбросить настройки:\n{exc}")
            return
        self.accept()

    @staticmethod
    def _hotkey_is_valid(hotkey: str) -> bool:
        try:
            import keyboard
        except ImportError:
            return True
        try:
            keyboard.parse_hotkey(hotkey)
            return True
        except Exception:
            return False

    def _save(self):
        hotkey = self.hotkey.text().strip() or "ctrl+shift+space"
        if not self._hotkey_is_valid(hotkey):
            QMessageBox.warning(
                self, "Горячая клавиша",
                f"Не удалось распознать сочетание «{hotkey}».\n"
                "Пример: ctrl+shift+space",
            )
            return
        window_hotkey = self.window_hotkey.text().strip() or "ctrl+alt+h"
        if not self._hotkey_is_valid(window_hotkey):
            QMessageBox.warning(
                self, "Горячая клавиша окна",
                f"Не удалось распознать сочетание «{window_hotkey}».\n"
                "Пример: ctrl+alt+h",
            )
            return
        if window_hotkey.lower() == hotkey.lower():
            QMessageBox.warning(
                self, "Горячие клавиши",
                "Для паузы прослушивания и для показа окна нужны разные сочетания.",
            )
            return
        names = [n.strip() for n in self.assistant_names.text().split(",") if n.strip()]
        audio_index = self.audio_device.currentIndex()
        microphone_index = self.microphone_device.currentIndex()
        audio_device_name = (
            self.audio_device.itemData(audio_index, Qt.ItemDataRole.UserRole + 1)
            or self.audio_device.currentText()
        ) if audio_index > 0 else "По умолчанию"
        microphone_device_name = (
            self.microphone_device.itemData(microphone_index, Qt.ItemDataRole.UserRole + 1)
            or self.microphone_device.currentText()
        ) if microphone_index > 0 else "По умолчанию"
        values = {
            "ollama_url": self.ollama_url.text().strip(),
            "answer_model": self.answer_model.currentText().strip(),
            "classifier_model": self.classifier_model.currentText().strip(),
            "embedding_model": self.embedding_model.currentText().strip(),
            "semantic_search_enabled": self.semantic_search_enabled.isChecked(),
            "knowledge_enabled": self.knowledge_enabled.isChecked(),
            "memory_enabled": self.memory_enabled.isChecked(),
            "knowledge_max_chunks": int(self.knowledge_max_chunks.value()),
            "knowledge_max_memories": int(self.knowledge_max_memories.value()),
            "knowledge_max_context_chars": int(self.knowledge_max_context_chars.value()),
            "warm_up_model": self.warm_up.isChecked(),
            "num_ctx": int(self.num_ctx.value()),
            "temperature": float(self.temperature.value()),
            "language": self.language.currentData(),
            "whisper_model": self.whisper_model.currentText().strip(),
            "whisper_device": self.whisper_device.currentData(),
            "audio_device": audio_device_name,
            "audio_device_id": str(self.audio_device.currentData() or ""),
            "system_audio_enabled": self.system_audio_enabled.isChecked(),
            "microphone_enabled": self.microphone_enabled.isChecked(),
            "microphone_during_tts": self.microphone_during_tts.isChecked(),
            "microphone_device": microphone_device_name,
            "microphone_device_id": str(self.microphone_device.currentData() or ""),
            "adaptive_vad_enabled": self.adaptive_vad.isChecked(),
            "vad_sensitivity": float(self.vad_sensitivity.value()),
            "silence_threshold": float(self.threshold.value()),
            "silence_duration": float(self.silence.value()),
            "hotkey": hotkey,
            "filter_background_speech": self.filter_speech.isChecked(),
            "filter_mode": self.filter_mode.currentData(),
            "log_transcripts": self.log_transcripts.isChecked(),
            "tts_mode": self.tts_mode.currentData(),
            "tts_rate": int(self.tts_rate.value()),
            "tts_volume": int(self.tts_volume.value()),
            "tts_voice": self.tts_voice.text().strip(),
            "require_assistant_name": self.require_name.isChecked(),
            "assistant_names": names,
            "background_alpha": int(self.opacity.value()),
            "compact_mode": self.compact_mode.isChecked(),
            "always_on_top": self.always_on_top.isChecked(),
            "hide_from_capture": self.hide_capture.isChecked(),
            "close_to_tray": self.close_to_tray.isChecked(),
            "start_in_tray": self.start_in_tray.isChecked(),
            "tray_icon_visible": self.tray_icon_visible.isChecked(),
            "toggle_window_hotkey": window_hotkey,
            "system_prompt": self.system_prompt.toPlainText().strip(),
            "max_history_messages": int(self.max_history.value()),
        }
        try:
            self.settings.update(values)
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить настройки:\n{exc}")
            return
        if self.autostart.isChecked() != autostart.is_enabled():
            if not autostart.set_enabled(self.autostart.isChecked()):
                QMessageBox.warning(
                    self, "Автозапуск",
                    "Не удалось изменить автозапуск. Остальные настройки сохранены.",
                )
        self.accept()
