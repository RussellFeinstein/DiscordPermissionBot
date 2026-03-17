"""Tests for services/local_store.py — per-guild JSON persistence."""

import json

import pytest

from config import PERMISSION_LEVELS_DEFAULT
from services import local_store


class TestPermissionLevels:
    def test_get_returns_defaults_when_no_file(self):
        levels = local_store.get_permission_levels(1)
        assert levels == PERMISSION_LEVELS_DEFAULT

    def test_set_permission_persists(self):
        local_store.set_permission(1, "Chat", "send_messages", False)
        levels = local_store.get_permission_levels(1)
        assert levels["Chat"]["send_messages"] is False

    def test_set_permission_none_removes_key(self):
        local_store.set_permission(1, "Chat", "send_messages", None)
        levels = local_store.get_permission_levels(1)
        assert "send_messages" not in levels["Chat"]

    def test_set_permission_unknown_level_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.set_permission(1, "Nonexistent", "view_channel", True)

    def test_create_level_empty(self):
        local_store.create_level(1, "Custom")
        levels = local_store.get_permission_levels(1)
        assert "Custom" in levels
        assert levels["Custom"] == {}

    def test_create_level_copy_from(self):
        local_store.create_level(1, "Custom", copy_from="View")
        levels = local_store.get_permission_levels(1)
        assert levels["Custom"] == PERMISSION_LEVELS_DEFAULT["View"]

    def test_create_level_duplicate_raises(self):
        with pytest.raises(ValueError, match="already exists"):
            local_store.create_level(1, "Chat")

    def test_delete_level(self):
        local_store.delete_level(1, "Admin")
        levels = local_store.get_permission_levels(1)
        assert "Admin" not in levels

    def test_delete_level_unknown_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.delete_level(1, "Nonexistent")

    def test_reset_levels_to_default(self):
        local_store.set_permission(1, "Chat", "send_messages", False)
        local_store.reset_levels_to_default(1)
        levels = local_store.get_permission_levels(1)
        assert levels == PERMISSION_LEVELS_DEFAULT

    def test_guilds_are_isolated(self):
        local_store.create_level(1, "GuildOneOnly")
        levels_2 = local_store.get_permission_levels(2)
        assert "GuildOneOnly" not in levels_2


class TestBundles:
    def test_get_returns_empty_default(self):
        bundles = local_store.get_bundles(1)
        assert bundles == {}

    def test_create_bundle(self):
        local_store.create_bundle(1, "raiders")
        bundles = local_store.get_bundles(1)
        assert bundles == {"raiders": []}

    def test_create_bundle_duplicate_raises(self):
        local_store.create_bundle(1, "raiders")
        with pytest.raises(ValueError, match="already exists"):
            local_store.create_bundle(1, "raiders")

    def test_delete_bundle(self):
        local_store.create_bundle(1, "raiders")
        local_store.delete_bundle(1, "raiders")
        assert "raiders" not in local_store.get_bundles(1)

    def test_delete_bundle_unknown_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.delete_bundle(1, "nope")

    def test_add_role_to_bundle(self):
        local_store.create_bundle(1, "raiders")
        local_store.add_role_to_bundle(1, "raiders", "12345")
        bundles = local_store.get_bundles(1)
        assert "12345" in bundles["raiders"]

    def test_add_role_idempotent(self):
        local_store.create_bundle(1, "raiders")
        local_store.add_role_to_bundle(1, "raiders", "12345")
        local_store.add_role_to_bundle(1, "raiders", "12345")
        assert local_store.get_bundles(1)["raiders"].count("12345") == 1

    def test_remove_role_from_bundle(self):
        local_store.create_bundle(1, "raiders")
        local_store.add_role_to_bundle(1, "raiders", "12345")
        local_store.remove_role_from_bundle(1, "raiders", "12345")
        assert "12345" not in local_store.get_bundles(1)["raiders"]

    def test_add_role_to_missing_bundle_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.add_role_to_bundle(1, "nope", "12345")


class TestExclusiveGroups:
    def test_get_returns_empty_default(self):
        assert local_store.get_exclusive_groups(1) == {}

    def test_create_and_add_role(self):
        local_store.create_exclusive_group(1, "rank")
        local_store.add_role_to_exclusive_group(1, "rank", "111")
        groups = local_store.get_exclusive_groups(1)
        assert "111" in groups["rank"]

    def test_create_duplicate_raises(self):
        local_store.create_exclusive_group(1, "rank")
        with pytest.raises(ValueError, match="already exists"):
            local_store.create_exclusive_group(1, "rank")

    def test_delete_group(self):
        local_store.create_exclusive_group(1, "rank")
        local_store.delete_exclusive_group(1, "rank")
        assert "rank" not in local_store.get_exclusive_groups(1)

    def test_remove_role_from_group(self):
        local_store.create_exclusive_group(1, "rank")
        local_store.add_role_to_exclusive_group(1, "rank", "111")
        local_store.remove_role_from_exclusive_group(1, "rank", "111")
        assert "111" not in local_store.get_exclusive_groups(1)["rank"]


class TestCategoryBaselines:
    def test_get_returns_empty_default(self):
        assert local_store.get_category_baselines(1) == {}

    def test_set_and_get(self):
        local_store.set_category_baseline(1, "100", "View")
        baselines = local_store.get_category_baselines(1)
        assert baselines["100"] == "View"

    def test_clear_baseline(self):
        local_store.set_category_baseline(1, "100", "View")
        local_store.clear_category_baseline(1, "100")
        assert "100" not in local_store.get_category_baselines(1)


class TestAccessRules:
    def test_get_returns_empty_default(self):
        data = local_store.get_access_rules_data(1)
        assert data == {"next_id": 1, "rules": []}

    def test_add_rule_returns_id(self):
        rule_id = local_store.add_access_rule(1, ["111"], "category", ["200"], "Chat")
        assert rule_id == 1

    def test_add_multiple_rules_increments_id(self):
        id1 = local_store.add_access_rule(1, ["111"], "category", ["200"], "Chat")
        id2 = local_store.add_access_rule(1, ["111"], "channel", ["300"], "View")
        assert id2 == id1 + 1

    def test_remove_rule(self):
        rule_id = local_store.add_access_rule(1, ["111"], "category", ["200"], "Chat")
        local_store.remove_access_rule(1, rule_id)
        data = local_store.get_access_rules_data(1)
        assert len(data["rules"]) == 0

    def test_remove_nonexistent_rule_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.remove_access_rule(1, 999)

    def test_update_rule_level(self):
        rule_id = local_store.add_access_rule(1, ["111"], "category", ["200"], "Chat")
        updated = local_store.update_access_rule(1, rule_id, level="Mod")
        assert updated["level"] == "Mod"

    def test_update_nonexistent_rule_raises(self):
        with pytest.raises(KeyError, match="not found"):
            local_store.update_access_rule(1, 999, level="Chat")


class TestBotAccess:
    def test_get_returns_empty_when_no_files(self):
        assert local_store.get_bot_access(1) == {}

    def test_grant_and_get(self):
        local_store.grant_bot_scope(1, "111", ["assign", "bundles"])
        access = local_store.get_bot_access(1)
        assert "assign" in access["111"]
        assert "bundles" in access["111"]

    def test_grant_preserves_scope_ordering(self):
        local_store.grant_bot_scope(1, "111", ["sync", "assign"])
        access = local_store.get_bot_access(1)
        assert access["111"].index("assign") < access["111"].index("sync")

    def test_revoke_scope(self):
        local_store.grant_bot_scope(1, "111", ["assign", "bundles"])
        local_store.revoke_bot_scope(1, "111", ["assign"])
        access = local_store.get_bot_access(1)
        assert "assign" not in access["111"]
        assert "bundles" in access["111"]

    def test_revoke_all_scopes_removes_role(self):
        local_store.grant_bot_scope(1, "111", ["assign"])
        local_store.revoke_bot_scope(1, "111", ["assign"])
        access = local_store.get_bot_access(1)
        assert "111" not in access

    def test_clear_bot_role(self):
        local_store.grant_bot_scope(1, "111", ["assign"])
        assert local_store.clear_bot_role(1, "111") is True
        assert "111" not in local_store.get_bot_access(1)

    def test_clear_bot_role_returns_false_if_not_found(self):
        assert local_store.clear_bot_role(1, "999") is False

    def test_migrate_from_bot_managers(self, tmp_data_dir):
        guild_dir = tmp_data_dir / "1"
        guild_dir.mkdir(parents=True, exist_ok=True)
        old_file = guild_dir / "bot_managers.json"
        old_file.write_text(json.dumps({"role_ids": ["111", "222"]}))

        access = local_store.get_bot_access(1)
        assert len(access["111"]) == 7
        assert len(access["222"]) == 7


class TestPruneHelpers:
    def test_prune_role_list_keeps_valid_ids(self):
        kept, removed = local_store._prune_role_list(["1", "2", "3"], {1, 3})
        assert kept == ["1", "3"]
        assert removed == 1

    def test_prune_role_list_keeps_legacy_names(self):
        kept, removed = local_store._prune_role_list(["OldRoleName", "1"], {1})
        assert "OldRoleName" in kept
        assert removed == 0

    def test_prune_access_rules(self):
        local_store.add_access_rule(1, ["100"], "category", ["200"], "Chat")
        local_store.add_access_rule(1, ["999"], "category", ["200"], "Chat")
        removed = local_store.prune_access_rules(1, valid_role_ids={100}, valid_channel_ids={200})
        assert removed == 1
        data = local_store.get_access_rules_data(1)
        assert len(data["rules"]) == 1

    def test_prune_category_baselines(self):
        local_store.set_category_baseline(1, "100", "View")
        local_store.set_category_baseline(1, "200", "Chat")
        removed = local_store.prune_category_baselines(1, valid_category_ids={100})
        assert removed == 1
        assert "200" not in local_store.get_category_baselines(1)

    def test_prune_bundle_roles(self):
        local_store.create_bundle(1, "raiders")
        local_store.add_role_to_bundle(1, "raiders", "100")
        local_store.add_role_to_bundle(1, "raiders", "999")
        removed = local_store.prune_bundle_roles(1, valid_role_ids={100})
        assert removed == 1
        assert "999" not in local_store.get_bundles(1)["raiders"]

    def test_prune_exclusive_group_roles(self):
        local_store.create_exclusive_group(1, "rank")
        local_store.add_role_to_exclusive_group(1, "rank", "100")
        local_store.add_role_to_exclusive_group(1, "rank", "999")
        removed = local_store.prune_exclusive_group_roles(1, valid_role_ids={100})
        assert removed == 1
        assert "999" not in local_store.get_exclusive_groups(1)["rank"]
