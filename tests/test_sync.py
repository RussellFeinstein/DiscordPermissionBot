"""Tests for services/sync.py — permission plan building and diffing."""

import discord
from unittest.mock import MagicMock

from services import sync, local_store
from tests.conftest import make_mock_role


def _make_category(cat_id, name="test-category"):
    cat = MagicMock(spec=discord.CategoryChannel)
    cat.id = cat_id
    cat.name = name
    cat.__hash__ = lambda self: hash(cat_id)
    cat.__eq__ = lambda self, other: getattr(other, "id", None) == cat_id
    return cat


def _make_channel(chan_id, name="test-channel", category_id=None, synced=True):
    chan = MagicMock(spec=discord.TextChannel)
    chan.id = chan_id
    chan.name = name
    chan.category_id = category_id
    chan.permissions_synced = synced
    chan.overwrites = {}
    chan.__hash__ = lambda self: hash(chan_id)
    chan.__eq__ = lambda self, other: getattr(other, "id", None) == chan_id
    return chan


def _make_guild(guild_id=99999, roles=None, categories=None, channels=None):
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id

    everyone = make_mock_role(guild_id, "@everyone")
    guild.default_role = everyone

    all_roles = [everyone] + (roles or [])
    guild.roles = all_roles

    cats = categories or []
    chans = channels or []
    guild.categories = cats
    guild.channels = cats + chans

    return guild


class TestLevelToOverwrite:
    def test_view_level_allows_view_denies_send(self):
        ow = sync.level_to_overwrite("View", 99999)
        assert ow.view_channel is True
        assert ow.send_messages is False

    def test_none_level_denies_view(self):
        ow = sync.level_to_overwrite("None", 99999)
        assert ow.view_channel is False

    def test_unknown_level_returns_neutral_overwrite(self):
        ow = sync.level_to_overwrite("NonexistentLevel", 99999)
        assert ow.view_channel is None


class TestBuildPermissionPlan:
    def test_empty_config_produces_empty_plan(self):
        guild = _make_guild()
        plan = sync.build_permission_plan(guild)
        assert plan.entries == {}

    def test_category_baseline_produces_everyone_entry(self):
        cat = _make_category(500)
        guild = _make_guild(categories=[cat])
        local_store.set_category_baseline(guild.id, "500", "None")

        plan = sync.build_permission_plan(guild)
        assert 500 in plan.entries
        entries = plan.entries[500]
        assert len(entries) == 1
        assert entries[0].target == guild.default_role
        assert entries[0].overwrite.view_channel is False

    def test_access_rule_adds_role_entry(self):
        cat = _make_category(500)
        role = make_mock_role(111, "Raiders")
        guild = _make_guild(roles=[role], categories=[cat])
        local_store.add_access_rule(guild.id, ["111"], "category", ["500"], "Chat")

        plan = sync.build_permission_plan(guild)
        assert 500 in plan.entries
        role_entries = [e for e in plan.entries[500] if e.target == role]
        assert len(role_entries) == 1
        assert role_entries[0].overwrite.send_messages is True

    def test_missing_category_skipped_gracefully(self):
        guild = _make_guild()
        local_store.set_category_baseline(guild.id, "999", "View")

        plan = sync.build_permission_plan(guild)
        assert 999 not in plan.entries

    def test_missing_role_skipped_gracefully(self):
        cat = _make_category(500)
        guild = _make_guild(categories=[cat])
        local_store.add_access_rule(guild.id, ["999"], "category", ["500"], "Chat")

        plan = sync.build_permission_plan(guild)
        assert 500 not in plan.entries or len(plan.entries.get(500, [])) == 0

    def test_unsynced_channel_gets_baseline_propagated(self):
        cat = _make_category(500)
        role = make_mock_role(111, "Raiders")
        chan = _make_channel(600, category_id=500, synced=False)
        guild = _make_guild(roles=[role], categories=[cat], channels=[chan])

        local_store.set_category_baseline(guild.id, "500", "None")
        local_store.add_access_rule(guild.id, ["111"], "channel", ["600"], "Chat")

        plan = sync.build_permission_plan(guild)
        chan_entries = plan.entries.get(600, [])
        targets = {e.target for e in chan_entries}
        assert guild.default_role in targets
        assert role in targets

    def test_channel_access_rule(self):
        cat = _make_category(500)
        role = make_mock_role(111, "Raiders")
        chan = _make_channel(600, category_id=500, synced=True)
        guild = _make_guild(roles=[role], categories=[cat], channels=[chan])
        local_store.add_access_rule(guild.id, ["111"], "channel", ["600"], "View")

        plan = sync.build_permission_plan(guild)
        assert 600 in plan.entries
        role_entries = [e for e in plan.entries[600] if e.target == role]
        assert len(role_entries) == 1
        assert role_entries[0].overwrite.view_channel is True


class TestDiffPermissionPlan:
    def test_empty_plan_empty_diff(self):
        guild = _make_guild()
        plan = sync.PermissionPlan()
        lines = sync.diff_permission_plan(plan, guild)
        assert lines == []

    def test_new_overwrite_shows_in_diff(self):
        cat = _make_category(500)
        cat.overwrites = {}
        guild = _make_guild(categories=[cat])

        plan = sync.PermissionPlan()
        entry = sync.OverwriteEntry(
            target=guild.default_role,
            overwrite=discord.PermissionOverwrite(view_channel=False),
            source="@everyone baseline -> None",
        )
        plan.add(500, entry)

        lines = sync.diff_permission_plan(plan, guild)
        assert len(lines) == 1
