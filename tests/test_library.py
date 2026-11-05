"""Finding games on disk, and subtitle packs. Everything runs in temporary folders."""
import json
import shutil
from pathlib import Path

import pytest

from gamemod.adapters.base import GameAdapter, SourceLine
from gamemod.adapters.tlou1 import psarc
from gamemod.core import library, pack, paths, steam
from gamemod.core import store as st
from gamemod.core.store import Store

GAME = Path(r"D:\SteamLibrary\steamapps\common\The Last of Us Part I")


class Game(GameAdapter):
    id = "demo"
    title = "Demo Game"
    steam_app_id = 42
    mod_title = "Hindi"
    where_to_enable = "Options"
    folder_names = ("Demo Game",)
    required_files = ("data/core.bin", "game.dll")

    def state(self):
        raise NotImplementedError

    def extract_lines(self):
        raise NotImplementedError

    def brief(self):
        raise NotImplementedError

    def install(self, translations, progress=lambda _: None, hindi_scale=1.12):
        raise NotImplementedError

    def uninstall(self, progress=lambda _: None):
        raise NotImplementedError


class OtherGame(Game):
    id = "other"
    title = "Other Game"
    steam_app_id = 43
    folder_names = ("Other Game",)
    required_files = ("other.bin",)


def make_game(folder: Path, cls=Game) -> Path:
    for name in cls.required_files:
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"x")
    return folder


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A pretend PC with an empty Steam and one empty drive."""
    root = tmp_path / "app"
    root.mkdir()
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(steam, "find_game", lambda app_id: None)
    monkeypatch.setattr(steam, "library_folders", lambda: [])
    monkeypatch.setattr(library, "_fixed_drives", lambda: [drive])
    return tmp_path, drive


def test_game_not_found(machine):
    entry = library.scan([Game])[0]
    assert not entry.found and entry.path is None


def test_found_through_steam(machine, monkeypatch):
    tmp_path, _ = machine
    folder = make_game(tmp_path / "anywhere" / "Renamed")
    monkeypatch.setattr(
        steam, "find_game", lambda app_id: steam.InstalledGame(42, "Demo", folder, "1") if app_id == 42 else None
    )
    entry = library.scan([Game])[0]
    assert entry.path == folder and entry.how == library.STEAM


def test_steam_record_for_a_deleted_game_does_not_count(machine, monkeypatch):
    tmp_path, _ = machine
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.setattr(steam, "find_game", lambda app_id: steam.InstalledGame(42, "Demo", gone, "1"))
    assert not library.scan([Game])[0].found


@pytest.mark.parametrize(
    "where",
    [
        "SteamLibrary/steamapps/common/Demo Game",
        "Program Files (x86)/Steam/steamapps/common/Demo Game",
        "Program Files/Epic Games/Demo Game",
        "Games/Demo Game",
    ],
)
def test_found_in_usual_places_without_steam(machine, where):
    _, drive = machine
    folder = make_game(drive / where)
    entry = library.scan([Game])[0]
    assert entry.path == folder and entry.how == library.FOLDER


def test_folder_with_the_right_name_but_wrong_files_does_not_count(machine):
    _, drive = machine
    (drive / "Games" / "Demo Game").mkdir(parents=True)
    assert not library.scan([Game])[0].found


def test_chosen_folder_is_remembered_and_wins(machine):
    tmp_path, drive = machine
    make_game(drive / "Games" / "Demo Game")
    chosen = make_game(tmp_path / "my stuff" / "demo")
    library.remember_folder(Game.id, chosen)
    entry = library.scan([Game])[0]
    assert entry.path == chosen and entry.how == library.MANUAL


def test_chosen_folder_that_was_deleted_falls_back_to_scanning(machine):
    tmp_path, drive = machine
    usual = make_game(drive / "Games" / "Demo Game")
    chosen = make_game(tmp_path / "moved")
    library.remember_folder(Game.id, chosen)
    shutil.rmtree(chosen)
    assert library.scan([Game])[0].path == usual


def test_damaged_saved_list_is_ignored(machine):
    (paths.app_root() / "data").mkdir()
    (paths.app_root() / "data" / "games.json").write_text("not json", encoding="utf-8")
    assert library.saved_folders() == {}


def test_identify_works_out_the_game_even_from_a_subfolder(tmp_path):
    folder = make_game(tmp_path / "x", OtherGame)
    assert library.identify_folder(folder, [Game, OtherGame]) == (OtherGame, folder)
    sub = folder / "a" / "b"
    sub.mkdir(parents=True)
    assert library.identify_folder(sub, [Game, OtherGame]) == (OtherGame, folder)


def test_identify_rejects_an_unsupported_folder(tmp_path):
    assert library.identify_folder(tmp_path, [Game, OtherGame]) is None


# Subtitle packs


def _lines():
    return [
        SourceLine("t", "a", 0, "Oh no", "", prefix="|@46| ", suffix="\n"),
        SourceLine("t", "b", 1, "Run!", ""),
        SourceLine("t", "c", 2, "Joel", ""),
        SourceLine("u", "d", 3, "Hold |gen:fire|", ""),
    ]


def _store(path) -> Store:
    store = Store(path)
    store.sync(_lines(), {"Joel": "जोएल"})
    return store


def _translated(path) -> Store:
    store = _store(path)
    for item in store.pending(10):
        target = "|gen:fire| दबाओ" if "gen:fire" in item.source else "चलो"
        store.save(item.id, target, st.DONE, model="claude")
    return store


def test_friend_gets_exactly_the_same_text_as_the_translator(machine):
    tmp_path, _ = machine
    mine = _translated(tmp_path / "mine.sqlite")
    file = pack.pack_path("demo")
    assert pack.write_pack(file, "demo", "Demo", mine.pack_lines()) == 3
    assert file == paths.app_root() / "subtitles" / "demo" / "hindi.json"

    friend = _store(tmp_path / "friend.sqlite")
    assert friend.count_pending() == 3
    assert pack.load_into_store(friend, "demo")
    assert friend.count_pending() == 0
    assert friend.line_targets() == mine.line_targets()
    assert friend.line_targets()["t"]["a"] == "|@46| चलो\n", "tags come from the player's own game"


def test_pack_holds_no_original_game_text(machine):
    tmp_path, _ = machine
    mine = _translated(tmp_path / "mine.sqlite")
    file = pack.pack_path("demo")
    pack.write_pack(file, "demo", "Demo", mine.pack_lines())
    text = file.read_text(encoding="utf-8")
    assert "Oh no" not in text and "Run!" not in text


def test_no_pack_is_not_an_error(machine):
    tmp_path, _ = machine
    assert pack.load_into_store(_store(tmp_path / "s.sqlite"), "demo") is False


def test_newer_pack_updates_lines_but_keeps_my_edits(machine):
    tmp_path, _ = machine
    file = pack.pack_path("demo")
    pack.write_pack(file, "demo", "Demo", {"t": {"a": "पहला", "b": "दूसरा"}})
    friend = _store(tmp_path / "friend.sqlite")
    pack.load_into_store(friend, "demo")
    edited = friend.search("Run!")[0]
    friend.edit(edited.id, "मेरा अनुवाद")

    pack.write_pack(file, "demo", "Demo", {"t": {"a": "नया", "b": "नया भी"}})
    pack.load_into_store(friend, "demo")
    assert friend.search("Oh no")[0].target == "नया"
    assert friend.search("Run!")[0].target == "मेरा अनुवाद"


def test_pack_does_not_overwrite_lines_translated_on_this_pc(machine):
    tmp_path, _ = machine
    mine = _translated(tmp_path / "mine.sqlite")
    pack.write_pack(pack.pack_path("demo"), "demo", "Demo", {"t": {"a": "दूसरा"}})
    pack.load_into_store(mine, "demo")
    assert mine.search("Oh no")[0].target == "चलो"


def test_bad_lines_in_a_pack_are_skipped(machine):
    tmp_path, _ = machine
    pack.write_pack(pack.pack_path("demo"), "demo", "Demo", {"t": {"a": "English only"}, "u": {"d": "दबाओ"}})
    friend = _store(tmp_path / "friend.sqlite")
    pack.load_into_store(friend, "demo")
    assert friend.search("Oh no")[0].status == st.PENDING  # not Hindi
    assert friend.search("gen:fire")[0].status == st.PENDING  # lost its tag


@pytest.mark.parametrize(
    "content,message",
    [
        ("not json", "damaged"),
        ('{"format": 1, "game": "demo"}', "damaged"),
        ('{"format": 1, "game": "demo", "lines": {"t": {"a": 5}}}', "damaged"),
        ('{"format": 99, "game": "demo", "lines": {}}', "newer version"),
        ('{"format": 1, "game": "else", "lines": {}}', "different game"),
    ],
)
def test_broken_packs_give_a_clear_message(machine, content, message):
    file = pack.pack_path("demo")
    file.parent.mkdir(parents=True)
    file.write_text(content, encoding="utf-8")
    with pytest.raises(pack.PackError, match=message):
        pack.read_pack(file, "demo")


# Telling whether another copy of the app already changed the game


@pytest.mark.skipif(not GAME.exists(), reason="game not installed")
def test_a_patch_is_noticed_without_its_backup(tmp_path):
    main = GAME / "build" / "pc" / "main"
    archive = tmp_path / "steam.psarc"
    shutil.copy(main / "steam.psarc", archive)
    assert not psarc.is_patched(archive)
    with psarc.PsarcReader(archive, GAME / "oo2core_9_win64.dll") as reader:
        target = reader.names()[0]
    backup = tmp_path / "b"
    psarc.patch_in_place(archive, {target: b"x" * 500}, backup, GAME / "oo2core_9_win64.dll")
    assert psarc.is_patched(archive)
    psarc.restore(archive, backup)
    assert not psarc.is_patched(archive)


def test_state_file_format_is_plain_json(machine):
    library.remember_folder("demo", Path("C:/x"))
    saved = json.loads((paths.app_root() / "data" / "games.json").read_text(encoding="utf-8"))
    assert saved == {"demo": str(Path("C:/x"))}
