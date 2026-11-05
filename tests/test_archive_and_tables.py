"""Checks against the real game files. All writes go to temporary copies."""
import hashlib
import shutil
from pathlib import Path

import pytest

from gamemod.adapters.tlou1 import psarc
from gamemod.adapters.tlou1.text_table import TextTable

GAME = Path(r"D:\SteamLibrary\steamapps\common\The Last of Us Part I")
MAIN = GAME / "build" / "pc" / "main"
OODLE = GAME / "oo2core_9_win64.dll"
TABLES = ["text2/eng.subtitles", "text2/eng.subtitles-systemic", "text2/eng.common", "text2/th.subtitles"]

needs_game = pytest.mark.skipif(not MAIN.exists(), reason="game not installed")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def core():
    with psarc.PsarcReader(MAIN / "core.psarc", OODLE) as reader:
        yield reader


@needs_game
@pytest.mark.parametrize("name", TABLES)
def test_text_table_rebuilds_byte_for_byte(core, name):
    data = core.read(name)
    assert TextTable.parse(data).build() == data


@needs_game
def test_text_table_replacement_changes_only_that_line(core):
    table = TextTable.parse(core.read("text2/eng.subtitles"))
    target = next(r for r in table.records if r.text)
    rebuilt = TextTable.parse(table.build({target.key: "नमस्ते".encode("utf-8")}))
    new = rebuilt.as_dict()
    old = table.as_dict()
    assert new[target.key] == "नमस्ते"
    assert sum(1 for k in old if old[k] != new[k]) == 1
    assert [r.key for r in rebuilt.records] == [r.key for r in table.records]


@needs_game
def test_patch_then_restore_is_byte_identical(tmp_path):
    archive = tmp_path / "steam.psarc"
    shutil.copy(MAIN / "steam.psarc", archive)
    before = sha256(archive)
    backup = tmp_path / "steam.backup"

    with psarc.PsarcReader(archive, OODLE) as reader:
        names = reader.names()
        originals = {n: reader.read(n) for n in names}
    target = names[0]
    # Large enough to need new blocks, small enough to fit the manifest's freed room.
    new_data = b"hindi-test" * 200

    psarc.patch_in_place(archive, {target: new_data}, backup, OODLE)
    with psarc.PsarcReader(archive, OODLE) as reader:
        assert reader.read(target) == new_data
        assert reader.names() == names
        for other in names[1:]:
            assert reader.read(other) == originals[other]
    assert sha256(archive) != before

    psarc.restore(archive, backup)
    assert sha256(archive) == before
    assert not backup.exists()


@needs_game
def test_interrupted_patch_can_be_restored(tmp_path):
    archive = tmp_path / "steam.psarc"
    shutil.copy(MAIN / "steam.psarc", archive)
    before = sha256(archive)
    backup = tmp_path / "steam.backup"

    with psarc.PsarcReader(archive, OODLE) as reader:
        target = reader.names()[0]
    psarc.patch_in_place(archive, {target: b"x" * 500}, backup, OODLE)

    # Simulate a crash during the table swap: journal says "patching", table half written.
    state = psarc.read_patch_state(backup)
    state["state"] = "patching"
    psarc._write_state(backup, state)
    with open(archive, "r+b") as f:
        f.seek(40)
        f.write(b"\xff" * 64)

    psarc.restore(archive, backup)
    assert sha256(archive) == before


@needs_game
def test_refuses_second_patch_and_changed_archive(tmp_path):
    archive = tmp_path / "steam.psarc"
    shutil.copy(MAIN / "steam.psarc", archive)
    backup = tmp_path / "steam.backup"
    with psarc.PsarcReader(archive, OODLE) as reader:
        target = reader.names()[0]
    psarc.patch_in_place(archive, {target: b"x" * 500}, backup, OODLE)

    with pytest.raises(psarc.PsarcError):
        psarc.patch_in_place(archive, {target: b"y" * 500}, backup, OODLE)

    # A Steam update replaced the archive: restoring over it must be refused.
    shutil.copy(MAIN / "steam.psarc", archive)
    with pytest.raises(psarc.PsarcError):
        psarc.restore(archive, backup)


@needs_game
def test_new_archive_reads_back(tmp_path):
    path = tmp_path / "new.psarc"
    files = {"text2/a.txt": b"hello", "fonts/big.bin": bytes(range(256)) * 600, "empty.bin": b""}
    psarc.write_archive(path, files)
    with psarc.PsarcReader(path) as reader:
        assert sorted(reader.names()) == sorted(files)
        for name, data in files.items():
            assert reader.read(name) == data
