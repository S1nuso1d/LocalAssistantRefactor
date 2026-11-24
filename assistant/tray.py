"""System-tray icon, its menu and the application icon."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

APP_NAME = "LocalAssistant"

MENU_STYLE = """
QMenu {
    background-color: #181C2B; color: #E8EAF5;
    border: 1px solid #343B59; border-radius: 10px; padding: 6px;
}
QMenu::item { padding: 8px 22px 8px 14px; border-radius: 7px; }
QMenu::item:selected { background-color: #4A57A8; color: white; }
QMenu::item:disabled { color: #727A93; }
QMenu::separator { height: 1px; background: #343B59; margin: 5px 8px; }
"""

_BARS = (0.34, 0.62, 0.9, 0.62, 0.34)


def paint_icon(size: int) -> QPixmap:
    """Rounded indigo tile with a white waveform (matches the header logo)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#8C9CFF"))
    gradient.setColorAt(1.0, QColor("#5262E0"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(gradient))
    inset = size * 0.04
    painter.drawRoundedRect(
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
        size * 0.24, size * 0.24,
    )

    painter.setBrush(QColor("#FFFFFF"))
    bar_w = size * 0.1
    gap = size * 0.07
    total = len(_BARS) * bar_w + (len(_BARS) - 1) * gap
    x = (size - total) / 2
    for level in _BARS:
        h = size * 0.56 * level
        painter.drawRoundedRect(
            QRectF(x, (size - h) / 2, bar_w, h), bar_w / 2, bar_w / 2
        )
        x += bar_w + gap
    painter.end()
    return pixmap


def make_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(paint_icon(size))
    return icon


class TrayController(QObject):
    """Tray icon with a menu; all actions are plain callbacks."""

    def __init__(
        self,
        *,
        toggle_window: Callable[[], None],
        is_window_visible: Callable[[], bool],
        toggle_listening: Callable[[], None],
        is_listening: Callable[[], bool],
        cycle_tts: Callable[[], None],
        tts_label: Callable[[], str],
        open_settings: Callable[[], None],
        quit_app: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._toggle_window = toggle_window
        self._is_window_visible = is_window_visible
        self._is_listening = is_listening
        self._tts_label = tts_label

        self.tray = QSystemTrayIcon(make_app_icon(), self)
        self.tray.setToolTip(APP_NAME)

        self.menu = QMenu()
        self.menu.setStyleSheet(MENU_STYLE)
        self._act_window = self.menu.addAction("Показать")
        self._act_listen = self.menu.addAction("Приостановить прослушивание")
        self._act_tts = self.menu.addAction("Озвучка")
        self.menu.addSeparator()
        act_settings = self.menu.addAction("Настройки…")
        self.menu.addSeparator()
        act_quit = self.menu.addAction("Выход")

        self._act_window.triggered.connect(lambda _c=False: toggle_window())
        self._act_listen.triggered.connect(lambda _c=False: toggle_listening())
        self._act_tts.triggered.connect(lambda _c=False: cycle_tts())
        act_settings.triggered.connect(lambda _c=False: open_settings())
        act_quit.triggered.connect(lambda _c=False: quit_app())

        self.menu.aboutToShow.connect(self.refresh_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.refresh_menu()

    def refresh_menu(self) -> None:
        self._act_window.setText(
            "Скрыть окно" if self._is_window_visible() else "Показать окно"
        )
        self._act_listen.setText(
            "Приостановить прослушивание"
            if self._is_listening() else "Возобновить прослушивание"
        )
        self._act_tts.setText(f"Озвучка: {self._tts_label()}")

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def set_icon_visible(self, visible: bool) -> None:
        self.tray.setVisible(bool(visible))

    def shutdown(self) -> None:
        """Remove the icon immediately so no ghost icon lingers after exit."""
        self.tray.hide()
        self.tray.deleteLater()
