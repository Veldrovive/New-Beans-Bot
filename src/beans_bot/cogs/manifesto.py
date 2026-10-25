"""
Manifestos: the bot espouses wisdom from members' manifestos.

- /manifesto wisdom posts one piece. Pick the member and the number, or leave either out for a random one.
- /manifesto list privately shows the numbered wisdom, so you can choose what to impart.
- Both work in any channel, so wisdom can land as a reply wherever it's needed.
- Only the extracted wisdom is ever quoted. The manifestos themselves stay private.
"""

from __future__ import annotations

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, requires_feature
from beans_bot.services import scripts
from beans_bot.services.manifesto import NoManifesto, NoSuchWisdom

MESSAGE_LIMIT = 2000  # Discord's cap on one message


def no_manifesto_text(member: discord.Member | None) -> str:
    if member is None:
        return "Nobody here has written a manifesto. A server of unexamined lives."
    return f"{member.mention} has no manifesto. They have no thoughts, only vibes."


def chunk_lines(lines: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Join lines into as few messages as fit under the limit."""
    chunks: list[str] = []
    for line in lines:
        if chunks and len(chunks[-1]) + 1 + len(line) <= limit:
            chunks[-1] += "\n" + line
        else:
            chunks.append(line)
    return chunks


class ManifestoCog(BeansCog):
    manifesto_group = app_commands.Group(name="manifesto", description="Espouse wisdom from members' manifestos.", guild_only=True)

    @manifesto_group.command(name="wisdom", description="Impart a piece of wisdom from a member's manifesto.")
    @app_commands.describe(
        member="Whose manifesto to quote. Leave it out for a random one.",
        wisdom_index="Which piece of wisdom (see /manifesto list). Leave it out for a random one.",
    )
    @requires_feature("manifesto")
    async def wisdom(
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
            await interaction.response.send_message(
                no_manifesto_text(member), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
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

    @manifesto_group.command(name="list", description="Privately read the numbered wisdom, to choose what to impart.")
    @app_commands.describe(member="Whose manifesto to read. Leave it out for all of them.")
    @requires_feature("manifesto")
    async def list_wisdom(self, interaction: discord.Interaction, member: discord.Member | None = None):
        try:
            manifestos = self.services.manifesto.manifestos(interaction.guild_id, member.id if member else None)
        except NoManifesto:
            await interaction.response.send_message(
                no_manifesto_text(member), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
            return

        lines: list[str] = []
        for manifesto in manifestos:
            lines.append(f"**The manifesto of <@{manifesto.author_id}>**")
            lines += [f"**{i}.** {text}" for i, text in enumerate(manifesto.wisdom, start=1)]
            lines.append("")
        lines.append("-# Impart one with /manifesto wisdom member:<them> wisdom_index:<number>")

        chunks = chunk_lines(lines)
        none = discord.AllowedMentions.none()
        await interaction.response.send_message(chunks[0], ephemeral=True, allowed_mentions=none)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=none)
