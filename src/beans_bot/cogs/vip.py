"""
VIP: two BeanCoin-bought roles that can see the VIP channel (channel permissions are set up in Discord).

- The bourgeoisie day pass: a flat price for a day of access. Buying again extends it.
- The 1%: permanent access, but only one member holds it. Claim it by paying at
  least the current price. The next price is what you paid plus the increment.
- The henchman: the 1% can hand one member a free henchman role that also sees
  the VIP channel. The henchman is fired when the 1% changes hands.

The roles follow the records: every minute, expired passes are ended and anyone
who has a VIP role they didn't pay for loses it (and anyone missing one gets it).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import tasks

from beans_bot.cogs.base import QUIET_HELP, BeansCog, in_bot_channel, requires_feature
from beans_bot.config.schema import VipConfig
from beans_bot.services import scripts
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.jail import get_member
from beans_bot.services.vip import AlreadyHenchman, AlreadyOnePercent, BidTooLow, InvalidHenchman, NotOnePercent


class VipCog(BeansCog):
    vip_group = app_commands.Group(name="vip", description="Buy your way into the VIP channel.", guild_only=True)

    async def cog_load(self):
        self.check_vip_roles.start()

    async def cog_unload(self):
        self.check_vip_roles.cancel()

    def vip_config(self, guild_id: int | None) -> VipConfig | None:
        config = self.guild_config(guild_id)
        return config.vip if config else None

    def announce_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        config = self.guild_config(guild.id)
        if config is None or config.bot_channel_id is None:
            return None
        channel = guild.get_channel(config.bot_channel_id)
        return channel if isinstance(channel, discord.abc.Messageable) else None

    def manageable_role(self, guild: discord.Guild, role_id: int) -> discord.Role | None:
        """The role, if it exists and the bot is allowed to hand it out."""
        role = guild.get_role(role_id)
        if role is None:
            self.logger.error(f"VIP role {role_id} not found in guild {guild.id}")
            return None
        if not role.is_assignable():
            self.logger.error(f"Can't assign VIP role {role_id} in guild {guild.id} (missing Manage Roles or role too high)")
            return None
        return role

    async def set_role(self, member: discord.Member, role: discord.Role, has_role: bool, reason: str) -> None:
        try:
            if has_role and role not in member.roles:
                await member.add_roles(role, reason=reason)
            elif not has_role and role in member.roles:
                await member.remove_roles(role, reason=reason)
        except discord.HTTPException:
            self.logger.warning(f"Could not update VIP role {role.id} for {member.id} in guild {member.guild.id}")

    # --- Timers -------------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def check_vip_roles(self):
        await self.expire_passes()
        await self.sync_roles()

    @check_vip_roles.before_loop
    async def before_check_vip_roles(self):
        await self.bot.wait_until_ready()

    async def expire_passes(self) -> None:
        for day_pass in await self.services.vip.due_to_expire():
            config = self.vip_config(day_pass.guild_id)
            guild = self.bot.get_guild(day_pass.guild_id)
            if config is None or guild is None or day_pass.id is None:
                continue
            if not await self.services.vip.end_pass(day_pass.id):
                continue
            member = await get_member(guild, day_pass.user_id)
            role = guild.get_role(config.day_pass.role_id)
            if member is None or role is None:
                continue
            await self.set_role(member, role, False, "VIP day pass expired.")
            channel = self.announce_channel(guild)
            if channel is not None and (text := scripts.pick(config.day_pass.expire_scripts, member=member.mention)):
                await channel.send(text)

    async def sync_roles(self) -> None:
        """Give the VIP roles to exactly the members whose records say they have them."""
        for guild in self.bot.guilds:
            config = self.vip_config(guild.id)
            if config is None:
                continue
            day_role = self.manageable_role(guild, config.day_pass.role_id)
            if day_role is not None:
                pass_holders = {p.user_id for p in await self.services.vip.active_passes(guild.id)}
                await self.sync_role(guild, day_role, pass_holders, "VIP day pass")
            one_percent_role = self.manageable_role(guild, config.one_percent.role_id)
            if one_percent_role is not None:
                title = await self.services.vip.one_percent(guild.id)
                await self.sync_role(guild, one_percent_role, {title.holder_user_id} if title else set(), "The 1%")
            if config.henchman is not None and (henchman_role := self.manageable_role(guild, config.henchman.role_id)):
                title = await self.services.vip.one_percent(guild.id)
                henchmen = {title.henchman_user_id} if title and title.henchman_user_id else set()
                await self.sync_role(guild, henchman_role, henchmen, "henchman")

    async def sync_role(self, guild: discord.Guild, role: discord.Role, user_ids: set[int], name: str) -> None:
        for member in role.members:
            if member.id not in user_ids:
                self.logger.info(f"{member.id} has the {name} role without paying for it in guild {guild.id}")
                await self.set_role(member, role, False, f"Not in the {name} records.")
        for user_id in user_ids - {m.id for m in role.members}:
            member = guild.get_member(user_id)  # Members who left the guild are skipped
            if member is not None:
                await self.set_role(member, role, True, f"In the {name} records.")

    # --- Commands -----------------------------------------------------------------------

    @vip_group.command(name="daypass", description="Join the bourgeoisie: buy a day in the VIP channel.")
    @requires_feature("vip")
    @in_bot_channel()
    async def daypass(self, interaction: discord.Interaction):
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        config = self.vip_config(interaction.guild_id)
        assert config is not None
        currency = self.services.currency
        role = self.manageable_role(interaction.guild, config.day_pass.role_id)
        if role is None:
            await interaction.response.send_message("The velvet rope is broken (I can't hand out that role).", ephemeral=True)
            return
        title = await self.services.vip.one_percent(interaction.guild.id)
        if title is not None and title.holder_user_id == interaction.user.id:
            await interaction.response.send_message("You're the 1%. You own the place, you don't need a day pass.", ephemeral=True)
            return

        try:
            day_pass = await self.services.vip.buy_day_pass(interaction.guild.id, interaction.user.id, config.day_pass)
        except InsufficientFunds as e:
            await interaction.response.send_message(
                f"A day pass costs {currency.format(config.day_pass.cost)} and you have {currency.format(e.balance)}. "
                f"Back to the proletariat with you.",
                ephemeral=True,
            )
            return

        await self.set_role(interaction.user, role, True, "Bought a VIP day pass.")
        text = scripts.pick(config.day_pass.purchase_scripts, buyer=interaction.user.mention)
        text = text or f"{interaction.user.mention} bought a VIP day pass."
        await interaction.response.send_message(
            f"{text}\n-# Paid {currency.format(config.day_pass.cost)}. "
            f"Pass expires {discord.utils.format_dt(day_pass.ends_at, 'R')}."
        )

    @vip_group.command(name="onepercent", description="Claim permanent VIP. Only one member can hold it at a time.")
    @app_commands.describe(amount="How much to pay, at least the current price (see /vip status)")
    @requires_feature("vip")
    @in_bot_channel()
    async def onepercent(self, interaction: discord.Interaction, amount: str):
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        config = self.vip_config(interaction.guild_id)
        assert config is not None
        currency = self.services.currency
        coins = currency.parse(amount)
        role = self.manageable_role(interaction.guild, config.one_percent.role_id)
        if role is None:
            await interaction.response.send_message("The penthouse is locked (I can't hand out that role).", ephemeral=True)
            return

        try:
            claim = await self.services.vip.claim_one_percent(interaction.guild.id, interaction.user.id, coins, config.one_percent)
        except AlreadyOnePercent:
            await interaction.response.send_message("You're already the 1%. You can't usurp yourself.", ephemeral=True)
            return
        except BidTooLow as e:
            await interaction.response.send_message(
                f"Peasant money. The 1% costs at least {currency.format(e.price)}.", ephemeral=True
            )
            return
        except InsufficientFunds as e:
            await interaction.response.send_message(
                f"You offered {currency.format(coins)} but only have {currency.format(e.balance)}. Nice try.",
                ephemeral=True,
            )
            return

        await self.set_role(interaction.user, role, True, "Claimed the 1%.")
        names = {"buyer": interaction.user.mention, "paid": currency.format(claim.paid), "new_price": currency.format(claim.new_price)}
        if claim.previous_holder_id is None:
            text = scripts.pick(config.one_percent.first_claim_scripts, **names)
            text = text or f"{interaction.user.mention} is the first member of the 1%. The new price is {names['new_price']}."
        else:
            previous = await get_member(interaction.guild, claim.previous_holder_id)
            if previous is not None:
                await self.set_role(previous, role, False, "Usurped from the 1%.")
            names["previous_holder"] = f"<@{claim.previous_holder_id}>"
            text = scripts.pick(config.one_percent.usurp_scripts, **names)
            text = text or f"{interaction.user.mention} has usurped {names['previous_holder']}. The new price is {names['new_price']}."
            if claim.previous_henchman_id is not None and config.henchman is not None:
                await self.fire_henchman_role(interaction.guild, config.henchman.role_id, claim.previous_henchman_id, "The 1% changed hands.")
                # A henchman who bought the title themselves got a promotion, not the sack
                if claim.previous_henchman_id != interaction.user.id:
                    henchman = f"<@{claim.previous_henchman_id}>"
                    fired = scripts.pick(config.henchman.unemployed_scripts, henchman=henchman, **names)
                    text += f"\n{fired or f'{henchman} is out of a job.'}"
        await interaction.response.send_message(text)

    async def fire_henchman_role(self, guild: discord.Guild, role_id: int, user_id: int, reason: str) -> None:
        """Take the henchman role off right away. sync_roles would catch it within a minute anyway."""
        member = await get_member(guild, user_id)
        role = guild.get_role(role_id)
        if member is not None and role is not None:
            await self.set_role(member, role, False, reason)

    @vip_group.command(name="henchman", description="1% only: appoint a henchman to share the VIP channel, or leave it empty to fire them.")
    @app_commands.describe(member="Your new henchman. Leave it out to fire the current one.")
    @requires_feature("vip")
    @in_bot_channel()
    async def henchman(self, interaction: discord.Interaction, member: discord.Member | None = None):
        assert interaction.guild is not None
        config = self.vip_config(interaction.guild_id)
        assert config is not None
        if config.henchman is None:
            await interaction.response.send_message("Henchmen aren't hiring in this server. Do your own dirty work.", ephemeral=True)
            return
        role = self.manageable_role(interaction.guild, config.henchman.role_id)
        if role is None:
            await interaction.response.send_message("The henchman uniforms are locked up (I can't hand out that role).", ephemeral=True)
            return
        vip = self.services.vip
        boss = interaction.user.mention

        if member is None:
            try:
                fired_id = await vip.dismiss_henchman(interaction.guild.id, interaction.user.id)
            except NotOnePercent:
                await interaction.response.send_message("Only the 1% can afford a henchman. You can't even afford a sidekick.", ephemeral=True)
                return
            if fired_id is None:
                await interaction.response.send_message("You don't have a henchman to fire. Lonely at the top, isn't it?", ephemeral=True)
                return
            await self.fire_henchman_role(interaction.guild, role.id, fired_id, "Fired by the 1%.")
            henchman = f"<@{fired_id}>"
            text = scripts.pick(config.henchman.dismiss_scripts, boss=boss, henchman=henchman)
            await interaction.response.send_message(text or f"{boss} has fired {henchman}. Hand in your badge.")
            return

        if member.bot:
            await interaction.response.send_message("Robots don't do henchwork. Pick a member with a pulse.", ephemeral=True)
            return
        try:
            previous_id = await vip.appoint_henchman(interaction.guild.id, interaction.user.id, member.id)
        except NotOnePercent:
            await interaction.response.send_message("Only the 1% can afford a henchman. You can't even afford a sidekick.", ephemeral=True)
            return
        except InvalidHenchman:
            await interaction.response.send_message("You can't be your own henchman. Delegate, you're rich now.", ephemeral=True)
            return
        except AlreadyHenchman:
            await interaction.response.send_message(f"{member.mention} is already your henchman. They're working as hard as they can.", ephemeral=True)
            return

        if previous_id is not None:
            await self.fire_henchman_role(interaction.guild, role.id, previous_id, "Replaced by the 1%.")
        await self.set_role(member, role, True, "Appointed henchman by the 1%.")
        text = scripts.pick(config.henchman.appoint_scripts, boss=boss, henchman=member.mention)
        text = text or f"{boss} has hired {member.mention} as their henchman."
        if previous_id is not None:
            text += f"\n-# <@{previous_id}> has been let go."
        await interaction.response.send_message(text)

    @vip_group.command(name="status", description="Who holds the 1%, what it costs, and how long your day pass lasts.")
    @app_commands.describe(quiet=QUIET_HELP)
    @requires_feature("vip")
    @in_bot_channel()
    async def status(self, interaction: discord.Interaction, quiet: bool = False):
        assert interaction.guild_id is not None
        config = self.vip_config(interaction.guild_id)
        assert config is not None
        currency = self.services.currency
        title = await self.services.vip.one_percent(interaction.guild_id)
        lines = ["**The 1%**"]
        if title is None:
            lines.append(f"- Unclaimed! Yours for {currency.format(config.one_percent.starting_price)}.")
        else:
            lines += [
                f"- Holder: <@{title.holder_user_id}> (paid {currency.format(title.paid)}, "
                f"since {discord.utils.format_dt(title.claimed_at, 'D')})",
                f"- Price to usurp: {currency.format(title.price)}",
            ]
            if config.henchman is not None:
                lines.append(f"- Henchman: {f'<@{title.henchman_user_id}>' if title.henchman_user_id else 'none, the position is open'}")
        day_pass = await self.services.vip.active_pass(interaction.guild_id, interaction.user.id)
        lines.append("\n**Your day pass**")
        if day_pass is None:
            lines.append(f"- None. A day in the bourgeoisie costs {currency.format(config.day_pass.cost)}.")
        else:
            lines.append(f"- Expires {discord.utils.format_dt(day_pass.ends_at, 'R')}")
        await interaction.response.send_message("\n".join(lines), allowed_mentions=discord.AllowedMentions.none(), ephemeral=quiet)
