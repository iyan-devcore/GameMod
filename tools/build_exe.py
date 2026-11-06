"""Build GameMod.exe with PyInstaller. build.bat and the GitHub release workflow both run this.

    python tools/build_exe.py

The exe is written next to this repo's README. Every game's glossary is bundled, so a new
game only needs its adapter folder.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build_tmp"


def main() -> None:
    args = [
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "GameMod",
        "--distpath", str(ROOT),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        "--icon", str(ROOT / "assets" / "logo" / "gamemod.ico"),
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
    ]
    for glossary in sorted((ROOT / "gamemod" / "adapters").glob("*/glossary.yaml")):
        args += ["--add-data", f"{glossary}{os.pathsep}gamemod/adapters/{glossary.parent.name}"]
    args += ["--hidden-import", "keyring.backends.Windows"]
    for module in ("PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "numpy", "PIL", "pytest"):
        args += ["--exclude-module", module]
    args.append(str(ROOT / "gamemod" / "main.py"))
    PyInstaller.__main__.run(args)
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
