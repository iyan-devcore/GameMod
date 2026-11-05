"""Put together the folder to share: GameMod.exe, the subtitles folder and a short guide.

Run build.bat, which calls this. The result is release/GameMod and release/GameMod.zip.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
FOLDER = RELEASE / "GameMod"


def main() -> None:
    packs = sorted((ROOT / "subtitles").glob("*/*.json"))
    if not packs:
        raise SystemExit("There are no subtitle packs. Run: python -m gamemod.cli export-pack")
    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    FOLDER.mkdir(parents=True)
    shutil.copy2(ROOT / "GameMod.exe", FOLDER / "GameMod.exe")
    shutil.copytree(ROOT / "subtitles", FOLDER / "subtitles")
    shutil.copy2(ROOT / "tools" / "release_readme.txt", FOLDER / "README.txt")
    archive = shutil.make_archive(str(RELEASE / "GameMod"), "zip", root_dir=RELEASE, base_dir="GameMod")
    size = Path(archive).stat().st_size / (1024 * 1024)
    print(f"Release ready: {FOLDER}")
    print(f"To send: {archive} ({size:.0f} MB), with packs: {[p.parent.name for p in packs]}")


if __name__ == "__main__":
    main()
