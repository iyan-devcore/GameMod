"""The app's behaviour, using a fake game. Nothing here touches the real game."""
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from gamemod.adapters.base import GameAdapter, GameRunning, InstallState, SourceLine
from gamemod.core import library, paths, pipeline, steam
from gamemod.core import store as st
from gamemod.core.pack import pack_path, write_pack
from gamemod.core.translate.base import Brief
from gamemod.ui import game_page, window
from gamemod.ui.game_page import GamePage
from gamemod.ui.theme import STYLE, collect_garbage_on_main_thread

LINES = [SourceLine("t", f"k{i}", i, f"Line {i}") for i in range(5)] + [
    SourceLine("t", "joel", 9, "Joel"),
    SourceLine("t", "tag", 10, "Hold |gen:fire|"),
]


class FakeAdapter(GameAdapter):
    id = "fake"
    title = "Fake Game"
    steam_app_id = 111
    mod_title = "Hindi Subtitles"
    where_to_enable = "Options > Language > Subtitles"
    required_files = ("fake.bin",)
    folder_names = ("Fake Game",)

    def __init__(self, game_path=None):
        self.game_path = game_path
        self.current = InstallState.NOT_INSTALLED
        self.installed = None
        self.block_with = None

    def state(self):
        return self.current

    def extract_lines(self):
        return LINES

    def brief(self):
        return Brief("system", {"Joel": "जोएल"})

    def install(self, translations, progress=lambda _: None, hindi_scale=1.12):
        if self.block_with:
            raise self.block_with
        progress("Installing into the game...")
        self.installed = translations
        self.current = InstallState.INSTALLED

    def uninstall(self, progress=lambda _: None):
        if self.block_with:
            raise self.block_with
        self.installed = None
        self.current = InstallState.NOT_INSTALLED


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    collect_garbage_on_main_thread(application)  # the same protection the real app uses
    return application


def wait_for(app, condition, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "data_dir", lambda game: tmp_path)
    monkeypatch.setattr(game_page, "data_dir", lambda game: tmp_path)
    monkeypatch.setattr(game_page, "load_settings", lambda: paths and type("S", (), {"hindi_scale": 1.12}))
    created = GamePage(FakeAdapter())
    wait_for(app, lambda: created.store is not None)
    yield created
    created.shutdown()
    created.store.close()


def translate_all(page):
    for item in page.store.pending(100):
        target = "|gen:fire| दबाओ" if "gen:fire" in item.source else "चलो"
        page.store.save(item.id, target, st.DONE)
    page._refresh()


def test_switch_is_off_and_disabled_before_any_translation(page):
    assert not page.switch.isChecked()
    assert not page.switch.isEnabled()
    assert "Subtitles are needed first" in page.install_status.text()
    assert page.translate_button.text() == "Translate with Gemini"
    assert "Subtitle files not found" in page.translate_status.text()


def test_turning_on_installs_and_shows_where_to_choose_hindi(app, page):
    translate_all(page)
    assert page.switch.isEnabled()
    page.switch.setChecked(True)
    wait_for(app, lambda: page.adapter.current is InstallState.INSTALLED and page.switch.isEnabled())
    assert page.adapter.installed["t"]["k0"] == "चलो"
    assert not page.where.isHidden()
    assert "up to date" in page.install_status.text()


def test_turning_off_uninstalls(app, page):
    translate_all(page)
    page.switch.setChecked(True)
    wait_for(app, lambda: page.adapter.current is InstallState.INSTALLED and page.switch.isEnabled())
    page.switch.setChecked(False)
    wait_for(app, lambda: page.adapter.current is InstallState.NOT_INSTALLED and page.switch.isEnabled())
    assert page.where.isHidden()


def test_game_running_shows_a_message_and_the_switch_goes_back(app, page):
    translate_all(page)
    page.adapter.block_with = GameRunning("Close The Last of Us Part I first.")
    page.switch.setChecked(True)
    wait_for(app, lambda: not page.banner.isHidden())
    assert "Close" in page.banner.text()
    assert not page.switch.isChecked()


def test_edit_after_install_offers_apply_changes(app, page):
    translate_all(page)
    page.switch.setChecked(True)
    wait_for(app, lambda: page.adapter.current is InstallState.INSTALLED and page.switch.isEnabled())
    assert page.update_button.isHidden()

    item = page.store.search("Line 0")[0]
    page.store.edit(item.id, "मेरा अनुवाद")
    page._refresh()
    assert not page.update_button.isHidden()
    assert "Apply changes" == page.update_button.text()

    page.update_button.click()
    wait_for(app, lambda: page.update_button.isHidden() and page.switch.isEnabled())
    assert page.adapter.installed["t"]["k0"] == "मेरा अनुवाद"


def test_steam_update_offers_reinstall(page):
    translate_all(page)
    page.adapter.current = InstallState.GAME_UPDATED
    page._refresh()
    assert page.update_button.text() == "Re-install"
    assert not page.update_button.isHidden()


def test_interrupted_install_offers_repair(page):
    translate_all(page)
    page.adapter.current = InstallState.INTERRUPTED
    page._refresh()
    assert page.update_button.text() == "Repair"


def test_missing_key_is_explained(app, page, monkeypatch):
    monkeypatch.setattr(game_page, "get_api_key", lambda: "")
    page.translate_button.click()
    wait_for(app, lambda: not page.banner.isHidden())
    assert "API key" in page.banner.text()


def test_review_saves_good_edit_and_refuses_broken_tags(app, page):
    translate_all(page)
    page._open_review()
    table = page.review.table
    rows = {table.item(r, 0).text(): r for r in range(table.rowCount())}

    table.item(rows["Line 1"], 1).setText("नया अनुवाद")
    assert page.store.search("Line 1")[0].target == "नया अनुवाद"
    assert page.store.search("Line 1")[0].status == st.EDITED
    assert table.item(rows["Line 1"], 2).text() == "Edited"

    table.item(rows["Hold |gen:fire|"], 1).setText("दबाओ")
    assert "Not saved" in page.review.message.text()
    assert page.store.search("gen:fire")[0].target == "|gen:fire| दबाओ"

    page.review.search.setText("Line 3")
    page.review.reload()
    assert table.rowCount() == 1


def test_all_lines_ready_hides_the_translate_button(page):
    translate_all(page)
    assert page.translate_button.isHidden()
    assert "All 7 lines are ready" in page.translate_status.text()


def test_pack_added_later_is_picked_up_without_gemini(app, page):
    assert page.store.count_pending() == 6
    lines = {"t": {f"k{i}": f"पंक्ति {i}" for i in range(5)}}
    lines["t"]["tag"] = "|gen:fire| दबाओ"
    write_pack(pack_path("fake"), "fake", "Fake Game", lines)

    page.reload()
    wait_for(app, lambda: page.store.count_pending() == 0 and page.switch.isEnabled())
    assert "All 7 lines are ready" in page.translate_status.text()
    assert page.translate_button.isHidden()


def test_damaged_pack_shows_a_clear_message(app, page):
    file = pack_path("fake")
    file.parent.mkdir(parents=True)
    file.write_text("not json", encoding="utf-8")
    page.reload()
    wait_for(app, lambda: not page.banner.isHidden())
    assert "damaged" in page.banner.text()


def test_change_made_by_another_copy_of_the_app_blocks_the_switch(page):
    translate_all(page)
    page.adapter.current = InstallState.INSTALLED_ELSEWHERE
    page._refresh()
    assert not page.switch.isEnabled()
    assert "another folder" in page.install_status.text()


def test_blocked_by_windows_explains_what_to_do(app, page):
    translate_all(page)
    page.adapter.block_with = PermissionError("Access is denied")
    page.switch.setChecked(True)
    wait_for(app, lambda: not page.banner.isHidden())
    assert "Run as administrator" in page.banner.text()
    assert not page.switch.isChecked()


# The game list


class OtherAdapter(FakeAdapter):
    id = "other"
    title = "Other Game"
    steam_app_id = 222
    required_files = ("other.bin",)
    folder_names = ("Other Game",)


@pytest.fixture
def pc(app, tmp_path, monkeypatch):
    """A pretend PC where Fake Game is installed and Other Game is not."""
    drive = tmp_path / "drive"
    (drive / "Games" / "Fake Game").mkdir(parents=True)
    (drive / "Games" / "Fake Game" / "fake.bin").write_bytes(b"x")
    monkeypatch.setattr(paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(pipeline, "data_dir", lambda game: tmp_path)
    monkeypatch.setattr(game_page, "data_dir", lambda game: tmp_path / game)
    monkeypatch.setattr(steam, "find_game", lambda app_id: None)
    monkeypatch.setattr(steam, "library_folders", lambda: [])
    monkeypatch.setattr(library, "_fixed_drives", lambda: [drive])
    (tmp_path / "fake").mkdir()
    (tmp_path / "other").mkdir()
    main = window.MainWindow([FakeAdapter, OtherAdapter])
    main.told = []
    main._tell = main.told.append
    yield main
    main.close()
    for page in main._pages.values():
        if isinstance(page, GamePage) and page.store:
            wait_for(app, lambda: page.store is not None)
            page.store.close()


def other_game_folder(pc_window, tmp_path) -> Path:
    folder = tmp_path / "somewhere" / "Other"
    folder.mkdir(parents=True)
    (folder / "other.bin").write_bytes(b"x")
    return folder


def test_window_shows_logo_and_lists_each_game_found_or_not(app, pc):
    assert not pc.windowIcon().isNull()
    assert not pc.findChild(QLabel, "appMark").pixmap().isNull()
    assert pc.game_list.count() == 2
    assert "Game found" in pc.game_list.item(0).text()
    assert "Game not found" in pc.game_list.item(1).text()
    assert not pc.game_list.item(0).icon().isNull()
    assert not pc.game_list.item(1).icon().isNull()
    assert isinstance(pc.stack.currentWidget(), GamePage)
    wait_for(app, lambda: pc.stack.currentWidget().store is not None)


def test_missing_game_shows_a_page_with_a_way_to_fix_it(pc):
    pc.game_list.setCurrentRow(1)
    assert isinstance(pc.stack.currentWidget(), window.MissingGamePage)


def test_choosing_the_folder_turns_the_cross_into_a_tick_and_is_remembered(app, pc, tmp_path):
    folder = other_game_folder(pc, tmp_path)
    pc._ask_for_folder = lambda: str(folder)
    pc.game_list.setCurrentRow(1)
    pc.stack.currentWidget().choose_button.click()

    assert "Game found" in pc.game_list.item(1).text()
    page = pc.stack.currentWidget()
    assert isinstance(page, GamePage) and page.adapter.game_path == folder
    wait_for(app, lambda: page.store is not None)
    assert library.saved_folders()["other"] == str(folder)


def test_choosing_the_wrong_folder_explains_and_changes_nothing(pc, tmp_path):
    pc._ask_for_folder = lambda: str(tmp_path)
    pc.game_list.setCurrentRow(1)
    pc.stack.currentWidget().choose_button.click()
    assert "doesn't have Other Game" in pc.told[0]
    assert "Game not found" in pc.game_list.item(1).text()
    assert library.saved_folders() == {}


def test_cancelling_the_folder_picker_does_nothing(pc):
    pc._ask_for_folder = lambda: ""
    pc.add_button.click()
    assert pc.told == []


def test_add_game_manually_works_out_which_game_it_is(app, pc, tmp_path):
    folder = other_game_folder(pc, tmp_path)
    pc._ask_for_folder = lambda: str(folder)
    pc.add_button.click()
    assert "Game found" in pc.game_list.item(1).text()
    assert pc.game_list.currentRow() == 1
    wait_for(app, lambda: pc.stack.currentWidget().store is not None)


def test_add_game_manually_rejects_an_unsupported_game(pc, tmp_path):
    pc._ask_for_folder = lambda: str(tmp_path)
    pc.add_button.click()
    assert "isn't a game GameMod supports" in pc.told[0]


def test_scan_again_finds_a_game_installed_since_opening(app, pc, tmp_path):
    pc.game_list.setCurrentRow(1)
    folder = tmp_path / "drive" / "Games" / "Other Game"
    folder.mkdir()
    (folder / "other.bin").write_bytes(b"x")
    pc.scan_button.click()
    assert "Game found" in pc.game_list.item(1).text()
    page = pc.stack.currentWidget()
    assert isinstance(page, GamePage)
    wait_for(app, lambda: page.store is not None)


def test_settings_still_opens(pc):
    pc._open_settings()
    assert pc.stack.currentWidget() is pc._pages["settings"]
