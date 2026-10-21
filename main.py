import logging
import sys

from PySide6.QtWidgets import QApplication


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
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
