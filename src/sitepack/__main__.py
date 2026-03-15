"""SitePack — Website Static Packager.

Entry point for the desktop GUI application.
"""

from __future__ import annotations

import logging
import sys


def setup_logging() -> None:
    """Configure application-wide logging."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(fmt)
    root.addHandler(console)

    # File handler (optional, in current directory)
    try:
        file_handler = logging.FileHandler("sitepack.log", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
    except OSError:
        pass  # If we can't write log file, just continue


def main() -> None:
    """Launch the SitePack GUI application."""
    setup_logging()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from sitepack.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SitePack")
    app.setOrganizationName("SitePack")
    app.setApplicationVersion("0.1.0")

    # Set app-wide font
    from PySide6.QtGui import QFont
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
