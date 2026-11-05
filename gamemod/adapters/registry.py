"""Games the mod manager supports. To add one, write its adapter and list it here."""
from __future__ import annotations

from gamemod.adapters.base import GameAdapter
from gamemod.adapters.tlou1.adapter import Tlou1Adapter

ADAPTERS: dict[int, type[GameAdapter]] = {
    Tlou1Adapter.steam_app_id: Tlou1Adapter,
}
