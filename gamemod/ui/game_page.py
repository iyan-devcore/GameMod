"""One game's page: translate, review, and turn the mod on or off."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gamemod.adapters.base import GameAdapter, InstallState
from gamemod.core import store as st
from gamemod.core.pack import PackError, load_into_store
from gamemod.core.paths import data_dir
from gamemod.core.pipeline import (
    Progress,
    RunResult,
    content_revision,
    install_translations,
    installed_revision,
    run_translation,
)
from gamemod.core.settings import get_api_key, load_settings
from gamemod.core.store import Store
from gamemod.core.translate.gemini import GeminiTranslator
from gamemod.ui.review_page import ReviewPage
from gamemod.ui.theme import ToggleSwitch, Worker, card

_RETRY_AFTER_LIMIT_MS = 60 * 60 * 1000
_RETRY_AFTER_BUSY_MS = 15 * 60 * 1000


@dataclass(frozen=True)
class Snapshot:
    state: InstallState
    counts: dict[str, int]
    revision: str
    installed_revision: str


class GamePage(QWidget):
    def __init__(self, adapter: GameAdapter, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")
        self.adapter = adapter
        self.store: Store | None = None
        self._worker: Worker | None = None
        self._translating = False
        self._changing_game = False
        self._stop = Event()
        self._applying_switch = False
        self._snapshot: Snapshot | None = None

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._start_translating)

        self.review = ReviewPage(self)
        self.review.closed.connect(self._close_review)
        self.review.changed.connect(self._refresh)
        self.review.hide()

        self._main = QWidget(self)
        self._build(self._main)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._main)
        outer.addWidget(self.review)

        self._busy("Loading the game's subtitles...")
        self._run(self._load, self._loaded)

    def _build(self, host: QWidget) -> None:
        root = QVBoxLayout(host)
        root.setContentsMargins(36, 30, 36, 30)
        root.setSpacing(14)

        heading = QLabel(self.adapter.title)
        heading.setObjectName("heading")
        root.addWidget(heading)
        sub = QLabel("Adds Hindi to the game's subtitle choices. English and the menus stay as they are.")
        sub.setObjectName("subheading")
        sub.setWordWrap(True)
        root.addWidget(sub)
        if self.adapter.game_path:
            found = QLabel(f"✓ Game found: {self.adapter.game_path}")
            found.setObjectName("found")
            found.setWordWrap(True)
            root.addWidget(found)

        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        root.addWidget(self.banner)

        # Step 1
        frame, layout = card()
        top = QHBoxLayout()
        title = QLabel("1  Subtitles")
        title.setObjectName("cardTitle")
        top.addWidget(title)
        top.addStretch()
        self.translate_button = QPushButton("Translate with Gemini")
        self.translate_button.setObjectName("primary")
        self.translate_button.clicked.connect(self._toggle_translating)
        top.addWidget(self.translate_button)
        layout.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.translate_status = QLabel()
        self.translate_status.setObjectName("muted")
        self.translate_status.setWordWrap(True)
        layout.addWidget(self.translate_status)
        root.addWidget(frame)

        # Step 2
        frame, layout = card()
        top = QHBoxLayout()
        title = QLabel("2  Review")
        title.setObjectName("cardTitle")
        top.addWidget(title)
        top.addStretch()
        self.review_button = QPushButton("Review lines")
        self.review_button.clicked.connect(self._open_review)
        top.addWidget(self.review_button)
        layout.addLayout(top)
        self.review_status = QLabel()
        self.review_status.setObjectName("muted")
        self.review_status.setWordWrap(True)
        layout.addWidget(self.review_status)
        root.addWidget(frame)

        # Step 3
        frame, layout = card()
        top = QHBoxLayout()
        title = QLabel(f"3  {self.adapter.mod_title}")
        title.setObjectName("cardTitle")
        top.addWidget(title)
        top.addStretch()
        self.update_button = QPushButton("Apply changes")
        self.update_button.clicked.connect(self._install)
        self.update_button.hide()
        top.addWidget(self.update_button)
        self.switch = ToggleSwitch()
        self.switch.toggled.connect(self._switch_changed)
        top.addWidget(self.switch)
        layout.addLayout(top)
        self.install_status = QLabel()
        self.install_status.setObjectName("muted")
        self.install_status.setWordWrap(True)
        layout.addWidget(self.install_status)
        self.where = QLabel(self.adapter.where_to_enable)
        self.where.setObjectName("hint")
        self.where.setWordWrap(True)
        self.where.hide()
        layout.addWidget(self.where)
        root.addWidget(frame)
        root.addStretch()

    # Background work

    def _run(self, job, on_success) -> None:
        worker = Worker(job)
        worker.succeeded.connect(on_success)
        worker.failed.connect(self._failed)
        self._worker = worker
        worker.start()

    def _busy(self, message: str) -> None:
        self.translate_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.update_button.setEnabled(False)
        self.switch.setEnabled(False)
        self.install_status.setText(message)

    def _failed(self, message: str) -> None:
        self._translating = False
        self.banner.setText(message)
        self.banner.show()
        self._refresh()

    def _load(self, _report) -> tuple[Store, str]:
        store = self.store or Store(data_dir(self.adapter.id) / "hindi.sqlite")
        store.sync(self.adapter.extract_lines(), self.adapter.brief().fixed)
        problem = ""
        try:
            load_into_store(store, self.adapter.id)
        except PackError as error:
            problem = str(error)
        return store, problem

    def _loaded(self, result: tuple[Store, str]) -> None:
        self.store, problem = result
        self.review.set_store(self.store)
        if problem:
            self.banner.setText(problem)
            self.banner.show()
        self._refresh()

    def is_busy(self) -> bool:
        return self.store is None or self._translating or self._changing_game

    def reload(self) -> None:
        """Pick up subtitle files added since the app opened."""
        if not self.is_busy():
            self.banner.hide()
            self._run(self._load, self._loaded)

    # Status

    def _read_snapshot(self) -> Snapshot:
        return Snapshot(
            self.adapter.state(),
            dict(self.store.counts()),
            content_revision(self.store.line_targets(), load_settings().hindi_scale),
            installed_revision(self.adapter.id),
        )

    def _refresh(self) -> None:
        if self.store is None:
            return
        snapshot = self._read_snapshot()
        self._snapshot = snapshot
        counts = snapshot.counts
        total = sum(counts.values())
        pending = counts.get(st.PENDING, 0) + counts.get(st.RETRY, 0)
        done = total - pending
        flagged = counts.get(st.REVIEW, 0)
        failed = counts.get(st.FAILED, 0)

        has_lines = done > counts.get(st.FIXED, 0) + counts.get(st.SKIP, 0)

        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.translate_button.setEnabled(True)
        self.translate_button.setVisible(self._translating or pending > 0 or failed > 0)
        if self._translating:
            self.translate_button.setText("Pause")
        elif pending == 0:
            self.translate_button.setText("Retry failed lines")
        else:
            self.translate_button.setText("Translate with Gemini")
        if not self._translating and not self._retry.isActive():
            if not pending:
                self.translate_status.setText(f"✓ All {total:,} lines are ready")
            elif has_lines:
                self.translate_status.setText(f"{done:,} of {total:,} lines are ready")
            else:
                self.translate_status.setText(
                    "✗ Subtitle files not found. Put the subtitles folder next to GameMod.exe, "
                    "then click Scan again."
                )

        self.review_button.setEnabled(True)
        if flagged or failed:
            parts = []
            if flagged:
                parts.append(f"{flagged:,} to check")
            if failed:
                parts.append(f"{failed:,} failed and will show in English")
            self.review_status.setText(", ".join(parts))
        else:
            self.review_status.setText("Nothing flagged. You can still read and edit any line.")

        state = snapshot.state
        installed = state is InstallState.INSTALLED
        self._applying_switch = True
        self.switch.setChecked(installed)
        self._applying_switch = False
        blocked = state in (InstallState.GAME_MISSING, InstallState.INSTALLED_ELSEWHERE)
        self.switch.setEnabled(not blocked and (installed or has_lines))
        self.where.setVisible(installed)

        out_of_date = installed and snapshot.revision != snapshot.installed_revision
        self.update_button.setVisible(
            out_of_date or state in (InstallState.GAME_UPDATED, InstallState.INTERRUPTED)
        )
        self.update_button.setEnabled(has_lines and not self._translating)

        if state is InstallState.GAME_MISSING:
            self.install_status.setText("The game could not be found.")
        elif state is InstallState.INSTALLED_ELSEWHERE:
            self.install_status.setText(
                "GameMod from another folder has already changed this game. Turn it off there first, "
                "or use Verify Integrity in Steam to reset the game."
            )
        elif state is InstallState.GAME_UPDATED:
            self.update_button.setText("Re-install")
            self.install_status.setText("Steam updated the game, which removed the mod. Your translations are safe.")
        elif state is InstallState.INTERRUPTED:
            self.update_button.setText("Repair")
            self.install_status.setText("The last install was interrupted. Click Repair to finish it safely.")
        elif not installed:
            if has_lines and pending:
                self.install_status.setText(
                    f"Off. If you turn it on now, the {pending:,} lines not yet translated will show in English."
                )
            elif has_lines:
                self.install_status.setText("Off. Close the game, then turn this on to add Hindi.")
            else:
                self.install_status.setText("Off. Subtitles are needed first.")
        elif out_of_date:
            self.update_button.setText("Apply changes")
            self.install_status.setText("On. New or edited translations are ready to apply.")
        elif pending:
            self.install_status.setText(f"On. {pending:,} lines still show in English until they are translated.")
        else:
            self.install_status.setText("On and up to date.")

    # Translating

    def _toggle_translating(self) -> None:
        if self._translating:
            self._stop.set()
            self.translate_button.setEnabled(False)
            self.translate_status.setText("Pausing after the current batch...")
            return
        if self._retry.isActive():
            self._retry.stop()
        if self.store and not self.store.count_pending():
            self.store.retry_failed()
        self._start_translating()

    def _start_translating(self) -> None:
        if self._translating or self.store is None:
            return
        key = get_api_key()
        if not key:
            self._failed("Add your Gemini API key in Settings first.")
            return
        self.banner.hide()
        self._stop.clear()
        self._translating = True
        self._refresh()
        store, brief = self.store, self.adapter.brief()

        def job(report) -> RunResult:
            return run_translation(
                store,
                GeminiTranslator(api_key=key),
                brief,
                on_progress=report,
                should_stop=self._stop.is_set,
            )

        worker = Worker(job)
        worker.reported.connect(self._translate_progress)
        worker.succeeded.connect(self._translate_finished)
        worker.failed.connect(self._failed)
        self._worker = worker
        worker.start()

    def _translate_progress(self, progress: Progress) -> None:
        total = sum(self.store.counts().values())
        done = total - self.store.count_pending()
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        extra = f"  ({progress.message})" if progress.message else ""
        self.translate_status.setText(f"Translating: {done:,} of {total:,} lines{extra}")

    def _translate_finished(self, result: RunResult) -> None:
        self._translating = False
        self._refresh()
        remaining = self.store.count_pending()
        if result.stopped == "daily_limit":
            self.translate_status.setText(
                f"Today's free Gemini limit is used up, with {remaining:,} lines left. Progress is saved. "
                "It will keep trying every hour while this app stays open, or click the button tomorrow."
            )
            self._retry.start(_RETRY_AFTER_LIMIT_MS)
        elif result.stopped == "busy":
            self.translate_status.setText("Gemini is busy right now. Trying again in 15 minutes.")
            self._retry.start(_RETRY_AFTER_BUSY_MS)
        elif result.stopped == "cancelled":
            self.translate_status.setText(f"Paused with {remaining:,} lines left. Progress is saved.")

    # Installing

    def _switch_changed(self, on: bool) -> None:
        if self._applying_switch:
            return
        if on:
            self._install()
        else:
            self._uninstall()

    def _install(self) -> None:
        adapter, store = self.adapter, self.store
        scale = load_settings().hindi_scale

        def job(report) -> None:
            install_translations(adapter, store, scale, report)

        self._change_game(job, "Starting...")

    def _uninstall(self) -> None:
        adapter = self.adapter

        def job(report) -> None:
            adapter.uninstall(report)

        self._change_game(job, "Removing...")

    def _change_game(self, job, message: str) -> None:
        self.banner.hide()
        self._busy(message)
        self._changing_game = True

        def guarded(report) -> None:
            try:
                job(report)
            except PermissionError:
                raise RuntimeError(
                    "Windows blocked GameMod from changing the game's files. Close the game and try "
                    "again. If it still happens, right-click GameMod.exe and choose Run as administrator."
                ) from None

        worker = Worker(guarded)
        worker.reported.connect(self._show_install_message)
        worker.succeeded.connect(self._game_changed)
        worker.failed.connect(self._game_change_failed)
        self._worker = worker
        worker.start()

    def _show_install_message(self, message: object) -> None:
        self.install_status.setText(str(message))

    def _game_changed(self, _result: object) -> None:
        self._changing_game = False
        self._refresh()

    def _game_change_failed(self, message: str) -> None:
        self._changing_game = False
        self._failed(message)

    # Review

    def _open_review(self) -> None:
        self._main.hide()
        self.review.show()
        self.review.reload()

    def _close_review(self) -> None:
        self.review.hide()
        self._main.show()
        self._refresh()

    def shutdown(self) -> None:
        self._stop.set()
        self._retry.stop()
        if self._worker is not None:
            # Never abandon a change to the game's files part way. A translation
            # request can simply be dropped, because its results are not saved yet.
            self._worker.wait(180_000 if self._changing_game else 500)
