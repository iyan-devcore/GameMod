"""Start the GameMod app."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from gamemod.ui.theme import STYLE, collect_garbage_on_main_thread
from gamemod.ui.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("GameMod")
    app.setStyleSheet(STYLE)
    collect_garbage_on_main_thread(app)
    window = MainWindow()
    window.show()
    code = app.exec()
    # Closing the window has already waited for any change to the game's files. Leave
    # immediately so a translation request still in flight cannot touch a closed Qt.
    os._exit(code)


if __name__ == "__main__":
    main()
