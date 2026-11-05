"""API key and Hindi text size."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gamemod.core.settings import get_api_key, load_settings, save_settings, set_api_key
from gamemod.core.translate.gemini import GeminiTranslator
from gamemod.ui.theme import HINDI_FONT, Worker, card


class SettingsPage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")
        self._settings = load_settings()
        self._worker: Worker | None = None
        self._tested_key = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 36, 30)
        root.setSpacing(14)
        heading = QLabel("Settings")
        heading.setObjectName("heading")
        root.addWidget(heading)

        frame, layout = card()
        title = QLabel("Gemini API key")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Paste a new key to replace the current one")
        row.addWidget(self.key, 1)
        self.save_key = QPushButton("Save and test")
        self.save_key.setObjectName("primary")
        self.save_key.clicked.connect(self._save_key)
        row.addWidget(self.save_key)
        layout.addLayout(row)
        self.key_status = QLabel("A key is set." if get_api_key() else "No key set yet.")
        self.key_status.setObjectName("muted")
        self.key_status.setWordWrap(True)
        layout.addWidget(self.key_status)
        root.addWidget(frame)

        frame, layout = card()
        title = QLabel("Hindi text size")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(90, 140)
        self.size.setSingleStep(2)
        self.size.setValue(round(self._settings.hindi_scale * 100))
        self.size.valueChanged.connect(self._size_changed)
        row.addWidget(self.size, 1)
        self.size_value = QLabel()
        self.size_value.setMinimumWidth(48)
        row.addWidget(self.size_value)
        layout.addLayout(row)
        self.preview = QLabel("चलो, यहाँ से निकलते हैं! क्या तुम ठीक हो?")
        layout.addWidget(self.preview)
        note = QLabel(
            "This changes Hindi only, compared with the game's English text. "
            "Use Apply changes on the game page afterwards. The game's own subtitle size setting still works too."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        root.addWidget(frame)
        root.addStretch()
        self._size_changed(self.size.value())

    def _size_changed(self, value: int) -> None:
        self.size_value.setText(f"{value}%")
        self.preview.setFont(QFont(HINDI_FONT, max(8, round(16 * value / 100))))
        self._settings.hindi_scale = value / 100
        save_settings(self._settings)

    def _save_key(self) -> None:
        text = self.key.text().strip() or get_api_key()
        if not text:
            self.key_status.setText("Paste a key first.")
            return
        self.save_key.setEnabled(False)
        self.key_status.setText("Testing the key...")

        def job(_report) -> None:
            GeminiTranslator(api_key=text).check()

        self._tested_key = text
        worker = Worker(job)
        worker.succeeded.connect(self._key_ok)
        worker.failed.connect(self._key_failed)
        self._worker = worker
        worker.start()

    def _key_ok(self, _result: object) -> None:
        set_api_key(self._tested_key)
        self.key.clear()
        self.save_key.setEnabled(True)
        self.key_status.setText("The key works and is saved in Windows Credential Manager.")

    def _key_failed(self, message: str) -> None:
        self.save_key.setEnabled(True)
        self.key_status.setText(f"The key did not work: {message}")
