import os
import json
import threading
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import uvicorn

# ----------------- CONFIG -----------------
LOCAL_TZ = ZoneInfo("Asia/Manila")           # change if needed
DATA_FILE = "boss_data.json"
PORT = int(os.environ.get("PORT", 8000))     # Render sets this automatically
TOKEN = os.environ.get("DISCORD_TOKEN")      # must be set in Render env

intents = discord.Intents.default()
# You may want to enable message_content in the dev portal and set here if you use message content
# intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

app = FastAPI()

# ----------------- KEEP-ALIVE ENDPOINT -----------------
# GET (returns a short body) and HEAD (returns empty 200) both handled.
@app.get("/", response_class=PlainTextResponse)
def root_get():
    return "OK"

@app.head("/")
def root_head():
    # Respond to HEAD with 200 and empty body (works with UptimeRobot free)
    return PlainTextResponse(content="", status_code=200)


def run_api():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


# ----------------- BOSS CONFIG -----------------
default_bosses = {
    # Regular cooldown bosses
    "Venatus": {"respawn_hours": 10},
    "Viorent": {"respawn_hours": 10},
    "Ego": {"respawn_hours": 21},
    "Livera": {"respawn_hours": 24},
    "Araneo": {"respawn_hours": 24},
    "Undomiel": {"respawn_hours": 24},
    "Lady Dalia": {"respawn_hours": 18},
    "General Aqulues": {"respawn_hours": 29},
    "Amentis": {"respawn_hours": 29},
    "Baron Braudmore": {"respawn_hours": 32},
    "Wannitas": {"respawn_hours": 48},
    "Metus": {"respawn_hours": 48},
    "Duplican": {"respawn_hours": 48},
    "Shuliar": {"respawn_hours": 35},
    "Gareth": {"respawn_hours": 32},
    "Titore": {"respawn_hours": 37},
    "Larba": {"respawn_hours": 35},
    "Catena": {"respawn_hours": 35},
    "Secreta": {"respawn_hours": 62},
    "Ordo": {"respawn_hours": 62},
    "Asta": {"respawn_hours": 62},
    "Supore": {"respawn_hours": 62},

    # Scheduled bosses
    "Clemantis": {"schedule": [("Monday", "11:30"), ("Thursday", "19:00")]},
    "Saphirus": {"schedule": [("Sunday", "17:00"), ("Tuesday", "11:30")]},
    "Neutro": {"schedule": [("Tuesday", "19:00"), ("Thursday", "11:30")]},
    "Thymele": {"schedule": [("Monday", "19:00"), ("Wednesday", "11:30")]},
    "Milavy": {"schedule": [("Saturday", "15:00")]},
    "Ringor": {"schedule": [("Saturday", "17:00")]},
    "Roderick": {"schedule": [("Friday", "19:00")]},
    "Auruaq": {"schedule": [("Wednesday", "21:00"), ("Friday", "22:00")]},
    "Chaiflock": {"schedule": [("Saturday", "22:00")]},
    "Benji": {"schedule": [("Sunday", "21:00")]},
}

WEEKDAY_MAP = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
}

# In-memory structures
boss_data = {}           # {str(guild_id): {boss_name: {...}}}
active_messages = {}     # {str(guild_id): discord.Message} - message created by /boss (not persisted)


# ----------------- PERSISTENCE -----------------
def save_boss_data():
    serial = {}
    for gid, bosses in boss_data.items():
        serial[gid] = {}
        for name, data in bosses.items():
            copyd = data.copy()
            # Convert datetime to ISO if present
            ns = copyd.get("next_spawn")
            if isinstance(ns, datetime):
                # store as ISO with tz
                copyd["next_spawn"] = ns.isoformat()
            serial[gid][name] = copyd
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serial, f, indent=2, ensure_ascii=False)


def load_boss_data():
    global boss_data
    if not os.path.exists(DATA_FILE):
        boss_data = {}
        return
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    boss_data = {}
    for gid, bosses in raw.items():
        boss_data[gid] = {}
        for name, data in bosses.items():
            dcopy = data.copy()
            ns = dcopy.get("next_spawn")
            if isinstance(ns, str):
                # Convert to aware datetime in LOCAL_TZ
                try:
                    dt = datetime.fromisoformat(ns)
                    # make aware and convert to LOCAL_TZ
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=LOCAL_TZ)
                    else:
                        dt = dt.astimezone(LOCAL_TZ)
                    dcopy["next_spawn"] = dt
                except Exception:
                    dcopy["next_spawn"] = None
            boss_data[gid][name] = dcopy


# ----------------- HELPERS -----------------
def ensure_guild(guild_id: int):
    gid = str(guild_id)
    if gid not in boss_data:
        boss_data[gid] = {}
        for name, data in default_bosses.items():
            boss_data[gid][name] = {
                "next_spawn": None,
                "auto": False,
                "skipped": False,
                **data
            }


def next_weekday_time(day_name: str, time_str: str):
    now = datetime.now(LOCAL_TZ)
    target_weekday = WEEKDAY_MAP[day_name]
    hour, minute = map(int, time_str.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (target_weekday - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= now:
        days_ahead = 7
    return (candidate + timedelta(days=days_ahead)).astimezone(LOCAL_TZ)


def compute_next_spawn(guild_id: int, boss_name: str):
    gid = str(guild_id)
    ensure_guild(guild_id)
    data = boss_data[gid].get(boss_name, {})
    now = datetime.now(LOCAL_TZ)

    if data.get("skipped"):
        return datetime.max.replace(tzinfo=LOCAL_TZ)

    ns = data.get("next_spawn")
    if isinstance(ns, datetime) and ns > now:
        return ns

    if "schedule" in data:
        times = [next_weekday_time(day, t) for day, t in data["schedule"]]
        return min(times)

    return datetime.max.replace(tzinfo=LOCAL_TZ)


def get_sorted_boss_list(guild_id: int):
    gid = str(guild_id)
    ensure_guild(guild_id)
    items = list(boss_data[gid].items())

    def sort_key(item):
        name, data = item
        ns = compute_next_spawn(guild_id, name)
        is_skipped = 1 if data.get("skipped") else 0
        is_alive = 1 if (data.get("next_spawn") is None and "schedule" not in data) else 0
        return (is_skipped, is_alive, ns)

    items.sort(key=sort_key)
    return items


# ----------------- EMBED / UI -----------------
def get_embed(guild_id: int, page: int = 0):
    gid = str(guild_id)
    ensure_guild(guild_id)
    sorted_bosses = get_sorted_boss_list(guild_id)
    per_page = 10
    total_pages = max(1, math.ceil(len(sorted_bosses) / per_page))
    start = page * per_page
    page_bosses = sorted_bosses[start:start + per_page]

    embed = discord.Embed(title=f"🕒 Boss Respawn Tracker (Page {page+1}/{total_pages})",
                          color=discord.Color.blurple())
    now = datetime.now(LOCAL_TZ)

    for name, data in page_bosses:
        if "schedule" in data:
            next_spawn = compute_next_spawn(guild_id, name)
            if next_spawn != datetime.max.replace(tzinfo=LOCAL_TZ):
                remaining = next_spawn - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60
                embed_value = (f"🗓️ Scheduled: {', '.join([f'{d} {t}' for d, t in data['schedule']])} "
                               f"({hours}h {minutes}m left) — {next_spawn.strftime('%a %I:%M %p %Z')}")
            else:
                embed_value = f"🗓️ Scheduled: {', '.join([f'{d} {t}' for d, t in data['schedule']])}"
            embed.add_field(name=name, value=embed_value, inline=False)

        elif data.get("next_spawn"):
            ns = data["next_spawn"]
            remaining = ns - now
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            embed.add_field(
                name=name,
                value=f"Next spawn: **{ns.astimezone(LOCAL_TZ).strftime('%I:%M %p %Z')}** ({hours}h {minutes}m left)"
                      + (" ⏳ *Auto*" if data.get("auto") else ""),
                inline=False
            )
        else:
            embed.add_field(
                name=name,
                value="✅ Alive / Available" if not data.get("skipped") else "⏸️ Skipped",
                inline=False
            )

    return embed, total_pages


class BossButton(discord.ui.Button):
    def __init__(self, guild_id: int, boss_name: str):
        super().__init__(label=f"RESET {boss_name}", style=discord.ButtonStyle.danger)
        self.guild_id = guild_id
        self.boss_name = boss_name

    async def callback(self, interaction: discord.Interaction):
        gid = str(self.guild_id)
        ensure_guild(self.guild_id)
        boss = boss_data[gid][self.boss_name]
        if "respawn_hours" in boss:
            boss["next_spawn"] = datetime.now(LOCAL_TZ) + timedelta(hours=boss["respawn_hours"])
            boss["auto"] = False
            boss["skipped"] = False
            save_boss_data()

        # edit persistent message if exists, otherwise reply
        msg = active_messages.get(gid)
        if msg:
            embed, _ = get_embed(self.guild_id, 0)
            await msg.edit(embed=embed, view=BossView(self.guild_id, 0))
            await interaction.response.send_message("✅ Reset and updated tracker.", ephemeral=True)
        else:
            await interaction.response.edit_message(embed=get_embed(self.guild_id, 0)[0], view=BossView(self.guild_id, 0))


class PrevButton(discord.ui.Button):
    def __init__(self, guild_id, page, total_pages):
        super().__init__(label="⬅️ Prev", style=discord.ButtonStyle.secondary, disabled=(page == 0))
        self.guild_id = guild_id
        self.page = page
        self.total_pages = total_pages

    async def callback(self, interaction):
        new_page = self.page - 1
        gid = str(self.guild_id)
        msg = active_messages.get(gid)
        embed, _ = get_embed(self.guild_id, new_page)
        if msg:
            await msg.edit(embed=embed, view=BossView(self.guild_id, new_page))
            await interaction.response.send_message("Changed page.", ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=BossView(self.guild_id, new_page))


class NextButton(discord.ui.Button):
    def __init__(self, guild_id, page, total_pages):
        super().__init__(label="➡️ Next", style=discord.ButtonStyle.secondary, disabled=(page + 1 >= total_pages))
        self.guild_id = guild_id
        self.page = page
        self.total_pages = total_pages

    async def callback(self, interaction):
        new_page = self.page + 1
        gid = str(self.guild_id)
        msg = active_messages.get(gid)
        embed, _ = get_embed(self.guild_id, new_page)
        if msg:
            await msg.edit(embed=embed, view=BossView(self.guild_id, new_page))
            await interaction.response.send_message("Changed page.", ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=BossView(self.guild_id, new_page))


class BossView(discord.ui.View):
    def __init__(self, guild_id: int, page: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        sorted_bosses = get_sorted_boss_list(guild_id)
        per_page = 10
        total_pages = max(1, math.ceil(len(sorted_bosses) / per_page))
        start = page * per_page
        page_bosses = sorted_bosses[start:start + per_page]

        for name, data in page_bosses:
            if "respawn_hours" in data:
                self.add_item(BossButton(guild_id, name))

        self.add_item(PrevButton(guild_id, page, total_pages))
        self.add_item(NextButton(guild_id, page, total_pages))


# ----------------- SLASH COMMANDS -----------------
@bot.tree.command(name="boss", description="Show the boss respawn tracker")
async def boss(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    ensure_guild(interaction.guild_id)
    embed, _ = get_embed(interaction.guild_id, 0)

    # send persistent message (store reference)
    await interaction.response.send_message(embed=embed, view=BossView(interaction.guild_id, 0))
    try:
        msg = await interaction.original_response()
        active_messages[gid] = msg
    except Exception:
        # if original_response not available, ignore
        pass


@bot.tree.command(name="maintenance", description="Set all bosses as alive (maintenance over)")
async def maintenance(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    ensure_guild(interaction.guild_id)
    for b in boss_data[gid].values():
        b["next_spawn"] = None
        b["auto"] = False
        b["skipped"] = False
    save_boss_data()

    # feedback and update persistent message if exists
    await interaction.response.send_message("🛠️ Maintenance complete — all bosses are now alive!", ephemeral=True)
    msg = active_messages.get(gid)
    if msg:
        embed, _ = get_embed(interaction.guild_id, 0)
        await msg.edit(embed=embed, view=BossView(interaction.guild_id, 0))


@bot.tree.command(name="skipall", description="Skip all cooldown bosses (clear timers and mark as skipped)")
async def skipall(interaction: discord.Interaction):
    gid = str(interaction.guild_id)
    ensure_guild(interaction.guild_id)
    count = 0
    for name, d in boss_data[gid].items():
        if "respawn_hours" in d:
            d["next_spawn"] = None
            d["auto"] = False
            d["skipped"] = True
            count += 1
    save_boss_data()
    await interaction.response.send_message(f"⏭️ Skipped all {count} cooldown bosses.", ephemeral=True)
    msg = active_messages.get(gid)
    if msg:
        embed, _ = get_embed(interaction.guild_id, 0)
        await msg.edit(embed=embed, view=BossView(interaction.guild_id, 0))


@bot.tree.command(name="setkilltime", description="Manually set boss killtime (hours/minutes to respawn)")
@app_commands.describe(boss="Boss name", hours="Hours until respawn", minutes="Minutes until respawn")
async def setkilltime(interaction: discord.Interaction, boss: str, hours: int, minutes: int = 0):
    gid = str(interaction.guild_id)
    ensure_guild(interaction.guild_id)
    bosses = boss_data[gid]
    if boss not in bosses:
        await interaction.response.send_message(f"❌ Boss `{boss}` not found.", ephemeral=True)
        return
    dt = datetime.now(LOCAL_TZ) + timedelta(hours=hours, minutes=minutes)
    bosses[boss]["next_spawn"] = dt
    bosses[boss]["auto"] = False
    bosses[boss]["skipped"] = False
    save_boss_data()

    await interaction.response.send_message(
        f"✅ Set `{boss}` to respawn in {hours}h {minutes}m (at {dt.strftime('%Y-%m-%d %H:%M %Z')}).", ephemeral=True
    )
    msg = active_messages.get(gid)
    if msg:
        embed, _ = get_embed(interaction.guild_id, 0)
        await msg.edit(embed=embed, view=BossView(interaction.guild_id, 0))


# ----------------- AUTO REFRESH (optional) -----------------
@tasks.loop(seconds=60)
async def refresh_active_messages():
    # Update embeds in-place so timers tick down on the persistent message(s)
    for gid, msg in list(active_messages.items()):
        try:
            embed, _ = get_embed(int(gid), 0)
            await msg.edit(embed=embed, view=BossView(int(gid), 0))
        except Exception:
            # if message not found (deleted) remove mapping
            active_messages.pop(gid, None)


# ----------------- BOT EVENTS -----------------
@bot.event
async def on_ready():
    load_boss_data()
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands.")
    except Exception as e:
        print("Sync error:", e)

    # start the auto-refresh task
    if not refresh_active_messages.is_running():
        refresh_active_messages.start()


# ----------------- START FASTAPI (thread) & RUN BOT -----------------
# Start FastAPI in background thread (daemon so it doesn't block)
threading.Thread(target=run_api, daemon=True).start()

if not TOKEN:
    print("ERROR: DISCORD_TOKEN environment variable not set.")
else:
    bot.run(TOKEN)
