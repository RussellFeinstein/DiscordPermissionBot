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
# Shared import helper (used by both the command and the post-create button)
# ---------------------------------------------------------------------------

async def _run_import(guild: discord.Guild, token: str, base_id: str) -> str:
    """
    Populate Roles, Categories, and Channels tables from the Discord guild.
    Returns a formatted result string (or raises on failure).
    """
    airtable_schema.ensure_discord_id_fields(token, base_id)

    api = Api(token)
    base = api.base(base_id)

    roles_table      = base.table(TABLES["roles"])
    categories_table = base.table(TABLES["categories"])
    channels_table   = base.table(TABLES["channels"])

    def _reconcile(table, discord_objects, name_field, id_field, skip_names=None):
        existing = table.all(fields=[name_field, id_field])

        by_discord_id: dict[str, dict] = {}
        by_name: dict[str, dict] = {}

        for rec in existing:
            f = rec["fields"]
            did = f.get(id_field, "")
            nm  = f.get(name_field, "")
            if did:
                by_discord_id[str(did)] = rec
            if nm:
                by_name[nm] = rec

        to_create = []
        to_update = []
        skipped   = 0

        for obj in discord_objects:
            if skip_names and obj.name in skip_names:
                continue

            discord_id_str = str(obj.id)

            if discord_id_str in by_discord_id:
                rec = by_discord_id[discord_id_str]
                if rec["fields"].get(name_field) != obj.name:
                    to_update.append({"id": rec["id"], "fields": {name_field: obj.name}})
                else:
                    skipped += 1
            elif obj.name in by_name:
                rec = by_name[obj.name]
                to_update.append({"id": rec["id"], "fields": {id_field: discord_id_str}})
            else:
                to_create.append({name_field: obj.name, id_field: discord_id_str})

        if to_create:
            table.batch_create(to_create)
        if to_update:
            table.batch_update(to_update)

        return len(to_create), len(to_update), skipped

    roles_created,    roles_updated,    roles_skipped    = _reconcile(
        roles_table, guild.roles,
        RoleFields.NAME, RoleFields.DISCORD_ID,
        skip_names={"@everyone"},
    )
    cats_created,     cats_updated,     cats_skipped     = _reconcile(
        categories_table, guild.categories,
        CategoryFields.NAME, CategoryFields.DISCORD_ID,
    )
    channels_created, channels_updated, channels_skipped = _reconcile(
        channels_table,
        [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)],
        ChannelFields.NAME, ChannelFields.DISCORD_ID,
    )

    lines = ["**Import complete:**"]
    lines.append(f"  Roles: {roles_created} added, {roles_updated} updated, {roles_skipped} unchanged")
    lines.append(f"  Categories: {cats_created} added, {cats_updated} updated, {cats_skipped} unchanged")
    lines.append(f"  Channels: {channels_created} added, {channels_updated} updated, {channels_skipped} unchanged")
    lines.append(
        "\nNext: open Airtable and fill in `Exclusive Group` for roles and "
        "`Baseline Permission` for categories, then create your Access Rules."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Follow-up view after detecting missing tables
# ---------------------------------------------------------------------------

class _ImportDiscordView(discord.ui.View):
    def __init__(self, token: str, base_id: str):
        super().__init__(timeout=300)
        self._token = token
        self._base_id = base_id

    @discord.ui.button(label="Import roles, categories & channels", style=discord.ButtonStyle.primary)
    async def do_import(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await _run_import(interaction.guild, self._token, self._base_id)
            reset_client(interaction.guild_id)
        except Exception as e:
            await interaction.followup.send(f"Import failed: `{e}`", ephemeral=True)
            self.stop()
            return
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="I'll fill in Airtable manually", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Skipped. Open Airtable and add your roles, categories, and channels manually.",
            ephemeral=True,
        )
        self.stop()


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
                "Would you like to populate the tables with your server's roles, "
                "categories, and channels now?",
                view=_ImportDiscordView(self._token, self._base_id),
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

        try:
            result = await _run_import(
                interaction.guild,
                config["airtable_token"],
                config["airtable_base_id"],
            )
            reset_client(interaction.guild_id)
        except Exception as e:
            await interaction.followup.send(f"Import failed: `{e}`", ephemeral=True)
            return

        await interaction.followup.send(result, ephemeral=True)

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
