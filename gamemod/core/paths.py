"""Where the app keeps its files, both from source and as a packaged exe."""
from __future__ import annotations

import sys
from pathlib import Path


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _source_root()


def asset(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", _source_root())) if getattr(sys, "frozen", False) else _source_root()
    return base.joinpath("assets", *parts)


def data_dir(game_id: str) -> Path:
    path = app_root() / "data" / game_id
    path.mkdir(parents=True, exist_ok=True)
    return path
