import discord
from discord.ext import commands
from discord import app_commands
import os
import json
from dotenv import load_dotenv

load_dotenv()

CONFIG_FILE = "config.json"

# Function to load and save configs (so it remembers settings for each server)
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# The persistent button view
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # timeout=None makes the button work forever

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = load_config()
        guild_id = str(interaction.guild_id)

        # Check if the server has set up verification
        if guild_id not in config:
            await interaction.response.send_message("Verification is not set up for this server. Please contact an admin.", ephemeral=True)
            return

        role_id = config[guild_id]
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("The verified role no longer exists. Please contact an admin.", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("You are already verified!", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You have been successfully verified! You can now see the rest of the server.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to give you this role. Please ensure my bot role is higher than the Verified role.", ephemeral=True)

class VerifyBot(commands.Bot):
    def __init__(self):
        # 👇 Added status=discord.Status.online to force it online 👇
        super().__init__(
            command_prefix="!", 
            intents=discord.Intents.all(),
            status=discord.Status.online
        )

    async def setup_hook(self):
        # This ensures the button still works even if the bot restarts
        self.add_view(VerifyView())
        # Sync slash commands globally
        await self.tree.sync()

bot = VerifyBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Multi-Server Verification bot is ready!")

@bot.tree.command(name="setup", description="Set up the verification message in this channel.")
@app_commands.describe(role="The role users will get when they verify.")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction, role: discord.Role):
    # Defer immediately so Discord doesn't time out
    await interaction.response.defer(ephemeral=True)

    # Save the role for this specific server
    config = load_config()
    config[str(interaction.guild_id)] = role.id
    save_config(config)

    embed = discord.Embed(
        title="🛡️ Server Verification",
        description="To gain access to the rest of the server, please click the button below.",
        color=discord.Color.green()
    )
    
    try:
        await interaction.channel.send(embed=embed, view=VerifyView())
        # Use followup.send since we deferred earlier
        await interaction.followup.send(f"Verification panel set up! Users will receive the **{role.name}** role.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to send messages in this channel.", ephemeral=True)

# This catches errors so you can see them in Discord
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Administrator permissions to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)
        print(f"Error: {error}")

# Run the bot
token = os.environ.get("DISCORD_TOKEN")
if not token:
    raise SystemExit("DISCORD_TOKEN environment variable is not set.")
bot.run(token)
