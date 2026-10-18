import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, owner_only


class AdminCog(BeansCog):
    admin = app_commands.Group(name="admin", description="Bot owner tools.")

    @admin.command(name="sync", description="Re-sync slash commands with Discord.")
    @owner_only()
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced = await self.bot.tree.sync()
        await interaction.followup.send(f"Synced {len(synced)} commands.", ephemeral=True)
