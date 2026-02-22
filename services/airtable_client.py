"""
airtable_client.py — per-guild Airtable clients with disk-cache fallback.

Each Discord server gets its own AirtableClient, looked up by guild ID.
Credentials come from data/{guild_id}/config.json (set via /setup airtable).

Usage:
    from services.airtable_client import get_airtable, reset_client

    client = get_airtable(guild_id)   # raises RuntimeError if not configured
    reset_client(guild_id)            # call after /setup to pick up new credentials
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pyairtable import Api

from config import TABLES, RoleFields
from services import guild_config

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent.parent / "data")

# Per-guild client instances
_clients: dict[int, "AirtableClient"] = {}


class AirtableClient:
    """
    Fetches and in-memory-caches all Airtable tables for one Discord guild.
    Falls back to data/{guild_id}/airtable_cache.json when Airtable is unreachable.
    """

    def __init__(self, token: str, base_id: str, guild_id: int):
        self._api = Api(token)
        self._base_id = base_id
        self._guild_id = guild_id
        self._cache: dict[str, list] = {}

    # ------------------------------------------------------------------
    # Disk cache
    # ------------------------------------------------------------------

    @property
    def _cache_file(self) -> Path:
        return _DATA_DIR / str(self._guild_id) / "airtable_cache.json"

    def _persist_cache(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(self._cache)
            payload["_saved_at"] = datetime.now(timezone.utc).isoformat()
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as e:
            print(f"[airtable:{self._guild_id}] Warning: could not save disk cache: {e}")

    def _load_disk_cache(self) -> dict:
        try:
            if self._cache_file.exists():
                with open(self._cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                saved_at = data.pop("_saved_at", "unknown")
                print(f"[airtable:{self._guild_id}] Loaded disk cache (saved {saved_at})")
                return data
        except Exception as e:
            print(f"[airtable:{self._guild_id}] Warning: could not read disk cache: {e}")
        return {}

    # ------------------------------------------------------------------
    # Fetch with fallback
    # ------------------------------------------------------------------

    def _table(self, key: str):
        return self._api.table(self._base_id, TABLES[key])

    def _fetch(self, key: str, force: bool = False) -> list:
        if key not in self._cache or force:
            try:
                data = self._table(key).all()
                self._cache[key] = data
                self._persist_cache()
            except Exception as e:
                print(f"[airtable:{self._guild_id}] Failed to fetch '{key}': {e}")
                disk = self._load_disk_cache()
                if key in disk:
                    print(f"[airtable:{self._guild_id}] Using disk cache for '{key}'")
                    self._cache[key] = disk[key]
                else:
                    raise RuntimeError(
                        f"Airtable unavailable and no disk cache for '{key}'. "
                        "Try again once Airtable is reachable."
                    ) from e
        return self._cache[key]

    def refresh(self) -> None:
        """Clear in-memory cache so the next call re-fetches from Airtable."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Table accessors
    # ------------------------------------------------------------------

    def get_roles(self, force: bool = False) -> list:
        return self._fetch("roles", force)

    def get_categories(self, force: bool = False) -> list:
        return self._fetch("categories", force)

    def get_channels(self, force: bool = False) -> list:
        return self._fetch("channels", force)

    def get_access_rules(self, force: bool = False) -> list:
        return self._fetch("access_rules", force)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def roles_by_id(self) -> dict[str, dict]:
        return {r["id"]: r for r in self.get_roles()}

    def categories_by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.get_categories()}

    def channels_by_id(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.get_channels()}

    def get_role_by_name(self, name: str) -> dict | None:
        for r in self.get_roles():
            if r["fields"].get(RoleFields.NAME) == name:
                return r
        return None

    def get_roles_by_exclusive_group(self, group: str) -> list[dict]:
        return [
            r for r in self.get_roles()
            if r["fields"].get(RoleFields.EXCLUSIVE_GROUP) == group
        ]

    def flush_updates(self, updates: list[tuple[str, str, dict]]) -> int:
        """
        Apply a batch of pending field updates to Airtable.
        Each entry is (table_key, airtable_record_id, fields_dict).
        Returns the number of records successfully updated.
        """
        applied = 0
        for table_key, record_id, fields in updates:
            try:
                self._table(table_key).update(record_id, fields)
                applied += 1
            except Exception as e:
                print(f"[airtable:{self._guild_id}] Failed to update record {record_id}: {e}")
        return applied


# ---------------------------------------------------------------------------
# Per-guild client registry
# ---------------------------------------------------------------------------

def get_airtable(guild_id: int) -> AirtableClient:
    """
    Return the AirtableClient for a guild.
    Raises RuntimeError if Airtable has not been configured for this server.
    """
    if guild_id not in _clients:
        config = guild_config.get(guild_id)
        if not config:
            raise RuntimeError(
                "Airtable is not configured for this server. "
                "An admin can run `/setup airtable` to connect an Airtable base."
            )
        _clients[guild_id] = AirtableClient(
            config["airtable_token"],
            config["airtable_base_id"],
            guild_id,
        )
    return _clients[guild_id]


def reset_client(guild_id: int) -> None:
    """Drop the cached client for a guild (call after /setup to pick up new credentials)."""
    _clients.pop(guild_id, None)
