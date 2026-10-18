"""Tomato reactions: thrown at jailed members, or a scolding for throwing one at an innocent."""

import discord

from beans_bot.cogs.base import BeansCog
from beans_bot.services import emoji, scripts


class TomatoesCog(BeansCog):
    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or self.is_own_user(payload.user_id):
            return
        config = self.guild_config(payload.guild_id)
        if config is None or config.jail is None or not emoji.matches(payload.emoji, config.jail.tomato_emoji):
            return
        message = await self.fetch_message(payload.channel_id, payload.message_id)
        if message is None:
            return

        target_id = message.author.id
        innocent = not await self.services.jail.is_jailed(payload.guild_id, target_id)
        await self.services.jail.record_tomato(
            guild_id=payload.guild_id,
            thrower_user_id=payload.user_id,
            target_user_id=target_id,
            message_id=message.id,
            innocent=innocent,
        )
        self.logger.info(f"Tomato thrown at {target_id} by {payload.user_id} in guild {payload.guild_id}")

        if innocent:
            text = scripts.pick(
                config.jail.assault_innocent_scripts,
                reacting_user=f"<@{payload.user_id}>",
                attacked_user=f"<@{target_id}>",
            )
            if text:
                await message.channel.send(text)
