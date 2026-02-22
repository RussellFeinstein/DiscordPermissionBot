"""
sync.py — builds a permission plan from Airtable + local store, applies it to Discord.

Flow:
  1. build_permission_plan()  → produces a PermissionPlan (pure data, no Discord calls)
  2. apply_permission_plan()  → applies the plan to Discord
  3. diff_permission_plan()   → returns a human-readable list of changes (for /preview)

Permission level definitions come from local_store (config.py defaults + any edits).
Airtable provides role/category/channel structure and access rules.
Raises RuntimeError if Airtable is not configured for the guild (/setup airtable).
"""

from __future__ import annotations
from dataclasses import dataclass, field
import discord

from config import RoleFields, CategoryFields, ChannelFields, AccessRuleFields
from services.airtable_client import get_airtable
from services import local_store


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OverwriteEntry:
    target: discord.Role | discord.Member
    overwrite: discord.PermissionOverwrite
    source: str   # human label e.g. "@everyone baseline → None"


@dataclass
class PermissionPlan:
    """
    Maps each Discord category/channel id to the overwrites that should be set on it.
    plan.entries[channel_or_category_id] = [OverwriteEntry, ...]
    """
    entries: dict[int, list[OverwriteEntry]] = field(default_factory=dict)

    def add(self, target_id: int, entry: OverwriteEntry) -> None:
        self.entries.setdefault(target_id, []).append(entry)


# ---------------------------------------------------------------------------
# Permission level → discord.PermissionOverwrite
# ---------------------------------------------------------------------------

def level_to_overwrite(level_name: str, guild_id: int) -> discord.PermissionOverwrite:
    """
    Look up a named permission level from local_store and convert to a
    discord.PermissionOverwrite.
      True  → explicitly allow
      False → explicitly deny
      key missing → neutral (inherit; discord.py default is None)
    """
    levels = local_store.get_permission_levels(guild_id)
    perms = levels.get(level_name, {})
    return discord.PermissionOverwrite(**perms)


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def build_permission_plan(guild: discord.Guild) -> PermissionPlan:
    """
    Reads Airtable (roles/categories/channels/access rules) and local
    permission level definitions, then produces a PermissionPlan describing
    exactly what overwrites should exist on every category and channel.

    No Discord API write calls are made here.
    Raises RuntimeError if Airtable is not configured for this guild.
    """
    plan = PermissionPlan()
    airtable = get_airtable(guild.id)

    # -- Airtable lookups --
    role_by_id = airtable.roles_by_id()
    cat_by_id = airtable.categories_by_id()
    ch_by_id = airtable.channels_by_id()

    # -- Discord lookups (by name) --
    discord_roles: dict[str, discord.Role] = {r.name: r for r in guild.roles}
    discord_cats: dict[str, discord.CategoryChannel] = {
        c.name: c for c in guild.categories
    }
    discord_channels: dict[str, discord.abc.GuildChannel] = {
        c.name: c for c in guild.channels if not isinstance(c, discord.CategoryChannel)
    }
    everyone = guild.default_role

    # ------------------------------------------------------------------
    # 1. @everyone baseline for every category (Categories.Baseline)
    # ------------------------------------------------------------------
    for cat_rec in airtable.get_categories():
        f = cat_rec["fields"]
        cat_name = f.get(CategoryFields.NAME)
        level_name: str = f.get(CategoryFields.BASELINE, "")

        discord_cat = discord_cats.get(cat_name)
        if not discord_cat or not level_name:
            continue

        plan.add(discord_cat.id, OverwriteEntry(
            target=everyone,
            overwrite=level_to_overwrite(level_name, guild.id),
            source=f"@everyone baseline → {level_name}",
        ))

    # ------------------------------------------------------------------
    # 2. Role-specific overwrites from Access Rules
    # ------------------------------------------------------------------
    for rule in airtable.get_access_rules():
        f = rule["fields"]

        role_refs: list[str] = f.get(AccessRuleFields.ROLES, [])
        scope: str = f.get(AccessRuleFields.CHANNEL_OR_CATEGORY, "")
        cat_refs: list[str] = f.get(AccessRuleFields.CATEGORIES, [])
        ch_refs: list[str] = f.get(AccessRuleFields.CHANNELS, [])
        level_name: str = f.get(AccessRuleFields.PERMISSION_LEVEL, "")
        overwrite_dir: str = f.get(AccessRuleFields.OVERWRITE, "Allow")

        if not role_refs or not level_name:
            continue

        base_overwrite = level_to_overwrite(level_name, guild.id)

        # Deny rules flip every explicit allow → explicit deny.
        if overwrite_dir == "Deny":
            flipped: dict[str, bool] = {}
            for attr, val in base_overwrite:
                if val is True:
                    flipped[attr] = False
                elif val is False:
                    flipped[attr] = True
            final_overwrite = discord.PermissionOverwrite(**flipped)
        else:
            final_overwrite = base_overwrite

        # Resolve target channels/categories
        targets: list[discord.abc.GuildChannel] = []
        if scope == "Category":
            for cat_id in cat_refs:
                at_cat = cat_by_id.get(cat_id)
                if at_cat:
                    dc = discord_cats.get(at_cat["fields"].get(CategoryFields.NAME))
                    if dc:
                        targets.append(dc)
        elif scope == "Channel":
            for ch_id in ch_refs:
                at_ch = ch_by_id.get(ch_id)
                if at_ch:
                    dc = discord_channels.get(at_ch["fields"].get(ChannelFields.NAME))
                    if dc:
                        targets.append(dc)

        # Resolve roles and add entries
        for role_id in role_refs:
            at_role = role_by_id.get(role_id)
            if not at_role:
                continue
            role_name = at_role["fields"].get(RoleFields.NAME)
            discord_role = discord_roles.get(role_name)
            if not discord_role:
                continue

            for target in targets:
                plan.add(target.id, OverwriteEntry(
                    target=discord_role,
                    overwrite=final_overwrite,
                    source=f"{role_name} → {level_name} ({overwrite_dir})",
                ))

    return plan


# ---------------------------------------------------------------------------
# Apply plan
# ---------------------------------------------------------------------------

async def apply_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> tuple[int, int]:
    """
    Apply every overwrite in the plan to Discord.
    Returns (applied_count, error_count).
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    applied = 0
    errors = 0

    for target_id, entries in plan.entries.items():
        channel = channels_by_id.get(target_id)
        if not channel:
            continue
        for entry in entries:
            try:
                await channel.set_permissions(entry.target, overwrite=entry.overwrite)
                applied += 1
            except discord.HTTPException as e:
                print(f"[sync] Failed on #{channel.name} for {entry.target}: {e}")
                errors += 1

    return applied, errors


# ---------------------------------------------------------------------------
# Diff / preview
# ---------------------------------------------------------------------------

def diff_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> list[str]:
    """
    Compare the plan against current Discord state.
    Returns human-readable change lines:
      "📝 #phoenix-raid-chat  |  Phoenix Raid Team  →  Chat (Allow)"
      "✅ #general            |  @everyone           →  Chat (no change)"
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    lines: list[str] = []

    for target_id, entries in plan.entries.items():
        channel = channels_by_id.get(target_id)
        if not channel:
            lines.append(f"⚠️  Channel/category ID {target_id} not found in Discord")
            continue

        current_overwrites = dict(channel.overwrites)
        for entry in entries:
            current = current_overwrites.get(entry.target)
            status = "✅" if current == entry.overwrite else "📝"
            lines.append(
                f"{status}  #{channel.name}  |  {entry.target.name}  →  {entry.source}"
            )

    return lines
