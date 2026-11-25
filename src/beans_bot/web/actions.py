"""Small helpers shared by the routers' action endpoints."""

from __future__ import annotations

import logging

from beans_bot.web.context import WebContext
from typing import Any

from beans_bot.config.schema import GuildConfig
from beans_bot.web.errors import bad_request, not_found, unavailable

# Every mutating request is logged at INFO here, with who asked, the guild and the target.
logger = logging.getLogger("beans_bot.web.actions")


def house_id(ctx: WebContext) -> int:
    """The bot's own account. Money endpoints can't run until the bot has logged in and set it."""
    house = ctx.services.coins.house_user_id
    if house is None:
        raise unavailable("The house account isn't known until the bot logs in to Discord. Try again once it's connected.")
    return house


def actor_user_id(ctx: WebContext) -> int:
    """The Discord user recorded on ledger rows for actions taken on the website."""
    user_id = ctx.config.web.actor_user_id
    if user_id is None:
        raise bad_request("web.actor_user_id isn't set in configs/config.yaml, so the ledger wouldn't know who did this. Set it to your Discord user ID.")
    return user_id


def feature(guild: GuildConfig, section: str) -> Any:
    """The guild's config section for a feature, or a 404 when the feature is off there."""
    value = getattr(guild, section)
    if value is None:
        raise not_found(f"{section} isn't set up for this server.")
    return value
