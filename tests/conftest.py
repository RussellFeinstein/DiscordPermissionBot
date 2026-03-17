"""Shared test fixtures for DiscordPermissionsManager tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect local_store._DATA_DIR to a temp directory for every test."""
    import services.local_store as ls

    monkeypatch.setattr(ls, "_DATA_DIR", tmp_path)
    ls._locks.clear()
    return tmp_path


def make_mock_role(role_id: int, name: str = "TestRole"):
    """Create a mock discord.Role with the given id and name."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.__hash__ = lambda self: hash(role_id)
    role.__eq__ = lambda self, other: getattr(other, "id", None) == role_id
    return role


def make_mock_interaction(
    guild_id: int = 99999,
    is_admin: bool = False,
    role_ids: list[int] | None = None,
    command_name: str = "status",
):
    """Create a mock discord.Interaction for access control tests."""
    interaction = AsyncMock()

    guild = MagicMock()
    guild.id = guild_id
    interaction.guild = guild
    interaction.guild_id = guild_id

    user = MagicMock()
    perms = MagicMock()
    perms.administrator = is_admin
    user.guild_permissions = perms
    roles = []
    for rid in (role_ids or []):
        r = MagicMock()
        r.id = rid
        roles.append(r)
    user.roles = roles
    interaction.user = user

    command = MagicMock()
    command.qualified_name = command_name
    interaction.command = command
    interaction.response = AsyncMock()

    return interaction
