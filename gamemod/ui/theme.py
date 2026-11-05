"""Colors and shared widgets."""
from __future__ import annotations

import gc
import queue
import threading
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QFrame, QVBoxLayout, QWidget

BACKGROUND = "#14161a"
SIDEBAR = "#1a1d22"
CARD = "#20242a"
BORDER = "#30353d"
TEXT = "#ecebe8"
MUTED = "#9aa0a8"
ACCENT = "#e3a33b"
SUCCESS = "#5cb878"
WARNING = "#e3a33b"
DANGER = "#e0605a"

HINDI_FONT = "Nirmala UI"

STYLE = f"""
* {{ font-family: "Segoe UI", "{HINDI_FONT}"; font-size: 14px; color: {TEXT}; }}
QMainWindow, QStackedWidget, #page {{ background: {BACKGROUND}; }}
#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#appTitle {{ font-size: 18px; font-weight: 600; padding: 4px 8px; }}
#sectionLabel {{ color: {MUTED}; font-size: 12px; padding: 12px 8px 4px 8px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 10px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:selected {{ background: {CARD}; color: {TEXT}; }}
QListWidget::item:hover:!selected {{ background: #22262c; }}
#heading {{ font-size: 26px; font-weight: 600; }}
#subheading {{ color: {MUTED}; }}
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
#cardTitle {{ font-size: 16px; font-weight: 600; }}
#muted {{ color: {MUTED}; }}
#found {{ color: {SUCCESS}; }}
#missing {{ color: {DANGER}; font-size: 16px; font-weight: 600; }}
#banner {{ background: #3a2f1b; border: 1px solid {WARNING}; border-radius: 8px; padding: 10px 12px; }}
#hint {{ background: #1b2a20; border: 1px solid #2f5a3c; border-radius: 8px; padding: 10px 12px; }}
QPushButton {{
    background: #2b3036; border: 1px solid {BORDER}; border-radius: 6px; padding: 8px 16px;
}}
QPushButton:hover {{ background: #343a42; }}
QPushButton:disabled {{ color: #6a7078; background: #23272c; }}
QPushButton#primary {{ background: {ACCENT}; color: #15171a; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #efb452; }}
QPushButton#primary:disabled {{ background: #5a4c30; color: #8c8370; }}
QPushButton#link {{ background: transparent; border: none; text-align: left; padding: 10px; }}
QPushButton#link:hover {{ background: #22262c; }}
QProgressBar {{
    background: #2b3036; border: none; border-radius: 4px; height: 8px; max-height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QLineEdit, QComboBox {{
    background: #181b1f; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 10px;
}}
QComboBox QAbstractItemView {{ background: {CARD}; selection-background-color: #343a42; }}
QTableWidget {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BORDER};
    alternate-background-color: #1c2025;
}}
QHeaderView::section {{
    background: {SIDEBAR}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; color: {MUTED};
}}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a4048; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSlider::groove:horizontal {{ background: #2b3036; height: 6px; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -5px 0; border-radius: 8px; }}
"""


def status_icon(good: bool) -> QIcon:
    """A green tick or a red cross, drawn so it never depends on an installed font."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(SUCCESS if good else DANGER))
    painter.drawEllipse(QRectF(2, 2, size - 4, size - 4))
    pen = QPen(QColor("#ffffff"), 8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if good:
        painter.drawPolyline([QPointF(18, 33), QPointF(28, 43), QPointF(46, 23)])
    else:
        painter.drawLine(QPointF(22, 22), QPointF(42, 42))
        painter.drawLine(QPointF(42, 22), QPointF(22, 42))
    painter.end()
    return QIcon(pixmap)


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 18)
    layout.setSpacing(8)
    return frame, layout


class ToggleSwitch(QAbstractButton):
    """A simple on/off switch."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(56, 30)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        on = self.isChecked()
        track = QColor(SUCCESS if on else "#464c54")
        if not self.isEnabled():
            track.setAlpha(90)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, 56, 30), 15, 15)
        painter.setBrush(QColor("#ffffff" if self.isEnabled() else "#a0a0a0"))
        painter.drawEllipse(QRectF(29 if on else 3, 3, 24, 24))


def collect_garbage_on_main_thread(parent: QObject) -> None:
    """Keep Python's cycle collector off the background threads.

    It normally runs on whichever thread happens to be allocating. If that is a worker,
    it can destroy a discarded Qt widget there, and Qt crashes the whole process.
    """
    gc.disable()
    timer = QTimer(parent)
    timer.setInterval(10_000)
    timer.timeout.connect(gc.collect)
    timer.start()


_events: queue.SimpleQueue = queue.SimpleQueue()
_pump: QTimer | None = None


def _deliver_events() -> None:
    while True:
        try:
            callback, value = _events.get_nowait()
        except queue.Empty:
            return
        try:
            callback(value)
        except RuntimeError as error:
            if "already deleted" not in str(error):  # its page has closed, so nobody is listening
                raise


class _Event:
    def __init__(self) -> None:
        self._callbacks: list[Callable[[object], None]] = []

    def connect(self, callback: Callable[[object], None]) -> None:
        self._callbacks.append(callback)

    def post(self, value: object) -> None:
        for callback in self._callbacks:
            _events.put((callback, value))


class Worker:
    """Run a slow job away from the window. The job receives a report function.

    The background thread never touches Qt. Qt can crash the whole process when a thread
    it did not create emits signals and then exits, or when a QThread is destroyed while
    running. Results go on a plain queue, and a timer delivers them on the main thread.
    """

    def __init__(self, job: Callable[[Callable[[object], None]], object]):
        self._job = job
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.reported = _Event()
        self.succeeded = _Event()
        self.failed = _Event()

    def start(self) -> None:
        global _pump
        if _pump is None:
            _pump = QTimer()
            _pump.setInterval(40)
            _pump.timeout.connect(_deliver_events)
            _pump.start()
        self._thread.start()

    def wait(self, milliseconds: int) -> None:
        if self._thread.is_alive():
            self._thread.join(milliseconds / 1000)

    def _run(self) -> None:
        try:
            result = self._job(self.reported.post)
        except Exception as error:  # shown to the user rather than crashing the window
            self.failed.post(str(error) or type(error).__name__)
        else:
            self.succeeded.post(result)
