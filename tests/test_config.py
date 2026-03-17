"""Tests for config.py — permission level and group structural integrity."""

import discord

from config import PERMISSION_LEVELS_DEFAULT, PERMISSION_GROUPS, ALL_PERMISSIONS


class TestPermissionLevelsDefault:
    def test_all_level_attrs_are_valid_overwrite_keys(self):
        valid_attrs = set(discord.PermissionOverwrite.VALID_NAMES)
        for level_name, perms in PERMISSION_LEVELS_DEFAULT.items():
            for attr in perms:
                assert attr in valid_attrs, (
                    f"Level '{level_name}' has invalid attribute '{attr}'"
                )

    def test_all_level_values_are_bool(self):
        for level_name, perms in PERMISSION_LEVELS_DEFAULT.items():
            for attr, value in perms.items():
                assert isinstance(value, bool), (
                    f"Level '{level_name}' attr '{attr}' is {type(value).__name__}"
                )

    def test_none_level_denies_view(self):
        assert PERMISSION_LEVELS_DEFAULT["None"]["view_channel"] is False

    def test_admin_level_grants_administrator(self):
        assert PERMISSION_LEVELS_DEFAULT["Admin"]["administrator"] is True

    def test_expected_levels_exist(self):
        expected = {"None", "View", "Chat", "Mod", "Admin"}
        assert set(PERMISSION_LEVELS_DEFAULT.keys()) == expected


class TestPermissionGroups:
    def test_all_group_attrs_are_valid_overwrite_keys(self):
        valid_attrs = set(discord.PermissionOverwrite.VALID_NAMES)
        for group_name, attrs in PERMISSION_GROUPS.items():
            for attr in attrs:
                assert attr in valid_attrs, (
                    f"Group '{group_name}' has invalid attribute '{attr}'"
                )

    def test_no_duplicate_attrs_within_groups(self):
        for group_name, attrs in PERMISSION_GROUPS.items():
            assert len(attrs) == len(set(attrs)), (
                f"Group '{group_name}' has duplicate attributes"
            )

    def test_no_duplicate_attrs_across_groups(self):
        seen = {}
        for group_name, attrs in PERMISSION_GROUPS.items():
            for attr in attrs:
                assert attr not in seen, (
                    f"'{attr}' appears in both '{seen[attr]}' and '{group_name}'"
                )
                seen[attr] = group_name

    def test_all_permissions_is_flat_union(self):
        expected = [p for group in PERMISSION_GROUPS.values() for p in group]
        assert ALL_PERMISSIONS == expected

    def test_expected_groups_exist(self):
        assert set(PERMISSION_GROUPS.keys()) == {"General", "Text", "Voice"}
