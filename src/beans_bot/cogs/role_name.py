"""Polls to rename the student role to "<Adjective> Robotics Student"."""

import discord
from discord import app_commands
from discord.ext import tasks
from sqlmodel import select

from beans_bot.cogs.base import BeansCog, in_bot_channel, requires_feature
from beans_bot.db.models import RoleNamePoll

POSITIVE_REACTIONS = ("👍", "🎉", "❤️", "🔥")
NEGATIVE_REACTIONS = ("👎",)
MAX_ADJECTIVE_LENGTH = 20


def validate_adjective(adjective: str) -> str | None:
    adjective = adjective.strip()
    if not adjective or len(adjective) > MAX_ADJECTIVE_LENGTH:
        return None
    return adjective.title()


def poll_text(adjective: str, votes_needed: int) -> str:
    return (
        f'React with 👍 or 👎 to rename the robotics students to "{adjective} Robotics Student" '
        f"(required votes: {votes_needed})"
    )


class RoleNameCog(BeansCog):
    async def cog_load(self):
        self.refresh_polls.start()

    async def cog_unload(self):
        self.refresh_polls.cancel()

    @app_commands.command(name="robrole", description="Start a poll to change the robotics student role's adjective.")
    @app_commands.describe(adjective=f"The new adjective, up to {MAX_ADJECTIVE_LENGTH} characters")
    @app_commands.guild_only()
    @requires_feature("role_name")
    @in_bot_channel()
    async def robrole(self, interaction: discord.Interaction, adjective: str):
        sanitized = validate_adjective(adjective)
        if sanitized is None:
            await interaction.response.send_message(
                f"Invalid adjective. Please provide a non-empty adjective of at most {MAX_ADJECTIVE_LENGTH} characters.",
                ephemeral=True,
            )
            return
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.role_name is not None and interaction.guild_id is not None

        await interaction.response.send_message(poll_text(sanitized, config.role_name.required_votes))
        message = await interaction.original_response()
        async with self.services.db.transaction() as session:
            session.add(
                RoleNamePoll(
                    poll_message_id=message.id,
                    guild_id=interaction.guild_id,
                    channel_id=message.channel.id,
                    initiator_user_id=interaction.user.id,
                    adjective=sanitized,
                )
            )

    async def process_poll(self, poll_message_id: int) -> None:
        async with self.services.db.transaction() as session:
            poll = await session.get(RoleNamePoll, poll_message_id)
        if poll is None or not poll.active:
            return
        config = self.guild_config(poll.guild_id)
        guild = self.bot.get_guild(poll.guild_id)
        if config is None or config.role_name is None or guild is None:
            return
        message = await self.fetch_message(poll.channel_id, poll.poll_message_id)
        if message is None:
            self.logger.warning(f"Poll message {poll.poll_message_id} not found in channel {poll.channel_id}")
            return

        positive_voters: set[int] = set()
        negative_voters: set[int] = set()
        for reaction in message.reactions:
            if str(reaction.emoji) in POSITIVE_REACTIONS:
                voters = positive_voters
            elif str(reaction.emoji) in NEGATIVE_REACTIONS:
                voters = negative_voters
            else:
                continue
            async for user in reaction.users():
                if self.is_own_user(user.id):
                    continue
                if not config.role_name.allow_self_voting and user.id == poll.initiator_user_id:
                    continue
                voters.add(user.id)

        votes_needed = config.role_name.required_votes - (len(positive_voters) - len(negative_voters))
        if votes_needed > 0:
            await message.edit(content=poll_text(poll.adjective, votes_needed))
            return

        role = guild.get_role(config.role_name.student_role_id)
        if role is None:
            self.logger.warning(f"Student role {config.role_name.student_role_id} not found in guild {guild.id}")
            return
        new_name = f"{poll.adjective} Robotics Student"
        await role.edit(name=new_name)
        async with self.services.db.transaction() as session:
            stored = await session.get(RoleNamePoll, poll_message_id)
            if stored is not None:
                stored.active = False
        await message.edit(content=f'Poll passed! The robotics student role has been renamed to "{new_name}".')

    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not self.is_own_user(payload.user_id):
            await self.process_poll(payload.message_id)

    @BeansCog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not self.is_own_user(payload.user_id):
            await self.process_poll(payload.message_id)

    @tasks.loop(seconds=60)
    async def refresh_polls(self):
        async with self.services.db.transaction() as session:
            poll_ids = (await session.exec(select(RoleNamePoll.poll_message_id).where(RoleNamePoll.active == True))).all()  # noqa: E712
        for poll_id in poll_ids:
            try:
                await self.process_poll(poll_id)
            except discord.HTTPException as e:
                self.logger.warning(f"Failed to refresh poll {poll_id}: {e}")

    @refresh_polls.before_loop
    async def before_refresh_polls(self):
        await self.bot.wait_until_ready()
