"""Modern dark-glass chat UI for Live Assistant."""
from __future__ import annotations

import ctypes
import logging
import math
import sys
from typing import Any

import keyboard
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal, QPointF
)
from PySide6.QtGui import (
    QFont, QFontMetrics, QPainter, QColor, QPen, QPainterPath
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QLineEdit, QGraphicsOpacityEffect,
    QMenu, QTextBrowser, QInputDialog, QMessageBox, QSizeGrip,
    QSystemTrayIcon, QStyle
)

from .audio_worker import AudioWorker
from .config import (
    DEFAULT_BACKGROUND_ALPHA,
    DEFAULT_BACKGROUND_SATURATION,
)
from .conversation_store import ConversationStore
from .processor import MessageProcessor
from .settings_dialog import SettingsDialog
from .settings_store import SettingsStore


BUILD_VERSION = "2026.10.21-r7"
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


def exclude_window_from_capture(hwnd: int) -> None:
    """Best-effort attempt to exclude this window from supported capture APIs."""
    if sys.platform != "win32":
        return

    try:
        result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
        logger.info("Window capture exclusion result: %s", bool(result))
    except Exception:
        logger.exception("Could not set window capture exclusion.")


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
        self.setFixedSize(66, 24)

        self.timer = QTimer(self)
        self.timer.setInterval(75)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

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
            bar_height = max(3.0, (level * 0.72 + idle) * height)
            x = i * (bar_width + spacing)
            y = (height - bar_height) / 2

            painter.setPen(Qt.NoPen)
            painter.setBrush(
                QColor(151, 166, 255, 190 if level > 0.18 else 105)
            )
            painter.drawRoundedRect(
                int(x), int(y), int(bar_width + 1),
                int(bar_height), 2, 2,
            )

        painter.end()


class ChatBubble(QFrame):
    """Responsive message bubble with Markdown rendering."""

    def __init__(self, text: str, role: str, max_width: int):
        super().__init__()
        self.role = role
        self.text = text
        self.max_bubble_width = max(220, int(max_width))
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

    def _build(self):
        is_user = self.role == "user"
        is_system = self.role == "system"

        if is_user:
            frame_bg = "rgba(197, 214, 255, 242)"
            border = "rgba(225, 234, 255, 185)"
            text_color = "#19243D"
            role_color = "#566789"
            role_text = "Вы"
            margins = (14, 11, 14, 12)
        elif is_system:
            frame_bg = "rgba(113, 43, 58, 180)"
            border = "rgba(255, 147, 164, 100)"
            text_color = "#FFF1F4"
            role_color = "#FFC0CC"
            role_text = "Система"
            margins = (13, 10, 13, 11)
        else:
            frame_bg = "transparent"
            border = "transparent"
            text_color = "#E9ECF8"
            role_color = "#9EADEB"
            role_text = "Live Assistant"
            margins = (3, 4, 3, 4)

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
        layout.setContentsMargins(*margins)
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
                background-color: rgba(7, 9, 16, 210);
                padding: 8px;
            }}
            a {{ color: #B7C5FF; }}
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

        self.body.setMarkdown(self.text)
        layout.addWidget(self.role_label)
        layout.addWidget(self.body)

    def set_markdown(self, text: str):
        self.text = text
        self.body.setMarkdown(text or " ")
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

        if self.role == "user":
            margins = 34
            metrics = QFontMetrics(self.body.font())
            lines = self.text.splitlines() or [self.text]
            longest_line = max(lines, key=len) if lines else ""
            estimated = metrics.horizontalAdvance(longest_line) + margins + 28
            desired = min(max_width, max(180, estimated))
        else:
            desired = max_width

        self.setFixedWidth(desired)

        if self.role == "user":
            left, right = 14, 14
        elif self.role == "system":
            left, right = 13, 13
        else:
            left, right = 3, 3

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

    def __init__(self):
        super().__init__()

        self.settings_store = SettingsStore()
        self.conversation_store = ConversationStore()

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
        self._exit_requested = False
        self._hotkey_registered = False
        self._drag_position = None
        self.tray_icon: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.tray_pause_action = None

        self._bubbles: list[ChatBubble] = []
        self._rows: list[QWidget] = []
        self._message_entries: list[dict[str, Any]] = []

        self._current_conversation_id: int | None = None
        self._current_conversation_title = "Новый диалог"

        self.setObjectName("mainWindow")
        self.setWindowTitle("Live Assistant")
        self._set_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(570, 730)
        self.setMinimumSize(430, 540)

        self._build_ui()
        self._apply_background_alpha()

        self.audio = AudioWorker(self.settings_store)
        self.processor = MessageProcessor(
            self.settings_store, self.conversation_store
        )

        self._connect_signals()
        self._init_system_tray()
        self.audio.start()
        self.processor.start()
        self._register_hotkey()

        self._apply_compact_mode(
            bool(self.settings_store.get("compact_mode", False))
        )

    def _init_system_tray(self):
        """Create a Windows notification-area icon when the tray is available."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("Системный трей недоступен; закрытие в трей отключено.")
            return

        self.tray_menu = QMenu(self)
        show_action = self.tray_menu.addAction("Открыть Live Assistant")
        show_action.triggered.connect(self._show_from_tray)

        self.tray_pause_action = self.tray_menu.addAction(
            "Приостановить прослушивание"
        )
        self.tray_pause_action.triggered.connect(self.toggle_listening)

        self.tray_menu.addSeparator()
        exit_action = self.tray_menu.addAction("Выход")
        exit_action.triggered.connect(self._exit_from_tray)

        self.tray_icon = QSystemTrayIcon(
            QApplication.style().standardIcon(QStyle.SP_ComputerIcon), self
        )
        self.tray_icon.setToolTip("Live Assistant")
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_from_tray()

    def _exit_from_tray(self):
        self._exit_requested = True
        self.close()

    def _set_window_flags(self):
        flags = Qt.Window | Qt.FramelessWindowHint

        if self.settings_store.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint

        self.setWindowFlags(flags)

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
            tooltip="Live Assistant",
        )
        self.logo_button.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(4)

        title = QLabel("Live Assistant")
        title.setObjectName("title")
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #A8E8C9; font-size: 7pt;")

        self.status_label = QLabel("Запуск…")
        self.status_label.setObjectName("status")
        self.status_label.setMaximumWidth(245)

        self.waveform = WaveformWidget()

        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addWidget(self.waveform)
        status_row.addStretch(1)

        title_stack.addWidget(title)
        title_stack.addLayout(status_row)

        self.cancel_button = IconButton(
            "stop",
            size=32,
            icon_size=16,
            variant="danger",
            tooltip="Остановить генерацию ответа",
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_generation)

        self.more_button = IconButton(
            "more",
            size=32,
            icon_size=18,
            tooltip="История и настройки",
        )
        self.more_button.clicked.connect(self._show_menu)

        self.minimize_button = IconButton(
            "minimize",
            size=32,
            icon_size=16,
            tooltip="Свернуть окно",
        )
        self.minimize_button.clicked.connect(self.showMinimized)

        self.close_button = IconButton(
            "close",
            size=32,
            icon_size=16,
            tooltip="Закрыть приложение",
        )
        self.close_button.clicked.connect(self.close)

        header_layout.addWidget(self.logo_button)
        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(self.cancel_button)
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

        self.input = QLineEdit()
        self.input.setPlaceholderText("Напишите сообщение…")
        self.input.setMinimumHeight(46)
        self.input.returnPressed.connect(self._send_typed_message)

        self.send_button = IconButton(
            "send",
            size=46,
            icon_size=20,
            variant="send",
            tooltip="Отправить сообщение",
        )
        self.send_button.clicked.connect(self._send_typed_message)

        composer.addWidget(self.input, 1)
        composer.addWidget(self.send_button)
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

        help_label = QLabel("Ctrl + Shift + Space")
        help_label.setObjectName("tiny")

        footer.addWidget(self.listen_button)
        footer.addWidget(help_label)
        footer.addStretch(1)

        self.resize_grip = QSizeGrip(self)
        self.resize_grip.setFixedSize(13, 13)
        footer.addWidget(
            self.resize_grip, 0, Qt.AlignBottom | Qt.AlignRight
        )

        root.addLayout(footer)

        self.setStyleSheet(STYLE)

        self._add_bubble(
            "Привет! Я слушаю системный звук. Можешь задать вопрос голосом "
            "или написать его внизу.",
            "assistant",
        )

    def _connect_signals(self):
        self.audio.phrase_detected.connect(self._on_phrase_detected)
        self.audio.status_changed.connect(self._update_status)
        self.audio.listening_changed.connect(self._update_listening)
        self.audio.audio_level.connect(self.waveform.set_level)

        self.processor.user_message.connect(self._show_user_message)
        self.processor.assistant_message.connect(self._show_assistant_message)
        self.processor.assistant_chunk.connect(self._append_stream_chunk)
        self.processor.answer_metadata.connect(self._show_answer_metadata)
        self.processor.typing_changed.connect(self._set_typing)
        self.processor.status_changed.connect(self._update_status)
        self.processor.conversation_cleared.connect(self._clear_chat_widgets)
        self.processor.conversation_loaded.connect(self._load_conversation)
        self.processor.conversations_changed.connect(
            self._on_conversations_changed
        )

        self.hotkey_toggle_signal.connect(self.toggle_listening)

    def _register_hotkey(self):
        try:
            hotkey = str(
                self.settings_store.get("hotkey", "ctrl+shift+space")
            )
            keyboard.add_hotkey(hotkey, self.hotkey_toggle_signal.emit)
            self._hotkey_registered = True

        except Exception:
            logger.exception("Не удалось зарегистрировать горячую клавишу.")
            self._hotkey_registered = False
            self._update_status("Горячая клавиша недоступна")

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
    ) -> ChatBubble:
        bubble = ChatBubble(text, role, self._available_bubble_width())

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

        QTimer.singleShot(30, self._scroll_to_bottom)
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

    def _show_user_message(self, text: str):
        self._remove_typing()

        for entry in self._message_entries:
            if entry.get("retry") is not None:
                entry["retry"].setVisible(False)

        self._add_bubble(text, "user", with_actions=False)

    def _show_assistant_message(self, text: str):
        self._remove_typing()

        is_error = text.startswith((
            "Ошибка ответа:",
            "Не удалось",
            "Ошибка классификатора:",
            "Ошибка обработки:",
        ))

        if self._streaming_bubble is not None and not is_error:
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
            if self._streaming_bubble is not None and is_error:
                self._remove_streaming_row()

            self._add_bubble(
                text, "system" if is_error else "assistant"
            )

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
                "", "assistant", with_actions=False
            )

            if self._message_entries:
                self._streaming_container = (
                    self._message_entries[-1]["container"]
                )

            self._streaming_text = ""

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
        self.cancel_button.setVisible(enabled)

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
        self._remove_latest_assistant_display()
        self.processor.regenerate_last_answer()

    def _scroll_to_bottom(self):
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

    def _load_conversation(
        self, conversation_id: int, title: str, messages: list[dict]
    ):
        self._current_conversation_id = conversation_id
        self._current_conversation_title = title
        self.setWindowTitle(f"Live Assistant — {title}")

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
                    metadata=message if role == "assistant" else None,
                )

        else:
            self._add_bubble(
                "Привет! Я слушаю системный звук. Можешь задать вопрос "
                "голосом или написать его внизу.",
                "assistant",
            )

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
                    f"Live Assistant — {self._current_conversation_title}"
                )
                break

    # ---------------------------------------------------------------
    # Composer, menu, settings, and app state
    # ---------------------------------------------------------------

    def _send_typed_message(self):
        text = self.input.text().strip()

        if not text:
            return

        self.input.clear()
        self.processor.enqueue_text(text, True)

    def _on_phrase_detected(self, text: str):
        preview = text.strip()

        if len(preview) > 180:
            preview = preview[:177] + "…"

        self.transcript_preview.setText(f"Распознано: «{preview}»")
        self.transcript_preview.setToolTip(text)
        self.processor.enqueue_text(text, False)

    def _cancel_generation(self):
        self.processor.cancel_current()
        self._update_status("Останавливаю генерацию…")

    def _show_menu(self):
        menu = QMenu(self)

        new_action = menu.addAction("Новый диалог")
        history_menu = menu.addMenu("История диалогов")

        histories = self.processor.list_conversations()

        if not histories:
            empty = history_menu.addAction("История пуста")
            empty.setEnabled(False)
        else:
            for conversation in histories[:30]:
                label = str(conversation["title"])[:55]

                if int(conversation["id"]) == self._current_conversation_id:
                    label = "✓  " + label

                action = history_menu.addAction(label)
                action.setData(int(conversation["id"]))

        search_action = menu.addAction("Поиск по истории")
        rename_action = menu.addAction("Переименовать диалог")
        clear_action = menu.addAction("Очистить текущий диалог")
        delete_action = menu.addAction("Удалить текущий диалог")

        menu.addSeparator()

        settings_action = menu.addAction("Настройки")
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

        pause_action = menu.addAction(
            "Возобновить прослушивание"
            if not self.audio.listening
            else "Приостановить прослушивание"
        )

        chosen = menu.exec(
            self.more_button.mapToGlobal(
                self.more_button.rect().bottomLeft()
            )
        )

        if chosen == new_action:
            self.processor.new_conversation()

        elif chosen == search_action:
            self._search_history()

        elif chosen == rename_action:
            title, ok = QInputDialog.getText(
                self,
                "Переименовать диалог",
                "Название:",
                text=self._current_conversation_title,
            )

            if ok and title.strip():
                self.processor.rename_current_conversation(title.strip())

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

        elif chosen == pause_action:
            self.toggle_listening()

        elif chosen is not None and chosen.data() is not None:
            try:
                self.processor.switch_conversation(
                    int(chosen.data())
                )
            except (TypeError, ValueError):
                pass

    def _search_history(self):
        query, ok = QInputDialog.getText(
            self,
            "Поиск по истории",
            "Найти в названиях и сообщениях:",
        )

        if not ok or not query.strip():
            return

        results = self.processor.list_conversations(query.strip())

        if not results:
            QMessageBox.information(
                self, "Поиск", "Совпадений не найдено."
            )
            return

        menu = QMenu(self)

        for item in results[:50]:
            action = menu.addAction(str(item["title"])[:70])
            action.setData(int(item["id"]))

        chosen = menu.exec(
            self.more_button.mapToGlobal(
                self.more_button.rect().bottomLeft()
            )
        )

        if chosen and chosen.data() is not None:
            self.processor.switch_conversation(int(chosen.data()))

    def _open_settings(self):
        before = self.settings_store.all()
        dialog = SettingsDialog(self.settings_store, self)

        if dialog.exec() != SettingsDialog.Accepted:
            return

        after = self.settings_store.all()

        self._apply_background_alpha()
        self._apply_compact_mode(
            bool(after.get("compact_mode", False))
        )
        self._set_window_flags()
        self.show()

        self._unregister_hotkey()
        self._register_hotkey()

        audio_restart_needed = any(
            before.get(key) != after.get(key)
            for key in ("audio_device", "whisper_model")
        )

        if audio_restart_needed:
            self._restart_audio_worker()

    def _restart_audio_worker(self):
        old_worker = self.audio
        old_worker.stop()

        if old_worker.isRunning() and not old_worker.wait(5000):
            self._update_status(
                "Не удалось перезапустить аудио: старый поток ещё занят"
            )
            return

        old_worker.deleteLater()

        self.audio = AudioWorker(self.settings_store)
        self.audio.phrase_detected.connect(self._on_phrase_detected)
        self.audio.status_changed.connect(self._update_status)
        self.audio.listening_changed.connect(self._update_listening)
        self.audio.audio_level.connect(self.waveform.set_level)
        self.audio.start()

    def _toggle_compact_mode(self):
        value = not bool(
            self.settings_store.get("compact_mode", False)
        )
        self.settings_store.update({"compact_mode": value})
        self._apply_compact_mode(value)

    def _apply_compact_mode(self, compact: bool):
        if compact:
            self.setMinimumSize(370, 480)
            self.resize(420, 570)
        else:
            self.setMinimumSize(430, 540)
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

        self.listen_button.style().unpolish(self.listen_button)
        self.listen_button.style().polish(self.listen_button)
        self.listen_button.update()
        self._update_tray_pause_action(enabled)

    def _update_tray_pause_action(self, enabled: bool):
        if self.tray_pause_action is not None:
            self.tray_pause_action.setText(
                "Приостановить прослушивание"
                if enabled else "Возобновить прослушивание"
            )

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

        saturation = max(
            0,
            min(
                100,
                int(
                    self.settings_store.get(
                        "background_saturation",
                        DEFAULT_BACKGROUND_SATURATION,
                    )
                ),
            ),
        ) / 100.0
        neutral = (18, 18, 22)
        tinted = (17, 19, 29)
        red, green, blue = (
            round(neutral[i] + (tinted[i] - neutral[i]) * saturation)
            for i in range(3)
        )

        self.setStyleSheet(
            STYLE
            + f"""
            QWidget#mainWindow {{
                background-color: rgba({red}, {green}, {blue}, {alpha});
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

        exclude_window_from_capture(hwnd)

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

    def closeEvent(self, event):
        if (
            not self._exit_requested
            and bool(self.settings_store.get("close_to_tray", False))
            and self.tray_icon is not None
            and self.tray_icon.isVisible()
        ):
            event.ignore()
            self.hide()
            return

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

        if self.tray_icon is not None:
            self.tray_icon.hide()
        event.accept()

    def _finish_close_if_ready(self):
        audio_running = (
            self.audio is not None and self.audio.isRunning()
        )
        processor_running = (
            self.processor is not None and self.processor.isRunning()
        )

        if not audio_running and not processor_running:
            self.close()


def create_window():
    """Create a window after QApplication exists."""
    return AssistantWindow()