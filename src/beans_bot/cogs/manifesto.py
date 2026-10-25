"""
Manifestos: the bot espouses wisdom from members' manifestos.

- /manifesto wisdom posts one piece. Pick the member and the number, or leave either out for a random one.
- /manifesto list privately shows the numbered wisdom, so you can choose what to impart.
- "Impart wisdom" in a message's Apps menu replies to that message with wisdom, using the reply scripts.
  Slash commands can't be replies, so this is how wisdom lands on a specific message.
- All of it works in any channel.
- Only the extracted wisdom is ever quoted. The manifestos themselves stay private.
"""

from __future__ import annotations

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, requires_feature
from beans_bot.services import scripts
from beans_bot.services.manifesto import NoManifesto, NoSuchWisdom, Wisdom

MESSAGE_LIMIT = 2000  # Discord's cap on one message


def no_manifesto_text(member_id: int | None) -> str:
    if member_id is None:
        return "Nobody here has written a manifesto. A server of unexamined lives."
    return f"<@{member_id}> has no manifesto. They have no thoughts, only vibes."


def chunk_lines(lines: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Join lines into as few messages as fit under the limit."""
    chunks: list[str] = []
    for line in lines:
        if chunks and len(chunks[-1]) + 1 + len(line) <= limit:
            chunks[-1] += "\n" + line
        else:
            chunks.append(line)
    return chunks


def wisdom_text(intro: str, wisdom: Wisdom) -> str:
    return f"{intro}\n> {wisdom.text}\n-# Wisdom #{wisdom.index} of {wisdom.total}"


class ImpartModal(discord.ui.Modal, title="Impart wisdom"):
    """Asked after "Impart wisdom" on a message. Both fields are optional; empty means random."""

    author = discord.ui.Label(
        text="Whose wisdom?",
        description="Leave it empty for a random manifesto.",
        component=discord.ui.UserSelect(min_values=0, max_values=1, required=False),
    )
    index = discord.ui.Label(
        text="Which piece of wisdom?",
        description="A number from /manifesto list. Leave it empty for a random one.",
        component=discord.ui.TextInput(required=False, max_length=4, placeholder="Random"),
    )

    def __init__(self, cog: ManifestoCog, target: discord.Message):
        super().__init__()
        self.cog = cog
        self.target = target

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.author.component, discord.ui.UserSelect)
        assert isinstance(self.index.component, discord.ui.TextInput)
        author_id = self.author.component.values[0].id if self.author.component.values else None
        raw_index = self.index.component.value.strip()
        if raw_index and not (raw_index.isdigit() and int(raw_index) >= 1):
            await interaction.response.send_message(
                f"\"{raw_index}\" isn't a wisdom number. Wisdom is counted 1, 2, 3, like a civilized person.", ephemeral=True
            )
            return
        await self.cog.impart(interaction, self.target, author_id, int(raw_index) if raw_index else None)


class ManifestoCog(BeansCog):
    manifesto_group = app_commands.Group(name="manifesto", description="Espouse wisdom from members' manifestos.", guild_only=True)

    def __init__(self, bot):
        super().__init__(bot)
        self.impart_menu = app_commands.ContextMenu(name="Impart wisdom", callback=self.impart_wisdom)

    async def cog_load(self):
        self.bot.tree.add_command(self.impart_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.impart_menu.name, type=self.impart_menu.type)

    async def pick(self, interaction: discord.Interaction, author_id: int | None, index: int | None) -> Wisdom | None:
        """A piece of wisdom, or None after telling the member why there isn't one."""
        try:
            return self.services.manifesto.pick(interaction.guild_id, author_id, index)
        except NoManifesto:
            text = no_manifesto_text(author_id)
        except NoSuchWisdom as e:
            text = f"There's no wisdom #{index}. The deepest anyone has gone is {e.most}. Nobody's that wise."
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        return None

    # --- Commands -----------------------------------------------------------------------

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
        wisdom = await self.pick(interaction, member.id if member else None, wisdom_index)
        if wisdom is None:
            return
        author = f"<@{wisdom.author_id}>"
        intro = scripts.pick(config.manifesto.intro_scripts, author=author) or f"From the manifesto of {author}:"
        self.logger.info(f"Quoting wisdom {wisdom.index} of {wisdom.author_id} in guild {interaction.guild_id}")
        await interaction.response.send_message(wisdom_text(intro, wisdom), allowed_mentions=discord.AllowedMentions.none())

    @manifesto_group.command(name="list", description="Privately read the numbered wisdom, to choose what to impart.")
    @app_commands.describe(member="Whose manifesto to read. Leave it out for all of them.")
    @requires_feature("manifesto")
    async def list_wisdom(self, interaction: discord.Interaction, member: discord.Member | None = None):
        none = discord.AllowedMentions.none()
        try:
            manifestos = self.services.manifesto.manifestos(interaction.guild_id, member.id if member else None)
        except NoManifesto:
            await interaction.response.send_message(no_manifesto_text(member.id if member else None), ephemeral=True, allowed_mentions=none)
            return

        lines: list[str] = []
        for manifesto in manifestos:
            lines.append(f"**The manifesto of <@{manifesto.author_id}>**")
            lines += [f"**{i}.** {text}" for i, text in enumerate(manifesto.wisdom, start=1)]
            lines.append("")
        lines.append("-# Impart one with /manifesto wisdom, or reply with it from a message's Apps > Impart wisdom")

        chunks = chunk_lines(lines)
        await interaction.response.send_message(chunks[0], ephemeral=True, allowed_mentions=none)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=none)

    # --- Replying with wisdom -----------------------------------------------------------

    @app_commands.guild_only()
    @requires_feature("manifesto")
    async def impart_wisdom(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_modal(ImpartModal(self, message))

    async def impart(self, interaction: discord.Interaction, target: discord.Message, author_id: int | None, index: int | None):
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.manifesto is not None
        wisdom = await self.pick(interaction, author_id, index)
        if wisdom is None:
            return
        names = {"imparter": interaction.user.mention, "author": f"<@{wisdom.author_id}>", "target": target.author.mention}
        intro = scripts.pick(config.manifesto.reply_scripts, **names)
        intro = intro or f"{names['imparter']} thinks the wisdom of {names['author']} will help you in this time, {names['target']}."
        text = wisdom_text(intro, wisdom)
        # The reply itself notifies the target. Nobody named in the text gets pinged.
        mentions = discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=True)
        try:
            await target.reply(text, allowed_mentions=mentions)
        except discord.HTTPException:
            # Can't reply there (no permission, or the message is gone), so post it where the menu was used
            self.logger.warning(f"Could not reply to message {target.id} in guild {interaction.guild_id}; posting instead")
            await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions.none())
            return
        self.logger.info(f"{interaction.user.id} imparted wisdom {wisdom.index} of {wisdom.author_id} on {target.id} in guild {interaction.guild_id}")
        await interaction.response.send_message("Wisdom imparted. They'll thank you someday. Probably.", ephemeral=True)
