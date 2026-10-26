"""
Manifestos: the bot espouses wisdom from members' manifestos.

- /manifesto wisdom posts one piece. Pick the member and the number, or leave either out for a random one.
- /manifesto list privately shows the numbered wisdom, so you can choose what to impart.
- "Impart wisdom" in a message's Apps menu replies to that message with wisdom, using the reply scripts.
  Slash commands can't be replies, so this is how wisdom lands on a specific message.
- /manifesto add puts a new piece of wisdom at the end of your own manifesto. Anyone can, so anyone can start one.
  The 1% can name another member and put words in their manifesto instead.
  /manifesto edit and /manifesto remove change what you added. The original manifesto can't be touched.
- All of it works in any channel.
- Only the extracted wisdom is ever quoted. The manifestos themselves stay private.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, requires_feature
from beans_bot.services import scripts
from beans_bot.services.errors import UserFacingError
from beans_bot.services.manifesto import NoManifesto, NoSuchWisdom, NotTheOnePercent, NotYourWisdom, Wisdom, WrittenInStone

MESSAGE_LIMIT = 2000  # Discord's cap on one message
WisdomText = app_commands.Range[str, 1, 1000]  # Leaves room for the intro and footer in one message


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
            return await self.services.manifesto.pick(interaction.guild_id, author_id, index)
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
            manifestos = await self.services.manifesto.manifestos(interaction.guild_id, member.id if member else None)
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

    @manifesto_group.command(name="add", description="Add a new piece of wisdom to your manifesto (or, for the 1%, anyone's).")
    @app_commands.describe(
        statement="The wisdom. It goes at the end of the manifesto.",
        member="Whose manifesto to add to. Only the 1% may pick someone else. Leave it out for your own.",
    )
    @requires_feature("manifesto")
    async def add(self, interaction: discord.Interaction, statement: WisdomText, member: discord.Member | None = None):
        assert interaction.guild_id is not None
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.manifesto is not None
        statement = statement.strip()
        if not statement:
            raise UserFacingError("That's blank. Even for a manifesto, you have to say something.")
        author_id = member.id if member else interaction.user.id
        try:
            wisdom = await self.services.manifesto.add(interaction.guild_id, author_id, statement, writer_id=interaction.user.id)
        except NotTheOnePercent:
            await interaction.response.send_message(
                f"Only the 1% gets to put words in {member.mention if member else 'their'} mouth. Buy the title, or write your own manifesto.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        author = f"<@{author_id}>"
        if author_id == interaction.user.id:
            intro = scripts.pick(config.manifesto.added_scripts, author=author) or f"{author} has amended their manifesto:"
        else:
            writer = interaction.user.mention
            intro = scripts.pick(config.manifesto.ghostwritten_scripts, writer=writer, author=author)
            intro = intro or f"{writer}, in their infinite wealth, has amended the manifesto of {author}:"
        await interaction.response.send_message(wisdom_text(intro, wisdom), allowed_mentions=discord.AllowedMentions.none())

    @manifesto_group.command(name="edit", description="Rewrite a piece of wisdom you added to your manifesto.")
    @app_commands.describe(wisdom_index="Which piece of your wisdom (see /manifesto list)", statement="What it should say now")
    @requires_feature("manifesto")
    async def edit(self, interaction: discord.Interaction, wisdom_index: app_commands.Range[int, 1], statement: WisdomText):
        guild_id = interaction.guild_id
        assert guild_id is not None
        statement = statement.strip()
        if not statement:
            raise UserFacingError("Editing it to nothing? Use /manifesto remove, coward.")
        old = await self.change(interaction, wisdom_index, self.services.manifesto.edit(guild_id, interaction.user.id, wisdom_index, statement))
        if old is not None:
            await interaction.response.send_message(f"Revised. History will forget you ever said \"{old}\".\n> {statement}", ephemeral=True)

    @manifesto_group.command(name="remove", description="Take back a piece of wisdom you added to your manifesto.")
    @app_commands.describe(wisdom_index="Which piece of your wisdom (see /manifesto list)")
    @requires_feature("manifesto")
    async def remove(self, interaction: discord.Interaction, wisdom_index: app_commands.Range[int, 1]):
        guild_id = interaction.guild_id
        assert guild_id is not None
        old = await self.change(interaction, wisdom_index, self.services.manifesto.remove(guild_id, interaction.user.id, wisdom_index))
        if old is not None:
            await interaction.response.send_message(
                f"Retracted. \"{old}\" was never said.\n-# Your later wisdom moves up a number", ephemeral=True
            )

    async def change(self, interaction: discord.Interaction, index: int, operation: Coroutine[Any, Any, str]) -> str | None:
        """Await an edit or remove, or tell the member why they can't and return None."""
        try:
            return await operation
        except NoManifesto:
            text = "You have no manifesto to edit. Write one first with /manifesto add."
        except NoSuchWisdom as e:
            text = f"There's no wisdom #{index}. Your manifesto stops at {e.most}. Short, but punchy."
        except WrittenInStone:
            text = f"Wisdom #{index} is from your original manifesto, and that's written in stone. You only get to change what you added."
        except NotYourWisdom:
            text = f"Someone else added wisdom #{index}. Hands off their thoughts."
        await interaction.response.send_message(text, ephemeral=True)
        return None

    async def _own_wisdom_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        own = await self.services.manifesto.own_wisdom(interaction.guild_id, interaction.user.id)
        return [app_commands.Choice(name=f"{w.index}. {w.text}"[:100], value=w.index) for w in own if current.lower() in f"{w.index}. {w.text}".lower()][:25]

    edit.autocomplete("wisdom_index")(_own_wisdom_autocomplete)
    remove.autocomplete("wisdom_index")(_own_wisdom_autocomplete)

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
