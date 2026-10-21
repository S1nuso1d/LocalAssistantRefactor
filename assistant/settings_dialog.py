"""Settings dialog for models, audio, hotkeys, and glass appearance."""
from __future__ import annotations

import logging

import soundcard as sc
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSlider, QVBoxLayout, QFileDialog, QTabWidget, QWidget,
)

from .ollama_client import OllamaClient
from .settings_store import SettingsStore

logger = logging.getLogger("live_assistant.settings")


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Настройки Live Assistant")
        self.setMinimumWidth(460)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background: #171B29; color: #F3F5FF; font-family: 'Segoe UI'; }
            QLabel { color: #E6EBFA; }
            QComboBox, QLineEdit, QDoubleSpinBox {
                background: #0D1120; color: #F5F7FF;
                border: 1px solid #414B68; border-radius: 8px; padding: 7px;
            }
            QCheckBox { spacing: 8px; }
            QPushButton {
                background: #3B4563; color: white; border: 1px solid #596685;
                border-radius: 8px; padding: 8px 12px;
            }
            QPushButton#save { background: #6B7CE4; border-color: #9FAEFF; }
            QSlider::groove:horizontal { height: 5px; background: #414B68; border-radius: 2px; }
            QSlider::handle:horizontal { background: #C5CEFF; width: 13px; margin: -5px 0; border-radius: 7px; }
        """)

        root = QVBoxLayout(self)
        title = QLabel("Настройки")
        title.setStyleSheet("font-size: 17pt; font-weight: 700; color: white;")
        subtitle = QLabel("Параметры сохраняются локально на этом компьютере.")
        subtitle.setStyleSheet("color: #AEB9D0;")
        root.addWidget(title)
        root.addWidget(subtitle)

        tabs = QTabWidget()
        root.addWidget(tabs)

        model_page = QWidget()
        voice_page = QWidget()
        appearance_page = QWidget()
        model_form = QFormLayout(model_page)
        voice_form = QFormLayout(voice_page)
        appearance_form = QFormLayout(appearance_page)
        model_form.setSpacing(12)
        voice_form.setSpacing(12)
        appearance_form.setSpacing(12)
        tabs.addTab(model_page, "Модель")
        tabs.addTab(voice_page, "Голос")
        tabs.addTab(appearance_page, "Оформление")

        form = model_form

        models = OllamaClient.list_models(str(settings.get("ollama_url")))
        for fallback in ("qwen3-coder:30b", "qwen2.5:14b", "qwen3-vl:latest", "gemma:2b"):
            if fallback not in models:
                models.append(fallback)
        models = sorted(set(models + [
            str(settings.get("answer_model", "qwen3-coder:30b")),
            str(settings.get("classifier_model", "qwen3-coder:30b")),
        ]))

        self.answer_model = QComboBox()
        self.answer_model.addItems(models)
        self.answer_model.setCurrentText(str(settings.get("answer_model", "qwen3-coder:30b")))
        form.addRow("Модель ответов", self.answer_model)

        self.classifier_model = QComboBox()
        self.classifier_model.addItems(models)
        self.classifier_model.setCurrentText(str(settings.get("classifier_model", "qwen3-coder:30b")))
        form.addRow("Модель классификации", self.classifier_model)

        self.assistant_mode = QComboBox()
        self.assistant_mode.addItem("Помощник-программист", "coding")
        self.assistant_mode.addItem("Обучение с подсказками", "tutor")
        self.assistant_mode.addItem("Универсальный / автоопределение", "general")
        mode_index = self.assistant_mode.findData(str(settings.get("assistant_mode", "coding")))
        self.assistant_mode.setCurrentIndex(max(0, mode_index))
        form.addRow("Профиль помощника", self.assistant_mode)

        project_row = QHBoxLayout()
        self.project_root = QLineEdit(str(settings.get("project_root", "") or ""))
        self.project_root.setReadOnly(True)
        self.project_root.setPlaceholderText("Папка не выбрана")
        browse_project = QPushButton("Выбрать…")
        browse_project.clicked.connect(self._browse_project)
        clear_project = QPushButton("Сбросить")
        clear_project.clicked.connect(lambda: self.project_root.clear())
        project_row.addWidget(self.project_root, 1)
        project_row.addWidget(browse_project)
        project_row.addWidget(clear_project)
        form.addRow("Папка проекта для чтения", project_row)
        self.project_context_enabled = QCheckBox(
            "Передавать содержимое выбранного проекта модели"
        )
        self.project_context_enabled.setChecked(
            bool(settings.get("project_context_enabled", True))
        )
        form.addRow("", self.project_context_enabled)

        form = voice_form
        self.language = QComboBox()
        self.language.addItem("Русский", "ru")
        self.language.addItem("English", "en")
        self.language.addItem("Автоопределение", "auto")
        lang = str(settings.get("language", "ru"))
        index = self.language.findData(lang)
        self.language.setCurrentIndex(max(0, index))
        form.addRow("Язык распознавания", self.language)

        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model.setCurrentText(str(settings.get("whisper_model", "base")))
        form.addRow("Модель Whisper", self.whisper_model)

        self.audio_device = QComboBox()
        self.audio_device.addItem("По умолчанию", "По умолчанию")
        try:
            speakers = sc.all_speakers()
        except Exception:
            logger.exception("Не удалось получить список устройств вывода.")
            speakers = []
        for speaker in speakers:
            self.audio_device.addItem(speaker.name, speaker.name)
        wanted_device = str(settings.get("audio_device", "По умолчанию"))
        idx = self.audio_device.findData(wanted_device)
        self.audio_device.setCurrentIndex(max(0, idx))
        form.addRow("Устройство вывода / loopback", self.audio_device)

        self.audio_source = QComboBox()
        self.audio_source.addItem("Системный звук (loopback)", "loopback")
        self.audio_source.addItem("Физический микрофон", "microphone")
        source_index = self.audio_source.findData(str(settings.get("audio_source", "loopback")))
        self.audio_source.setCurrentIndex(max(0, source_index))
        form.addRow("Источник распознавания", self.audio_source)

        self.microphone_device = QComboBox()
        self.microphone_device.addItem("По умолчанию", "По умолчанию")
        try:
            microphones = sc.all_microphones(include_loopback=False)
        except Exception:
            logger.exception("Не удалось получить список микрофонов.")
            microphones = []
        for microphone in microphones:
            self.microphone_device.addItem(microphone.name, microphone.name)
        microphone_index = self.microphone_device.findData(
            str(settings.get("microphone_device", "По умолчанию"))
        )
        self.microphone_device.setCurrentIndex(max(0, microphone_index))
        form.addRow("Физический микрофон", self.microphone_device)

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.001, 0.05)
        self.threshold.setSingleStep(0.001)
        self.threshold.setDecimals(3)
        self.threshold.setValue(float(settings.get("silence_threshold", 0.008)))
        form.addRow("Порог чувствительности", self.threshold)

        self.silence = QDoubleSpinBox()
        self.silence.setRange(0.4, 2.5)
        self.silence.setSingleStep(0.1)
        self.silence.setSuffix(" с")
        self.silence.setValue(float(settings.get("silence_duration", 0.9)))
        form.addRow("Пауза для окончания фразы", self.silence)

        self.max_phrase_duration = QDoubleSpinBox()
        self.max_phrase_duration.setRange(5.0, 120.0)
        self.max_phrase_duration.setSingleStep(5.0)
        self.max_phrase_duration.setSuffix(" с")
        self.max_phrase_duration.setValue(float(settings.get("max_phrase_duration", 40.0)))
        form.addRow("Максимальная длина фразы", self.max_phrase_duration)

        self.hotkey = QLineEdit(str(settings.get("hotkey", "ctrl+shift+space")))
        self.hotkey.setPlaceholderText("ctrl+shift+space")
        form.addRow("Горячая клавиша прослушивания", self.hotkey)

        self.show_hotkey = QLineEdit(
            str(settings.get("show_hotkey", "ctrl+shift+a"))
        )
        self.show_hotkey.setPlaceholderText("ctrl+shift+a")
        form.addRow("Глобальный вызов помощника", self.show_hotkey)

        self.activation_mode = QComboBox()
        self.activation_mode.addItem("Постоянное прослушивание", "continuous")
        self.activation_mode.addItem("Включать/выключать клавишей", "toggle")
        self.activation_mode.addItem("Удерживать клавишу для речи", "push_to_talk")
        activation_index = self.activation_mode.findData(
            str(settings.get("activation_mode", "continuous"))
        )
        self.activation_mode.setCurrentIndex(max(0, activation_index))
        form.addRow("Режим активации", self.activation_mode)

        form = appearance_form
        opacity_layout = QHBoxLayout()
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(175, 250)
        self.opacity.setValue(int(settings.get("background_alpha", 224)))
        self.opacity_label = QLabel(f"{self.opacity.value()} / 255")
        self.opacity.valueChanged.connect(lambda n: self.opacity_label.setText(f"{n} / 255"))
        opacity_layout.addWidget(self.opacity, 1)
        opacity_layout.addWidget(self.opacity_label)
        form.addRow("Плотность тёмного стекла", opacity_layout)

        saturation_layout = QHBoxLayout()
        self.saturation = QSlider(Qt.Horizontal)
        self.saturation.setRange(0, 100)
        self.saturation.setValue(
            max(0, min(100, int(settings.get("background_saturation", 65))))
        )
        self.saturation_label = QLabel(f"{self.saturation.value()}%")
        self.saturation.valueChanged.connect(
            lambda n: self.saturation_label.setText(f"{n}%")
        )
        saturation_layout.addWidget(self.saturation, 1)
        saturation_layout.addWidget(self.saturation_label)
        form.addRow("Насыщенность оттенка фона", saturation_layout)

        self.filter_speech = QCheckBox("Фильтровать фоновую речь")
        self.filter_speech.setChecked(bool(settings.get("filter_background_speech", True)))
        voice_form.addRow("Фильтр фоновой речи", self.filter_speech)

        self.require_name = QCheckBox("Отвечать только после обращения к ассистенту")
        self.require_name.setChecked(bool(settings.get("require_assistant_name", False)))
        voice_form.addRow("Режим обращения", self.require_name)

        self.speech_output = QCheckBox("Автоматически озвучивать ответы голосом Windows")
        self.speech_output.setChecked(bool(settings.get("speech_output_enabled", False)))
        voice_form.addRow("Озвучивание ответов", self.speech_output)

        self.compact_mode = QCheckBox("Запускать в компактном режиме")
        self.compact_mode.setChecked(bool(settings.get("compact_mode", False)))
        form.addRow("Окно", self.compact_mode)

        self.always_on_top = QCheckBox("Закреплять окно поверх других окон")
        self.always_on_top.setChecked(bool(settings.get("always_on_top", True)))
        form.addRow("Поведение окна", self.always_on_top)

        self.close_to_tray = QCheckBox(
            "При закрытии окна продолжать работу в системном трее"
        )
        self.close_to_tray.setChecked(bool(settings.get("close_to_tray", False)))
        form.addRow("Системный трей", self.close_to_tray)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("save")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addSpacing(8)
        root.addWidget(buttons)

    def _browse_project(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку проекта для чтения",
            self.project_root.text().strip() or "",
        )
        if selected:
            self.project_root.setText(selected)

    def _save(self):
        values = {
            "answer_model": self.answer_model.currentText(),
            "assistant_mode": self.assistant_mode.currentData(),
            "project_root": self.project_root.text().strip(),
            "project_context_enabled": self.project_context_enabled.isChecked(),
            "classifier_model": self.classifier_model.currentText(),
            "language": self.language.currentData(),
            "whisper_model": self.whisper_model.currentText(),
            "audio_device": self.audio_device.currentData(),
            "audio_source": self.audio_source.currentData(),
            "microphone_device": self.microphone_device.currentData(),
            "activation_mode": self.activation_mode.currentData(),
            "max_phrase_duration": float(self.max_phrase_duration.value()),
            "speech_output_enabled": self.speech_output.isChecked(),
            "silence_threshold": float(self.threshold.value()),
            "silence_duration": float(self.silence.value()),
            "hotkey": self.hotkey.text().strip() or "ctrl+shift+space",
            "show_hotkey": self.show_hotkey.text().strip() or "ctrl+shift+a",
            "background_alpha": int(self.opacity.value()),
            "background_saturation": int(self.saturation.value()),
            "filter_background_speech": self.filter_speech.isChecked(),
            "require_assistant_name": self.require_name.isChecked(),
            "compact_mode": self.compact_mode.isChecked(),
            "always_on_top": self.always_on_top.isChecked(),
            "close_to_tray": self.close_to_tray.isChecked(),
        }
        try:
            self.settings.update(values)
        except OSError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить настройки:\n{exc}")
            return
        self.accept()
