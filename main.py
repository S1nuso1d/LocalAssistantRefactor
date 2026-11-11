import logging
import logging.handlers
import sys

from PySide6.QtWidgets import QApplication

from assistant.settings_store import app_data_dir


def setup_logging() -> None:
    """Log to console and to a rotating file in %LOCALAPPDATA%\\LocalAssistant."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
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


def main() -> int:
    # Construct Qt before importing the audio stack. On some Windows setups
    # this avoids the COM apartment warning triggered by early audio imports.
    app = QApplication(sys.argv)
    app.setApplicationName("Live Assistant")
    app.setStyle("Fusion")

    from assistant.ui import AssistantWindow

    window = AssistantWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    setup_logging()
    sys.exit(main())
