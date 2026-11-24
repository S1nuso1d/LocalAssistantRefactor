import logging
import logging.handlers
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from assistant.settings_store import app_data_dir
from assistant.shutdown import install_console_interrupt_handler


def setup_logging() -> None:
    """Log to console and to a rotating file in %LOCALAPPDATA%\\LocalAssistant.

    Recognised speech is never written to the log unless the user explicitly
    enables it in the settings (see assistant/privacy.py).
    """
    handlers: list[logging.Handler] = []
    # pythonw.exe sets stdout/stderr to None; avoid a broken StreamHandler
    # when the app starts without a console window.
    if getattr(sys, "stderr", None) is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                app_data_dir() / "live_assistant.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError:
        pass  # console logging is still enough to run

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    def log_uncaught(exc_type, exc, tb):
        logging.getLogger("live_assistant").critical(
            "Необработанное исключение", exc_info=(exc_type, exc, tb)
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = log_uncaught


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # Construct Qt before importing the audio stack. On some Windows setups
    # this avoids the COM apartment warning triggered by early audio imports.
    app = QApplication(argv)
    app.setApplicationName("LocalAssistant")
    app.setStyle("Fusion")
    # The app lives in the system tray: closing the window must not quit it.
    app.setQuitOnLastWindowClosed(False)

    from assistant.single_instance import SingleInstance
    from assistant.tray import make_app_icon

    app.setWindowIcon(make_app_icon())

    # A second launch just brings the running copy to the front.
    single = SingleInstance()
    if not single.listen():
        if single.existing_instance_notified:
            return 0
        QMessageBox.critical(
            None,
            "LocalAssistant",
            "Не удалось защититься от запуска нескольких копий. "
            "Проверьте, не заблокировал ли Windows локальный IPC-сокет.",
        )
        return 2

    app.aboutToQuit.connect(single.close)

    from assistant.ui import AssistantWindow

    window = AssistantWindow()
    single.activated.connect(window.show_from_tray)

    # Never block Windows logoff/shutdown by "closing to tray".
    try:
        app.commitDataRequest.connect(lambda _manager: window.prepare_quit())
    except AttributeError:
        logging.getLogger("live_assistant").warning(
            "commitDataRequest недоступен в этой версии Qt."
        )

    start_hidden = "--tray" in argv[1:] or bool(
        window.settings_store.get("start_in_tray", False)
    )
    if start_hidden and window.tray is not None:
        logging.getLogger("live_assistant").info("Запуск свёрнутым в трей.")
    else:
        window.show()

    previous_sigint_handler = install_console_interrupt_handler(
        window.quit_application,
        schedule=lambda callback: QTimer.singleShot(0, callback),
    )
    try:
        return app.exec()
    finally:
        try:
            signal.signal(signal.SIGINT, previous_sigint_handler)
        except (OSError, ValueError):
            logging.getLogger("live_assistant").exception(
                "Не удалось восстановить обработчик Ctrl+C."
            )


if __name__ == "__main__":
    setup_logging()
    sys.exit(main())
