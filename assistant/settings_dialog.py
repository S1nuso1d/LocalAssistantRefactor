"""Settings dialog: models, voice, window and assistant behaviour."""
from __future__ import annotations

import logging

import soundcard as sc
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .ollama_client import OllamaClient
from .settings_store import DEFAULT_SETTINGS, SettingsStore

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
    def __init__(self, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._worker: _ModelsWorker | None = None
        self.setWindowTitle("Настройки Live Assistant")
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
        self._build_window_tab(tabs)
        self._build_assistant_tab(tabs)

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

        status_row = QHBoxLayout()
        self.models_status = QLabel("Проверяю Ollama…")
        self.models_status.setObjectName("hint")
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self._refresh_models)
        status_row.addWidget(self.models_status, 1)
        status_row.addWidget(self.refresh_button)
        form.addRow("", status_row)

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
        self.language.addItem("Русский", "ru")
        self.language.addItem("English", "en")
        self.language.addItem("Автоопределение", "auto")
        self.language.setCurrentIndex(max(0, self.language.findData(str(s.get("language", "ru")))))
        form.addRow("Язык распознавания", self.language)

        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model.setCurrentText(str(s.get("whisper_model", "base")))
        form.addRow("Модель Whisper", self.whisper_model)

        self.whisper_device = QComboBox()
        self.whisper_device.addItem("Процессор (CPU)", "cpu")
        self.whisper_device.addItem("Видеокарта NVIDIA (CUDA)", "cuda")
        self.whisper_device.addItem("Автовыбор", "auto")
        self.whisper_device.setCurrentIndex(
            max(0, self.whisper_device.findData(str(s.get("whisper_device", "cpu"))))
        )
        form.addRow("Запуск Whisper", self.whisper_device)

        self.audio_device = QComboBox()
        self.audio_device.addItem("По умолчанию", "По умолчанию")
        try:
            speakers = sc.all_speakers()
        except Exception:
            logger.exception("Не удалось получить список устройств вывода.")
            speakers = []
        for speaker in speakers:
            self.audio_device.addItem(speaker.name, speaker.name)
        wanted = str(s.get("audio_device", "По умолчанию"))
        self.audio_device.setCurrentIndex(max(0, self.audio_device.findData(wanted)))
        form.addRow("Системный звук", self.audio_device)

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.001, 0.05)
        self.threshold.setSingleStep(0.001)
        self.threshold.setDecimals(3)
        self.threshold.setValue(float(s.get("silence_threshold", 0.008)))
        form.addRow("Порог чувствительности", self.threshold)

        self.silence = QDoubleSpinBox()
        self.silence.setRange(0.4, 2.5)
        self.silence.setSingleStep(0.1)
        self.silence.setSuffix(" с")
        self.silence.setValue(float(s.get("silence_duration", 0.9)))
        form.addRow("Пауза для окончания фразы", self.silence)

        self.hotkey = QLineEdit(str(s.get("hotkey", "ctrl+shift+space")))
        self.hotkey.setPlaceholderText("ctrl+shift+space")
        form.addRow("Горячая клавиша", self.hotkey)

        self.filter_speech = QCheckBox("Фильтровать фоновую речь")
        self.filter_speech.setChecked(bool(s.get("filter_background_speech", True)))
        form.addRow("Фильтр", self.filter_speech)

        self.require_name = QCheckBox("Отвечать только после обращения к ассистенту")
        self.require_name.setChecked(bool(s.get("require_assistant_name", False)))
        form.addRow("Режим обращения", self.require_name)

        self.assistant_names = QLineEdit(", ".join(s.get("assistant_names") or []))
        self.assistant_names.setPlaceholderText("помощник, ассистент")
        self.assistant_names.setToolTip("Имена через запятую, на которые откликается ассистент.")
        form.addRow("Имена ассистента", self.assistant_names)
        tabs.addTab(page, "Голос")

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
            "Best-effort: работает не во всех программах. Выключите, если нужно "
            "сделать скриншот окна."
        )
        form.addRow("Приватность", self.hide_capture)
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

    # ---- models -----------------------------------------------------
    def _refresh_models(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.models_status.setText("Проверяю Ollama…")
        self.refresh_button.setEnabled(False)
        self._worker = _ModelsWorker(self.ollama_url.text().strip(), self)
        self._worker.loaded.connect(self._on_models_loaded)
        self._worker.start()

    def _on_models_loaded(self, models: list) -> None:
        self.refresh_button.setEnabled(True)
        for combo in (self.answer_model, self.classifier_model):
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
        names = [n.strip() for n in self.assistant_names.text().split(",") if n.strip()]
        values = {
            "ollama_url": self.ollama_url.text().strip(),
            "answer_model": self.answer_model.currentText().strip(),
            "classifier_model": self.classifier_model.currentText().strip(),
            "num_ctx": int(self.num_ctx.value()),
            "temperature": float(self.temperature.value()),
            "language": self.language.currentData(),
            "whisper_model": self.whisper_model.currentText(),
            "whisper_device": self.whisper_device.currentData(),
            "audio_device": self.audio_device.currentData(),
            "silence_threshold": float(self.threshold.value()),
            "silence_duration": float(self.silence.value()),
            "hotkey": hotkey,
            "filter_background_speech": self.filter_speech.isChecked(),
            "require_assistant_name": self.require_name.isChecked(),
            "assistant_names": names,
            "background_alpha": int(self.opacity.value()),
            "compact_mode": self.compact_mode.isChecked(),
            "always_on_top": self.always_on_top.isChecked(),
            "hide_from_capture": self.hide_capture.isChecked(),
            "system_prompt": self.system_prompt.toPlainText().strip(),
            "max_history_messages": int(self.max_history.value()),
        }
        try:
            self.settings.update(values)
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить настройки:\n{exc}")
            return
        self.accept()
