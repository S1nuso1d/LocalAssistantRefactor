"""Settings dialog for models, audio, hotkeys, and glass appearance."""
from __future__ import annotations

import logging

import soundcard as sc
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSlider, QVBoxLayout,
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

        form = QFormLayout()
        form.setSpacing(12)
        root.addLayout(form)

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
        form.addRow("Системный звук", self.audio_device)

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

        self.hotkey = QLineEdit(str(settings.get("hotkey", "ctrl+shift+space")))
        self.hotkey.setPlaceholderText("ctrl+shift+space")
        form.addRow("Горячая клавиша", self.hotkey)

        opacity_layout = QHBoxLayout()
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(175, 250)
        self.opacity.setValue(int(settings.get("background_alpha", 224)))
        self.opacity_label = QLabel(f"{self.opacity.value()} / 255")
        self.opacity.valueChanged.connect(lambda n: self.opacity_label.setText(f"{n} / 255"))
        opacity_layout.addWidget(self.opacity, 1)
        opacity_layout.addWidget(self.opacity_label)
        form.addRow("Плотность тёмного стекла", opacity_layout)

        self.filter_speech = QCheckBox("Фильтровать фоновую речь")
        self.filter_speech.setChecked(bool(settings.get("filter_background_speech", True)))
        form.addRow("Голос", self.filter_speech)

        self.require_name = QCheckBox("Отвечать только после обращения к ассистенту")
        self.require_name.setChecked(bool(settings.get("require_assistant_name", False)))
        form.addRow("Режим обращения", self.require_name)

        self.compact_mode = QCheckBox("Запускать в компактном режиме")
        self.compact_mode.setChecked(bool(settings.get("compact_mode", False)))
        form.addRow("Окно", self.compact_mode)

        self.always_on_top = QCheckBox("Закреплять окно поверх других окон")
        self.always_on_top.setChecked(bool(settings.get("always_on_top", True)))
        form.addRow("Поведение окна", self.always_on_top)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("save")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addSpacing(8)
        root.addWidget(buttons)

    def _save(self):
        values = {
            "answer_model": self.answer_model.currentText(),
            "classifier_model": self.classifier_model.currentText(),
            "language": self.language.currentData(),
            "whisper_model": self.whisper_model.currentText(),
            "audio_device": self.audio_device.currentData(),
            "silence_threshold": float(self.threshold.value()),
            "silence_duration": float(self.silence.value()),
            "hotkey": self.hotkey.text().strip() or "ctrl+shift+space",
            "background_alpha": int(self.opacity.value()),
            "filter_background_speech": self.filter_speech.isChecked(),
            "require_assistant_name": self.require_name.isChecked(),
            "compact_mode": self.compact_mode.isChecked(),
            "always_on_top": self.always_on_top.isChecked(),
        }
        try:
            self.settings.update(values)
        except OSError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить настройки:\n{exc}")
            return
        self.accept()
