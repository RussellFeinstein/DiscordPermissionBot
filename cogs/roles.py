"""
roles.py — /assign and /remove commands.

Bundles (named collections of roles applied together) are managed via
/bundle commands in cogs/admin.py and stored in data/{guild_id}/bundles.json.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import RoleFields, ExclusiveGroups
from services.airtable_client import get_airtable
from services import local_store


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------

def _exclusive_group_roles(
    guild: discord.Guild,
    group: str,
    guild_id: int,
) -> list[discord.Role]:
    """
    Return every Discord role that belongs to a given exclusive group
    (as defined in the Airtable Roles table).
    """
    airtable = get_airtable(guild_id)
    group_role_names = {
        r["fields"].get(RoleFields.NAME)
        for r in airtable.get_roles()
        if r["fields"].get(RoleFields.EXCLUSIVE_GROUP) == group
    }
    return [r for r in guild.roles if r.name in group_role_names]


async def _apply_bundle(
    member: discord.Member,
    bundle_roles: list[discord.Role],
    guild: discord.Guild,
) -> tuple[list[discord.Role], list[discord.Role]]:
    """
    Add all roles in bundle_roles to the member, automatically removing
    any conflicting roles from the same exclusive group (if Airtable is configured).

    Returns (added, removed).
    """
    added: list[discord.Role] = []
    to_remove: list[discord.Role] = []

    try:
        airtable = get_airtable(guild.id)

        # Find exclusive groups of roles being added
        exclusive_groups: set[str] = set()
        for role in bundle_roles:
            at_role = airtable.get_role_by_name(role.name)
            if at_role:
                group = at_role["fields"].get(RoleFields.EXCLUSIVE_GROUP, ExclusiveGroups.NONE)
                if group != ExclusiveGroups.NONE:
                    exclusive_groups.add(group)

        # Collect conflicting roles the member already has
        for group in exclusive_groups:
            group_roles = _exclusive_group_roles(guild, group, guild.id)
            for gr in group_roles:
                if gr in member.roles and gr not in bundle_roles:
                    to_remove.append(gr)

    except RuntimeError:
        # Airtable not configured — skip exclusive group resolution, just add roles
        pass

    # Apply changes
    if to_remove:
        await member.remove_roles(*to_remove, reason="Exclusive group conflict — bundle assignment")
    await member.add_roles(*bundle_roles, reason="Bundle assignment")

    added = bundle_roles
    return added, to_remove


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /assign
    # ------------------------------------------------------------------
    @app_commands.command(
        name="assign",
        description="Assign a role bundle to one or more members.",
    )
    @app_commands.describe(
        member="Member to assign roles to",
        bundle="The name of the bundle to apply",
        member2="Additional member",
        member3="Additional member",
        member4="Additional member",
        member5="Additional member",
    )
    @app_commands.default_permissions(manage_roles=True)
    async def assign(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        bundle: str,
        member2: discord.Member | None = None,
        member3: discord.Member | None = None,
        member4: discord.Member | None = None,
        member5: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        bundles = local_store.get_bundles(interaction.guild_id)
        if bundle not in bundles:
            names = ", ".join(sorted(bundles.keys())) or "(none defined yet)"
            await interaction.followup.send(
                f"Bundle **{bundle}** not found.\nAvailable bundles: {names}",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        discord_roles_map = {r.name: r for r in guild.roles}

        bundle_roles = [
            discord_roles_map[name]
            for name in bundles[bundle]
            if name in discord_roles_map
        ]
        missing = [name for name in bundles[bundle] if name not in discord_roles_map]

        if not bundle_roles:
            await interaction.followup.send(
                f"No matching Discord roles found for bundle **{bundle}**.",
                ephemeral=True,
            )
            return

        members = [m for m in [member, member2, member3, member4, member5] if m is not None]
        lines = []
        for m in members:
            try:
                added, removed = await _apply_bundle(m, bundle_roles, guild)
                line = f"**{m.display_name}**: added {', '.join(r.name for r in added)}"
                if removed:
                    line += f"; removed (exclusive group) {', '.join(r.name for r in removed)}"
                lines.append(line)
            except discord.Forbidden:
                lines.append(
                    f"**{m.display_name}**: ⚠️ Missing permissions — make sure the bot's role "
                    "is above all roles it needs to manage."
                )

        if missing:
            lines.append("⚠️ Not found in Discord: " + ", ".join(missing))

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @assign.autocomplete("bundle")
    async def assign_bundle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        bundles = local_store.get_bundles(interaction.guild_id)
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted(bundles.keys())
            if current.lower() in name.lower()
        ][:25]

    # ------------------------------------------------------------------
    # /remove
    # ------------------------------------------------------------------
    @app_commands.command(
        name="remove",
        description="Remove a role bundle from one or more members.",
    )
    @app_commands.describe(
        member="Member to remove roles from",
        bundle="The name of the bundle to remove",
        member2="Additional member",
        member3="Additional member",
        member4="Additional member",
        member5="Additional member",
    )
    @app_commands.default_permissions(manage_roles=True)
    async def remove_bundle(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        bundle: str,
        member2: discord.Member | None = None,
        member3: discord.Member | None = None,
        member4: discord.Member | None = None,
        member5: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        bundles = local_store.get_bundles(interaction.guild_id)
        if bundle not in bundles:
            names = ", ".join(sorted(bundles.keys())) or "(none defined yet)"
            await interaction.followup.send(
                f"Bundle **{bundle}** not found.\nAvailable bundles: {names}",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        discord_roles_map = {r.name: r for r in guild.roles}

        members = [m for m in [member, member2, member3, member4, member5] if m is not None]
        lines = []
        for m in members:
            roles_to_remove = [
                discord_roles_map[name]
                for name in bundles[bundle]
                if name in discord_roles_map and discord_roles_map[name] in m.roles
            ]
            if not roles_to_remove:
                lines.append(f"**{m.display_name}**: no roles from this bundle to remove")
                continue
            try:
                await m.remove_roles(*roles_to_remove, reason=f"Bundle removal: {bundle}")
                lines.append(
                    f"**{m.display_name}**: removed {', '.join(r.name for r in roles_to_remove)}"
                )
            except discord.Forbidden:
                lines.append(
                    f"**{m.display_name}**: ⚠️ Missing permissions — make sure the bot's role "
                    "is above all roles it needs to manage."
                )

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @remove_bundle.autocomplete("bundle")
    async def remove_bundle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        bundles = local_store.get_bundles(interaction.guild_id)
        return [
            app_commands.Choice(name=name, value=name)
            for name in sorted(bundles.keys())
            if current.lower() in name.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(RolesCog(bot))
