"""Palace Tang fat cow stats: visits, and how many meters of fat cow each member eats."""

import discord
from discord import app_commands
from sqlalchemy import func
from sqlmodel import col, select

from beans_bot.cogs.base import QUIET_HELP, BeansCog, requires_feature
from beans_bot.config.schema import FatCowReactions
from beans_bot.db.models import FatCowConsumption, FatCowVisit
from beans_bot.services import emoji

ON_START_MESSAGE = """
The ritual of the meter fat cow has begun...

In solemn accordance, all ye who are of the league of the fat cow, log your consumption.
`/fatcow consumed <meters>`

Those not of the cow, submit your attendance.
`/fatcow went`
"""


def reaction_for(reactions: FatCowReactions, meters: float) -> str:
    if meters == 0:
        return reactions.no_fat_cow_emoji
    if meters < reactions.low_fat_cow_threshold:
        return reactions.low_fat_cow_emoji
    if meters < reactions.medium_fat_cow_threshold:
        return reactions.medium_fat_cow_emoji
    if meters < reactions.high_fat_cow_threshold:
        return reactions.high_fat_cow_emoji
    return reactions.very_high_fat_cow_emoji


class FatCowCog(BeansCog):
    fatcow = app_commands.Group(name="fatcow", description="Palace Tang fat cow stats.", guild_only=True)

    @fatcow.command(name="start", description="Record that we went to Palace Tang today.")
    @requires_feature("fat_cow")
    async def start(self, interaction: discord.Interaction):
        assert interaction.guild_id is not None
        async with self.services.db.transaction() as session:
            session.add(FatCowVisit(guild_id=interaction.guild_id))
        await interaction.response.send_message(ON_START_MESSAGE)

    @fatcow.command(name="consumed", description="Record how much fat cow you ate.")
    @app_commands.describe(meters="Meters of fat cow eaten")
    @requires_feature("fat_cow")
    async def consumed(self, interaction: discord.Interaction, meters: app_commands.Range[float, 0.0]):
        await self.record(interaction, meters)

    @fatcow.command(name="went", description="Record that you went without eating any fat cow.")
    @requires_feature("fat_cow")
    async def went(self, interaction: discord.Interaction):
        await self.record(interaction, 0.0)

    async def record(self, interaction: discord.Interaction, meters: float) -> None:
        assert interaction.guild_id is not None
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.fat_cow is not None
        async with self.services.db.transaction() as session:
            session.add(FatCowConsumption(guild_id=interaction.guild_id, user_id=interaction.user.id, meters=meters))

        if meters == 0:
            text = f"{interaction.user.mention} went to Palace Tang."
        else:
            text = f"{interaction.user.mention} ate {meters:g} meters of fat cow."
        await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions.none())

        spec = reaction_for(config.fat_cow.reactions, meters)
        if not spec:
            return
        message = await interaction.original_response()
        for e in emoji.resolve(interaction.guild, spec):
            try:
                await message.add_reaction(e)
            except discord.HTTPException as err:
                self.logger.error(f"Failed to add reaction {e} in guild {interaction.guild_id}: {err}")

    @fatcow.command(name="leaderboard", description="Show everyone's fat cow stats.")
    @app_commands.describe(quiet=QUIET_HELP)
    @requires_feature("fat_cow")
    async def leaderboard(self, interaction: discord.Interaction, quiet: bool = False):
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        async with self.services.db.transaction() as session:
            visits = (
                await session.exec(select(func.count()).select_from(FatCowVisit).where(FatCowVisit.guild_id == guild_id))
            ).one()
            total = func.sum(FatCowConsumption.meters).label("total")
            stats = (
                await session.exec(
                    select(FatCowConsumption.user_id, total, func.count(col(FatCowConsumption.id)))
                    .where(FatCowConsumption.guild_id == guild_id)
                    .group_by(col(FatCowConsumption.user_id))
                    .order_by(total.desc())
                )
            ).all()

        if visits == 0:
            await interaction.response.send_message("Leaderboard:\nNo fat cow visits recorded yet.", ephemeral=quiet)
            return
        lines = ["Total visits to the Palace:", f"{visits}", "", "Leaderboard:"]
        for user_id, meters, times in stats:
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"Unknown User ({user_id})"
            lines.append(
                f"{name}: {meters:.2f} meters total, {times} attendances, {meters / times:.2f} meters per tang"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=quiet)
