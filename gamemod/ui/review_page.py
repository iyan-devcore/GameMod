"""Read and edit translations side by side."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gamemod.core import store as st
from gamemod.core.store import Item, Store
from gamemod.core.translate.qa import check_translation
from gamemod.ui.theme import DANGER, HINDI_FONT, MUTED, SUCCESS, WARNING

_PAGE = 200
_FILTERS = [
    ("All lines", None),
    ("Needs checking", (st.REVIEW, st.FAILED)),
    ("Edited by me", (st.EDITED,)),
    ("Not translated yet", (st.PENDING, st.RETRY)),
]
_STATUS_TEXT = {
    st.DONE: ("Done", MUTED),
    st.REVIEW: ("Check", WARNING),
    st.FAILED: ("Failed", DANGER),
    st.EDITED: ("Edited", SUCCESS),
    st.FIXED: ("Glossary", MUTED),
    st.SKIP: ("No words", MUTED),
    st.PENDING: ("Waiting", MUTED),
    st.RETRY: ("Waiting", MUTED),
}


class ReviewPage(QWidget):
    closed = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")
        self.store: Store | None = None
        self._items: list[Item] = []
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 26, 36, 26)
        root.setSpacing(12)

        top = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self.closed)
        top.addWidget(back)
        heading = QLabel("Review lines")
        heading.setObjectName("heading")
        top.addWidget(heading)
        top.addStretch()
        root.addLayout(top)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search English or Hindi")
        self.search.setClearButtonEnabled(True)
        self._search_delay = QTimer(self)
        self._search_delay.setSingleShot(True)
        self._search_delay.setInterval(250)
        self._search_delay.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _text: self._search_delay.start())
        controls.addWidget(self.search, 1)
        self.filter = QComboBox()
        for label, _statuses in _FILTERS:
            self.filter.addItem(label)
        self.filter.currentIndexChanged.connect(lambda _index: self.reload())
        controls.addWidget(self.filter)
        root.addLayout(controls)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["English", "Hindi (double-click to edit)", "Status"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._edited)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.message = QLabel()
        self.message.setObjectName("muted")
        bottom.addWidget(self.message, 1)
        self.more = QPushButton("Show more")
        self.more.clicked.connect(self._load_more)
        bottom.addWidget(self.more)
        root.addLayout(bottom)

    def set_store(self, store: Store) -> None:
        self.store = store

    def reload(self) -> None:
        if self.store is None:
            return
        self._items = []
        self.table.setRowCount(0)
        self._load_more()

    def _load_more(self) -> None:
        statuses = _FILTERS[self.filter.currentIndex()][1]
        batch = self.store.search(self.search.text().strip(), statuses, _PAGE, len(self._items))
        self.more.setVisible(len(batch) == _PAGE)
        self._loading = True
        hindi_font = QFont(HINDI_FONT, 12)
        for item in batch:
            row = self.table.rowCount()
            self.table.insertRow(row)
            english = QTableWidgetItem(item.source)
            english.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 0, english)
            hindi = QTableWidgetItem(item.target or "")
            hindi.setFont(hindi_font)
            if item.note:
                hindi.setToolTip(item.note)
            self.table.setItem(row, 1, hindi)
            label, color = _STATUS_TEXT.get(item.status, (item.status, MUTED))
            status = QTableWidgetItem(label)
            status.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            status.setForeground(QColor(color))
            self.table.setItem(row, 2, status)
        self._items.extend(batch)
        self._loading = False
        self.table.resizeRowsToContents()
        self.message.setText(f"Showing {len(self._items):,} lines" if self._items else "No lines found")

    def _edited(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() != 1:
            return
        item = self._items[cell.row()]
        text = cell.text().strip()
        if text == (item.target or ""):
            return
        check = check_translation(item.source, text)
        self._loading = True
        if check.blocking:
            cell.setText(item.target or "")
            self.message.setText(f"Not saved: {check.blocking[0]}")
            self._loading = False
            return
        self.store.edit(item.id, text)
        self._items[cell.row()] = Item(
            item.id, item.source, item.hint, text, st.EDITED, "", item.first_order
        )
        label, color = _STATUS_TEXT[st.EDITED]
        status = self.table.item(cell.row(), 2)
        status.setText(label)
        status.setForeground(QColor(color))
        self._loading = False
        self.message.setText("Saved. Use Apply changes on the game page to put it in the game.")
        self.changed.emit()
