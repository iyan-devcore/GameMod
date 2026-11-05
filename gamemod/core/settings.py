"""App settings. The API key lives in Windows Credential Manager, not in a file."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

import keyring
from keyring.errors import KeyringError

from gamemod.core.paths import app_root

_KEYRING_SERVICE = "GameMod"
_KEYRING_USER = "gemini_api_key"


@dataclass
class Settings:
    hindi_scale: float = 1.12


def _path():
    folder = app_root() / "data"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def load_settings() -> Settings:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return Settings(hindi_scale=float(raw.get("hindi_scale", Settings.hindi_scale)))


def save_settings(settings: Settings) -> None:
    _path().write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def get_api_key() -> str:
    try:
        saved = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except KeyringError:
        saved = None
    return saved or os.environ.get("GOOGLE_API_KEY", "")


def set_api_key(key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key)
