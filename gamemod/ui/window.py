"""Main window: the supported games on the left, each marked found or not found."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gamemod.adapters.base import GameAdapter
from gamemod.adapters.registry import ADAPTERS
from gamemod.core import library
from gamemod.core.library import GameEntry
from gamemod.core.paths import asset
from gamemod.ui.game_page import GamePage
from gamemod.ui.settings_page import SettingsPage
from gamemod.ui.theme import status_icon

_SETTINGS = "settings"


class MissingGamePage(QWidget):
    """Shown for a game the scan could not find."""

    def __init__(self, adapter_class: type[GameAdapter], on_choose, on_scan):
        super().__init__()
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(12)
        heading = QLabel(adapter_class.title)
        heading.setObjectName("heading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        status = QLabel("✗ Game not found on this PC")
        status.setObjectName("missing")
        layout.addWidget(status)
        body = QLabel(
            "GameMod checked your Steam libraries and the usual install folders. If the game is "
            f"installed somewhere else, choose {adapter_class.folder_hint}."
        )
        body.setObjectName("subheading")
        body.setWordWrap(True)
        layout.addWidget(body)
        buttons = QHBoxLayout()
        self.choose_button = QPushButton("Choose game folder")
        self.choose_button.setObjectName("primary")
        self.choose_button.clicked.connect(lambda: on_choose(adapter_class))
        buttons.addWidget(self.choose_button)
        scan_button = QPushButton("Scan again")
        scan_button.clicked.connect(lambda: on_scan())
        buttons.addWidget(scan_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()


class MainWindow(QMainWindow):
    def __init__(self, adapters: Sequence[type[GameAdapter]] | None = None):
        super().__init__()
        self.setWindowTitle("GameMod")
        icon = QIcon()
        for size in (16, 32, 256):
            icon.addFile(str(asset("logo", f"gamemod-icon-{size}.png")))
        self.setWindowIcon(icon)
        self.resize(1040, 720)
        self.setMinimumSize(820, 600)
        self._adapters = list(adapters) if adapters is not None else list(ADAPTERS.values())
        self._entries: dict[str, GameEntry] = {}
        self._pages: dict[str, QWidget] = {}
        self._found_icon = status_icon(True)
        self._missing_icon = status_icon(False)

        central = QWidget()
        self.setCentralWidget(central)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(262)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 12)
        side.setSpacing(2)
        side.addLayout(self._brand())
        label = QLabel("GAMES")
        label.setObjectName("sectionLabel")
        side.addWidget(label)

        self.game_list = QListWidget()
        self.game_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.game_list.setTextElideMode(Qt.ElideRight)
        self.game_list.setIconSize(QSize(22, 22))
        for adapter_class in self._adapters:
            item = QListWidgetItem(adapter_class.title)
            item.setData(Qt.UserRole, adapter_class.id)
            item.setToolTip(adapter_class.title)
            self.game_list.addItem(item)
        self.game_list.currentItemChanged.connect(self._select)
        side.addWidget(self.game_list, 1)

        self.scan_button = QPushButton("Scan again")
        self.scan_button.setObjectName("link")
        self.scan_button.clicked.connect(lambda: self.scan())
        side.addWidget(self.scan_button)
        self.add_button = QPushButton("Add game manually")
        self.add_button.setObjectName("link")
        self.add_button.clicked.connect(lambda: self._add_game())
        side.addWidget(self.add_button)
        settings_button = QPushButton("Settings")
        settings_button.setObjectName("link")
        settings_button.clicked.connect(self._open_settings)
        side.addWidget(settings_button)
        row.addWidget(sidebar)

        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)

        self.scan()
        if self._adapters:
            self.game_list.setCurrentRow(0)

    def _brand(self) -> QHBoxLayout:
        """The logo mark and app name at the top of the sidebar."""
        ratio = self.devicePixelRatioF()
        mark = QPixmap(str(asset("logo", "gamemod-mark-128.png"))).scaled(
            round(28 * ratio), round(28 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        mark.setDevicePixelRatio(ratio)
        logo = QLabel()
        logo.setObjectName("appMark")
        logo.setPixmap(mark)
        title = QLabel("GameMod")
        title.setObjectName("appTitle")
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(logo)
        row.addWidget(title)
        row.addStretch()
        return row

    # Finding games

    def scan(self) -> None:
        """Look for every supported game and update the ticks and crosses."""
        self._entries = {entry.adapter_class.id: entry for entry in library.scan(self._adapters)}
        for row in range(self.game_list.count()):
            item = self.game_list.item(row)
            entry = self._entries[item.data(Qt.UserRole)]
            note = "Game found" if entry.found else "Game not found"
            item.setText(f"{entry.adapter_class.title}\n{note}")
            item.setIcon(self._found_icon if entry.found else self._missing_icon)

        for game_id, page in list(self._pages.items()):
            if game_id == _SETTINGS:
                continue
            entry = self._entries[game_id]
            if isinstance(page, GamePage):
                if entry.found and Path(page.adapter.game_path) == entry.path:
                    page.reload()
                elif not page.is_busy():
                    self._discard_page(game_id)
            elif entry.found:
                self._discard_page(game_id)
        self._select(self.game_list.currentItem())

    def _discard_page(self, game_id: str) -> None:
        page = self._pages.pop(game_id)
        if isinstance(page, GamePage):
            page.shutdown()
        self.stack.removeWidget(page)
        page.deleteLater()

    def _ask_for_folder(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Choose the game's folder")

    def _tell(self, message: str) -> None:
        QMessageBox.information(self, "GameMod", message)

    def _add_game(self, expected: type[GameAdapter] | None = None) -> None:
        """Let the user point at a game's folder. The app works out which game it is."""
        selected = self._ask_for_folder()
        if not selected:
            return
        if expected is not None:
            folder = library.matching_folder(Path(selected), expected)
            match = (expected, folder) if folder else None
            problem = f"That folder doesn't have {expected.title}. Please choose {expected.folder_hint}."
        else:
            match = library.identify_folder(Path(selected), self._adapters)
            names = ", ".join(cls.title for cls in self._adapters)
            problem = f"That folder isn't a game GameMod supports yet. Supported games: {names}."
        if match is None:
            self._tell(problem)
            return
        adapter_class, folder = match
        library.remember_folder(adapter_class.id, folder)
        self.scan()
        for row in range(self.game_list.count()):
            if self.game_list.item(row).data(Qt.UserRole) == adapter_class.id:
                self.game_list.setCurrentRow(row)

    # Pages

    def _select(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        game_id = item.data(Qt.UserRole)
        page = self._pages.get(game_id)
        if page is None:
            entry = self._entries[game_id]
            if entry.found:
                page = GamePage(entry.adapter_class(entry.path))
            else:
                page = MissingGamePage(entry.adapter_class, self._add_game, self.scan)
            self._pages[game_id] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _open_settings(self) -> None:
        self.game_list.clearSelection()
        self.game_list.setCurrentItem(None)
        page = self._pages.get(_SETTINGS)
        if page is None:
            page = SettingsPage()
            self._pages[_SETTINGS] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def closeEvent(self, event) -> None:
        for page in self._pages.values():
            if isinstance(page, GamePage):
                page.shutdown()
        super().closeEvent(event)
