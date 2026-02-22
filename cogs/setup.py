"""
setup.py — /setup commands for per-guild Airtable configuration.

/setup airtable        — opens a modal to enter Airtable token + base ID,
                         validates credentials, offers to auto-create missing tables
/setup import-discord  — populates Roles, Categories, and Channels tables from Discord
/setup status          — shows what's configured for this server
"""

import discord
from discord import app_commands
from discord.ext import commands
from pyairtable import Api

from services import guild_config, airtable_schema
from services.airtable_client import reset_client
from config import TABLES, RoleFields, CategoryFields, ChannelFields


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class AirtableSetupModal(discord.ui.Modal, title="Connect Airtable"):
    token = discord.ui.TextInput(
        label="Personal Access Token",
        placeholder="pat...",
        style=discord.TextStyle.short,
        required=True,
    )
    base_id = discord.ui.TextInput(
        label="Base ID",
        placeholder="appXXXXXXXX",
        style=discord.TextStyle.short,
        required=True,
        min_length=4,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        token = self.token.value.strip()
        base_id = self.base_id.value.strip()

        if not base_id.startswith("app"):
            await interaction.followup.send(
                "Base ID should start with `app` — check the URL of your Airtable base.",
                ephemeral=True,
            )
            return

        # Validate credentials by attempting to list tables
        try:
            missing = airtable_schema.check_missing(token, base_id)
        except Exception as e:
            await interaction.followup.send(
                f"Could not connect to Airtable: `{e}`\n\n"
                "Check that your token is correct and has `data.records:read` + "
                "`schema.bases:read` scopes with access to this base.",
                ephemeral=True,
            )
            return

        # Save and reset cached client so the next call picks up the new credentials
        guild_config.save(interaction.guild_id, token, base_id)
        reset_client(interaction.guild_id)

        if not missing:
            await interaction.followup.send(
                "Airtable connected! All required tables found.\n"
                "Use `/sync-permissions` to apply permissions from Airtable.",
                ephemeral=True,
            )
        else:
            missing_fmt = "\n".join(f"  • **{t}**" for t in missing)
            embed = discord.Embed(
                title="Airtable Connected",
                description=(
                    f"Credentials saved. The following tables are missing from your base:\n"
                    f"{missing_fmt}\n\n"
                    "The bot can create them automatically with the correct schema."
                ),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(
                embed=embed,
                view=_CreateTablesView(token, base_id),
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Follow-up view after detecting missing tables
# ---------------------------------------------------------------------------

class _CreateTablesView(discord.ui.View):
    def __init__(self, token: str, base_id: str):
        super().__init__(timeout=120)
        self._token = token
        self._base_id = base_id

    @discord.ui.button(label="Create missing tables", style=discord.ButtonStyle.success)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            created = airtable_schema.create_missing(self._token, self._base_id)
            created_fmt = ", ".join(f"**{t}**" for t in created)
            await interaction.followup.send(
                f"Created: {created_fmt}\n\n"
                "Your Airtable base is ready. Add your roles, categories, and channels, "
                "then run `/sync-permissions`.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to create tables: `{e}`\n\n"
                "Make sure your token has the `schema.bases:write` scope.",
                ephemeral=True,
            )
        self.stop()

    @discord.ui.button(label="I'll set them up manually", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Skipped. See the README for the required table schema.",
            ephemeral=True,
        )
        self.stop()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    setup_group = app_commands.Group(
        name="setup",
        description="Configure this bot for your server",
        default_permissions=discord.Permissions(administrator=True),
    )

    @setup_group.command(
        name="airtable",
        description="Connect your Airtable base for permission sync",
    )
    async def setup_airtable(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AirtableSetupModal())

    @setup_group.command(
        name="import-discord",
        description="Populate Roles, Categories, and Channels tables from this Discord server",
    )
    async def setup_import_discord(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        config = guild_config.get(interaction.guild_id)
        if not config:
            await interaction.followup.send(
                "Airtable is not configured. Run `/setup airtable` first.",
                ephemeral=True,
            )
            return

        token = config["airtable_token"]
        base_id = config["airtable_base_id"]
        guild = interaction.guild

        try:
            api = Api(token)
            base = api.base(base_id)

            roles_table      = base.table(TABLES["roles"])
            categories_table = base.table(TABLES["categories"])
            channels_table   = base.table(TABLES["channels"])

            # Fetch names already in Airtable so we skip duplicates
            existing_roles = {
                r["fields"].get(RoleFields.NAME)
                for r in roles_table.all(fields=[RoleFields.NAME])
            }
            existing_cats = {
                r["fields"].get(CategoryFields.NAME)
                for r in categories_table.all(fields=[CategoryFields.NAME])
            }
            existing_channels = {
                r["fields"].get(ChannelFields.NAME)
                for r in channels_table.all(fields=[ChannelFields.NAME])
            }

            # Roles — skip @everyone and any already present
            new_roles = [
                {RoleFields.NAME: role.name}
                for role in guild.roles
                if role.name != "@everyone" and role.name not in existing_roles
            ]

            # Categories
            new_cats = [
                {CategoryFields.NAME: cat.name}
                for cat in guild.categories
                if cat.name not in existing_cats
            ]

            # All non-category channels
            new_channels = [
                {ChannelFields.NAME: ch.name}
                for ch in guild.channels
                if not isinstance(ch, discord.CategoryChannel)
                and ch.name not in existing_channels
            ]

            if new_roles:
                roles_table.batch_create(new_roles)
            if new_cats:
                categories_table.batch_create(new_cats)
            if new_channels:
                channels_table.batch_create(new_channels)

            # Drop the cached client so the next call re-fetches fresh data
            reset_client(interaction.guild_id)

        except Exception as e:
            await interaction.followup.send(
                f"Import failed: `{e}`",
                ephemeral=True,
            )
            return

        skipped_roles    = len(existing_roles)
        skipped_cats     = len(existing_cats)
        skipped_channels = len(existing_channels)

        lines = ["**Import complete:**"]
        lines.append(f"  Roles: {len(new_roles)} added, {skipped_roles} already existed")
        lines.append(f"  Categories: {len(new_cats)} added, {skipped_cats} already existed")
        lines.append(f"  Channels: {len(new_channels)} added, {skipped_channels} already existed")
        lines.append("\nNext: open Airtable and fill in `Exclusive Group` for roles and `Baseline Permission` for categories, then create your Access Rules.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @setup_group.command(
        name="status",
        description="Show what's configured for this server",
    )
    async def setup_status(self, interaction: discord.Interaction):
        from services import local_store

        gid = interaction.guild_id
        configured = guild_config.is_configured(gid)

        embed = discord.Embed(
            title="Bot Status",
            color=discord.Color.green() if configured else discord.Color.orange(),
        )
        embed.add_field(
            name="Airtable",
            value=(
                "Connected — use `/sync-permissions` to apply permissions"
                if configured
                else "Not configured — run `/setup airtable`"
            ),
            inline=False,
        )

        levels = local_store.get_permission_levels(gid)
        bundles = local_store.get_bundles(gid)
        embed.add_field(name="Permission levels", value=str(len(levels)), inline=True)
        embed.add_field(name="Role bundles",      value=str(len(bundles)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupCog(bot))
