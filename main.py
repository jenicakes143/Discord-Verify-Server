import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import random
import asyncio
import aiohttp
import re
from datetime import timedelta
from collections import deque
from dotenv import load_dotenv

load_dotenv()

CONFIG_FILE = "config.json"

# ============================================================
#  CONFIG STORAGE
# ============================================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


# ============================================================
#  MODERATION HELPER: PARSE DURATION
# ============================================================
def parse_duration(duration_str: str):
    """Converts 10m, 2h, 1d into seconds."""
    if not duration_str:
        return None
    match = re.match(r"(\d+)([smhd])", duration_str.lower())
    if not match:
        return None
    amount, unit = match.groups()
    amount = int(amount)
    if unit == 's': return amount
    if unit == 'm': return amount * 60
    if unit == 'h': return amount * 3600
    if unit == 'd': return amount * 86400
    return None


# ============================================================
#  AI CHAT ENGINE (ModelGo Configuration)
# ============================================================
MODELGO_API_KEY = os.environ.get("MODELGO_API_KEY")
MODELGO_URL     = "https://api.modelgo.ai/v1/chat/completions"
MODELGO_MODEL   = "gpt-4o-mini" 

_chat_history: dict[int, deque] = {}       
_HISTORY_TURNS = 6
_last_mention_reply: dict[int, float] = {} 

def _system_prompt(message: discord.Message) -> str:
    guild_name = message.guild.name if message.guild else "a server"
    bot_name = message.guild.me.display_name if message.guild else "the bot"
    user_name = message.author.display_name
    return (
        f"You are {bot_name}, a friendly Discord chat bot in the server '{guild_name}'. "
        f"You are talking to {user_name}. "
        "Be casual, warm, and human. Use short replies (1-3 sentences max). "
        "Do NOT use any emojis under any circumstances. "
        "Never mention you are an AI or language model unless directly asked. "
        "If someone asks about verification, tell them to click the Verify button. "
        "If someone asks for admin help, tell them to ping a moderator. "
        "Never use markdown headers or bullet lists — chat like a real person."
    )

async def get_ai_reply(text: str, message: discord.Message) -> str:
    if not MODELGO_API_KEY:
        return generate_fallback_reply(text, message)

    channel_id = message.channel.id
    if channel_id not in _chat_history:
        _chat_history[channel_id] = deque(maxlen=_HISTORY_TURNS)
    history = _chat_history[channel_id]

    messages = [{"role": "system", "content": _system_prompt(message)}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    payload = {
        "model": MODELGO_MODEL,
        "messages": messages,
        "max_tokens": 120,
        "temperature": 0.9,
        "top_p": 0.95,
    }
    headers = {
        "Authorization": f"Bearer {MODELGO_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                MODELGO_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status != 200:
                    print(f"[AI] ModelGo HTTP {r.status}: {await r.text()}")
                    return generate_fallback_reply(text, message)
                data = await r.json()
                reply = data["choices"][0]["message"]["content"].strip()
                history.append({"role": "user", "content": text})
                history.append({"role": "assistant", "content": reply})
                return reply
    except Exception as e:
        print(f"[AI] Error: {e}")
        return generate_fallback_reply(text, message)

def generate_fallback_reply(text: str, message: discord.Message) -> str:
    name = message.author.display_name
    t = text.strip().lower()
    if not t: return f"Hey {name}! What's up?"
    if any(w in t for w in ["hi", "hello", "hey"]): return f"Hey {name}!"
    if any(w in t for w in ["thank", "thx", "ty"]): return "You're welcome!"
    if "how are you" in t: return "Doing great, thanks! How about you?"
    if "?" in t: return "Good question! An admin might know better."
    return "Gotcha! Tell me more."


# ============================================================
#  PERSISTENT VERIFY BUTTON & DM
# ============================================================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify / Unverify", style=discord.ButtonStyle.primary, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        guild_id = str(interaction.guild_id)
        if guild_id not in config:
            return await interaction.response.send_message("Verification not set up. Contact an admin.", ephemeral=True)

        role = interaction.guild.get_role(config[guild_id])
        if not role:
            return await interaction.response.send_message("Verified role no longer exists. Contact an admin.", ephemeral=True)

        # Unverify Logic
        if role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("You have been unverified.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to remove this role.", ephemeral=True)
        
        # Verify Logic
        else:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("You have been successfully verified!", ephemeral=True)
                
                # SEND THE DM EMBED
                embed = discord.Embed(
                    title="Roles Updated",
                    description=f"You verified in the server, **{interaction.guild.name}**, and changed your roles.\n\n**Roles Added**\n{role.mention}",
                    color=discord.Color.purple()
                )
                if interaction.guild.icon:
                    embed.set_thumbnail(url=interaction.guild.icon.url)
                
                embed.set_footer(text=f"Sent by {interaction.guild.me.display_name} on behalf of {interaction.guild.name}.")
                
                try:
                    await interaction.user.send(embed=embed)
                except discord.Forbidden:
                    pass # User has DMs disabled, ignore silently
                    
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to give you this role. Ensure my role is higher.", ephemeral=True)


# ============================================================
#  BOT CLASS
# ============================================================
class VerifyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!", "."),
            intents=discord.Intents.all(),
            status=discord.Status.online,
        )

    async def setup_hook(self):
        self.add_view(VerifyView())
        await self.tree.sync()


bot = VerifyBot()


@bot.event
async def on_ready():
    print("=" * 55)
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"AI backend: {'ModelGo (live)' if MODELGO_API_KEY else 'Fallback (no API KEY)'}")
    print("=" * 55)


# ============================================================
#  ON_MESSAGE — AI mention responder
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    await bot.process_commands(message)

    if bot.user not in message.mentions:
        return

    now = asyncio.get_event_loop().time()
    if now - _last_mention_reply.get(message.author.id, 0) < 2.5:
        return
    _last_mention_reply[message.author.id] = now

    text = message.content
    for m in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        text = text.replace(m, "")
    
    async with message.channel.typing():
        await asyncio.sleep(random.uniform(0.6, 1.4))
        reply = await get_ai_reply(text.strip(), message)

    try:
        await message.reply(reply, mention_author=False)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ============================================================
#  MODERATION COMMANDS (No Emojis)
# ============================================================
@bot.hybrid_command(name="mute", description="Timeout a member.")
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def mute_cmd(ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
    seconds = parse_duration(duration)
    if not seconds:
        return await ctx.send("Invalid duration format. Use s, m, h, or d (e.g., 10m, 1h, 2d).", ephemeral=True)
    
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot mute this user because their role is higher than or equal to mine.", ephemeral=True)

    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason)
        await ctx.send(f"**{member.mention}** has been muted for **{duration}**.\n**Reason:** {reason}")
    except Exception as e:
        await ctx.send(f"Failed to mute: {e}")


@bot.hybrid_command(name="unmute", description="Remove a timeout from a member.")
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def unmute_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not member.is_timed_out():
        return await ctx.send(f"**{member.name}** is not currently muted.")
        
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot unmute this user because their role is higher than or equal to mine.", ephemeral=True)

    try:
        await member.timeout(None, reason=reason)
        await ctx.send(f"**{member.mention}** has been unmuted.\n**Reason:** {reason}")
    except Exception as e:
        await ctx.send(f"Failed to unmute: {e}")


@bot.hybrid_command(name="kick", description="Kick a member from the server.")
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick_cmd(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot kick this user because their role is higher than or equal to mine.", ephemeral=True)

    try:
        await member.kick(reason=reason)
        await ctx.send(f"**{member.name}** has been kicked.\n**Reason:** {reason}")
    except Exception as e:
        await ctx.send(f"Failed to kick: {e}")


async def unban_later(guild: discord.Guild, user: discord.User, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await guild.unban(user, reason="Temporary ban expired.")
        print(f"Unbanned {user.name} after {seconds} seconds.")
    except Exception as e:
        print(f"Failed to unban {user.name}: {e}")


@bot.hybrid_command(name="ban", description="Ban a member from the server.")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban_cmd(ctx: commands.Context, member: discord.User, duration: str = None, *, reason: str = "No reason provided"):
    if isinstance(member, discord.Member) and member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot ban this user because their role is higher than or equal to mine.", ephemeral=True)

    try:
        await ctx.guild.ban(member, reason=reason)
        
        if duration:
            seconds = parse_duration(duration)
            if not seconds:
                return await ctx.send(f"**{member.name}** has been banned permanently (invalid duration format given).\n**Reason:** {reason}")
            
            await ctx.send(f"**{member.name}** has been banned for **{duration}**.\n**Reason:** {reason}")
            asyncio.create_task(unban_later(ctx.guild, member, seconds))
        else:
            await ctx.send(f"**{member.name}** has been banned permanently.\n**Reason:** {reason}")
            
    except Exception as e:
        await ctx.send(f"Failed to ban: {e}")


@bot.hybrid_command(name="unban", description="Unban a user from the server using their ID.")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban_cmd(ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
    try:
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"**{user.name}** has been unbanned.\n**Reason:** {reason}")
    except discord.NotFound:
        await ctx.send(f"**{user.name}** is not currently banned from this server.")
    except Exception as e:
        await ctx.send(f"Failed to unban: {e}")


# ============================================================
#  SETUP & OTHER COMMANDS
# ============================================================
@bot.hybrid_command(name="setup", description="Set up the verification message in this channel.")
@app_commands.describe(role="The role users will get when they verify.")
@commands.has_permissions(administrator=True)
async def setup_verify(ctx: commands.Context, role: discord.Role):
    # 1. Defer immediately to prevent timeout
    await ctx.defer(ephemeral=True)

    # 2. Check if the role exists
    if not role:
        return await ctx.send("That role doesn't exist or I don't have permission to see it.")

    config = load_config()
    config[str(ctx.guild.id)] = role.id
    save_config(config)

    embed = discord.Embed(
        title="🛡️ Server Verification",
        description="To gain access to the rest of the server, please click the button below.\n\n*Click again to unverify and hide the channels.*",
        color=discord.Color.green(),
    )
    
    try:
        # Send the panel publicly
        await ctx.send(embed=embed, view=VerifyView())
        # 3. Send the success message (works for both slash and prefix)
        await ctx.send(f"Verification panel set up! Users will receive the **{role.name}** role.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to send messages here.")


@bot.hybrid_command(name="reset_chat", description="Clear the bot's chat memory for this channel.")
async def reset_chat(ctx: commands.Context):
    _chat_history.pop(ctx.channel.id, None)
    await ctx.send("Chat memory cleared for this channel.", ephemeral=True)


# ============================================================
#  ERROR HANDLERS (Catches invalid roles/users)
# ============================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You need the correct permissions to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.BadArgument) or isinstance(error, app_commands.CommandInvokeError):
        await interaction.response.send_message("Invalid argument provided. Make sure you mentioned a valid user or role.", ephemeral=True)
    else:
        try:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
        except:
            pass
        print(f"Slash Cmd Error: {error}")

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the correct permissions to use this command.")
    elif isinstance(error, commands.BadArgument) or isinstance(error, commands.MemberNotFound) or isinstance(error, commands.UserNotFound):
        await ctx.send("Invalid argument provided. Make sure you mentioned a valid user or provided a valid role.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("I don't have the permissions required to do that.")
    elif isinstance(error, commands.CommandNotFound):
        pass 
    else:
        await ctx.send(f"Error: {error}")
        print(f"Prefix Cmd Error: {error}")


# ============================================================
#  RUN
# ============================================================
token = os.environ.get("DISCORD_TOKEN")
if not token:
    raise SystemExit("DISCORD_TOKEN environment variable is not set.")
bot.run(token)
