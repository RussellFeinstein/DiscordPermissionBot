# Discord Permission Bot

A Discord bot for managing server permissions and role assignments at scale, driven by an Airtable database.

## What it does

- **Permission sync** — reads your Airtable config and applies permission overwrites to every category and channel in one command
- **Role bundles** — assign or remove a named group of roles from a member in one command, with automatic exclusive-group conflict resolution (e.g. promoting Trial → Member auto-removes Trial)
- **Permission levels** — named access tiers (None / View / Chat / Mod / Admin) that are edited interactively inside Discord, no code changes needed

---

## For server admins — invite the bot

1. Invite the bot using the invite link (ask the bot host for this)
2. The bot will post a welcome message with getting started instructions
3. Use `/bundle create` and `/level edit` right away — no Airtable required
4. For permission sync: run `/setup airtable` → `/setup import-discord` → fill in Airtable → `/sync-permissions`

---

## Stack

- Python 3.11+
- [discord.py](https://discordpy.readthedocs.io/) v2
- [pyairtable](https://pyairtable.readthedocs.io/)
- [python-dotenv](https://pypi.org/project/python-dotenv/)

---

## Self-hosting

### 1. Create a Discord application

1. Go to https://discord.com/developers/applications and click **New Application**
2. Give it a name, then click **Bot** in the left sidebar
3. Scroll down to **Privileged Gateway Intents** and enable **Server Members Intent**
4. Scroll back up to the **Token** section and click **Reset Token** — copy the token that appears (you only see it once)

> **Keep your token secret.** It gives full control of the bot. Never paste it into chat, GitHub, or anywhere public. Put it only in your `.env` file (which is gitignored).

### 2. Generate an invite URL

1. In the left sidebar, go to **OAuth2 → URL Generator**
2. Under **Scopes**, check `bot` and `applications.commands`
3. Under **Bot Permissions**, check `Administrator`
4. Copy the URL at the bottom, open it in your browser, and invite the bot to your server

### 3. Set up your environment

Copy `.env.example` to a new file called `.env` and fill in your token:

```
DISCORD_BOT_TOKEN=paste-your-token-here
```

`.env` is gitignored — it stays on your machine only. Never edit `.env.example` with real values.

### 4. Install and run

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
python main.py
```

The bot will log in and sync slash commands globally. Commands appear in Discord within a few minutes.

> **Tip:** Add your server's ID as `DISCORD_GUILD_ID` in `.env` for instant command sync during development instead of waiting.

---

## Deploying to Railway

Railway is the recommended hosting platform — it handles deploys from GitHub and supports persistent volumes so your data survives restarts and redeployments.

### 1. Create a Railway project

1. Go to [railway.app](https://railway.app) and create an account
2. Click **New Project → Deploy from GitHub repo** and select this repository
3. Railway will detect Python automatically via `requirements.txt`

### 2. Set environment variables

In your Railway project → **Variables**, add:

| Variable | Value |
|---|---|
| `DISCORD_BOT_TOKEN` | Your bot token from the Discord Developer Portal |
| `DATA_DIR` | `/data` |

### 3. Add a persistent volume

In your Railway project → **Volumes**, click **Add Volume**:

- **Mount path**: `/data`

This is where all per-guild data (Airtable credentials, bundles, permission levels, cache) is stored. Without this, data is wiped on every redeploy.

### 4. Deploy

Railway deploys automatically on every push to your default branch. The `railway.toml` in this repo sets the start command and restart policy.

---

## Commands

### Setup  *(admin only)*

| Command | Description |
|---|---|
| `/setup airtable` | Connect your Airtable base — opens a secure modal for token + base ID |
| `/setup import-discord` | Populate Roles, Categories, and Channels tables from this server |
| `/setup status` | Show what's configured for this server |

### Permissions  *(admin only)*

| Command | Description |
|---|---|
| `/preview-permissions` | Show what `/sync-permissions` would change without applying anything |
| `/sync-permissions` | Read Airtable and apply all permission levels to Discord |

### Role bundles  *(officers + admins)*

| Command | Description |
|---|---|
| `/assign @member <bundle>` | Apply a bundle of roles to a member — auto-removes conflicting exclusive-group roles |
| `/remove @member <bundle>` | Remove a bundle of roles from a member |

### Permission level management  *(admin only)*

| Command | Description |
|---|---|
| `/level list` | List all permission levels |
| `/level view <name>` | Show all permissions for a level |
| `/level edit <name>` | Interactive editor — pick group → pick permission → set Allow / Deny / Neutral |
| `/level set <name> <permission> <value>` | Set one permission directly (with autocomplete) |
| `/level create <name>` | Create a new level (optionally clone from existing) |
| `/level delete <name>` | Delete a level |
| `/level reset-defaults` | Restore all levels to built-in defaults |

### Bundle management  *(admin only)*

| Command | Description |
|---|---|
| `/bundle list` | List all bundles and their roles |
| `/bundle view <name>` | Show roles in a bundle |
| `/bundle create <name>` | Create a new empty bundle |
| `/bundle delete <name>` | Delete a bundle |
| `/bundle add-role <bundle> <role>` | Add a Discord role to a bundle |
| `/bundle remove-role <bundle> <role>` | Remove a role from a bundle |

---

## Airtable setup

Run `/setup airtable` and enter your credentials in the secure modal:

- **Personal Access Token** — create at https://airtable.com/create/tokens with scopes:
  - `data.records:read`
  - `schema.bases:read`
  - `schema.bases:write` *(only needed for auto-creating tables)*
- **Base ID** — the `appXXXXXXXX` part of your base URL

The bot checks whether the required tables exist and offers to create them automatically.

### Airtable schema

The bot reads four tables. Field names must match exactly — they are configured in `config.py`.

#### Roles
| Field | Type | Notes |
|---|---|---|
| Role Name | Text | Must match the Discord role name exactly |
| Exclusive Group | Single select | None, Leadership, Team Officer, Membership Status, Team Assignment |

#### Categories
| Field | Type | Notes |
|---|---|---|
| Category Name | Text | Must match the Discord category name exactly |
| Baseline Permission | Single select | `@everyone` permission level — None, View, Chat, Mod, Admin |

#### Channels
| Field | Type | Notes |
|---|---|---|
| Channel Name | Text | Must match the Discord channel name exactly |

#### Access Rules
| Field | Type | Notes |
|---|---|---|
| Roles | Linked record | → Roles |
| Channel/Category | Single select | `Category` or `Channel` |
| Channel Categories | Linked record | → Categories |
| Channels | Linked record | → Channels |
| Permission Level | Single select | None, View, Chat, Mod, Admin |
| Overwrite | Single select | `Allow` grants access · `Deny` blocks it |

---

## Permission levels

The five built-in levels are defined in `config.py`. Edits made via `/level` commands are saved to `data/{guild_id}/permission_levels.json` and take precedence over the defaults.

| Level | Effect |
|---|---|
| **None** | Channel is invisible |
| **View** | Can see and read history, cannot interact |
| **Chat** | Standard member — read, send, react, voice |
| **Mod** | Chat + manage messages/threads, mute/move/kick members, manage channels |
| **Admin** | Full Discord administrator |

---

## File structure

```
main.py                  # Bot entry point
config.py                # Table/field names, permission level defaults, permission groups
requirements.txt
.env.example
services/
  guild_config.py        # Per-guild Airtable credentials
  airtable_client.py     # Per-guild Airtable clients with disk-cache fallback
  airtable_schema.py     # Validates and auto-creates Airtable tables
  local_store.py         # Per-guild permission levels and bundles
  sync.py                # Builds permission plan and applies it to Discord
cogs/
  setup.py               # /setup airtable, /setup import-discord, /setup status
  permissions.py         # /preview-permissions, /sync-permissions
  roles.py               # /assign, /remove
  admin.py               # /level and /bundle management commands
data/                    # Runtime data — gitignored, auto-created on first run
  {guild_id}/
    config.json          # Airtable credentials for this server
    permission_levels.json
    bundles.json
    airtable_cache.json
```
