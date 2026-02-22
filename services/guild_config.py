"""
guild_config.py — per-guild Airtable credentials.

Each Discord server stores its own Airtable token and base ID in
data/{guild_id}/config.json (gitignored).  Server admins configure
this via /setup airtable.
"""

import json
import os
from pathlib import Path

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent.parent / "data")


def _path(guild_id: int) -> Path:
    return _DATA_DIR / str(guild_id) / "config.json"


def get(guild_id: int) -> dict | None:
    """Return {"airtable_token": ..., "airtable_base_id": ...} or None."""
    p = _path(guild_id)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(guild_id: int, token: str, base_id: str) -> None:
    """Persist Airtable credentials for a guild."""
    d = _DATA_DIR / str(guild_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "config.json", "w", encoding="utf-8") as f:
        json.dump({"airtable_token": token, "airtable_base_id": base_id}, f, indent=2)


def is_configured(guild_id: int) -> bool:
    return get(guild_id) is not None
