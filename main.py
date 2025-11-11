import discord
import sqlite3
import os
from datetime import datetime, timedelta
from discord.ext import commands
from discord.utils import get, escape_markdown
import atexit
import requests
from discord.ext import tasks
import pytz
import json
import asyncio
import random
import string

# Initialize bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = commands.Bot(command_prefix="!", intents=intents)
client.remove_command('help')

# Database setup
conn = sqlite3.connect('ctf_team.db')
cursor = conn.cursor()

# Create tables with proper schema
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                first_bloods INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS solved_challenges (
                challenge_name TEXT,
                category TEXT,
                difficulty TEXT,
                first_blood INTEGER DEFAULT 0,
                user_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id))''')

cursor.execute('''CREATE TABLE IF NOT EXISTS active_challenges (
                challenge_name TEXT,
                category TEXT,
                user_id TEXT,
                thread_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS ctf_participation (
    user_id TEXT,
    event_id TEXT,
    PRIMARY KEY (user_id, event_id)
)''')
conn.commit()

SCOREBOARD_CHANNEL_ID = 1437730193274966136  # Scoreboard channel
SCOREBOARD_CONFIG_KEY = "scoreboard_message_id"

# Store/retrieve scoreboard message ID in DB
def get_scoreboard_message_id():
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (SCOREBOARD_CONFIG_KEY,))
    row = cursor.fetchone()
    return int(row[0]) if row else None

def set_scoreboard_message_id(message_id):
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (SCOREBOARD_CONFIG_KEY, str(message_id)))
    conn.commit()

async def get_scoreboard_channel(guild):
    return guild.get_channel(SCOREBOARD_CHANNEL_ID)

async def update_scoreboard_message(guild, debug_ctx=None):
    channel = await get_scoreboard_channel(guild)
    if not channel:
        msg = f"Scoreboard channel '{SCOREBOARD_CHANNEL_ID}' not found!"
        print(msg)
        if debug_ctx:
            await debug_ctx.send(msg)
        return
    embed = await generate_scoreboard_embed()
    message_id = get_scoreboard_message_id()
    msg = None
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(content=None, embed=embed)
            return
        except Exception as e:
            print(f"Failed to edit scoreboard message: {e}")
    # If not found, create a new scoreboard message and store its ID
    msg = await channel.send(embed=embed)
    set_scoreboard_message_id(msg.id)

SCOREBOARD_MESSAGE_ID = 1437731093678788628  # Hardcoded message ID to always update
FIRSTBLOOD_CHANNEL_ID = 1437730232072147076

CTFTIME_TEAM_ID = 303159
CTFTIME_TEAM_CHANNEL_ID = 1437730458464161792
CTFTIME_TEAM_CONFIG_KEY = "ctftime_team_message_id"

UPCOMING_CTFS_CHANNEL_ID = 1437730129865347083
CTF_ANNOUNCE_CONFIG_KEY = "ctf_announce_message_ids"  # Will store as JSON: {event_id: message_id}

CTF_RUNNING_CATEGORY_ID = 1437730339924607036
CTF_ARCHIVE_CATEGORY_ID = 1437730381091835974
CTF_CHANNELS_CONFIG_KEY = "ctf_channels"  # Will store as JSON: {event_id: channel_id}

CTF_ROLES_CONFIG_KEY = "ctf_roles"  # Will store as JSON: {event_id: role_id}

async def generate_scoreboard_embed():
    cursor.execute(
        '''SELECT u.user_id, COUNT(sc.challenge_name), u.points, u.first_bloods
            FROM users u
            LEFT JOIN solved_challenges sc ON u.user_id = sc.user_id
            GROUP BY u.user_id
            ORDER BY u.points DESC LIMIT 10''')
    leaderboard = cursor.fetchall()
    embed = discord.Embed(
        title="🏆 Server Scoreboard 🏆",
        description="Here are the top 10 players ranked by points:",
        color=discord.Color.gold()
    )
    for rank, (uid, flags, points, fbs) in enumerate(leaderboard, 1):
        user = await client.fetch_user(uid)
        if rank == 1:
            medal = "🥇"
        elif rank == 2:
            medal = "🥈"
        elif rank == 3:
            medal = "🥉"
        else:
            medal = f"Rank #{rank}:"
        embed.add_field(
            name=f"{medal} Rank #{rank}: {user.name}",
            value=f"🩸 First Bloods: {fbs} | ⛳ FLAG: {flags} | ✨ Points: {points}",
            inline=False
        )
    return embed

@tasks.loop(hours=24)
async def update_ctftime_team_stats():
    await client.wait_until_ready()
    guild = discord.utils.get(client.guilds)
    channel = guild.get_channel(CTFTIME_TEAM_CHANNEL_ID)
    if not channel:
        print(f"CTFtime team channel with ID {CTFTIME_TEAM_CHANNEL_ID} not found.")
        return
    embed = await generate_ctftime_team_embed()
    message_id = get_ctftime_team_message_id()
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(content=None, embed=embed)
            return
        except Exception as e:
            print(f"Failed to edit CTFtime team message: {e}")
    # If message doesn't exist, send a new one and store its ID
    msg = await channel.send(embed=embed)
    set_ctftime_team_message_id(msg.id)

def get_ctftime_team_message_id():
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (CTFTIME_TEAM_CONFIG_KEY,))
    row = cursor.fetchone()
    return int(row[0]) if row else None

def set_ctftime_team_message_id(message_id):
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (CTFTIME_TEAM_CONFIG_KEY, str(message_id)))
    conn.commit()

async def generate_ctftime_team_embed():
    url = f"https://ctftime.org/api/v1/teams/{CTFTIME_TEAM_ID}/"
    resp = requests.get(url)
    if resp.status_code != 200:
        embed = discord.Embed(title="CTFtime Team Stats", description="Failed to fetch team data from CTFtime.", color=discord.Color.red())
        return embed
    data = resp.json()
    team_name = data.get("name", "Unknown")
    rating_points = data.get("rating_points", "N/A")
    logo_url = data.get("logo", "https://ctftime.org/static/images/logo_ctftime.png")
    rating = data.get("rating", {})
    latest_year = max(rating.keys(), default=None)
    country_place = None
    rating_place = None
    if latest_year:
        year_data = rating[latest_year]
        country_place = year_data.get("country_place", "N/A")
        rating_place = year_data.get("rating_place", None)
    if not country_place:
        country_place = "N/A"
    if not rating_place:
        rating_place = data.get("place", "N/A")
    country = data.get("country", "KR")
    embed = discord.Embed(
        title=f"🏆 Team Profile: {team_name}",
        color=discord.Color.gold()
    )
    embed.add_field(name="👤 Team Name", value=team_name, inline=False)
    embed.add_field(name="⭐ Rating Points", value=rating_points, inline=False)
    embed.add_field(name="🌍 International Place", value=f"#{rating_place}", inline=False)
    embed.add_field(name=f"🇰🇷 Country Place", value=f"#{country_place}", inline=False)
    embed.set_footer(text="Updated every 24 hours by Lain Agent")
    embed.set_thumbnail(url=logo_url)
    return embed

def get_ctf_announce_message_ids():
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (CTF_ANNOUNCE_CONFIG_KEY,))
    row = cursor.fetchone()
    if row:
        return json.loads(row[0])
    return {}

def set_ctf_announce_message_ids(mapping):
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (CTF_ANNOUNCE_CONFIG_KEY, json.dumps(mapping)))
    conn.commit()

async def generate_ctf_announcement_embed(event, now=None):
    title = event.get("title", "Unknown CTF")
    start = event.get("start", "")
    finish = event.get("finish", "")
    canceled = event.get("onsite", False) == "canceled" or event.get("format", "").lower() == "canceled"
    # Format times
    start_dt = datetime.fromisoformat(start.replace('Z', '+00:00')) if start else None
    finish_dt = datetime.fromisoformat(finish.replace('Z', '+00:00')) if finish else None
    start_str = start_dt.strftime('%A, %B %d, %Y at %I:%M %p') if start_dt else 'N/A'
    finish_str = finish_dt.strftime('%A, %B %d, %Y at %I:%M %p') if finish_dt else 'N/A'
    # Determine status
    if canceled:
        status_emoji = "❌"
        status_text = "Canceled!"
    elif start_dt and finish_dt and now:
        if now < start_dt:
            status_emoji = "⏳"
            status_text = "Upcoming!"
        elif start_dt <= now <= finish_dt:
            status_emoji = "🟢"
            status_text = "Ongoing!"
        else:
            status_emoji = "❌"
            status_text = "Ended!"
    else:
        status_emoji = "⏳"
        status_text = "Upcoming!"
    embed = discord.Embed(
        title=f"🎯 Get Ready for a CTF Challenge: \"{title}\"! 🎯",
        color=discord.Color.red()
    )
    embed.add_field(name="🏁 Event", value=f'"{title}"', inline=False)
    embed.add_field(name="📅 Start Date & Time", value=start_str, inline=False)
    embed.add_field(name="📅 End Date & Time", value=finish_str, inline=False)
    embed.add_field(name=f"{status_emoji} Status", value=status_text, inline=False)
    embed.set_footer(text="The team is waiting for you! We need you, don't forget that!")
    if event.get("logo"):
        embed.set_thumbnail(url=event["logo"])
    return embed

def get_ctf_channels_mapping():
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (CTF_CHANNELS_CONFIG_KEY,))
    row = cursor.fetchone()
    if row:
        return json.loads(row[0])
    return {}

def set_ctf_channels_mapping(mapping):
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (CTF_CHANNELS_CONFIG_KEY, json.dumps(mapping)))
    conn.commit()

def get_ctf_roles_mapping():
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (CTF_ROLES_CONFIG_KEY,))
    row = cursor.fetchone()
    if row:
        return json.loads(row[0])
    return {}

def set_ctf_roles_mapping(mapping):
    cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (CTF_ROLES_CONFIG_KEY, json.dumps(mapping)))
    conn.commit()

async def create_ctf_role_and_permissions(guild, ctf_name, event_id, ctf_channel):
    # Role name: ctf_name (already sanitized)
    role = discord.utils.get(guild.roles, name=ctf_name)
    if not role:
        role = await guild.create_role(name=ctf_name, mentionable=False)
    # Store mapping event_id -> role_id
    ctf_roles = get_ctf_roles_mapping()
    ctf_roles[event_id] = role.id
    set_ctf_roles_mapping(ctf_roles)
    # Set channel permissions: only users with the role (and admins) can see/talk
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    # Optionally allow admins (manage_channels) to always see
    for admin_role in guild.roles:
        if admin_role.permissions.administrator:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True)
    await ctf_channel.edit(overwrites=overwrites)
    return role

@tasks.loop(hours=24)
async def announce_upcoming_ctfs():
    await client.wait_until_ready()
    now = datetime.now(pytz.utc)
    weekday = now.weekday()
    if weekday not in [2, 3]:  # 2=Wednesday, 3=Thursday
        return
    start_ts = int(now.timestamp())
    finish_ts = int((now + timedelta(days=30)).timestamp())
    url = f"https://ctftime.org/api/v1/events/?limit=10&start={start_ts}&finish={finish_ts}"
    resp = requests.get(url)
    if resp.status_code != 200:
        print("Failed to fetch upcoming CTFs from CTFtime.")
        return
    events = resp.json()
    events = sorted(events, key=lambda e: e.get('start', ''))[:4]
    top2 = sorted(events, key=lambda e: e.get('weight', 0), reverse=True)[:2]
    to_post = None
    if weekday == 2:
        to_post = top2[0] if len(top2) > 0 else None
    elif weekday == 3:
        to_post = top2[1] if len(top2) > 1 else None
    if not to_post:
        print("No CTF to announce today.")
        return
    channel = None
    for guild in client.guilds:
        ch = guild.get_channel(UPCOMING_CTFS_CHANNEL_ID)
        if ch:
            channel = ch
            break
    if not channel:
        print(f"Upcoming CTFs channel with ID {UPCOMING_CTFS_CHANNEL_ID} not found.")
        return
    mapping = get_ctf_announce_message_ids()
    event_id = str(to_post["id"])
    if event_id in mapping:
        print(f"Event {event_id} already announced, skipping.")
        return
    embed = await generate_ctf_announcement_embed(to_post, now=now)
    msg = await channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
    await msg.add_reaction("🔥")
    mapping[event_id] = msg.id
    set_ctf_announce_message_ids(mapping)

    # --- Step 1: Create a text channel for the CTF ---
    guild = channel.guild
    ctf_name = to_post.get("title", "ctf").replace(" ", "-")
    ctf_name = ctf_name[:90]  # Discord channel name limit is 100 chars, keep some margin
    running_category = guild.get_channel(CTF_RUNNING_CATEGORY_ID)
    if running_category and isinstance(running_category, discord.CategoryChannel):
        ctf_channel = await guild.create_text_channel(
            name=ctf_name,
            category=running_category,
            reason=f"Channel for CTF: {ctf_name}"
        )
        # Set channel description and send credentials/info message
        await set_ctf_channel_description_and_message(
            ctf_channel,
            ctf_name,
            to_post.get('url', 'N/A'),
            to_post.get('discord_url') or to_post.get('discord')
        )
        ctf_channels = get_ctf_channels_mapping()
        ctf_channels[event_id] = ctf_channel.id
        set_ctf_channels_mapping(ctf_channels)
        print(f"Created channel {ctf_channel.name} for CTF {event_id}")
        # Create role and set permissions
        role = await create_ctf_role_and_permissions(guild, ctf_name, event_id, ctf_channel)
        await send_ctf_start_message(guild, event_id, ctf_name, role)
    else:
        print(f"CTF Running category with ID {CTF_RUNNING_CATEGORY_ID} not found or not a category.")

async def send_ctf_start_message(guild, event_id, ctf_name, role):
    ctf_channels = get_ctf_channels_mapping()
    channel_id = ctf_channels.get(event_id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    await channel.send(
        f"{role.mention}\n\n"
        f"🎉 The CTF Event '{escape_markdown(ctf_name)}' has officially started! 🎉\n\n"
        "Note: Do not share final solutions or flags until after the contest has ended. Please do not upload them during the contest unless the contest is limited size team player.\n\n"
        "Note: Please please don't forget to use !trying when you start trying to solve a challenge.\n"
        "!trying <namecategory> <namechallenge>\n"
        "Example:\n!trying crypto task1\n\n"
        "Note: Don't forget to use !add after solving the challenge.\n"
        "!add <namecategory> <namechallenge> <difficulty> <firstblood>\n"
        "Example for firstblood challenge solved:\n!add Crypto task1 easy 1\n\n"
        "Example for a new challenge solved:\n!add Crypto task1 easy\nor\n!add Crypto task1 easy 0\n\n"
        "Note: Set the difficulty of the challenge based on the author's rating. If the author has not set the difficulty of the challenge, set it to easy and it will be modified later.\n\n"
        "Let's go!🚀🔥"
    )

async def send_ctf_end_message(guild, event_id, ctf_name, role):
    ctf_channels = get_ctf_channels_mapping()
    channel_id = ctf_channels.get(event_id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    await channel.send(
        f"{role.mention}\n\n"
        f"🎉 The CTF Event '{escape_markdown(ctf_name)}' has officially ended! 🎉\n\n"
        "Thank you all for being an integral part of the team! Each and every one of you was truly amazing and brought your unique energy to the event. 💪✨\n\n"
        "We can't wait to compete alongside you in future challenges and create even more unforgettable moments together. 🚀🔥\n\n"
        "Please don't forget to share your writeups if you have any in the writeup channel."
    )
    await delete_ctf_role(guild, event_id)

async def make_channel_readonly(channel, role):
    overwrites = channel.overwrites
    # Set the CTF role to read-only
    if role in overwrites:
        overwrites[role].send_messages = False
    else:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    # Default role stays hidden
    await channel.edit(overwrites=overwrites)

async def make_channel_archived_public(channel):
    overwrites = channel.overwrites
    # Remove all role-specific overwrites except @everyone
    for role in list(overwrites.keys()):
        if role != channel.guild.default_role:
            del overwrites[role]
    # Set @everyone to view and read, but not send
    overwrites[channel.guild.default_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    await channel.edit(overwrites=overwrites)

@tasks.loop(minutes=30)
async def check_and_archive_ctf_channels():
    await client.wait_until_ready()
    now = datetime.now(pytz.utc)
    ctf_channels = get_ctf_channels_mapping()
    ctf_roles = get_ctf_roles_mapping()
    for event_id, channel_id in ctf_channels.items():
        # Fetch event info from CTFtime
        url = f"https://ctftime.org/api/v1/events/{event_id}/"
        resp = requests.get(url)
        if resp.status_code != 200:
            continue
        event = resp.json()
        finish = event.get("finish", "")
        ctf_name = event.get("title", "ctf").replace(" ", "-")
        finish_dt = datetime.fromisoformat(finish.replace('Z', '+00:00')) if finish else None
        if finish_dt and now > finish_dt:
            # Archive the channel if not already archived
            for guild in client.guilds:
                channel = guild.get_channel(channel_id)
                role_id = ctf_roles.get(event_id)
                role = guild.get_role(role_id) if role_id else None
                if channel and channel.category_id != CTF_ARCHIVE_CATEGORY_ID:
                    archive_category = guild.get_channel(CTF_ARCHIVE_CATEGORY_ID)
                    if archive_category and isinstance(archive_category, discord.CategoryChannel):
                        await channel.edit(category=archive_category, reason="CTF ended, archiving channel")
                        if role:
                            await make_channel_readonly(channel, role)
                            await send_ctf_end_message(guild, event_id, ctf_name, role)
                            await delete_ctf_role(guild, event_id)
                            await make_channel_archived_public(channel)

# Bot events
@client.event
async def on_ready():
    print(f'Bot ready as {client.user}')
    print(f'Database path: {os.path.abspath("ctf_team.db")}')
    if not update_ctftime_team_stats.is_running():
        update_ctftime_team_stats.start()
    if not announce_upcoming_ctfs.is_running():
        announce_upcoming_ctfs.start()
    if not check_and_archive_ctf_channels.is_running():
        check_and_archive_ctf_channels.start()
    # Ensure scoreboard message exists
    guild = discord.utils.get(client.guilds)
    await update_scoreboard_message(guild)

@client.event
async def on_raw_reaction_add(payload):
    if payload.emoji.name != "🔥":
        return
    # Only care about CTF announcement messages
    mapping = get_ctf_announce_message_ids()
    event_id = None
    for eid, msg_id in mapping.items():
        if int(msg_id) == payload.message_id:
            event_id = eid
            break
    if not event_id:
        return
    # Get guild, member, and role
    guild = client.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    ctf_roles = get_ctf_roles_mapping()
    role_id = ctf_roles.get(event_id)
    if not role_id:
        return
    role = guild.get_role(role_id)
    if not role:
        return
    # Assign role
    await member.add_roles(role, reason="Reacted to CTF announcement")
    # Record participation
    cursor.execute('''INSERT OR IGNORE INTO ctf_participation (user_id, event_id) VALUES (?, ?)''', (str(member.id), event_id))
    conn.commit()

@client.event
async def on_raw_reaction_remove(payload):
    if payload.emoji.name != "🔥":
        return
    mapping = get_ctf_announce_message_ids()
    event_id = None
    for eid, msg_id in mapping.items():
        if int(msg_id) == payload.message_id:
            event_id = eid
            break
    if not event_id:
        return
    guild = client.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return
    ctf_roles = get_ctf_roles_mapping()
    role_id = ctf_roles.get(event_id)
    if not role_id:
        return
    role = guild.get_role(role_id)
    if not role:
        return
    # Remove role
    await member.remove_roles(role, reason="Removed reaction from CTF announcement")

# Challenge tracking commands
@client.command()
async def trying(ctx, category: str, challenge_name: str):
    """Create a discussion thread for a challenge"""
    thread_name = f"{category.lower()} - {challenge_name}"

    try:
        # Create thread
        thread = await ctx.channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=f"Discussion for {challenge_name}")

        # Record in database with all 5 columns
        cursor.execute(
            '''INSERT INTO active_challenges 
                         (challenge_name, category, user_id, thread_id, timestamp)
                         VALUES (?,?,?,?,?)''',
            (challenge_name, category.lower(), ctx.author.id, thread.id,
             datetime.now()))
        conn.commit()

        # Send notifications
        await thread.send(f"🚧 {ctx.author.mention} is now trying the challenge `{challenge_name}` under the `{category.capitalize()}` category\n\n")

    except Exception as e:
        await ctx.send(f"❌ Error creating thread: {e}")
        conn.rollback()


@client.command()
async def working(ctx):
    """List active challenges"""
    cursor.execute('''SELECT * FROM active_challenges''')
    active = cursor.fetchall()
    if not active:
        await ctx.send(embed=discord.Embed(title="🔨 Active Challenges", description="No active challenges!", color=discord.Color.orange()))
        return
    desc = ""
    for challenge in active:
        name, cat, uid, thread_id, _ = challenge
        user = await client.fetch_user(uid)
        desc += (
            f"**{name} ({cat.capitalize()})**\n"
            f"Started by: {user.mention}\n\n"
        )
    embed = discord.Embed(
        title="🔨 Active Challenges",
        description=desc,
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)


# Challenge solving commands
@client.command()
async def add(ctx,
              category: str,
              challenge_name: str,
              difficulty: str,
              first_blood: int = 0):
    """Record a solved challenge"""
    allowed_categories = [
        'misc', 'web', 'crypto', 'reverse', 'blockchain', 'dfir', 'osint', 'pwn', 'android', 'ppc'
    ]
    allowed_difficulties = ['easy', 'medium', 'hard']
    category = category.lower()
    difficulty = difficulty.lower()
    if category not in allowed_categories:
        await ctx.send(f"❌ Invalid category: `{category}`. Allowed categories: {', '.join(allowed_categories)}")
        return
    if difficulty not in allowed_difficulties:
        await ctx.send(f"❌ Invalid difficulty: `{difficulty}`. Allowed difficulties: {', '.join(allowed_difficulties)}")
        return
    user = ctx.author

    # Check duplicates
    cursor.execute(
        '''SELECT * FROM solved_challenges 
           WHERE challenge_name = ? AND user_id = ?''',
        (challenge_name, user.id))
    if cursor.fetchone():
        await ctx.send("❌ Challenge already recorded!")
        return

    # Calculate points
    points = {'easy': 10, 'medium': 25, 'hard': 40}.get(difficulty, 0)
    if first_blood == 1:
        points += {'easy': 10, 'medium': 15, 'hard': 20}.get(difficulty, 0)

    try:
        # Update database
        cursor.execute(
            '''INSERT INTO solved_challenges 
               (challenge_name, category, difficulty, first_blood, user_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (challenge_name, category, difficulty, first_blood, user.id, datetime.now()))

        # Update user stats
        cursor.execute(
            '''SELECT points, first_bloods FROM users WHERE user_id = ?''',
            (user.id,))
        if user_data := cursor.fetchone():
            new_points = user_data[0] + points
            new_fb = user_data[1] + (1 if first_blood == 1 else 0)
            cursor.execute(
                '''UPDATE users SET points = ?, first_bloods = ? WHERE user_id = ?''',
                (new_points, new_fb, user.id))
        else:
            cursor.execute(
                '''INSERT INTO users (user_id, first_bloods, points)
                   VALUES (?, ?, ?)''',
                (user.id, (1 if first_blood == 1 else 0), points))

        # Remove from active challenges
        cursor.execute(
            '''DELETE FROM active_challenges 
               WHERE challenge_name = ? AND user_id = ?''',
            (challenge_name, user.id))

        conn.commit()

        # Check if this is the user's first solve in this category
        cursor.execute('''SELECT COUNT(*) FROM solved_challenges WHERE user_id = ? AND category = ?''', (str(user.id), category))
        cat_count = cursor.fetchone()[0]
        if cat_count == 1:
            await give_category_role_and_congrats(user, ctx.guild, category, ctx.channel)

        # 📸 Use different image if first blood
        if first_blood == 1:
            # Build the special first blood embed
            fb_embed = discord.Embed(
                title="🏆 First Blood!",
                description=(
                    f"🩸 **First blood** on the {category} challenge **{challenge_name}** goes to {user.mention}!"
                ),
                color=discord.Color.red()
            )
            fb_embed.add_field(name="Difficulty", value=difficulty.capitalize(), inline=False)
            if difficulty == "easy":
                filename = "Easy_FirstBlood.gif"
            elif difficulty == "medium":
                filename = "Medium_FirstBlood.gif"
            elif difficulty == "hard":
                filename = "Hard_FirstBlood.gif"
            else:
                filename = "solved.webp"
            fb_embed.set_image(url=f"attachment://{filename}")
            # Send to current channel
            file1 = discord.File(filename, filename=filename)
            await ctx.send(embed=fb_embed, file=file1)
            # Send to firstblood channel
            fb_channel = ctx.guild.get_channel(FIRSTBLOOD_CHANNEL_ID)
            if fb_channel:
                file2 = discord.File(filename, filename=filename)
                await fb_channel.send(embed=fb_embed, file=file2)
        else:
            embed = discord.Embed(
                title="✅ Challenge Solved!",
                description=f"🎉 {user.mention} solved a challenge!",
                color=discord.Color.green()
            )
            embed.add_field(name="Category", value=category.capitalize(), inline=True)
            embed.add_field(name="Challenge", value=challenge_name, inline=True)
            embed.add_field(name="Difficulty", value=difficulty.capitalize(), inline=True)
            file = discord.File("solved.webp", filename="solved.webp")
            embed.set_image(url="attachment://solved.webp")
            await ctx.send(embed=embed, file=file)
        # Update scoreboard after challenge is solved, with debug context
        await update_scoreboard_message(ctx.guild, debug_ctx=ctx)

    except Exception as e:
        await ctx.send(f"❌ Database error: {e}")
        conn.rollback()

# Information commands
@client.command()
async def solved(ctx):
    """List all solved challenges"""
    cursor.execute(
        '''SELECT * FROM solved_challenges ORDER BY timestamp DESC''')
    if not (solved := cursor.fetchall()):
        await ctx.send("No challenges solved yet!")
        return

    desc = ""
    for challenge in solved:
        name, cat, diff, _, uid, _ = challenge
        user = await client.fetch_user(uid)
        desc += (
            f"**{name} ({cat.capitalize()})**\n"
                     f"Solved by: {user.mention}\n"
                     f"Status: ✅\n"
            f"Difficulty: {diff.capitalize()}\n\n"
        )
    embed = discord.Embed(
        title="🔎 Solved Challenges",
        description=desc,
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)


@client.command()
async def scoreboard(ctx):
    """Show leaderboard"""
    embed = await generate_scoreboard_embed()
    await ctx.send(embed=embed)


@client.command()
async def reset_scoreboard(ctx):
    """ADMIN ONLY: Reset the scoreboard (clears all users and solved challenges)."""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You do not have permission to use this command. (Admin only)")
        return
    try:
        cursor.execute('DELETE FROM users')
        cursor.execute('DELETE FROM solved_challenges')
        cursor.execute('DELETE FROM active_challenges')
        cursor.execute('DELETE FROM ctf_participation')
        conn.commit()
        await update_scoreboard_message(ctx.guild, debug_ctx=ctx)
        await ctx.send("✅ Complete database reset! Cleared: users, solved challenges, active challenges (working), and CTF participation.")
    except Exception as e:
        await ctx.send(f"❌ Error resetting database: {e}")
        conn.rollback()


@client.command()
async def categories(ctx):
    """List all challenge categories"""
    cursor.execute('''SELECT DISTINCT category FROM solved_challenges''')
    cats = [row[0].capitalize() for row in cursor.fetchall()]
    await ctx.send(
        f"📂 **Categories:** {', '.join(cats) if cats else 'None yet!'}")


@client.command()
async def profile(ctx, member: discord.Member = None):
    """Show player profile"""
    member = member or ctx.author
    # Get basic stats
    cursor.execute(
        '''SELECT first_bloods, points FROM users WHERE user_id = ?''',
        (member.id, ))
    if not (result := cursor.fetchone()):
        await ctx.send(f"No data for {member.name}!")
        return
    fbs, points = result
    # Total flags
    cursor.execute(
        '''SELECT COUNT(*) FROM solved_challenges WHERE user_id = ?''',
        (member.id, ))
    flags = cursor.fetchone()[0]
    # Rank (by points)
    cursor.execute(
        '''SELECT user_id FROM users ORDER BY points DESC''')
    all_users = [row[0] for row in cursor.fetchall()]
    rank = all_users.index(str(member.id)) + 1 if str(member.id) in all_users else 'N/A'
    # Per-category breakdown
    categories = ['pwn', 'reverse', 'dfir', 'web', 'crypto', 'misc', 'blockchain', 'osint', 'android', 'ppc']
    cat_counts = {}
    for cat in categories:
        cursor.execute(
            '''SELECT COUNT(*) FROM solved_challenges WHERE user_id = ? AND category = ?''',
            (member.id, cat))
        cat_counts[cat] = cursor.fetchone()[0]
    # Firstblood challenges solved
    cursor.execute(
        '''SELECT COUNT(*) FROM solved_challenges WHERE user_id = ? AND first_blood = 1''',
        (member.id, ))
    fb_solved = cursor.fetchone()[0]
    # Real CTF participation
    cursor.execute('''SELECT event_id FROM ctf_participation WHERE user_id = ?''', (str(member.id),))
    ctf_rows = cursor.fetchall()
    total_ctfs = len(ctf_rows)
    ctfs_participated = ', '.join([row[0] for row in ctf_rows]) if ctf_rows else 'N/A'
    # Per-category breakdown as a single field
    all_cats = ['pwn', 'reverse', 'dfir', 'web', 'crypto', 'misc', 'blockchain', 'osint', 'android', 'ppc']
    all_breakdown = '\n'.join([f"🔹 {cat.capitalize()}: {cat_counts[cat]}" for cat in all_cats])
    # Build embed
    embed = discord.Embed(
        title=f"🧑‍💻 Player Profile: {member.name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏆 Total CTFs Participated", value=total_ctfs, inline=True)
    embed.add_field(name="💎 Points", value=points, inline=True)
    embed.add_field(name="🏅 Rank", value=f"#{rank}", inline=True)
    embed.add_field(name="🎯 CTFs Participated", value=ctfs_participated, inline=False)
    embed.add_field(name="🛡️ Total Challenges Solved", value=flags, inline=True)
    embed.add_field(name="🩸 Firstblood Challenges Solved", value=fb_solved, inline=True)
    embed.add_field(name="🧩 Challenges Solved by Type", value=all_breakdown, inline=False)
    await ctx.send(embed=embed)


@client.command(name='help')
async def show_help(ctx):
    """Show command guide"""
    guide = (
        "**🎮 CTF Bot Guide 🎯**\n\n"
        "__**Challenge Tracking:**__\n"
        "`!trying <category> <name>` — Start challenge thread\n"
        "`!add <category> <name> <easy/medium/hard> <1=first blood>` — Record a solved challenge\n"
        "`!unsolve <name>` — Revoke a solved challenge and update your stats\n\n"
        "__**Information:**__\n"
        "`!solved` — Show solved challenges\n"
        "`!working` — Active challenges\n"
        "`!scoreboard` — Leaderboard\n"
        "`!profile [@user]` — Player stats\n"
        "`!categories` — List categories\n"
        "`!help` — Show this guide\n\n"
        "__**Notes:**__\n"
        "- Categories: misc, web, crypto, reverse, blockchain, dfir, osint, pwn, android, ppc\n"
        "- Difficulties: easy, medium, hard\n"
        "- Use `1` for first blood, or omit for a regular solve."
    )
    embed = discord.Embed(
        title="📖 Help & Guide",
        description=guide,
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed)


@client.command()
async def unsolve(ctx, challenge_name: str):
    """Revoke a solved challenge for the user and update stats."""
    user = ctx.author
    # Find the solved challenge for this user
    cursor.execute(
        '''SELECT difficulty, first_blood FROM solved_challenges WHERE challenge_name = ? AND user_id = ?''',
        (challenge_name, user.id))
    row = cursor.fetchone()
    if not row:
        await ctx.send(f"❌ Challenge `{challenge_name}` not found in your solved list.")
        return
    difficulty, first_blood = row
    # Calculate points to subtract
    points = {'easy': 10, 'medium': 25, 'hard': 40}.get(difficulty, 0)
    if first_blood == 1:
        points += {'easy': 10, 'medium': 15, 'hard': 20}.get(difficulty, 0)
    # Remove the solved challenge
    cursor.execute(
        '''DELETE FROM solved_challenges WHERE challenge_name = ? AND user_id = ?''',
        (challenge_name, user.id))
    # Update user stats
    cursor.execute(
        '''SELECT points, first_bloods FROM users WHERE user_id = ?''',
        (user.id,))
    user_data = cursor.fetchone()
    if user_data:
        new_points = max(0, user_data[0] - points)
        new_fb = max(0, user_data[1] - (1 if first_blood == 1 else 0))
        cursor.execute(
            '''UPDATE users SET points = ?, first_bloods = ? WHERE user_id = ?''',
            (new_points, new_fb, user.id))
    conn.commit()
    await ctx.send(f"✅ Challenge `{challenge_name}` has been revoked from your solved list and your stats updated.")
    await update_scoreboard_message(ctx.guild, debug_ctx=ctx)


# For test_announce simulation: track which CTFs are waiting for a start trigger
ctf_test_simulation_state = {}

# Patch test_announce: after sending the announcement message, store info for simulation
# In on_raw_reaction_add, if the event_id is in ctf_test_simulation_state and not started, send the start message, start the 10s timer, and mark as started

# --- Patch for test_announce ---
# After sending the announcement message and creating channel/role, store info in ctf_test_simulation_state:
# ctf_test_simulation_state[event_id] = {"guild_id": guild.id, "ctf_name": ctf_name, "ctf_channel_id": ctf_channel.id, "role_id": role.id, "started": False}

# --- Patch for on_raw_reaction_add ---
# If event_id in ctf_test_simulation_state and not started:
#   send_ctf_start_message(...)
#   start 10s timer to simulate end
#   mark as started

# --- Patch for simulate_ctf_end ---
# Use 10s for test simulation
async def simulate_ctf_end(guild, event_id, ctf_name, ctf_channel, role, test_mode=False):
    if test_mode:
        await asyncio.sleep(10)
    else:
        await asyncio.sleep(30)
    archive_category = guild.get_channel(CTF_ARCHIVE_CATEGORY_ID)
    if archive_category and isinstance(archive_category, discord.CategoryChannel):
        await ctf_channel.edit(category=archive_category, reason="CTF ended (simulated), archiving channel")
        await make_channel_readonly(ctf_channel, role)
        await send_ctf_end_message(guild, event_id, ctf_name, role)

# --- Patch for test_announce ---
# Remove send_ctf_start_message and simulate_ctf_end from immediate execution, only trigger on reaction

@client.command()
async def test_announce(ctx):
    """Test command to immediately announce the top 2 weighted CTFs and create their channels."""
    now = datetime.now(pytz.utc)
    start_ts = int(now.timestamp())
    finish_ts = int((now + timedelta(days=30)).timestamp())
    url = f"https://ctftime.org/api/v1/events/?limit=10&start={start_ts}&finish={finish_ts}"
    resp = requests.get(url)
    if resp.status_code != 200:
        await ctx.send("Failed to fetch upcoming CTFs from CTFtime.")
        return
    events = resp.json()
    events = sorted(events, key=lambda e: e.get('start', ''))[:4]
    top2 = sorted(events, key=lambda e: e.get('weight', 0), reverse=True)[:2]
    if not top2:
        await ctx.send("No CTFs to announce for testing.")
        return
    channel = ctx.guild.get_channel(UPCOMING_CTFS_CHANNEL_ID)
    if not channel:
        await ctx.send(f"Upcoming CTFs channel with ID {UPCOMING_CTFS_CHANNEL_ID} not found.")
        return
    mapping = get_ctf_announce_message_ids()
    announced = 0
    for to_post in top2:
        event_id = str(to_post["id"])
        if event_id in mapping:
            await ctx.send(f"Event {event_id} already announced, skipping.")
            continue
        embed = await generate_ctf_announcement_embed(to_post, now=now)
        msg = await channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
        await msg.add_reaction("🔥")
        mapping[event_id] = msg.id
        set_ctf_announce_message_ids(mapping)

        # --- Create a text channel for the CTF ---
        guild = channel.guild
        ctf_name = to_post.get("title", "ctf").replace(" ", "-")
        ctf_name = ctf_name[:90]
        running_category = guild.get_channel(CTF_RUNNING_CATEGORY_ID)
        if running_category and isinstance(running_category, discord.CategoryChannel):
            # Set explicit overwrites for the bot
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
            }
            ctf_channel = await guild.create_text_channel(
                name=ctf_name,
                category=running_category,
                overwrites=overwrites,
                reason=f"Channel for CTF: {ctf_name}"
            )
            # Set channel description and send credentials/info message
            await set_ctf_channel_description_and_message(
                ctf_channel,
                ctf_name,
                to_post.get('url', 'N/A'),
                to_post.get('discord_url') or to_post.get('discord')
            )
            ctf_channels = get_ctf_channels_mapping()
            ctf_channels[event_id] = ctf_channel.id
            set_ctf_channels_mapping(ctf_channels)
            await ctx.send(f"Created channel {ctf_channel.mention} for CTF '{ctf_name}'")
            # Create role and set permissions
            role = await create_ctf_role_and_permissions(guild, ctf_name, event_id, ctf_channel)
            # Wait a moment to ensure channel is ready
            await asyncio.sleep(1)
            # Try to send start message and catch errors
            try:
                await send_ctf_start_message(guild, event_id, ctf_name, role)
            except Exception as e:
                await ctx.send(f"Error sending start message: {e}")
            # Wait 10 seconds, then archive and send end message
            await asyncio.sleep(10)
            archive_category = guild.get_channel(CTF_ARCHIVE_CATEGORY_ID)
            if archive_category and isinstance(archive_category, discord.CategoryChannel):
                await ctf_channel.edit(category=archive_category, reason="CTF ended (simulated), archiving channel")
                await make_channel_readonly(ctf_channel, role)
                try:
                    await send_ctf_end_message(guild, event_id, ctf_name, role)
                except Exception as e:
                    await ctx.send(f"Error sending end message: {e}")
            await delete_ctf_role(guild, event_id)
            await make_channel_archived_public(ctf_channel)
        else:
            await ctx.send(f"CTF Running category with ID {CTF_RUNNING_CATEGORY_ID} not found or not a category.")
        announced += 1
    if announced:
        await ctx.send(f"Test announcement complete. {announced} CTF(s) announced and channels created.")
    else:
        await ctx.send("No new CTFs were announced (all were already announced).")

async def delete_ctf_role(guild, event_id):
    ctf_roles = get_ctf_roles_mapping()
    role_id = ctf_roles.get(event_id)
    if role_id:
        role = guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason="CTF ended, cleaning up role")
            except Exception as e:
                print(f"Error deleting role for event {event_id}: {e}")
        # Remove from mapping
        del ctf_roles[event_id]
        set_ctf_roles_mapping(ctf_roles)

def generate_random_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(random.choice(chars) for _ in range(length))

async def set_ctf_channel_description_and_message(ctf_channel, ctf_name, ctf_url, ctf_discord=None):
    email = "example@gmail.com" # change to your email
    password = generate_random_password(14)
    team_name = "Team name " # change to your team name
    desc_lines = [
        f"CTF: {ctf_name}",
        f"Link: {ctf_url}",
        f"Team Name: {team_name}",
        f"Email: {email}",
        f"Password: {password}"
    ]
    if ctf_discord:
        desc_lines.append(f"Discord: {ctf_discord}")
    desc = " | ".join(desc_lines)
    try:
        await ctf_channel.edit(topic=desc[:1024])
    except Exception as e:
        print(f"Error setting channel topic: {e}")

def get_random_color():
    return discord.Color(random.randint(0, 0xFFFFFF))

async def give_category_role_and_congrats(user, guild, category, channel):
    role_name = category.capitalize()
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        role = await guild.create_role(name=role_name, colour=get_random_color(), mentionable=False, reason="First solve in category")
    if role not in user.roles:
        await user.add_roles(role, reason="First solve in category")
        # Build congratulatory embed
        embed = discord.Embed(
            title=f"🎉✨ Congratulations, {user.display_name}! ✨🎉",
            description="🌟 You have just received a new role in the server! 🌟",
            color=role.color
        )
        embed.add_field(name="🏷️ Role Name", value=role_name, inline=True)
        embed.add_field(name="🏠 Server", value=guild.name, inline=True)
        embed.add_field(name="🎯 Message", value="Keep up the great work, and don't stop here! 🚀 Maybe aim for another role soon? 😉", inline=False)
        embed.add_field(name="💬 Need help or have questions?", value="Feel free to ask in the server. We're here to support you! 😁", inline=False)
        embed.add_field(name="🎮 Remember", value="We are here to play for fun and to learn from each other's experiences. Let's grow together! 🌟🤝", inline=False)
        embed.add_field(name="🔔 Note", value="Enjoy your new role privileges and have fun! 🎉", inline=False)
        try:
            await user.send(embed=embed)
        except Exception:
            await channel.send(f"{user.mention}", embed=embed)

# Run bot
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("❌ Error: Missing Discord token!")
else:
    client.run(TOKEN)