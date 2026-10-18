import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog


class BasicCog(BeansCog):
    @app_commands.command(name="ping", description="Check that the bot is responsive.")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Pong! Latency: {round(self.bot.latency * 1000)}ms")
