"""
Manifestos: /manifesto has the bot espouse a piece of wisdom from a member's manifesto.

- Pick the member and the wisdom number, or leave either out for a random one.
- Only the extracted wisdom is ever quoted. The manifestos themselves stay private.
"""

from __future__ import annotations

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, in_bot_channel, requires_feature
from beans_bot.services import scripts
from beans_bot.services.manifesto import NoManifesto, NoSuchWisdom


class ManifestoCog(BeansCog):
    @app_commands.command(name="manifesto", description="Hear a piece of wisdom from a member's manifesto.")
    @app_commands.describe(
        member="Whose manifesto to quote. Leave it out for a random one.",
        wisdom_index="Which piece of wisdom, from 1. Leave it out for a random one.",
    )
    @app_commands.guild_only()
    @requires_feature("manifesto")
    @in_bot_channel()
    async def manifesto(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        wisdom_index: app_commands.Range[int, 1] | None = None,
    ):
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.manifesto is not None
        try:
            wisdom = self.services.manifesto.pick(interaction.guild_id, member.id if member else None, wisdom_index)
        except NoManifesto:
            if member is None:
                text = "Nobody here has written a manifesto. A server of unexamined lives."
            else:
                text = f"{member.mention} has no manifesto. They have no thoughts, only vibes."
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        except NoSuchWisdom as e:
            await interaction.response.send_message(
                f"There's no wisdom #{wisdom_index}. The deepest anyone has gone is {e.most}. Nobody's that wise.", ephemeral=True
            )
            return

        author = f"<@{wisdom.author_id}>"
        intro = scripts.pick(config.manifesto.intro_scripts, author=author) or f"From the manifesto of {author}:"
        self.logger.info(f"Quoting wisdom {wisdom.index} of {wisdom.author_id} in guild {interaction.guild_id}")
        await interaction.response.send_message(
            f"{intro}\n> {wisdom.text}\n-# Wisdom #{wisdom.index} of {wisdom.total}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
