"""
The config, shown and never edited. It lives in the YAML files in git.

Secrets come out masked (pydantic dumps SecretStr as stars). Numbers too big for JavaScript, which in
practice are Discord IDs, come out as strings.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from beans_bot.config.schema import GuildConfig
from beans_bot.web.deps import Ctx, Guild
from beans_bot.web.errors import not_found

router = APIRouter(prefix="/api", tags=["config"])

MAX_SAFE_INTEGER = 2**53 - 1


def json_safe(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


@router.get("/guilds/{guild_id}/config/{section}")
async def guild_section(section: str, guild: Guild) -> Any:
    """One section of a guild's config, or null when the feature is off."""
    if section not in GuildConfig.model_fields:
        raise not_found(f"There's no config section called {section}.")
    value = getattr(guild, section)
    return json_safe(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)


@router.get("/config/global")
async def global_config(ctx: Ctx) -> Any:
    """Everything outside `guilds`, with secrets masked."""
    return json_safe(ctx.config.model_dump(mode="json", exclude={"guilds"}))
