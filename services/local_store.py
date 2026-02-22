"""
local_store.py — per-guild persistence for permission levels and bundles.

Data lives in data/{guild_id}/ (gitignored).
Falls back to config.py defaults when no file exists yet for that guild.
"""

import copy
import json
import os
from pathlib import Path

from config import PERMISSION_LEVELS_DEFAULT, BUNDLES_DEFAULT

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent.parent / "data")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _guild_dir(guild_id: int) -> Path:
    d = _DATA_DIR / str(guild_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(path: Path, default: dict) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return copy.deepcopy(default)


def _save(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Permission levels
# ---------------------------------------------------------------------------

def get_permission_levels(guild_id: int) -> dict[str, dict[str, bool]]:
    """
    Returns {level_name: {discord_attr: True | False}}.
    Omitted keys mean neutral (inherit from role/server defaults).
    """
    return _load(_guild_dir(guild_id) / "permission_levels.json", PERMISSION_LEVELS_DEFAULT)


def set_permission(guild_id: int, level_name: str, attr: str, value: bool | None) -> None:
    """
    Set a single permission attribute on a level.
    value=None removes the key (neutral/inherit).
    Raises KeyError if level_name does not exist.
    """
    levels = get_permission_levels(guild_id)
    if level_name not in levels:
        raise KeyError(f"Permission level '{level_name}' not found")
    if value is None:
        levels[level_name].pop(attr, None)
    else:
        levels[level_name][attr] = value
    _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def create_level(guild_id: int, name: str, copy_from: str | None = None) -> None:
    """Create a new permission level, optionally cloning an existing one."""
    levels = get_permission_levels(guild_id)
    if name in levels:
        raise ValueError(f"Permission level '{name}' already exists")
    levels[name] = dict(levels[copy_from]) if copy_from else {}
    _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def delete_level(guild_id: int, name: str) -> None:
    levels = get_permission_levels(guild_id)
    if name not in levels:
        raise KeyError(f"Permission level '{name}' not found")
    del levels[name]
    _save(_guild_dir(guild_id) / "permission_levels.json", levels)


def reset_levels_to_default(guild_id: int) -> None:
    """Overwrite the JSON file with the factory defaults from config.py."""
    _save(_guild_dir(guild_id) / "permission_levels.json", copy.deepcopy(PERMISSION_LEVELS_DEFAULT))


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

def get_bundles(guild_id: int) -> dict[str, list[str]]:
    """Returns {bundle_name: [role_name, ...]}."""
    return _load(_guild_dir(guild_id) / "bundles.json", BUNDLES_DEFAULT)


def create_bundle(guild_id: int, name: str) -> None:
    bundles = get_bundles(guild_id)
    if name in bundles:
        raise ValueError(f"Bundle '{name}' already exists")
    bundles[name] = []
    _save(_guild_dir(guild_id) / "bundles.json", bundles)


def delete_bundle(guild_id: int, name: str) -> None:
    bundles = get_bundles(guild_id)
    if name not in bundles:
        raise KeyError(f"Bundle '{name}' not found")
    del bundles[name]
    _save(_guild_dir(guild_id) / "bundles.json", bundles)


def add_role_to_bundle(guild_id: int, bundle_name: str, role_name: str) -> None:
    bundles = get_bundles(guild_id)
    if bundle_name not in bundles:
        raise KeyError(f"Bundle '{bundle_name}' not found")
    if role_name not in bundles[bundle_name]:
        bundles[bundle_name].append(role_name)
        _save(_guild_dir(guild_id) / "bundles.json", bundles)


def remove_role_from_bundle(guild_id: int, bundle_name: str, role_name: str) -> None:
    bundles = get_bundles(guild_id)
    if bundle_name not in bundles:
        raise KeyError(f"Bundle '{bundle_name}' not found")
    bundles[bundle_name] = [r for r in bundles[bundle_name] if r != role_name]
    _save(_guild_dir(guild_id) / "bundles.json", bundles)
