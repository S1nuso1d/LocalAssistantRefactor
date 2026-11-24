"""Modern dark-glass chat UI for LocalAssistant."""
from __future__ import annotations

import ctypes
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

import keyboard
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal, QPointF, QPoint,
    QSize, QUrl
)
from PySide6.QtGui import (
    QDesktopServices, QFontMetrics, QKeySequence, QPainter, QColor, QPen,
    QPainterPath, QShortcut
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QGraphicsOpacityEffect,
    QMenu, QTextBrowser, QInputDialog, QMessageBox, QSizeGrip,
    QPlainTextEdit, QFileDialog, QSystemTrayIcon
)

from .audio_worker import AudioWorker
from .config import DEFAULT_BACKGROUND_ALPHA
from . import winintegration
from .capture_guard import CaptureGuard
from .conversation_store import ConversationStore
from .diagnostics_dialog import DiagnosticsDialog
from .history_dialog import HistoryDialog
from .phrase_log import PhraseLog
from .phrase_log_dialog import PhraseLogDialog
from .processor import MessageProcessor
from .settings_dialog import SettingsDialog
from .settings_store import SettingsStore, app_data_dir
from .tray import TrayController
from .tts import SpeechEngine
from .version import APP_VERSION
from .text_utils import (
    extract_code_blocks, format_time, safe_filename, split_thinking,
    stream_interval_ms,
)


BUILD_VERSION = f"1.0.0 · {APP_VERSION}"
TTS_MODE_LABELS = {
    "off": "выключена",
    "voice": "только ответы на голосовые вопросы",
    "always": "все ответы",
}
TTS_MODE_ORDER = ("off", "voice", "always")
WELCOME_TEXT = (
    "Привет! Я готов принимать голосовые обращения из выбранных аудиоисточников. "
    "Можешь задать вопрос голосом или написать его внизу. Окно скрывается в трей: "
    "вернуть его можно кликом по значку или горячей клавишей."
)
logger = logging.getLogger("live_assistant.ui")


STYLE = """
QWidget {
    font-family: "Segoe UI";
    font-size: 10pt;
    color: #E8EAF5;
}

QWidget#mainWindow {
    background-color: rgba(17, 19, 29, 238);
    border: 1px solid rgba(174, 183, 224, 65);
    border-radius: 22px;
}

QFrame#topBar {
    background: transparent;
    border: none;
}

QLabel#title {
    color: #F7F8FF;
    font-size: 13pt;
    font-weight: 650;
    background: transparent;
}

QLabel#status {
    color: #A8B0C7;
    font-size: 8pt;
    background: transparent;
}

QLabel#tiny {
    color: #8F98B2;
    font-size: 8pt;
    background: transparent;
}

QLabel#transcript {
    color: #A8B0C7;
    background-color: rgba(255, 255, 255, 5);
    border: 1px solid rgba(185, 195, 235, 24);
    border-radius: 10px;
    padding: 7px 10px;
    font-size: 8pt;
}

QPushButton {
    color: #E5E9F7;
    background-color: rgba(255, 255, 255, 7);
    border: 1px solid rgba(185, 195, 235, 30);
    border-radius: 11px;
    padding: 8px 12px;
}

QPushButton:hover {
    background-color: rgba(151, 164, 224, 20);
    border-color: rgba(185, 198, 255, 90);
}

QPushButton:pressed {
    background-color: rgba(126, 141, 220, 35);
}

QPushButton#iconButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 0;
}

QPushButton#iconButton:hover {
    background-color: rgba(175, 187, 240, 16);
    border-color: rgba(175, 187, 240, 24);
}

QPushButton#iconButton:pressed {
    background-color: rgba(175, 187, 240, 28);
}

QPushButton#brandButton {
    background-color: rgba(125, 139, 240, 25);
    border: 1px solid rgba(162, 174, 255, 45);
    border-radius: 13px;
}

QPushButton#brandButton:hover {
    background-color: rgba(125, 139, 240, 42);
}

QPushButton#sendButton {
    background-color: #DCE4FF;
    border: 1px solid rgba(255, 255, 255, 180);
    border-radius: 16px;
}

QPushButton#sendButton:hover {
    background-color: #FFFFFF;
}

QPushButton#sendButton:pressed {
    background-color: #BBCBFF;
}

QPushButton#cancelButton {
    background-color: rgba(239, 95, 118, 15);
    border: 1px solid rgba(255, 128, 150, 60);
    border-radius: 10px;
}

QPushButton#cancelButton:hover {
    background-color: rgba(239, 95, 118, 32);
    border-color: rgba(255, 128, 150, 120);
}

QPushButton#listenButton[active="true"] {
    background-color: rgba(100, 225, 177, 12);
    border-color: rgba(100, 225, 177, 60);
}

QPushButton#listenButton[active="false"] {
    background-color: rgba(240, 195, 110, 10);
    border-color: rgba(240, 195, 110, 55);
}

QLineEdit {
    color: #F4F5FF;
    background-color: rgba(9, 11, 18, 170);
    border: 1px solid rgba(171, 183, 231, 45);
    border-radius: 16px;
    padding: 12px 15px;
    selection-background-color: #667CE0;
}

QLineEdit:focus {
    border-color: rgba(150, 165, 255, 145);
    background-color: rgba(10, 12, 21, 220);
}

QLineEdit::placeholder {
    color: #777F98;
}

QPlainTextEdit#composer {
    color: #F4F5FF;
    background-color: rgba(9, 11, 18, 170);
    border: 1px solid rgba(171, 183, 231, 45);
    border-radius: 16px;
    padding: 12px 15px;
    selection-background-color: #667CE0;
}

QPlainTextEdit#composer:focus {
    border-color: rgba(150, 165, 255, 145);
    background-color: rgba(10, 12, 21, 220);
}

QToolTip {
    background-color: #1B1F31;
    color: #E8EAF5;
    border: 1px solid rgba(180, 191, 235, 60);
    border-radius: 6px;
    padding: 5px 8px;
}

QScrollArea {
    background: transparent;
    border: none;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

QScrollBar:vertical {
    background: transparent;
    width: 5px;
    margin: 3px 0;
}

QScrollBar::handle:vertical {
    background: rgba(164, 177, 225, 70);
    border-radius: 2px;
    min-height: 26px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(164, 177, 225, 125);
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QMenu {
    background-color: rgba(25, 28, 42, 250);
    color: #ECEFFD;
    border: 1px solid rgba(180, 191, 235, 55);
    border-radius: 12px;
    padding: 5px;
}

QMenu::item {
    padding: 9px 20px;
    border-radius: 7px;
}

QMenu::item:selected {
    background-color: rgba(127, 143, 225, 48);
}

QMenu::separator {
    height: 1px;
    background: rgba(180, 191, 235, 30);
    margin: 5px 9px;
}

QSizeGrip {
    background: transparent;
}
"""


def enable_acrylic(hwnd: int) -> bool:
    """Best-effort Windows Acrylic behind the tinted Qt surface."""
    if sys.platform != "win32":
        return False

    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        policy = ACCENT_POLICY(4, 2, 0xD91C1710, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA(
            19,
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )

        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
        ]
        fn.restype = ctypes.c_int

        result = bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))

        if result:
            logger.info("Windows Acrylic activated.")

        return result

    except Exception:
        logger.exception("Windows Acrylic unavailable; using alpha background.")
        return False


class IconButton(QPushButton):
    """Minimal vector icon button drawn without external image assets."""

    ICONS = {
        "sparkle",
        "more",
        "minimize",
        "close",
        "stop",
        "send",
        "pause",
        "play",
        "copy",
        "retry",
        "plus",
        "search",
        "settings",
        "history",
        "activity",
    }

    def __init__(
        self,
        icon: str,
        parent: QWidget | None = None,
        *,
        size: int = 36,
        icon_size: int = 18,
        variant: str = "default",
        tooltip: str = "",
    ):
        super().__init__(parent)

        self.icon_name = icon
        self.icon_size = icon_size
        self.variant = variant
        self._icon_color = QColor("#C8D0E8")
        self._hover_color = QColor("#F5F6FF")

        self.setObjectName(
            "sendButton" if variant == "send"
            else "cancelButton" if variant == "danger"
            else "brandButton" if variant == "brand"
            else "iconButton"
        )
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        if tooltip:
            self.setToolTip(tooltip)

    def _draw_icon(self, painter: QPainter) -> None:
        s = float(self.icon_size)
        cx = self.width() / 2
        cy = self.height() / 2

        painter.save()
        painter.translate(cx - s / 2, cy - s / 2)
        painter.setRenderHint(QPainter.Antialiasing, True)

        color = self._icon_color
        if self.variant == "send":
            color = QColor("#1D2743")
        elif self.variant == "danger":
            color = QColor("#FF9BAE")

        pen = QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        path = QPainterPath()

        if self.icon_name == "sparkle":
            path.moveTo(s * 0.50, s * 0.08)
            path.lineTo(s * 0.63, s * 0.38)
            path.lineTo(s * 0.92, s * 0.50)
            path.lineTo(s * 0.63, s * 0.62)
            path.lineTo(s * 0.50, s * 0.92)
            path.lineTo(s * 0.37, s * 0.62)
            path.lineTo(s * 0.08, s * 0.50)
            path.lineTo(s * 0.37, s * 0.38)
            path.closeSubpath()
            painter.drawPath(path)

        elif self.icon_name == "more":
            painter.setBrush(color)
            for x in (0.20, 0.50, 0.80):
                painter.drawEllipse(QPointF(s * x, s * 0.50), 1.15, 1.15)

        elif self.icon_name == "minimize":
            path.moveTo(s * 0.20, s * 0.65)
            path.lineTo(s * 0.80, s * 0.65)
            painter.drawPath(path)

        elif self.icon_name == "close":
            path.moveTo(s * 0.25, s * 0.25)
            path.lineTo(s * 0.75, s * 0.75)
            path.moveTo(s * 0.75, s * 0.25)
            path.lineTo(s * 0.25, s * 0.75)
            painter.drawPath(path)

        elif self.icon_name == "stop":
            painter.setBrush(color)
            painter.drawRoundedRect(
                int(s * 0.29), int(s * 0.29),
                int(s * 0.42), int(s * 0.42), 2, 2,
            )

        elif self.icon_name == "send":
            path.moveTo(s * 0.50, s * 0.15)
            path.lineTo(s * 0.50, s * 0.80)
            path.moveTo(s * 0.23, s * 0.43)
            path.lineTo(s * 0.50, s * 0.16)
            path.lineTo(s * 0.77, s * 0.43)
            painter.drawPath(path)

        elif self.icon_name == "pause":
            path.moveTo(s * 0.36, s * 0.25)
            path.lineTo(s * 0.36, s * 0.75)
            path.moveTo(s * 0.64, s * 0.25)
            path.lineTo(s * 0.64, s * 0.75)
            painter.drawPath(path)

        elif self.icon_name == "play":
            path.moveTo(s * 0.36, s * 0.23)
            path.lineTo(s * 0.75, s * 0.50)
            path.lineTo(s * 0.36, s * 0.77)
            path.closeSubpath()
            painter.drawPath(path)

        elif self.icon_name == "copy":
            painter.drawRoundedRect(
                int(s * 0.34), int(s * 0.17),
                int(s * 0.46), int(s * 0.49), 2, 2,
            )
            painter.drawRoundedRect(
                int(s * 0.19), int(s * 0.34),
                int(s * 0.46), int(s * 0.49), 2, 2,
            )

        elif self.icon_name == "retry":
            path.moveTo(s * 0.76, s * 0.36)
            path.lineTo(s * 0.76, s * 0.12)
            path.lineTo(s * 0.54, s * 0.15)
            painter.drawPath(path)
            painter.drawArc(
                int(s * 0.19), int(s * 0.18),
                int(s * 0.61), int(s * 0.64),
                35 * 16, 285 * 16,
            )

        elif self.icon_name == "plus":
            path.moveTo(s * 0.50, s * 0.22)
            path.lineTo(s * 0.50, s * 0.78)
            path.moveTo(s * 0.22, s * 0.50)
            path.lineTo(s * 0.78, s * 0.50)
            painter.drawPath(path)

        elif self.icon_name == "search":
            painter.drawEllipse(
                QPointF(s * 0.43, s * 0.42), s * 0.23, s * 0.23
            )
            path.moveTo(s * 0.60, s * 0.60)
            path.lineTo(s * 0.82, s * 0.82)
            painter.drawPath(path)

        elif self.icon_name == "history":
            painter.drawEllipse(
                QPointF(s * 0.50, s * 0.50), s * 0.36, s * 0.36
            )
            path.moveTo(s * 0.50, s * 0.28)
            path.lineTo(s * 0.50, s * 0.50)
            path.lineTo(s * 0.66, s * 0.60)
            painter.drawPath(path)

        elif self.icon_name == "activity":
            path.moveTo(s * 0.10, s * 0.53)
            path.lineTo(s * 0.29, s * 0.53)
            path.lineTo(s * 0.40, s * 0.25)
            path.lineTo(s * 0.57, s * 0.77)
            path.lineTo(s * 0.70, s * 0.43)
            path.lineTo(s * 0.90, s * 0.43)
            painter.drawPath(path)

        elif self.icon_name == "settings":
            painter.drawEllipse(
                QPointF(s * 0.50, s * 0.50), s * 0.15, s * 0.15
            )
            for angle in range(0, 360, 60):
                painter.save()
                painter.translate(s * 0.50, s * 0.50)
                painter.rotate(angle)
                path = QPainterPath()
                path.moveTo(0, -s * 0.23)
                path.lineTo(0, -s * 0.39)
                painter.drawPath(path)
                painter.restore()

        painter.restore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        self._icon_color = (
            self._hover_color if self.underMouse()
            else QColor("#1D2743") if self.variant == "send"
            else QColor("#FF9BAE") if self.variant == "danger"
            else QColor("#C8D0E8")
        )
        self._draw_icon(painter)
        painter.end()


class DraggableHeader(QFrame):
    def __init__(self, owner: QWidget):
        super().__init__(owner)
        self.owner = owner
        self._offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._offset = (
                event.globalPosition().toPoint()
                - self.owner.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self._offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._offset = None
        super().mouseReleaseEvent(event)


class WaveformWidget(QWidget):
    """Subtle animated waveform. Audio levels come from the loopback worker."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = [0.06] * 24
        self._phase = 0
        self._active = True
        self.setFixedSize(66, 24)

        self.timer = QTimer(self)
        self.timer.setInterval(75)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def set_active(self, active: bool):
        self._active = bool(active)
        self.update()

    def set_level(self, value: float):
        self._levels.append(max(0.02, min(1.0, float(value))))
        self._levels = self._levels[-24:]
        self.update()

    def _tick(self):
        self._phase = (self._phase + 1) % 1000
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        spacing = 2.0
        bar_width = max(1.5, (width - spacing * 23) / 24)

        for i, level in enumerate(self._levels):
            idle = 0.08 + 0.06 * (
                0.5 + 0.5 * math.sin(self._phase / 4 + i * 0.7)
            )
            if not self._active:
                level, idle = 0.0, 0.04
            bar_height = max(3.0, (level * 0.72 + idle) * height)
            x = i * (bar_width + spacing)
            y = (height - bar_height) / 2

            painter.setPen(Qt.NoPen)
            if self._active:
                color = QColor(151, 166, 255, 190 if level > 0.18 else 105)
            else:
                color = QColor(240, 195, 110, 55)
            painter.setBrush(color)
            painter.drawRoundedRect(
                int(x), int(y), int(bar_width + 1),
                int(bar_height), 2, 2,
            )

        painter.end()


class ElidedLabel(QLabel):
    """Single-line label that truncates with an ellipsis instead of
    forcing the window (and header) wider."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        self.setToolTip(text)
        self._apply()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(30, super().minimumSizeHint().height())

    def _apply(self) -> None:
        width = max(10, self.width())
        super().setText(
            self.fontMetrics().elidedText(self._full, Qt.ElideRight, width)
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()


class ComposerEdit(QPlainTextEdit):
    """Multi-line input: Enter sends, Shift+Enter inserts a newline.

    Grows with its content up to MAX_HEIGHT, then scrolls.
    """

    submitted = Signal()
    MIN_HEIGHT = 46
    MAX_HEIGHT = 150

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setPlaceholderText(
            "Напишите сообщение…  (Enter — отправить, Shift+Enter — новая строка)"
        )
        self.setFixedHeight(self.MIN_HEIGHT)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def _fit(self, *_args) -> None:
        # For QPlainTextEdit the document height is measured in lines.
        lines = max(1, int(self.document().documentLayout().documentSize().height()))
        height = lines * self.fontMetrics().lineSpacing() + 36
        self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, height)))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatBubble(QFrame):
    """Responsive message bubble with Markdown rendering."""

    def __init__(
        self,
        text: str,
        role: str,
        max_width: int,
        timestamp: float | None = None,
    ):
        super().__init__()
        self.role = role
        self.raw_text = text
        self.text = text  # visible text without reasoning; used by "Copy"
        self.timestamp = timestamp
        self.max_bubble_width = max(220, int(max_width))
        self._margins = (14, 11, 14, 12)
        self.body: QTextBrowser

        self._build()
        self.set_bubble_width(self.max_bubble_width)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.fade_animation = QPropertyAnimation(
            self.opacity_effect, b"opacity", self
        )
        self.fade_animation.setDuration(200)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.fade_animation.start()

    def _display_markdown(self, raw: str) -> str:
        """Hide <think> blocks of reasoning models; keep a short note."""
        if self.role != "assistant":
            self.text = raw
            return raw or " "

        thinking, answer, still_thinking = split_thinking(raw)
        self.text = answer
        if still_thinking and not answer:
            return "*💭 Думаю…*"
        if thinking and answer:
            words = len(thinking.split())
            return f"*💭 Рассуждение скрыто · {words} сл.*\n\n{answer}"
        return answer or " "

    def _build(self):
        is_user = self.role == "user"
        is_system = self.role == "system"

        if is_user:
            frame_bg = (
                "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                "stop:0 rgba(124, 140, 255, 245), "
                "stop:1 rgba(92, 106, 232, 245))"
            )
            border = "rgba(205, 214, 255, 120)"
            text_color = "#FFFFFF"
            role_color = "#E3E8FF"
            link_color = "#FFFFFF"
            code_bg = "rgba(12, 16, 52, 150)"
            role_text = "Вы"
            self._margins = (14, 10, 14, 12)
        elif is_system:
            frame_bg = "rgba(113, 43, 58, 180)"
            border = "rgba(255, 147, 164, 100)"
            text_color = "#FFF1F4"
            role_color = "#FFC0CC"
            link_color = "#FFD6DD"
            code_bg = "rgba(60, 10, 22, 160)"
            role_text = "Система"
            self._margins = (13, 10, 13, 11)
        else:
            frame_bg = "rgba(255, 255, 255, 10)"
            border = "rgba(190, 200, 245, 34)"
            text_color = "#E9ECF8"
            role_color = "#9EADEB"
            link_color = "#B7C5FF"
            code_bg = "rgba(7, 9, 16, 210)"
            role_text = "LocalAssistant"
            self._margins = (14, 10, 14, 11)

        clock = format_time(self.timestamp)
        if clock:
            role_text = f"{role_text}  ·  {clock}"

        self.setObjectName("chatBubble")
        self.setStyleSheet(f"""
            QFrame#chatBubble {{
                background-color: {frame_bg};
                border: 1px solid {border};
                border-radius: 15px;
            }}
            QLabel#bubbleRole {{
                color: {role_color};
                font-size: 8pt;
                font-weight: 600;
                background: transparent;
                border: none;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*self._margins)
        layout.setSpacing(5)

        self.base_role_text = role_text
        self.role_label = QLabel(role_text)
        self.role_label.setObjectName("bubbleRole")
        self.role_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.body = QTextBrowser()
        self.body.setReadOnly(True)
        self.body.setOpenLinks(False)
        self.body.setOpenExternalLinks(False)
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.body.setAttribute(Qt.WA_TranslucentBackground, True)
        self.body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; "
            f"color: {text_color}; padding: 0; }}"
        )

        self.body.document().setDocumentMargin(1)
        self.body.document().setDefaultStyleSheet(f"""
            body {{ color: {text_color}; }}
            pre, code {{
                font-family: Consolas, 'Cascadia Code', monospace;
            }}
            pre {{
                background-color: {code_bg};
                padding: 8px;
            }}
            a {{ color: {link_color}; }}
            table {{ border-collapse: collapse; }}
            td, th {{
                border: 1px solid #58617D;
                padding: 4px 6px;
            }}
            blockquote {{
                border-left: 3px solid #8596ED;
                margin-left: 4px;
                padding-left: 8px;
            }}
        """)

        self.body.setMarkdown(self._display_markdown(self.raw_text))
        layout.addWidget(self.role_label)
        layout.addWidget(self.body)

        # One-click "copy" chips for fenced code blocks (assistant only).
        self._code_blocks: list[tuple[str, str]] = []
        self.code_row = QWidget()
        self.code_row.setStyleSheet("background: transparent;")
        self.code_layout = QHBoxLayout(self.code_row)
        self.code_layout.setContentsMargins(0, 2, 0, 0)
        self.code_layout.setSpacing(6)
        self.code_row.hide()
        layout.addWidget(self.code_row)
        self._update_code_row()

    def _update_code_row(self):
        if self.role != "assistant":
            return
        blocks = extract_code_blocks(self.text)
        if len(blocks) == len(self._code_blocks):
            self._code_blocks = blocks  # contents may have changed; chips lazy-read
            return
        self._code_blocks = blocks

        while self.code_layout.count():
            item = self.code_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        for index, (language, _code) in enumerate(blocks[:6]):
            label = language or "код"
            if len(blocks) > 1:
                label += f" #{index + 1}"
            button = QPushButton(f"⧉ {label}")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip("Копировать блок кода")
            button.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,12);"
                " color: #C5CEFF; border: 1px solid rgba(190,200,245,45);"
                " border-radius: 9px; padding: 3px 10px; font-size: 8pt; }"
                "QPushButton:hover { background: rgba(140,155,240,55);"
                " color: white; }"
            )
            button.clicked.connect(
                lambda _checked=False, i=index, b=button, t=label:
                self._copy_code_block(i, b, t)
            )
            self.code_layout.addWidget(button)

        self.code_layout.addStretch(1)
        self.code_row.setVisible(bool(blocks))

    def _copy_code_block(self, index: int, button: QPushButton, label: str):
        if index >= len(self._code_blocks):
            return
        QApplication.clipboard().setText(self._code_blocks[index][1])
        button.setText("✓ Скопировано")

        def restore():
            try:
                button.setText(f"⧉ {label}")
            except RuntimeError:
                pass  # chip was rebuilt or the bubble was deleted

        QTimer.singleShot(1300, restore)

    def set_markdown(self, text: str):
        self.raw_text = text
        self.body.setMarkdown(self._display_markdown(text))
        self._update_code_row()
        self._size_body()

    def set_metadata(self, model: str, duration_ms: int):
        seconds = max(0, duration_ms) / 1000.0
        self.role_label.setText(
            f"{self.base_role_text}  ·  {model}  ·  {seconds:.1f} с"
        )
        self.role_label.setToolTip(
            f"Модель: {model}\nВремя генерации: {seconds:.2f} с"
        )

    def set_bubble_width(self, max_width: int):
        max_width = max(220, int(max_width))
        left, right = self._margins[0], self._margins[2]

        if self.role == "user":
            metrics = QFontMetrics(self.body.font())
            lines = self.text.splitlines() or [self.text]
            longest_line = max(lines, key=len) if lines else ""
            estimated = (
                metrics.horizontalAdvance(longest_line) + left + right + 28
            )
            desired = min(max_width, max(180, estimated))
        else:
            desired = max_width

        self.setFixedWidth(desired)

        body_width = max(180, desired - left - right - 4)
        self.body.setFixedWidth(body_width)
        self.body.document().setTextWidth(max(100, body_width - 8))
        self._size_body()

    def _size_body(self):
        try:
            self.body.document().setTextWidth(max(100, self.body.width() - 8))
            height = (
                int(self.body.document().documentLayout().documentSize().height())
                + 12
            )
            self.body.setFixedHeight(max(26, height))
            self.adjustSize()
        except RuntimeError:
            pass


class TypingBubble(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QFrame {
                background: rgba(32, 36, 53, 210);
                border: 1px solid rgba(185, 200, 240, 55);
                border-radius: 12px;
            }
            QLabel {
                color: #BFC9EB;
                background: transparent;
                border: none;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 6, 11, 6)
        layout.setSpacing(9)

        self.label = QLabel("Генерирую ответ")
        self.dots = QLabel("·  ·  ·")
        layout.addWidget(self.label)
        layout.addWidget(self.dots)

        self._step = 0
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._animate)
        self.timer.start()

    def _animate(self):
        self._step = (self._step + 1) % 3
        self.dots.setText(("●  ·  ·", "·  ●  ·", "·  ·  ●")[self._step])

    def stop(self):
        self.timer.stop()


class AssistantWindow(QWidget):
    hotkey_toggle_signal = Signal()
    speaking_changed = Signal(bool)
    window_toggle_signal = Signal()

    def __init__(self):
        super().__init__()

        self.settings_store = SettingsStore()
        self.conversation_store = ConversationStore()
        # Protect every window of the app (dialogs, menus, tooltips too) from
        # screen capture; installed before anything is shown.
        self.capture_guard = CaptureGuard(
            bool(self.settings_store.get("hide_from_capture", True)), self
        )
        QApplication.instance().installEventFilter(self.capture_guard)
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._audio_source_status = {
            "system": "Ожидает запуска",
            "microphone": "Отключён",
        }
        self._quitting = False
        self._window_hotkey_ok = False
        self.tray: TrayController | None = None
        # State callbacks arrive from the TTS thread; the Qt signal marshals
        # them onto the GUI thread.
        self.phrase_log = PhraseLog()
        self._phrase_dialog: PhraseLogDialog | None = None
        self.speech = SpeechEngine(
            self.settings_store,
            on_state=lambda speaking: self.speaking_changed.emit(speaking),
        )

        self.audio: AudioWorker | None = None
        self.processor: MessageProcessor | None = None

        self._acrylic_enabled = False
        self._typing_bubble: TypingBubble | None = None
        self._typing_row: QWidget | None = None
        self._streaming_bubble: ChatBubble | None = None
        self._streaming_container: QWidget | None = None
        self._streaming_text = ""
        self._pending_stream_text = ""

        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(45)
        self._stream_timer.timeout.connect(self._flush_stream_text)

        self._close_pending = False
        self._hotkey_registered = False
        self._drag_position = None
        self._generating = False
        self._task_busy = False
        self._audio_restart_in_progress = False
        self._audio_restart_worker: AudioWorker | None = None
        self._audio_restart_was_listening = True
        self._stick_to_bottom = True
        self._geometry_restored = False

        self._bubbles: list[ChatBubble] = []
        self._rows: list[QWidget] = []
        self._message_entries: list[dict[str, Any]] = []

        self._current_conversation_id: int | None = None
        self._current_conversation_title = "Новый диалог"

        self.setObjectName("mainWindow")
        self.setWindowTitle("LocalAssistant")
        self._set_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(570, 730)
        self.setMinimumSize(430, 540)
        self._restore_geometry()

        self._build_ui()
        self._apply_background_alpha()

        self.audio = AudioWorker(self.settings_store)
        self.processor = MessageProcessor(
            self.settings_store, self.conversation_store
        )

        self._connect_signals()
        self._install_shortcuts()
        self._create_tray()
        self.audio.start()
        self.processor.start()
        self._register_hotkey()

        self._apply_compact_mode(
            bool(self.settings_store.get("compact_mode", False)),
            initial=True,
        )
        self.input.setFocus()

    def _set_window_flags(self):
        flags = Qt.Window | Qt.FramelessWindowHint

        if self._tray_available:
            # Tool windows get no taskbar button and are absent from Alt+Tab;
            # the tray icon is the way back to the window.
            flags |= Qt.Tool

        if self.settings_store.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint

        self.setWindowFlags(flags)
        # Changing flags may recreate the native window, which loses Acrylic.
        self._acrylic_enabled = False

    def _restore_geometry(self):
        geometry = self.settings_store.get("window_geometry")
        if not geometry or self.settings_store.get("compact_mode", False):
            return
        try:
            x, y, w, h = (int(v) for v in geometry)
        except (TypeError, ValueError):
            return
        # Ignore positions that would put the window off every screen.
        if QApplication.screenAt(QPoint(x + 40, y + 40)) is None:
            return
        self.resize(max(430, w), max(540, h))
        self.move(x, y)
        self._geometry_restored = True

    def _save_geometry(self):
        if self.settings_store.get("compact_mode", False):
            return
        geo = self.frameGeometry()
        try:
            self.settings_store.update({
                "window_geometry": [geo.x(), geo.y(), self.width(), self.height()]
            })
        except OSError:
            logger.exception("Не удалось сохранить положение окна.")

    def _install_shortcuts(self):
        bindings = (
            ("Ctrl+N", lambda: self.processor.new_conversation()),
            ("Ctrl+K", self._open_history),
            ("Ctrl+,", self._open_settings),
            ("Ctrl+L", lambda: self.input.setFocus()),
            ("Ctrl+E", self._export_conversation),
            ("Esc", self._escape_pressed),
        )
        self._shortcuts = []
        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def _escape_pressed(self):
        if self._task_busy or self.speech.speaking:
            self._cancel_generation()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 13, 16, 14)
        root.setSpacing(11)

        header = DraggableHeader(self)
        header.setObjectName("topBar")

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(1, 1, 1, 5)
        header_layout.setSpacing(10)

        self.logo_button = IconButton(
            "sparkle",
            size=38,
            icon_size=20,
            variant="brand",
            tooltip="LocalAssistant",
        )
        self.logo_button.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(4)

        title = QLabel("LocalAssistant")
        title.setObjectName("title")
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #A8E8C9; font-size: 7pt;")

        self.status_label = ElidedLabel("Запуск…")
        self.status_label.setObjectName("status")

        self.waveform = WaveformWidget()

        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label, 1)

        title_stack.addWidget(title)
        title_stack.addLayout(status_row)

        self.cancel_button = IconButton(
            "stop",
            size=32,
            icon_size=16,
            variant="danger",
            tooltip="Остановить текущую обработку и озвучку (Esc)",
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_generation)

        self.new_button = IconButton(
            "plus",
            size=32,
            icon_size=18,
            tooltip="Новый диалог (Ctrl+N)",
        )
        self.new_button.clicked.connect(
            lambda: self.processor.new_conversation()
        )

        self.history_button = IconButton(
            "history",
            size=32,
            icon_size=18,
            tooltip="История диалогов (Ctrl+K)",
        )
        self.history_button.clicked.connect(self._open_history)

        self.diagnostics_button = IconButton(
            "activity",
            size=32,
            icon_size=17,
            tooltip="Центр диагностики",
        )
        self.diagnostics_button.clicked.connect(self._open_diagnostics)

        self.more_button = IconButton(
            "more",
            size=32,
            icon_size=18,
            tooltip="Меню",
        )
        self.more_button.clicked.connect(self._show_menu)

        self.minimize_button = IconButton(
            "minimize",
            size=32,
            icon_size=16,
            tooltip="Скрыть в трей (Ctrl+Alt+H)",
        )
        self.minimize_button.clicked.connect(self._minimize_clicked)

        self.close_button = IconButton(
            "close",
            size=32,
            icon_size=16,
            tooltip="Закрыть окно (приложение останется в трее)",
        )
        self.close_button.clicked.connect(self.close)

        header_layout.addWidget(self.logo_button)
        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(self.new_button)
        header_layout.addWidget(self.history_button)
        header_layout.addWidget(self.diagnostics_button)
        header_layout.addWidget(self.more_button)
        header_layout.addWidget(self.minimize_button)
        header_layout.addWidget(self.close_button)

        root.addWidget(header)

        self.transcript_preview = QLabel(
            "Последняя распознанная фраза появится здесь"
        )
        self.transcript_preview.setObjectName("transcript")
        self.transcript_preview.setWordWrap(True)
        self.transcript_preview.setMaximumHeight(42)
        self.transcript_preview.setToolTip(
            "Последняя фраза, распознанная Whisper"
        )
        root.addWidget(self.transcript_preview)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.chat_content = QWidget()
        self.chat_content.setStyleSheet("background: transparent;")

        self.chat_layout = QVBoxLayout(self.chat_content)
        self.chat_layout.setContentsMargins(5, 8, 5, 9)
        self.chat_layout.setSpacing(14)
        self.chat_layout.addStretch(1)

        self.scroll.setWidget(self.chat_content)
        root.addWidget(self.scroll, 1)

        composer = QHBoxLayout()
        composer.setSpacing(8)

        self.input = ComposerEdit()
        self.input.submitted.connect(self._send_typed_message)

        self.send_button = IconButton(
            "send",
            size=46,
            icon_size=20,
            variant="send",
            tooltip="Отправить сообщение",
        )
        self.send_button.clicked.connect(self._send_typed_message)

        self.cancel_button.setFixedSize(46, 46)
        self.cancel_button.setIconSize(QSize(20, 20))
        self.cancel_button.icon_size = 20

        composer.addWidget(self.input, 1, Qt.AlignBottom)
        composer.addWidget(self.cancel_button, 0, Qt.AlignBottom)
        composer.addWidget(self.send_button, 0, Qt.AlignBottom)
        root.addLayout(composer)

        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.listen_button = IconButton(
            "pause",
            size=34,
            icon_size=16,
            tooltip="Приостановить системное прослушивание",
        )
        self.listen_button.setObjectName("listenButton")
        self.listen_button.setProperty("active", "true")
        self.listen_button.clicked.connect(self.toggle_listening)

        self.hotkey_label = QLabel(self._hotkey_hint())
        self.hotkey_label.setObjectName("tiny")
        self.hotkey_label.setToolTip("Горячая клавиша паузы прослушивания")

        footer.addWidget(self.listen_button)
        footer.addWidget(self.waveform)
        footer.addWidget(self.hotkey_label)
        footer.addStretch(1)

        self.resize_grip = QSizeGrip(self)
        self.resize_grip.setFixedSize(13, 13)
        footer.addWidget(
            self.resize_grip, 0, Qt.AlignBottom | Qt.AlignRight
        )

        root.addLayout(footer)

        self.setStyleSheet(STYLE)

        self.scroll.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed
        )

        self._add_bubble(WELCOME_TEXT, "assistant")

    def _hotkey_hint(self) -> str:
        hotkey = str(self.settings_store.get("hotkey", "ctrl+shift+space"))
        return " + ".join(part.strip().capitalize() for part in hotkey.split("+"))

    def _connect_audio_signals(self, worker: AudioWorker):
        worker.phrase_detected_with_source.connect(self._on_phrase_detected)
        worker.status_changed.connect(self._update_status)
        worker.source_status_changed.connect(self._on_audio_source_status)
        worker.listening_changed.connect(self._update_listening)
        worker.audio_level.connect(self.waveform.set_level)
        worker.segment_dropped.connect(
            lambda text: self._on_phrase_decision(
                text, False, "ненадёжное распознавание (похоже на шум или музыку)"
            )
        )

    def _on_audio_source_status(self, source_id: str, status: str):
        self._audio_source_status[source_id] = str(status)
        if status.startswith(("Недоступен:", "Выбранное устройство недоступно")):
            label = "Микрофон" if source_id == "microphone" else "Системный звук"
            self._update_status(f"{label}: {status}"[:180])

    def _connect_signals(self):
        self._connect_audio_signals(self.audio)

        self.processor.error_message.connect(self._show_error_message)
        self.processor.user_message.connect(self._show_user_message)
        self.processor.assistant_message.connect(self._show_assistant_message)
        self.processor.assistant_chunk.connect(self._append_stream_chunk)
        self.processor.answer_metadata.connect(self._show_answer_metadata)
        self.processor.typing_changed.connect(self._set_typing)
        self.processor.busy_changed.connect(self._set_task_busy)
        self.processor.status_changed.connect(self._update_status)
        self.processor.conversation_cleared.connect(self._clear_chat_widgets)
        self.processor.conversation_loaded.connect(self._load_conversation)
        self.processor.conversations_changed.connect(
            self._on_conversations_changed
        )

        self.hotkey_toggle_signal.connect(self.toggle_listening)
        self.speaking_changed.connect(self._on_speaking_changed)
        self.processor.phrase_decision.connect(self._on_phrase_decision)
        self.processor.answer_finished.connect(self._on_answer_finished)
        self.processor.assistant_message_removed.connect(
            self._remove_latest_assistant_display
        )

    def _register_hotkey(self):
        registered = False
        self._window_hotkey_ok = False

        for key, default, signal in (
            ("hotkey", "ctrl+shift+space", self.hotkey_toggle_signal),
            ("toggle_window_hotkey", "ctrl+alt+h", self.window_toggle_signal),
        ):
            try:
                keyboard.add_hotkey(
                    str(self.settings_store.get(key, default)), signal.emit
                )
                registered = True
                if key == "toggle_window_hotkey":
                    self._window_hotkey_ok = True
            except Exception:
                logger.exception("Не удалось зарегистрировать горячую клавишу %s.", key)

        self._hotkey_registered = registered

        if not registered or not self._window_hotkey_ok:
            self._update_status("Горячая клавиша недоступна")

        self._apply_tray_visibility()

    def _unregister_hotkey(self):
        if self._hotkey_registered:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                logger.exception("Не удалось снять горячую клавишу.")

            self._hotkey_registered = False

    # ---------------------------------------------------------------
    # Messages, Markdown, streaming and message actions
    # ---------------------------------------------------------------

    def _available_bubble_width(self) -> int:
        width = self.scroll.viewport().width()

        if width < 100:
            width = self.width() - 60

        return max(220, int(width * 0.91))

    def _add_bubble(
        self,
        text: str,
        role: str,
        with_actions: bool = True,
        metadata: dict | None = None,
        timestamp: float | None = None,
    ) -> ChatBubble:
        if metadata and metadata.get("created_at"):
            timestamp = float(metadata["created_at"])
        bubble = ChatBubble(
            text, role, self._available_bubble_width(), timestamp
        )

        container = QWidget()
        container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Minimum
        )
        container.setStyleSheet("background: transparent;")

        vertical = QVBoxLayout(container)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(2)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble, 0, Qt.AlignRight | Qt.AlignTop)
        else:
            row.addWidget(bubble, 0, Qt.AlignLeft | Qt.AlignTop)
            row.addStretch(1)

        vertical.addLayout(row)

        action_row = None
        copy_button = None
        retry_button = None

        if role == "assistant" and with_actions:
            action_row, copy_button, retry_button = (
                self._create_message_actions(bubble)
            )
            vertical.addWidget(action_row, 0, Qt.AlignLeft)

        insert_at = max(0, self.chat_layout.count() - 1)
        self.chat_layout.insertWidget(insert_at, container)

        self._bubbles.append(bubble)
        self._rows.append(container)

        if metadata and role == "assistant" and metadata.get("model"):
            try:
                bubble.set_metadata(
                    str(metadata["model"]),
                    int(metadata.get("duration_ms") or 0),
                )
            except (TypeError, ValueError):
                pass

        self._message_entries.append({
            "bubble": bubble,
            "container": container,
            "role": role,
            "actions": action_row,
            "copy": copy_button,
            "retry": retry_button,
        })

        force = role == "user"
        QTimer.singleShot(30, lambda: self._scroll_to_bottom(force=force))
        return bubble

    def _create_message_actions(self, bubble: ChatBubble):
        row = QWidget()
        row.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(row)
        layout.setContentsMargins(1, 2, 0, 0)
        layout.setSpacing(3)

        copy_button = IconButton(
            "copy",
            size=28,
            icon_size=15,
            tooltip="Копировать ответ",
        )
        copy_button.clicked.connect(
            lambda _checked=False, b=bubble:
            QApplication.clipboard().setText(b.text)
        )

        retry_button = IconButton(
            "retry",
            size=28,
            icon_size=16,
            tooltip="Повторить генерацию последнего ответа",
        )
        retry_button.clicked.connect(self._regenerate_last)

        layout.addWidget(copy_button)
        layout.addWidget(retry_button)
        layout.addStretch(1)

        return row, copy_button, retry_button

    def _on_phrase_decision(self, text: str, accepted: bool, reason: str):
        self.phrase_log.add(text, accepted, reason)
        if self._phrase_dialog is not None and self._phrase_dialog.isVisible():
            self._phrase_dialog.reload()

    def _open_phrase_log(self):
        if self._phrase_dialog is None:
            self._phrase_dialog = PhraseLogDialog(
                self,
                self.phrase_log,
                lambda text: self.processor.enqueue_text(text, True),
            )
        self._phrase_dialog.reload()
        self._phrase_dialog.show()
        self._phrase_dialog.raise_()
        self._phrase_dialog.activateWindow()

    def _on_answer_finished(self, text: str, from_voice: bool):
        mode = str(self.settings_store.get("tts_mode", "off"))
        if mode == "always" or (mode == "voice" and from_voice):
            self.speech.speak(text)

    def _on_speaking_changed(self, speaking: bool):
        # Mute capture while speaking so the assistant does not hear itself.
        if self.audio is not None:
            self.audio.set_speaking(speaking)
        self._update_stop_visibility()
        if speaking:
            self._update_status("Озвучиваю ответ…")
        elif self.audio is not None and self.audio.listening:
            self._update_status("Прослушивание активно")

    def _update_stop_visibility(self):
        self.cancel_button.setVisible(
            self._task_busy or self.speech.speaking
        )

    def _set_task_busy(self, enabled: bool):
        self._task_busy = bool(enabled)
        self._update_stop_visibility()

    def _show_user_message(self, text: str):
        self.speech.stop()
        self._remove_typing()

        for entry in self._message_entries:
            if entry.get("retry") is not None:
                entry["retry"].setVisible(False)

        self._add_bubble(
            text, "user", with_actions=False, timestamp=time.time()
        )

    def _show_error_message(self, text: str):
        self._remove_typing()

        if self._streaming_bubble is not None:
            self._remove_streaming_row()

        self._add_bubble(text, "system", with_actions=False,
                         timestamp=time.time())
        self._scroll_to_bottom()

    def _show_assistant_message(self, text: str):
        self._remove_typing()

        if self._streaming_bubble is not None:
            if self._stream_timer.isActive():
                self._stream_timer.stop()

            self._pending_stream_text = ""
            bubble = self._streaming_bubble
            bubble.set_markdown(text)
            self._add_actions_to_existing_stream()

            self._streaming_bubble = None
            self._streaming_container = None
            self._streaming_text = ""
            self._pending_stream_text = ""

        else:
            self._add_bubble(text, "assistant", timestamp=time.time())

        self._scroll_to_bottom()

    def _show_answer_metadata(self, model: str, duration_ms: int):
        for entry in reversed(self._message_entries):
            if entry["role"] == "assistant":
                entry["bubble"].set_metadata(model, duration_ms)
                break

    def _append_stream_chunk(self, chunk: str):
        if not chunk:
            return

        self._remove_typing()

        if self._streaming_bubble is None:
            self._streaming_bubble = self._add_bubble(
                "", "assistant", with_actions=False, timestamp=time.time()
            )

            if self._message_entries:
                self._streaming_container = (
                    self._message_entries[-1]["container"]
                )

            self._streaming_text = ""
            self._stream_timer.setInterval(stream_interval_ms(0))

        self._pending_stream_text += chunk

        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream_text(self):
        if (
            not self._pending_stream_text
            or self._streaming_bubble is None
        ):
            self._stream_timer.stop()
            return

        self._streaming_text += self._pending_stream_text
        self._pending_stream_text = ""

        self._streaming_bubble.set_markdown(self._streaming_text)
        self._stream_timer.setInterval(
            stream_interval_ms(len(self._streaming_text))
        )
        self._scroll_to_bottom()

    def _add_actions_to_existing_stream(self):
        if self._stream_timer.isActive():
            self._stream_timer.stop()

        if (
            self._streaming_container is None
            or self._streaming_bubble is None
        ):
            return

        action_row, copy_button, retry_button = (
            self._create_message_actions(self._streaming_bubble)
        )

        self._streaming_container.layout().addWidget(action_row)

        for entry in reversed(self._message_entries):
            if entry["bubble"] is self._streaming_bubble:
                entry["actions"] = action_row
                entry["copy"] = copy_button
                entry["retry"] = retry_button
                break

    def _remove_streaming_row(self):
        if self._stream_timer.isActive():
            self._stream_timer.stop()

        if self._streaming_container is not None:
            self.chat_layout.removeWidget(self._streaming_container)
            self._streaming_container.deleteLater()

        if self._streaming_bubble in self._bubbles:
            self._bubbles.remove(self._streaming_bubble)

        self._message_entries = [
            entry for entry in self._message_entries
            if entry.get("bubble") is not self._streaming_bubble
        ]

        self._rows = [
            row for row in self._rows
            if row is not self._streaming_container
        ]

        self._streaming_bubble = None
        self._streaming_container = None
        self._streaming_text = ""
        self._pending_stream_text = ""

    def _set_typing(self, enabled: bool):
        self._generating = enabled
        self._update_stop_visibility()

        if enabled:
            if self._typing_bubble is not None:
                return

            self._typing_bubble = TypingBubble()
            self._typing_row = QWidget()
            self._typing_row.setStyleSheet("background: transparent;")

            row = QHBoxLayout(self._typing_row)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(self._typing_bubble, 0, Qt.AlignLeft)
            row.addStretch(1)

            self.chat_layout.insertWidget(
                max(0, self.chat_layout.count() - 1),
                self._typing_row,
            )
            self._scroll_to_bottom()

        else:
            self._remove_typing()

    def _remove_typing(self):
        if self._typing_bubble is not None:
            self._typing_bubble.stop()

        if self._typing_row is not None:
            self.chat_layout.removeWidget(self._typing_row)
            self._typing_row.deleteLater()

        self._typing_bubble = None
        self._typing_row = None

    def _remove_latest_assistant_display(self):
        for entry in reversed(self._message_entries):
            if entry["role"] == "assistant":
                container = entry["container"]

                self.chat_layout.removeWidget(container)
                container.deleteLater()
                self._message_entries.remove(entry)

                if entry["bubble"] in self._bubbles:
                    self._bubbles.remove(entry["bubble"])

                if container in self._rows:
                    self._rows.remove(container)

                break

    def _regenerate_last(self):
        self.processor.regenerate_last_answer()

    def _on_scroll_changed(self, value: int):
        scrollbar = self.scroll.verticalScrollBar()
        self._stick_to_bottom = value >= scrollbar.maximum() - 48

    def _scroll_to_bottom(self, force: bool = False):
        """Follow new content only while the user is already at the bottom,
        so reading earlier messages is not interrupted by streaming."""
        if not (force or self._stick_to_bottom):
            return
        scrollbar = self.scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_chat_widgets(self, show_welcome: bool = True):
        self._remove_typing()
        self._remove_streaming_row()

        self._bubbles.clear()
        self._rows.clear()
        self._message_entries.clear()

        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        if show_welcome:
            self._add_bubble(
                "История очищена. Продолжаем!", "assistant"
            )
        self._stick_to_bottom = True

    def _load_conversation(
        self, conversation_id: int, title: str, messages: list[dict]
    ):
        self.speech.stop()
        self._current_conversation_id = conversation_id
        self._current_conversation_title = title
        self.setWindowTitle(f"LocalAssistant — {title}")

        self._clear_chat_widgets(show_welcome=False)

        if messages:
            last_assistant_index = max(
                (
                    i for i, item in enumerate(messages)
                    if item.get("role") == "assistant"
                ),
                default=-1,
            )

            for i, message in enumerate(messages):
                role = message.get("role", "assistant")

                if role not in {"user", "assistant", "system"}:
                    role = "assistant"

                self._add_bubble(
                    str(message.get("content", "")),
                    role,
                    with_actions=(
                        role == "assistant" and i == last_assistant_index
                    ),
                    metadata=message,
                )

        else:
            self._add_bubble(WELCOME_TEXT, "assistant")

        QTimer.singleShot(60, lambda: self._scroll_to_bottom(force=True))

        self._on_conversations_changed(
            self.processor.list_conversations()
        )

    def _on_conversations_changed(self, items):
        for item in items:
            if int(item.get("id", -1)) == self._current_conversation_id:
                self._current_conversation_title = str(
                    item.get(
                        "title", self._current_conversation_title
                    )
                )
                self.setWindowTitle(
                    f"LocalAssistant — {self._current_conversation_title}"
                )
                break

    # ---------------------------------------------------------------
    # Composer, menu, settings, and app state
    # ---------------------------------------------------------------

    def _send_typed_message(self):
        text = self.input.toPlainText().strip()

        if not text:
            return

        self.input.clear()
        self._stick_to_bottom = True
        self.processor.enqueue_text(text, True)

    def _on_phrase_detected(self, text: str, source_id: str = "system"):
        preview = text.strip()
        source_label = "микрофон" if source_id == "microphone" else "системный звук"

        if len(preview) > 180:
            preview = preview[:177] + "…"

        self.transcript_preview.setText(
            f"Распознано ({source_label}): «{preview}»"
        )
        self.transcript_preview.setToolTip(f"Источник: {source_label}\n{text}")
        self.processor.enqueue_text(text, False)

    def _cancel_generation(self):
        self.speech.stop()
        if self._task_busy:
            self.processor.cancel_current()
            self._update_status("Останавливаю обработку…")

    def _show_menu(self):
        menu = QMenu(self)

        new_action = menu.addAction("Новый диалог\tCtrl+N")
        history_action = menu.addAction("История и поиск…\tCtrl+K")
        knowledge_action = menu.addAction("База знаний и долговременная память…")
        diagnostics_action = menu.addAction("Центр диагностики…")

        menu.addSeparator()

        rename_action = menu.addAction("Переименовать диалог")
        export_action = menu.addAction("Экспортировать в Markdown…\tCtrl+E")
        clear_action = menu.addAction("Очистить текущий диалог")
        delete_action = menu.addAction("Удалить текущий диалог")

        menu.addSeparator()

        log_action = menu.addAction("Журнал фильтра…")
        recheck_action = menu.addAction("Проверить Ollama")
        logs_action = menu.addAction("Открыть папку данных и логов")
        about_action = menu.addAction("О программе…")
        settings_action = menu.addAction("Настройки…\tCtrl+,")
        compact_action = menu.addAction(
            "Обычный размер"
            if self.settings_store.get("compact_mode", False)
            else "Компактный режим"
        )
        pin_action = menu.addAction(
            "Открепить от верхнего слоя"
            if self.settings_store.get("always_on_top", True)
            else "Закрепить поверх окон"
        )

        menu.addSeparator()

        hide_action = menu.addAction("Скрыть в трей\tCtrl+Alt+H")
        quit_action = menu.addAction("Выйти из приложения")
        menu.addSeparator()

        pause_action = menu.addAction(
            "Возобновить прослушивание"
            if not self.audio.listening
            else "Приостановить прослушивание"
        )

        tts_mode = str(self.settings_store.get("tts_mode", "off"))
        if self.speech.available:
            tts_action = menu.addAction(
                f"Озвучка: {TTS_MODE_LABELS.get(tts_mode, tts_mode)}"
            )
        else:
            tts_action = menu.addAction("Озвучка недоступна (нужна Windows)")
            tts_action.setEnabled(False)

        chosen = menu.exec(
            self.more_button.mapToGlobal(
                self.more_button.rect().bottomLeft()
            )
        )

        if chosen is None:
            return

        if chosen == new_action:
            self.processor.new_conversation()

        elif chosen == history_action:
            self._open_history()

        elif chosen == knowledge_action:
            self._open_knowledge_base()

        elif chosen == diagnostics_action:
            self._open_diagnostics()

        elif chosen == rename_action:
            title, ok = QInputDialog.getText(
                self,
                "Переименовать диалог",
                "Название:",
                text=self._current_conversation_title,
            )

            if ok and title.strip():
                self.processor.rename_current_conversation(title.strip())

        elif chosen == export_action:
            self._export_conversation()

        elif chosen == clear_action:
            answer = QMessageBox.question(
                self,
                "Очистить диалог",
                "Удалить все сообщения из текущего диалога?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer == QMessageBox.Yes:
                self.processor.clear_conversation()

        elif chosen == delete_action:
            answer = QMessageBox.question(
                self,
                "Удалить диалог",
                "Удалить текущий диалог и сообщения из локальной истории?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer == QMessageBox.Yes:
                self.processor.delete_current_conversation()

        elif chosen == log_action:
            self._open_phrase_log()

        elif chosen == recheck_action:
            self.processor.recheck_ollama()

        elif chosen == about_action:
            self._show_about()

        elif chosen == logs_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir())))

        elif chosen == settings_action:
            self._open_settings()

        elif chosen == compact_action:
            self._toggle_compact_mode()

        elif chosen == pin_action:
            current = bool(
                self.settings_store.get("always_on_top", True)
            )
            self.settings_store.update({
                "always_on_top": not current
            })
            self._set_window_flags()
            self.show()

        elif chosen == hide_action:
            self.hide_to_tray()

        elif chosen == quit_action:
            self.quit_application()

        elif chosen == pause_action:
            self.toggle_listening()

        elif chosen == tts_action:
            self._cycle_tts_mode()

    def _open_diagnostics(self):
        """Show a non-blocking support snapshot without exposing transcript data."""
        try:
            knowledge_path = (
                self.processor.knowledge.path
                if self.processor.knowledge is not None
                else app_data_dir() / "knowledge.sqlite3"
            )
            dialog = DiagnosticsDialog(
                settings=self.settings_store.all(),
                conversation_db=self.conversation_store.path,
                knowledge_db=knowledge_path,
                audio_status=dict(self._audio_source_status),
                listening=bool(self.audio and self.audio.listening),
                speaking=bool(self.speech.speaking),
                build_version=BUILD_VERSION,
                parent=self,
            )
            dialog.exec()
        except Exception as exc:
            logger.exception("Не удалось открыть центр диагностики")
            QMessageBox.critical(
                self, "Центр диагностики",
                f"Не удалось открыть диагностику:\n{exc}",
            )

    def _open_knowledge_base(self):
        """Open the document/memory manager using the processor's shared Ollama client."""
        try:
            from .knowledge_dialog import KnowledgeBaseDialog

            dialog = KnowledgeBaseDialog(
                self.settings_store,
                embedder=self.processor.embed_texts,
                parent=self,
            )
            dialog.exec()
        except Exception as exc:
            logger.exception("Не удалось открыть локальную базу знаний")
            QMessageBox.critical(
                self, "База знаний",
                f"Не удалось открыть локальную базу знаний:\n{exc}\n\n"
                "История диалогов и обычный чат не затронуты.",
            )

    def _show_about(self):
        listen_key = str(self.settings_store.get("hotkey", "ctrl+shift+space"))
        window_key = str(self.settings_store.get("toggle_window_hotkey", "ctrl+alt+h"))
        QMessageBox.information(
            self,
            "О программе",
            f"LocalAssistant {BUILD_VERSION}\n\n"
            "Голосовой ассистент: захватывает выбранные аудиоисточники, "
            "распознаёт речь через Whisper и отвечает через Ollama. "
            "По умолчанию используется локальная Ollama; если указан удалённый адрес, "
            "текст запросов и контекст передаются этому серверу.\n\n"
            f"Пауза прослушивания: {listen_key}\n"
            f"Показать/скрыть окно: {window_key}\n"
            "Ctrl+N новый диалог · Ctrl+K история · Ctrl+, настройки · "
            "Esc остановить\n\n"
            f"Защита от записи экрана: {self._capture_state_text()}\n\n"
            f"Данные и логи: {app_data_dir()}",
        )

    def _open_history(self):
        dialog = HistoryDialog(
            self,
            lambda query, limit, offset: self.processor.list_conversations(
                query, limit, offset
            ),
            self._current_conversation_id,
        )

        if dialog.exec() != HistoryDialog.Accepted:
            return

        chosen = dialog.selected_id

        if chosen is not None and chosen != self._current_conversation_id:
            self.processor.switch_conversation(chosen)

    def _export_conversation(self):
        if self._current_conversation_id is None:
            return

        suggested = safe_filename(self._current_conversation_title) + ".md"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Экспорт диалога",
            suggested,
            "Markdown (*.md);;Текст (*.txt)",
            options=QFileDialog.DontUseNativeDialog,
        )

        if not path:
            return

        try:
            content = self.conversation_store.export_markdown(
                self._current_conversation_id
            )
            Path(path).write_text(content, encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Экспорт", f"Не удалось сохранить файл:\n{exc}"
            )
            return

        self._update_status(f"Диалог сохранён: {Path(path).name}")

    def _open_settings(self):
        before = self.settings_store.all()
        dialog = SettingsDialog(
            self.settings_store, self, self._capture_state_text
        )

        if dialog.exec() != SettingsDialog.Accepted:
            return

        after = self.settings_store.all()

        self._apply_background_alpha()
        self._apply_compact_mode(
            bool(after.get("compact_mode", False))
        )
        self._set_window_flags()
        self.show()
        self._apply_capture_exclusion()
        self.hotkey_label.setText(self._hotkey_hint())
        self._apply_tray_visibility()

        self._unregister_hotkey()
        self._register_hotkey()

        audio_restart_needed = any(
            before.get(key) != after.get(key)
            for key in (
                "audio_device", "audio_device_id", "system_audio_enabled",
                "microphone_enabled", "microphone_device", "microphone_device_id",
                "adaptive_vad_enabled", "vad_sensitivity", "silence_threshold",
                "silence_duration", "whisper_model", "whisper_device",
            )
        )

        if audio_restart_needed:
            self._restart_audio_worker()

        # A new server address or model deserves an immediate re-check.
        if any(
            before.get(key) != after.get(key)
            for key in ("ollama_url", "answer_model", "classifier_model")
        ):
            self.processor.recheck_ollama()

    def _restart_audio_worker(self):
        """Restart audio without blocking the Qt GUI thread.

        Whisper may still be finishing a native inference when settings change.
        The old QThread is allowed to finish naturally; only its finished signal
        creates the replacement worker.  Repeated settings changes while a
        restart is pending are coalesced because the new worker reads the latest
        SettingsStore snapshot when it starts.
        """
        if self._quitting:
            return
        if self._audio_restart_in_progress:
            logger.info("Audio restart already pending; latest settings will be used.")
            return

        old_worker = self.audio
        if old_worker is None:
            self._start_replacement_audio_worker(True)
            return

        self._audio_restart_in_progress = True
        self._audio_restart_worker = old_worker
        self._audio_restart_was_listening = old_worker.listening
        self._update_status("Перезапускаю аудиосистему…")
        old_worker.finished.connect(self._finish_audio_worker_restart)

        if old_worker.isRunning():
            old_worker.stop()
        else:
            QTimer.singleShot(0, self._finish_audio_worker_restart)

    def _finish_audio_worker_restart(self):
        old_worker = self._audio_restart_worker
        if old_worker is not None:
            try:
                old_worker.finished.disconnect(self._finish_audio_worker_restart)
            except (RuntimeError, TypeError):
                pass
            old_worker.deleteLater()
            if self.audio is old_worker:
                self.audio = None

        was_listening = self._audio_restart_was_listening
        self._audio_restart_worker = None
        self._audio_restart_in_progress = False

        # A close request may have arrived while Whisper was finishing.  In
        # that case do not create another worker during application shutdown.
        if self._quitting:
            self._finish_close_if_ready()
            return

        self._start_replacement_audio_worker(was_listening)

    def _start_replacement_audio_worker(self, listening: bool) -> None:
        if self._quitting:
            return
        worker = AudioWorker(self.settings_store)
        self.audio = worker
        self._connect_audio_signals(worker)
        worker.set_listening(listening)
        worker.start()
        self._update_status(
            "Слушаю системный звук" if listening else "Прослушивание на паузе"
        )

    def _toggle_compact_mode(self):
        value = not bool(
            self.settings_store.get("compact_mode", False)
        )
        self.settings_store.update({"compact_mode": value})
        self._apply_compact_mode(value)

    def _apply_compact_mode(self, compact: bool, initial: bool = False):
        if compact:
            self.setMinimumSize(370, 480)
            self.resize(420, 570)
        else:
            self.setMinimumSize(430, 540)
            if not (initial and self._geometry_restored):
                self.resize(570, 730)

        QTimer.singleShot(0, self._resize_bubbles)

    def _update_status(self, text: str):
        self.status_label.setText(text)
        lower = text.lower()

        if "ошибка" in lower or "не удалось" in lower or "недоступна" in lower:
            color = "#FF929F"
        elif (
            "пауз" in lower
            or "пропущена фильтром" in lower
            or "останов" in lower
        ):
            color = "#F0CB7C"
        elif (
            "генерир" in lower
            or "анализир" in lower
            or "распозна" in lower
        ):
            color = "#C0B2FF"
        else:
            color = "#A8E8C9"

        self.status_dot.setStyleSheet(
            f"color: {color}; font-size: 7pt;"
        )

    def _update_listening(self, enabled: bool):
        self.listen_button.icon_name = "pause" if enabled else "play"
        self.listen_button.setProperty(
            "active", "true" if enabled else "false"
        )
        self.listen_button.setToolTip(
            "Приостановить прослушивание"
            if enabled
            else "Возобновить прослушивание"
        )

        self.waveform.set_active(enabled)

        self.listen_button.style().unpolish(self.listen_button)
        self.listen_button.style().polish(self.listen_button)
        self.listen_button.update()

    def toggle_listening(self):
        self.audio.set_listening(not self.audio.listening)

    def _resize_bubbles(self):
        width = self._available_bubble_width()

        for bubble in list(self._bubbles):
            try:
                bubble.set_bubble_width(width)
            except RuntimeError:
                pass

        self._scroll_to_bottom()

    def _apply_background_alpha(self):
        alpha = max(
            175,
            min(
                250,
                int(
                    self.settings_store.get(
                        "background_alpha", DEFAULT_BACKGROUND_ALPHA
                    )
                ),
            ),
        )

        self.setStyleSheet(
            STYLE
            + f"""
            QWidget#mainWindow {{
                background-color: rgba(17, 19, 29, {alpha});
                border: 1px solid rgba(174, 183, 224, 65);
                border-radius: 22px;
            }}
            """
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._resize_bubbles)

    def showEvent(self, event):
        super().showEvent(event)

        hwnd = int(self.winId())

        if not self._acrylic_enabled:
            self._acrylic_enabled = enable_acrylic(hwnd)

        self._apply_capture_exclusion()

    def _apply_capture_exclusion(self):
        self.capture_guard.set_enabled(
            bool(self.settings_store.get("hide_from_capture", True))
        )

    def _capture_state_text(self) -> str:
        """Human-readable result of a live read-back of the window affinity."""
        state = self.capture_guard.state_of(self)
        return winintegration.STATE_TEXT.get(state, state)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_position is not None
            and event.buttons() & Qt.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint() - self._drag_position
            )
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_position = None
        super().mouseReleaseEvent(event)

    # ---------------------------------------------------------------
    # Tray integration
    # ---------------------------------------------------------------

    def _create_tray(self):
        if not self._tray_available:
            logger.warning("Системный трей недоступен: работаю с обычным окном.")
            return

        self.tray = TrayController(
            toggle_window=self.toggle_window_visibility,
            is_window_visible=self.isVisible,
            toggle_listening=self.toggle_listening,
            is_listening=lambda: self.audio.listening,
            cycle_tts=self._cycle_tts_mode,
            tts_label=lambda: TTS_MODE_LABELS.get(
                str(self.settings_store.get("tts_mode", "off")), "выключена"
            ),
            open_settings=self._open_settings_from_tray,
            quit_app=self.quit_application,
            parent=self,
        )
        self.window_toggle_signal.connect(self.toggle_window_visibility)
        self._apply_tray_visibility()

    def _apply_tray_visibility(self):
        """The icon may be hidden by choice, but never when that would leave
        no way to get the window back."""
        if self.tray is None:
            return
        wanted = bool(self.settings_store.get("tray_icon_visible", True))
        self.tray.set_icon_visible(wanted or not self._window_hotkey_ok)

    def _minimize_clicked(self):
        if self.tray is not None:
            self.hide_to_tray()
        else:
            self.showMinimized()

    def hide_to_tray(self):
        if self._phrase_dialog is not None:
            self._phrase_dialog.hide()
        self.hide()

    def show_from_tray(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def toggle_window_visibility(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_from_tray()

    def _open_settings_from_tray(self):
        self.show_from_tray()
        self._open_settings()

    def _cycle_tts_mode(self):
        mode = str(self.settings_store.get("tts_mode", "off"))
        index = TTS_MODE_ORDER.index(mode) if mode in TTS_MODE_ORDER else 0
        new_mode = TTS_MODE_ORDER[(index + 1) % len(TTS_MODE_ORDER)]
        self.settings_store.update({"tts_mode": new_mode})
        if new_mode == "off":
            self.speech.stop()
        self._update_status(f"Озвучка: {TTS_MODE_LABELS[new_mode]}")

    def prepare_quit(self):
        """Called on Windows logoff/shutdown so closing is never refused."""
        self._quitting = True

    def quit_application(self):
        self._quitting = True
        self.close()

    def closeEvent(self, event):
        # The window's own close button only hides it; the app lives on in the
        # tray until "Выход" is chosen (or Windows is shutting down).
        if (
            not self._quitting
            and self.tray is not None
            and bool(self.settings_store.get("close_to_tray", True))
        ):
            event.ignore()
            self.hide_to_tray()
            return

        self._quitting = True
        self.speech.stop()

        if not self._close_pending:
            self._save_geometry()

        self._unregister_hotkey()

        if self.audio is not None:
            self.audio.stop()

        if self.processor is not None:
            self.processor.stop()

        audio_running = (
            self.audio is not None and self.audio.isRunning()
        )
        processor_running = (
            self.processor is not None and self.processor.isRunning()
        )

        if audio_running or processor_running:
            event.ignore()
            self.hide()

            if not self._close_pending:
                self._close_pending = True

                if self.audio is not None:
                    self.audio.finished.connect(
                        self._finish_close_if_ready
                    )

                if self.processor is not None:
                    self.processor.finished.connect(
                        self._finish_close_if_ready
                    )

            return

        if self.tray is not None:
            self.tray.shutdown()

        event.accept()
        QTimer.singleShot(0, QApplication.quit)

    def _finish_close_if_ready(self):
        audio_running = (
            self.audio is not None and self.audio.isRunning()
        )
        processor_running = (
            self.processor is not None and self.processor.isRunning()
        )

        if not audio_running and not processor_running:
            self.close()
