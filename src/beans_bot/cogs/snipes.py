"""
Snipes: photos of members posted in the snipe channel, verified by the Council of Teds.

- Tag exactly one member with the photo and the snipe starts. Tag nobody, or several, and the
  bot replies with a picker that only the poster can use, then deletes it once they answer.
- The bot marks the photo pending and pings the council. When more than half the council has
  reacted with their crests, the ping is deleted and replaced with who verified it.
- Fake snipes get jail reactions, which the jail cog already handles.
- Only a crest's owner may react with it. Anyone else's crest reaction is removed, anywhere in the server.
- Pending snipes are recounted from the photo's reactions every few minutes, in case the bot missed some.
"""

from __future__ import annotations

import datetime

import discord
from discord.ext import tasks

from beans_bot.cogs.base import BeansCog
from beans_bot.config.schema import SnipesConfig
from beans_bot.db.types import utcnow
from beans_bot.services import emoji, scripts
from beans_bot.services.snipes import AlreadySniped


def has_image(message: discord.Message) -> bool:
    return any((a.content_type or "").startswith("image/") for a in message.attachments)


def join_names(names: list[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


class TargetSelect(discord.ui.Select):
    """Picks one of the members the poster tagged."""

    def __init__(self, picker: TargetPicker, candidates: list[discord.Member]):
        options = [discord.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in candidates[:25]]
        super().__init__(placeholder="Who's in the photo?", options=options)
        self.picker = picker

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.pick(interaction, int(self.values[0]))


class AnyoneSelect(discord.ui.UserSelect):
    """Picks any member, for photos where the poster tagged nobody."""

    def __init__(self, picker: TargetPicker):
        super().__init__(placeholder="Who's in the photo?")
        self.picker = picker

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.pick(interaction, self.values[0].id)


class TargetPicker(discord.ui.View):
    """Asks the poster who's in their photo. Only they can answer. Doesn't survive a bot restart."""

    def __init__(self, cog: SnipesCog, photo: discord.Message, candidates: list[discord.Member], timeout: datetime.timedelta):
        super().__init__(timeout=timeout.total_seconds())
        self.cog = cog
        self.photo = photo
        self.prompt: discord.Message | None = None
        self.add_item(TargetSelect(self, candidates) if candidates else AnyoneSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.photo.author.id:
            return True
        await interaction.response.send_message("Not your photo, not your snipe. Go lurk in a bush of your own.", ephemeral=True)
        return False

    async def pick(self, interaction: discord.Interaction, target_id: int) -> None:
        assert interaction.guild is not None
        target = interaction.guild.get_member(target_id)
        if target_id == self.photo.author.id:
            await interaction.response.send_message("Sniping yourself is called a selfie. Pick someone else.", ephemeral=True)
            return
        if target is None or target.bot:
            await interaction.response.send_message("That's not a member you can snipe. Pick a human who's still here.", ephemeral=True)
            return

        self.stop()
        await interaction.response.defer(ephemeral=True)
        if await self.cog.begin(self.photo, target_id):
            await interaction.followup.send(
                f"Snipe of {target.mention} started. The council has been alerted and will judge you shortly.", ephemeral=True
            )
        else:
            await interaction.followup.send("This photo is already a snipe. One victim per photo, you vulture.", ephemeral=True)
        await self.delete_prompt()

    async def on_timeout(self) -> None:
        await self.delete_prompt()

    async def delete_prompt(self) -> None:
        if self.prompt is None:
            return
        try:
            await self.prompt.delete()
        except discord.HTTPException:
            self.cog.logger.info(f"Could not delete snipe picker {self.prompt.id}")


class SnipesCog(BeansCog):
    async def cog_load(self):
        self.recount_pending.start()

    async def cog_unload(self):
        self.recount_pending.cancel()

    def snipes_config(self, guild_id: int | None) -> SnipesConfig | None:
        config = self.guild_config(guild_id)
        return config.snipes if config else None

    async def react(self, message: discord.Message, spec: str) -> None:
        try:
            await emoji.react(message, spec)
        except discord.HTTPException:
            self.logger.info(f"Failed to react to snipe {message.id}")

    async def unreact(self, message: discord.Message, spec: str) -> None:
        """Remove the bot's own reaction, leaving anyone else's."""
        assert message.guild is not None
        for e in emoji.resolve(message.guild, spec):
            try:
                await message.remove_reaction(e, message.guild.me)
            except discord.HTTPException:
                self.logger.info(f"Failed to remove reaction from snipe {message.id}")

    # --- Starting snipes ----------------------------------------------------------------

    @BeansCog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        config = self.snipes_config(message.guild.id)
        if config is None or message.channel.id != config.channel_id or not has_image(message):
            return

        # raw_mentions only has people tagged in the text, not the author of a message being replied to.
        candidates: list[discord.Member] = []
        for user_id in dict.fromkeys(message.raw_mentions):
            member = message.guild.get_member(user_id)
            if member is not None and not member.bot and member.id != message.author.id:
                candidates.append(member)
        if len(candidates) == 1:
            await self.begin(message, candidates[0].id)
            return

        picker = TargetPicker(self, message, candidates, config.picker_timeout)
        text = scripts.pick(config.picker_scripts, sniper=message.author.mention)
        text = text or f"Lovely photo, {message.author.mention}. Who's the victim?"
        if candidates:
            text += f"\n-# You tagged {len(candidates)} people. A snipe only gets one victim."
        expires = discord.utils.format_dt(utcnow() + config.picker_timeout, "R")
        text += f"\n-# Only {message.author.mention} can answer. This offer expires {expires}."
        try:
            picker.prompt = await message.reply(text, view=picker, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.logger.warning(f"Could not send snipe picker for {message.id} in guild {message.guild.id}")
            picker.stop()

    async def begin(self, photo: discord.Message, target_id: int) -> bool:
        """Start the snipe, mark the photo pending and ping the council. False if it was already a snipe."""
        assert photo.guild is not None
        config = self.snipes_config(photo.guild.id)
        assert config is not None
        try:
            await self.services.snipes.start(photo.guild.id, photo.channel.id, photo.id, photo.author.id, target_id)
        except AlreadySniped:
            return False
        await self.react(photo, config.pending_emoji)
        await self.alert_council(photo, target_id, config)
        return True

    async def alert_council(self, photo: discord.Message, target_id: int, config: SnipesConfig) -> None:
        guild = photo.guild
        assert guild is not None
        council = self.services.council.members(guild.id)
        needed = self.services.council.majority(guild.id)
        names = {
            "council": " ".join(f"<@{m.user_id}>" for m in council),
            "sniper": photo.author.mention,
            "target": f"<@{target_id}>",
        }
        text = scripts.pick(config.alert_scripts, **names)
        text = text or f"{names['council']}: {names['sniper']} claims to have sniped {names['target']}. Judge them."
        crests = " ".join(str(e) for m in council for e in emoji.resolve(guild, m.crest_emoji))
        guild_config = self.guild_config(guild.id)
        jail = guild_config.jail if guild_config else None
        fake = f", or {' '.join(str(e) for e in emoji.resolve(guild, jail.jail_emoji))} if it's a fake" if jail else ""
        text += f"\n-# React to the photo with your crest ({crests}) to verify it{fake}. {needed} of {len(council)} crests needed."
        mentions = discord.AllowedMentions(users=[discord.Object(m.user_id) for m in council], replied_user=False)
        try:
            alert = await photo.reply(text, allowed_mentions=mentions)
        except discord.HTTPException:
            self.logger.warning(f"Could not alert the council about snipe {photo.id} in guild {guild.id}")
            return
        await self.services.snipes.set_alert_message(photo.id, alert.id)

    # --- Votes --------------------------------------------------------------------------

    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or self.is_own_user(payload.user_id):
            return
        owner = self.services.council.crest_owner(payload.guild_id, payload.emoji)
        if owner is None:
            return
        if owner.user_id != payload.user_id:
            await self.remove_stolen_crest(payload)
            return
        config = self.snipes_config(payload.guild_id)
        if config is None or payload.channel_id != config.channel_id:
            return
        if await self.services.snipes.vote(payload.message_id, payload.user_id, self.services.council.majority(payload.guild_id)):
            await self.announce_verified(payload.message_id)

    @BeansCog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        owner = self.services.council.crest_owner(payload.guild_id, payload.emoji)
        config = self.snipes_config(payload.guild_id)
        if owner is None or owner.user_id != payload.user_id or config is None or payload.channel_id != config.channel_id:
            return
        await self.services.snipes.unvote(payload.message_id, payload.user_id)

    async def remove_stolen_crest(self, payload: discord.RawReactionActionEvent) -> None:
        message = await self.fetch_message(payload.channel_id, payload.message_id)
        if message is None:
            return
        try:
            await message.remove_reaction(payload.emoji, discord.Object(payload.user_id))
            self.logger.info(f"Removed a stolen crest {payload.emoji} from {payload.user_id} in guild {payload.guild_id}")
        except discord.HTTPException:
            self.logger.warning(f"Could not remove stolen crest {payload.emoji} from {payload.user_id} in guild {payload.guild_id}")

    async def announce_verified(self, message_id: int) -> None:
        snipe = await self.services.snipes.get(message_id)
        if snipe is None:
            return
        config = self.snipes_config(snipe.guild_id)
        guild = self.bot.get_guild(snipe.guild_id)
        channel = self.bot.get_channel(snipe.channel_id)
        if config is None or guild is None or not isinstance(channel, discord.abc.Messageable):
            return

        if snipe.alert_message_id is not None and (alert := await self.fetch_message(snipe.channel_id, snipe.alert_message_id)):
            try:
                await alert.delete()
            except discord.HTTPException:
                self.logger.info(f"Could not delete council alert {alert.id}")

        verifiers = []
        for user_id in await self.services.snipes.voter_ids(message_id):
            member = self.services.council.member(guild.id, user_id)
            verifiers.append(member.council_name if member else f"<@{user_id}>")
        names = {"sniper": f"<@{snipe.sniper_user_id}>", "target": f"<@{snipe.target_user_id}>", "verifiers": join_names(verifiers)}
        text = scripts.pick(config.verified_scripts, **names)
        text = text or f"The council has spoken: {names['sniper']} really did snipe {names['target']}. Verified by {names['verifiers']}."
        mentions = discord.AllowedMentions(users=[discord.Object(snipe.sniper_user_id)], replied_user=False)

        photo = await self.fetch_message(snipe.channel_id, snipe.message_id)
        try:
            if photo is None:
                await channel.send(text, allowed_mentions=mentions)
            else:
                await self.unreact(photo, config.pending_emoji)
                await self.react(photo, config.verified_emoji)
                await photo.reply(text, allowed_mentions=mentions)
        except discord.HTTPException:
            self.logger.warning(f"Could not announce verified snipe {message_id} in guild {guild.id}")

    # --- Timers -------------------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def recount_pending(self):
        """Catch crest reactions added or removed while the bot was down."""
        for snipe in await self.services.snipes.pending():
            if self.snipes_config(snipe.guild_id) is None:
                continue
            photo = await self.fetch_message(snipe.channel_id, snipe.message_id)
            if photo is None:
                continue
            voters: set[int] = set()
            try:
                for reaction in photo.reactions:
                    owner = self.services.council.crest_owner(snipe.guild_id, reaction.emoji)
                    if owner is None:
                        continue
                    async for user in reaction.users():
                        if user.id == owner.user_id:
                            voters.add(owner.user_id)
            except discord.HTTPException:
                self.logger.info(f"Could not recount crests on snipe {snipe.message_id}")
                continue
            needed = self.services.council.majority(snipe.guild_id)
            if await self.services.snipes.sync_votes(snipe.message_id, voters, needed):
                await self.announce_verified(snipe.message_id)

    @recount_pending.before_loop
    async def before_recount_pending(self):
        await self.bot.wait_until_ready()
