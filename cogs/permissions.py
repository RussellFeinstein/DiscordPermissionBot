import discord
from discord import app_commands
from discord.ext import commands

from services.airtable_client import get_airtable
from services.sync import build_permission_plan, apply_permission_plan, diff_permission_plan

# Max characters Discord allows in a single message
_DISCORD_MAX = 2000
# Characters reserved for code block wrappers
_CODE_BLOCK_OVERHEAD = 8  # ```\n...\n```

_NOT_CONFIGURED = (
    "Airtable is not configured for this server.\n"
    "An admin can run `/setup airtable` to connect an Airtable base."
)


def _chunk_lines(lines: list[str], max_len: int = _DISCORD_MAX - _CODE_BLOCK_OVERHEAD) -> list[str]:
    """Split a list of lines into chunks that fit within Discord's message limit."""
    chunks, current = [], []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > max_len:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class PermissionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /preview-permissions
    # ------------------------------------------------------------------
    @app_commands.command(
        name="preview-permissions",
        description="Show what /sync-permissions would change without applying anything.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def preview_permissions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        try:
            plan = build_permission_plan(guild)
        except RuntimeError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        lines = diff_permission_plan(plan, guild)

        if not lines:
            await interaction.followup.send("No permission changes detected.", ephemeral=True)
            return

        summary = f"**Permission preview — {len(lines)} overwrite(s)**\n"
        chunks = _chunk_lines(lines)

        await interaction.followup.send(
            summary + f"```\n{chunks[0]}\n```",
            ephemeral=True,
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(f"```\n{chunk}\n```", ephemeral=True)

    # ------------------------------------------------------------------
    # /sync-permissions
    # ------------------------------------------------------------------
    @app_commands.command(
        name="sync-permissions",
        description="Read Airtable and apply all permission levels to Discord.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def sync_permissions(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild

        # Bust the cache so we always work from fresh Airtable data
        try:
            get_airtable(guild.id).refresh()
            plan = build_permission_plan(guild)
        except RuntimeError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        total = sum(len(v) for v in plan.entries.values())
        if total == 0:
            await interaction.followup.send("No overwrites to apply.", ephemeral=True)
            return

        await interaction.followup.send(
            f"Applying **{total}** permission overwrite(s) across "
            f"**{len(plan.entries)}** channel(s)/category(s)…",
            ephemeral=True,
        )

        applied, removed, errors = await apply_permission_plan(plan, guild)

        # Flush any name-drift corrections and Discord ID backfills queued during planning.
        drift_count = 0
        if plan.airtable_updates:
            try:
                drift_count = get_airtable(guild.id).flush_updates(plan.airtable_updates)
            except Exception as e:
                print(f"[sync] Failed to flush Airtable updates: {e}")

        result = f"Done — **{applied}** applied"
        if removed:
            result += f", **{removed}** stale overwrite(s) removed"
        if drift_count:
            result += f", **{drift_count}** Airtable name(s) refreshed"
        result += "."
        if errors:
            result += f"  ⚠️ {errors} error(s) — check bot logs."

        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PermissionsCog(bot))
