"""Tests for services/access.py — scope-based bot access control."""

import pytest

from services import access, local_store
from tests.conftest import make_mock_interaction


class TestUserHasScope:
    def test_admin_always_passes(self):
        i = make_mock_interaction(is_admin=True)
        assert access.user_has_scope(i, "assign") is True

    def test_non_admin_without_scope_fails(self):
        i = make_mock_interaction(is_admin=False, role_ids=[111])
        assert access.user_has_scope(i, "assign") is False

    def test_non_admin_with_scope_passes(self):
        local_store.grant_bot_scope(99999, "111", ["assign"])
        i = make_mock_interaction(is_admin=False, role_ids=[111])
        assert access.user_has_scope(i, "assign") is True

    def test_wrong_scope_fails(self):
        local_store.grant_bot_scope(99999, "111", ["assign"])
        i = make_mock_interaction(is_admin=False, role_ids=[111])
        assert access.user_has_scope(i, "sync") is False


class TestCheckScope:
    @pytest.mark.asyncio
    async def test_admin_bypasses_check(self):
        i = make_mock_interaction(is_admin=True, command_name="status")
        assert await access.check_scope(i) is True

    @pytest.mark.asyncio
    async def test_no_guild_denied(self):
        i = make_mock_interaction(command_name="status")
        i.guild = None
        result = await access.check_scope(i)
        assert result is False
        i.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_command_denied(self):
        i = make_mock_interaction(is_admin=False, command_name="unknown-cmd")
        result = await access.check_scope(i)
        assert result is False

    @pytest.mark.asyncio
    async def test_scope_granted_passes(self):
        local_store.grant_bot_scope(99999, "111", ["status"])
        i = make_mock_interaction(is_admin=False, role_ids=[111], command_name="status")
        assert await access.check_scope(i) is True

    @pytest.mark.asyncio
    async def test_scope_denied_sends_error(self):
        i = make_mock_interaction(is_admin=False, role_ids=[111], command_name="sync-permissions")
        result = await access.check_scope(i)
        assert result is False
        i.response.send_message.assert_called_once()


class TestCmdScopeMapping:
    def test_all_scopes_referenced(self):
        referenced = set(access.CMD_SCOPE.values())
        for scope in access.ALL_SCOPES:
            assert scope in referenced, f"Scope '{scope}' is never referenced by CMD_SCOPE"

    def test_all_cmd_scope_values_are_valid(self):
        for cmd, scope in access.CMD_SCOPE.items():
            assert scope in access.ALL_SCOPES, (
                f"CMD_SCOPE['{cmd}'] = '{scope}' is not in ALL_SCOPES"
            )
