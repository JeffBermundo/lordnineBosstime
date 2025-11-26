import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import os
import threading
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import uvicorn

# ========== CONFIGURATION ==========
intents = discord.Intents.default()
LOCAL_TZ = ZoneInfo("Asia/Manila") 
bot = commands.Bot(command_prefix="!", intents=intents)

# ========== FASTAPI PING ==========
app = FastAPI()

@app.get("/", response_class=PlainTextResponse)
@app.head("/")
def root():
    return "Bot is running ✅"

def run_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Start the API in a separate thread
threading.Thread(target=run_api, daemon=True).start()


# ========== BOSS DEFINITIONS ==========
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

# Guild-based data (memory-only)
boss_data = {}  # {guild_id: {boss_name: {...}}}

def get_guild_bosses(guild_id: int):
    if guild_id not in boss_data:
        boss_data[guild_id] = {
            name: {
                "next_spawn": None,
                "auto": False,
                "skipped": False,
                **data
            }
            for name, data in default_bosses.items()
        }
    return boss_data[guild_id]

# ======== HELPERS =========
WEEKDAY_MAP = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6
}

def next_weekday_time(day_name: str, time_str: str):
    now = datetime.now(LOCAL_TZ)
    target_weekday = WEEKDAY_MAP[day_name]
    hour, minute = map(int, time_str.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (target_weekday - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= now:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)

def compute_next_spawn(guild_id: int, boss_name: str):
    bosses = get_guild_bosses(guild_id)
    data = bosses.get(boss_name, {})
    now = datetime.now(LOCAL_TZ)

    if data.get("skipped"):
        return datetime.max.replace(tzinfo=LOCAL_TZ)

    ns = data.get("next_spawn")
    if isinstance(ns, datetime) and ns > now:
        return ns

    if "schedule" in default_bosses.get(boss_name, {}):
        schedule = default_bosses[boss_name]["schedule"]
        times = [next_weekday_time(day, t) for day, t in schedule]
        return min(times)

    return datetime.max.replace(tzinfo=LOCAL_TZ)  # alive without timer

def get_sorted_boss_list(guild_id: int):
    bosses = get_guild_bosses(guild_id)
    items = list(bosses.items())

    def sort_key(item):
        name, data = item
        ns = compute_next_spawn(guild_id, name)
        is_skipped = 1 if data.get("skipped") else 0
        is_alive = 1 if (data.get("next_spawn") is None and "schedule" not in data) else 0
        return (is_skipped, is_alive, ns)

    items.sort(key=sort_key)
    return items

# ========== EMBED ==========
def get_embed(guild_id: int, page: int = 0):
    sorted_bosses = get_sorted_boss_list(guild_id)
    per_page = 10
    total_pages = math.ceil(len(sorted_bosses) / per_page)
    start = page * per_page
    end = start + per_page
    page_bosses = sorted_bosses[start:end]

    embed = discord.Embed(
        title=f"🕒 Boss Respawn Tracker (Page {page+1}/{total_pages})",
        color=discord.Color.blurple()
    )
    now = datetime.now(LOCAL_TZ)

    for name, data in page_bosses:
        if "schedule" in data:
            next_spawn = compute_next_spawn(guild_id, name)
            remaining = next_spawn - now
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            embed.add_field(
                name=name,
                value=f"🗓️ Scheduled: {', '.join([f'{d} {t}' for d,t in data['schedule']])} "
                      f"({hours}h {minutes}m left)" if next_spawn != datetime.max.replace(tzinfo=LOCAL_TZ) else "🗓️ Scheduled",
                inline=False
            )
        elif data["next_spawn"]:
            respawn_time = data["next_spawn"].strftime("%I:%M %p")
            remaining = data["next_spawn"] - now
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            embed.add_field(
                name=name,
                value=f"Next spawn: **{respawn_time}** ({hours}h {minutes}m left)"
                      + (" ⏳ *Auto*" if data["auto"] else ""),
                inline=False
            )
        else:
            embed.add_field(
                name=name,
                value="✅ Alive / Available" if not data.get("skipped") else "⏸️ Skipped",
                inline=False
            )

    return embed, total_pages

# ========== BUTTONS / PAGINATION ==========
class BossButton(discord.ui.Button):
    def __init__(self, guild_id: int, boss_name: str):
        super().__init__(label=f"RESET {boss_name}", style=discord.ButtonStyle.red)
        self.guild_id = guild_id
        self.boss_name = boss_name

    async def callback(self, interaction: discord.Interaction):
        bosses = get_guild_bosses(self.guild_id)
        boss = bosses[self.boss_name]
        if "respawn_hours" in boss:
            boss["next_spawn"] = datetime.now(LOCAL_TZ) + timedelta(hours=boss["respawn_hours"])
            boss["auto"] = False
            boss["skipped"] = False
        await interaction.response.edit_message(
            embed=get_embed(self.guild_id, 0)[0],
            view=BossView(self.guild_id, 0)
        )

class PrevButton(discord.ui.Button):
    def __init__(self, guild_id, page, total_pages):
        super().__init__(label="⬅️ Prev", style=discord.ButtonStyle.gray, disabled=(page == 0))
        self.guild_id = guild_id
        self.page = page
        self.total_pages = total_pages

    async def callback(self, interaction):
        new_page = self.page - 1
        embed, total_pages = get_embed(self.guild_id, new_page)
        await interaction.response.edit_message(embed=embed, view=BossView(self.guild_id, new_page))

class NextButton(discord.ui.Button):
    def __init__(self, guild_id, page, total_pages):
        super().__init__(label="➡️ Next", style=discord.ButtonStyle.gray, disabled=(page + 1 >= total_pages))
        self.guild_id = guild_id
        self.page = page
        self.total_pages = total_pages

    async def callback(self, interaction):
        new_page = self.page + 1
        embed, total_pages = get_embed(self.guild_id, new_page)
        await interaction.response.edit_message(embed=embed, view=BossView(self.guild_id, new_page))

class BossView(discord.ui.View):
    def __init__(self, guild_id: int, page: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        sorted_bosses = get_sorted_boss_list(guild_id)
        per_page = 10
        total_pages = math.ceil(len(sorted_bosses)/per_page)
        start = page*per_page
        end = start+per_page
        page_bosses = sorted_bosses[start:end]

        for name, data in page_bosses:
            if "respawn_hours" in data:
                self.add_item(BossButton(guild_id, name))

        self.add_item(PrevButton(guild_id, page, total_pages))
        self.add_item(NextButton(guild_id, page, total_pages))

# ========== SLASH COMMANDS ==========
@bot.tree.command(name="boss", description="Show the boss respawn tracker")
async def boss(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    embed, total_pages = get_embed(guild_id, 0)
    await interaction.response.send_message(embed=embed, view=BossView(guild_id, 0))

@bot.tree.command(name="maintenance", description="Set all bosses as alive (maintenance over)")
async def maintenance(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    bosses = get_guild_bosses(guild_id)
    for boss in bosses.values():
        boss["next_spawn"] = None
        boss["auto"] = False
        boss["skipped"] = False
    await interaction.response.send_message("🛠️ Maintenance complete — all bosses are now alive!")
    embed, _ = get_embed(guild_id, 0)
    await interaction.channel.send(embed=embed, view=BossView(guild_id, 0))

@bot.tree.command(name="skipall", description="Skip all cooldown bosses (clear timers and mark as skipped)")
async def skipall(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    bosses = get_guild_bosses(guild_id)
    count = 0
    for name, data in bosses.items():
        if "respawn_hours" in data:  # Only cooldown bosses
            data["next_spawn"] = None
            data["auto"] = False
            data["skipped"] = True
            count += 1

    await interaction.response.send_message(
        f"⏭️ Skipped all {count} cooldown bosses. They’ll appear at the bottom of the list."
    )

    embed, _ = get_embed(guild_id, 0)
    await interaction.channel.send(embed=embed, view=BossView(guild_id, 0))

@bot.tree.command(name="setkilltime", description="Manually set boss killtime (hours/minutes to respawn)")
@app_commands.describe(boss="Boss name", hours="Hours until respawn", minutes="Minutes until respawn")
async def setkilltime(interaction: discord.Interaction, boss: str, hours: int, minutes: int = 0):
    guild_id = interaction.guild_id
    bosses = get_guild_bosses(guild_id)
    if boss not in bosses:
        await interaction.response.send_message(f"❌ Boss `{boss}` not found.", ephemeral=True)
        return
    dt = datetime.now(LOCAL_TZ) + timedelta(hours=hours, minutes=minutes)
    bosses[boss]["next_spawn"] = dt
    bosses[boss]["auto"] = False
    bosses[boss]["skipped"] = False
    await interaction.response.send_message(
        f"✅ Set `{boss}` to respawn in {hours}h {minutes}m (at {dt.strftime('%Y-%m-%d %H:%M')})."
    )
    embed, _ = get_embed(guild_id, 0)
    await interaction.channel.send(embed=embed, view=BossView(guild_id, 0))

# ========== READY ==========
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Sync error: {e}")

# ===================== RUN =====================
bot.run(os.environ["DISCORD_TOKEN"])
