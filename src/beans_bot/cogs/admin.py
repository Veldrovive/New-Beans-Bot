"""
Bot owner tools. Every reply here is ephemeral, so nobody sees who is working the puppet.
- /admin say posts a message as the bot in a channel.
- /admin dm sends a member a DM from the bot.
"""

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, owner_only
from beans_bot.services.errors import UserFacingError

# Discord's message length limit.
MessageText = app_commands.Range[str, 1, 2000]
# Owner puppeting can ping members, but never @everyone or roles by accident.
PUPPET_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


class AdminCog(BeansCog):
    # default_permissions hides the group from members' command list. owner_only is the real check.
    admin = app_commands.Group(
        name="admin", description="Bot owner tools.", default_permissions=discord.Permissions(administrator=True)
    )

    @admin.command(name="sync", description="Re-sync slash commands with Discord.")
    @owner_only()
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced = await self.bot.tree.sync()
        await interaction.followup.send(f"Synced {len(synced)} commands.", ephemeral=True)

    @admin.command(name="say", description="Post a message as the bot. Only you see that you sent it.")
    @app_commands.describe(message="What the bot says", channel="Where to say it (defaults to here)")
    @app_commands.guild_only()
    @owner_only()
    async def say(
        self, interaction: discord.Interaction, message: MessageText, channel: discord.TextChannel | discord.Thread | None = None
    ):
        target = channel or interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            raise UserFacingError("I can't talk in here. Pick a text channel and I'll be your mouthpiece.")
        try:
            sent = await target.send(message, allowed_mentions=PUPPET_MENTIONS)
        except discord.HTTPException as e:
            raise UserFacingError(f"Discord gagged me ({e.text or e.status}). Check my permissions in that channel.")
        self.logger.info(f"Owner {interaction.user.id} had the bot say in channel {sent.channel.id}: {message!r}")
        await interaction.response.send_message(f"Said it. Your hand is invisible. {sent.jump_url}", ephemeral=True)

    @admin.command(name="dm", description="Send a member a DM from the bot. Only you see that you sent it.")
    @app_commands.describe(member="Who gets the DM", message="What the bot says")
    @owner_only()
    async def dm(self, interaction: discord.Interaction, member: discord.User, message: MessageText):
        if member.bot:
            raise UserFacingError("Bots don't read their DMs. Trust me.")
        try:
            await member.send(message, allowed_mentions=PUPPET_MENTIONS)
        except discord.Forbidden:
            raise UserFacingError(f"{member.mention} has their DMs locked. Whisper denied.")
        except discord.HTTPException as e:
            raise UserFacingError(f"Discord ate the DM ({e.text or e.status}).")
        self.logger.info(f"Owner {interaction.user.id} had the bot DM {member.id}: {message!r}")
        await interaction.response.send_message(f"Slid into {member.mention}'s DMs. They'll never know it was you.", ephemeral=True)
