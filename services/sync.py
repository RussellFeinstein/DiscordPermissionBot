"""
sync.py — builds a permission plan from Airtable + local store, applies it to Discord.

Flow:
  1. build_permission_plan()  → produces a PermissionPlan (pure data, no Discord calls)
  2. apply_permission_plan()  → applies the plan to Discord (sets planned overwrites,
                                removes stale overwrites on planned channels)
  3. diff_permission_plan()   → returns a human-readable list of changes (for /preview)

Resolution strategy
-------------------
Discord objects (roles, categories, channels) are resolved by Discord ID first.
If the Airtable record has no Discord ID yet (e.g. manually added row), resolution
falls back to name matching and queues a backfill write.  If an ID resolves but the
stored name no longer matches Discord, the new name is queued as an Airtable update.
Both kinds of pending writes are stored on plan.airtable_updates and flushed by the
caller after the permission apply is done.

Permission level definitions come from local_store (config.py defaults + any edits).
Airtable provides role/category/channel structure and access rules.
Raises RuntimeError if Airtable is not configured for the guild (/setup airtable).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TypeVar
import discord

from config import RoleFields, CategoryFields, ChannelFields, AccessRuleFields
from services.airtable_client import get_airtable
from services import local_store

_T = TypeVar("_T", discord.Role, discord.CategoryChannel, "discord.abc.GuildChannel")


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

    plan.airtable_updates holds (table_key, record_id, fields) tuples queued
    during resolution — name-drift corrections and Discord ID backfills.
    Flush them via airtable_client.flush_updates() after applying permissions.
    """
    entries: dict[int, list[OverwriteEntry]] = field(default_factory=dict)
    airtable_updates: list[tuple[str, str, dict]] = field(default_factory=list)

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
# ID-first resolution helper
# ---------------------------------------------------------------------------

def _resolve(
    at_record: dict,
    id_field: str,
    name_field: str,
    by_discord_id: dict[int, _T],
    by_name: dict[str, _T],
    table_key: str,
    airtable_updates: list[tuple[str, str, dict]],
    kind: str,
) -> _T | None:
    """
    Resolve an Airtable record to a Discord object.

    1. If the record has a Discord ID, look up by ID.
       - Found: check for name drift; queue Airtable update if drifted.
       - Not found: the Discord object was deleted — warn and skip.
    2. No Discord ID: fall back to name match.
       - Found: queue an Airtable update to backfill the Discord ID.
       - Not found: warn and skip.
    """
    discord_id_str: str = at_record["fields"].get(id_field, "") or ""
    at_name: str = at_record["fields"].get(name_field, "") or ""
    record_id: str = at_record["id"]

    if discord_id_str:
        try:
            discord_id = int(discord_id_str)
        except ValueError:
            print(f"[sync] WARNING: {kind} '{at_name}' has invalid Discord ID '{discord_id_str}' — falling back to name")
            discord_id = None

        if discord_id:
            obj = by_discord_id.get(discord_id)
            if obj:
                if obj.name != at_name:
                    airtable_updates.append((table_key, record_id, {name_field: obj.name}))
                    print(f"[sync] Name drift: {kind} '{at_name}' → '{obj.name}' (queued Airtable update)")
                return obj
            else:
                print(f"[sync] WARNING: {kind} '{at_name}' (Discord ID {discord_id}) no longer exists in Discord — skipping")
                return None

    # No valid Discord ID — fall back to name match
    if not at_name:
        return None

    obj = by_name.get(at_name)
    if obj:
        airtable_updates.append((table_key, record_id, {id_field: str(obj.id)}))
        return obj

    print(f"[sync] WARNING: {kind} '{at_name}' not found in Discord — skipping")
    return None


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

    # -- Discord lookups indexed by both Discord ID and name --
    # Warn about duplicate names upfront — last-one-wins in the name dicts.
    role_names = [r.name for r in guild.roles]
    dup_roles = {n for n in role_names if role_names.count(n) > 1}
    if dup_roles:
        print(f"[sync] WARNING: duplicate role names in Discord (only last match used for name fallback): {sorted(dup_roles)}")

    cat_names = [c.name for c in guild.categories]
    dup_cats = {n for n in cat_names if cat_names.count(n) > 1}
    if dup_cats:
        print(f"[sync] WARNING: duplicate category names in Discord (only last match used for name fallback): {sorted(dup_cats)}")

    ch_names = [c.name for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    dup_channels = {n for n in ch_names if ch_names.count(n) > 1}
    if dup_channels:
        print(f"[sync] WARNING: duplicate channel names in Discord (only last match used for name fallback): {sorted(dup_channels)}")

    discord_roles_by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
    discord_roles_by_name: dict[str, discord.Role] = {r.name: r for r in guild.roles}

    discord_cats_by_id: dict[int, discord.CategoryChannel] = {c.id: c for c in guild.categories}
    discord_cats_by_name: dict[str, discord.CategoryChannel] = {c.name: c for c in guild.categories}

    discord_channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels if not isinstance(c, discord.CategoryChannel)
    }
    discord_channels_by_name: dict[str, discord.abc.GuildChannel] = {
        c.name: c for c in guild.channels if not isinstance(c, discord.CategoryChannel)
    }

    everyone = guild.default_role

    # ------------------------------------------------------------------
    # 1. @everyone baseline for every category (Categories.Baseline)
    # ------------------------------------------------------------------
    for cat_rec in airtable.get_categories():
        level_name: str = cat_rec["fields"].get(CategoryFields.BASELINE, "")
        if not level_name:
            continue

        discord_cat = _resolve(
            cat_rec,
            CategoryFields.DISCORD_ID, CategoryFields.NAME,
            discord_cats_by_id, discord_cats_by_name,
            "categories", plan.airtable_updates, "category",
        )
        if not discord_cat:
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
                if not at_cat:
                    continue
                dc = _resolve(
                    at_cat,
                    CategoryFields.DISCORD_ID, CategoryFields.NAME,
                    discord_cats_by_id, discord_cats_by_name,
                    "categories", plan.airtable_updates, "category",
                )
                if dc:
                    targets.append(dc)
        elif scope == "Channel":
            for ch_id in ch_refs:
                at_ch = ch_by_id.get(ch_id)
                if not at_ch:
                    continue
                dc = _resolve(
                    at_ch,
                    ChannelFields.DISCORD_ID, ChannelFields.NAME,
                    discord_channels_by_id, discord_channels_by_name,
                    "channels", plan.airtable_updates, "channel",
                )
                if dc:
                    targets.append(dc)

        # Resolve roles and add entries
        for role_id in role_refs:
            at_role = role_by_id.get(role_id)
            if not at_role:
                continue
            discord_role = _resolve(
                at_role,
                RoleFields.DISCORD_ID, RoleFields.NAME,
                discord_roles_by_id, discord_roles_by_name,
                "roles", plan.airtable_updates, "role",
            )
            if not discord_role:
                continue

            for target in targets:
                plan.add(target.id, OverwriteEntry(
                    target=discord_role,
                    overwrite=final_overwrite,
                    source=f"{discord_role.name} → {level_name} ({overwrite_dir})",
                ))

    return plan


# ---------------------------------------------------------------------------
# Rate-limit helper
# ---------------------------------------------------------------------------

# Brief pause between Discord permission writes to stay well inside the
# global rate limit (50 req/s).  discord.py handles per-route limits
# automatically; this guards against bulk syncs on large servers.
_WRITE_DELAY = 0.1   # seconds


async def _set_with_backoff(
    channel: discord.abc.GuildChannel,
    target: discord.Role | discord.Member,
    overwrite: discord.PermissionOverwrite | None,
    max_retries: int = 3,
) -> bool:
    """
    Call channel.set_permissions with exponential backoff on 429s.
    overwrite=None removes the overwrite (stale-cleanup path).
    Returns True on success, False after all retries are exhausted.
    """
    delay = 1.0
    for attempt in range(max_retries):
        try:
            await channel.set_permissions(target, overwrite=overwrite)
            await asyncio.sleep(_WRITE_DELAY)
            return True
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = float(getattr(e, "retry_after", delay))
                print(
                    f"[sync] Rate limited on #{channel.name} — "
                    f"retrying in {retry_after:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(retry_after)
                delay *= 2
            else:
                print(f"[sync] HTTP {e.status} on #{channel.name} for {target}: {e.text}")
                return False
    print(f"[sync] Gave up on #{channel.name} for {target} after {max_retries} attempts")
    return False


# ---------------------------------------------------------------------------
# Apply plan
# ---------------------------------------------------------------------------

async def apply_permission_plan(
    plan: PermissionPlan,
    guild: discord.Guild,
) -> tuple[int, int, int]:
    """
    For every channel/category in the plan:
      - Remove overwrites that exist in Discord but are NOT in the plan (stale).
      - Apply every overwrite that IS in the plan.

    Returns (applied_count, removed_count, error_count).
    """
    channels_by_id: dict[int, discord.abc.GuildChannel] = {
        c.id: c for c in guild.channels
    }
    applied = 0
    removed = 0
    errors = 0

    for target_id, entries in plan.entries.items():
        channel = channels_by_id.get(target_id)
        if not channel:
            continue

        planned_targets = {entry.target for entry in entries}

        # Remove stale overwrites: exist on Discord, not in the plan for this channel.
        for existing_target in list(channel.overwrites):
            if existing_target not in planned_targets:
                ok = await _set_with_backoff(channel, existing_target, None)
                if ok:
                    removed += 1
                    print(f"[sync] Removed stale overwrite: #{channel.name} / {existing_target.name}")
                else:
                    errors += 1

        # Apply planned overwrites.
        for entry in entries:
            ok = await _set_with_backoff(channel, entry.target, entry.overwrite)
            if ok:
                applied += 1
            else:
                errors += 1

    return applied, removed, errors


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
      "🗑️  #general            |  OldRole             →  (removed — not in plan)"
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
        planned_targets = {entry.target for entry in entries}

        # Stale overwrites that will be removed.
        for existing_target in current_overwrites:
            if existing_target not in planned_targets:
                lines.append(
                    f"🗑️  #{channel.name}  |  {existing_target.name}  →  (removed — not in plan)"
                )

        # Planned overwrites (changed or unchanged).
        for entry in entries:
            current = current_overwrites.get(entry.target)
            status = "✅" if current == entry.overwrite else "📝"
            lines.append(
                f"{status}  #{channel.name}  |  {entry.target.name}  →  {entry.source}"
            )

    return lines
