import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import random
import asyncio
import aiohttp
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
#  AI CHAT ENGINE (ModelGo Configuration)
# ============================================================
MODELGO_API_KEY = os.environ.get("MODELGO_API_KEY")

# ModelGo's OpenAI-compatible endpoint
MODELGO_URL     = "https://api.modelgo.ai/v1/chat/completions"

# IMPORTANT: Choose a model from your ModelGo dashboard. 
# Example names: "gpt-4o", "claude-3-5-sonnet", "gpt-4o-mini"
# If this specific model fails, check your ModelGo console for a valid model ID.
MODELGO_MODEL   = "gpt-4o-mini" 

_chat_history: dict[int, deque] = {}       # channel_id -> deque of msgs
_HISTORY_TURNS = 6
_last_mention_reply: dict[int, float] = {} # user_id -> timestamp


def _system_prompt(message: discord.Message) -> str:
    guild_name = message.guild.name if message.guild else "a server"
    bot_name = message.guild.me.display_name if message.guild else "the bot"
    user_name = message.author.display_name
    return (
        f"You are {bot_name}, a friendly Discord chat bot in the server '{guild_name}'. "
        f"You are talking to {user_name}. "
        "Be casual, warm, and human. Use short replies (1-3 sentences max). "
        "Use emojis occasionally but not every message. "
        "Never mention you are an AI or language model unless directly asked. "
        "If someone asks about verification, tell them to click the Verify button on the panel. "
        "If someone asks for admin help, tell them to ping a moderator. "
        "Never use markdown headers or bullet lists — chat like a real person."
    )


async def get_ai_reply(text: str, message: discord.Message) -> str:
    if not MODELGO_API_KEY:
        print("[AI] No MODELGO_API_KEY found. Using fallback.")
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


# ============================================================
#  FALLBACK REPLIES (if no API key / AI fails)
# ============================================================
def generate_fallback_reply(text: str, message: discord.Message) -> str:
    name = message.author.display_name
    t = text.strip().lower()

    if not t:
        return random.choice([f"Hey {name}! What's up? 👋", "You rang? 👀", "Yes? How can I help?"])
    if any(w in t for w in ["hi", "hello", "hey", "yo", "sup"]):
        return random.choice([f"Hey {name}! 👋", "Hello! What can I do for you?", "Heyyy!"])
    if any(w in t for w in ["thank", "thx", "ty", "appreciate"]):
        return random.choice(["You're welcome! 😊", "Anytime!", "No problem at all!"])
    if "how are you" in t or "how r u" in t:
        return "Doing great, thanks! How about you? 😄"
    if "help" in t or "how do i" in t:
        return "Happy to help! If you need server access, click **Verify**. Admins use `/setup`."
    if any(w in t for w in ["bye", "cya", "later", "goodbye"]):
        return random.choice(["See ya! 👋", "Take care!", "Later!"])
    if any(w in t for w in ["lol", "lmao", "haha", "😂"]):
        return random.choice(["Haha glad you found that funny 😄", "Lol right? 😂"])
    if "?" in t:
        return random.choice([
            "Good question! An admin might know better than me 🤔",
            "Hmm, not sure on that one — worth asking a mod!",
        ])
    return random.choice([
        "Gotcha! Tell me more?",
        "Interesting! What else is on your mind?",
        "Haha, fair enough 😄",
        "I'm all ears 👂",
        "Sounds good to me!",
    ])


# ============================================================
#  PERSISTENT VERIFY BUTTON
# ============================================================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify / Unverify",
                       style=discord.ButtonStyle.primary,
                       custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction,
                            button: discord.ui.Button):
        config = load_config()
        guild_id = str(interaction.guild_id)

        if guild_id not in config:
            await interaction.response.send_message(
                "Verification is not set up for this server. Contact an admin.",
                ephemeral=True)
            return

        role = interaction.guild.get_role(config[guild_id])
        if not role:
            await interaction.response.send_message(
                "The verified role no longer exists. Contact an admin.",
                ephemeral=True)
            return

        if role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(
                    "✅ You have been unverified. The role has been removed.",
                    ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to remove this role. "
                    "My bot role must be higher than the Verified role.",
                    ephemeral=True)
        else:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(
                    "✅ You have been successfully verified! "
                    "You can now see the rest of the server.",
                    ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to give you this role. "
                    "My bot role must be higher than the Verified role.",
                    ephemeral=True)


# ============================================================
#  BOT CLASS
# ============================================================
class VerifyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
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
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"🧠 AI backend: {'ModelGo (live)' if MODELGO_API_KEY else 'Fallback (no MODELGO_API_KEY)'}")
    print(f"🌐 Servers: {len(bot.guilds)}")
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
    text = text.strip()

    async with message.channel.typing():
        await asyncio.sleep(random.uniform(0.6, 1.4))
        reply = await get_ai_reply(text, message)

    try:
        await message.reply(reply, mention_author=False)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ============================================================
#  SLASH COMMANDS
# ============================================================
@bot.tree.command(name="setup",
                  description="Set up the verification message in this channel.")
@app_commands.describe(role="The role users will get when they verify.")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)

    config = load_config()
    config[str(interaction.guild_id)] = role.id
    save_config(config)

    embed = discord.Embed(
        title="🛡️ Server Verification",
        description=(
            "To gain access to the rest of the server, please click the "
            "button below.\n\n*Click again to unverify and hide the channels.*"
        ),
        color=discord.Color.green(),
    )
    try:
        await interaction.channel.send(embed=embed, view=VerifyView())
        await interaction.followup.send(
            f"✅ Verification panel set up! Users will receive the "
            f"**{role.name}** role.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to send messages in this channel.",
            ephemeral=True)


@bot.tree.command(name="reset_chat",
                  description="Clear the bot's chat memory for this channel.")
async def reset_chat(interaction: discord.Interaction):
    _chat_history.pop(interaction.channel_id, None)
    await interaction.response.send_message(
        "🧠 Chat memory cleared for this channel.", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need Administrator permissions to use this command.",
            ephemeral=True)
    else:
        try:
            await interaction.response.send_message(
                f"❌ An error occurred: {error}", ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(
                f"❌ An error occurred: {error}", ephemeral=True)
        print(f"Error: {error}")


# ============================================================
#  RUN
# ============================================================
token = os.environ.get("DISCORD_TOKEN")
if not token:
    raise SystemExit("DISCORD_TOKEN environment variable is not set.")
bot.run(token)
