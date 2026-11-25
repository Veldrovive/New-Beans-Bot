"""Manifestos: each one's authors and numbered lines."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from beans_bot.services.manifesto import NoManifesto, NoSuchWisdom, NotYourWisdom, WrittenInStone
from beans_bot.web.actions import feature, logger
from beans_bot.web.deps import Actor, Ctx, Guild
from beans_bot.web.errors import bad_request, conflict, not_found
from beans_bot.web.schemas import Done, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/manifestos", tags=["manifestos"])


class LineOut(BaseModel):
    index: int  # From 1, as /manifesto uses
    text: str
    from_file: bool  # A wisdom.json line: written in stone
    added_by: Snowflake | None
    added_at: datetime.datetime | None
    edited_at: datetime.datetime | None


class ManifestoOut(BaseModel):
    author_ids: list[Snowflake]
    lines: list[LineOut]


@router.get("")
async def manifestos(guild_id: int, ctx: Ctx, _: Guild) -> list[ManifestoOut]:
    try:
        found = await ctx.services.manifesto.manifestos(guild_id)
    except NoManifesto:
        return []
    return [
        ManifestoOut(
            author_ids=m.author_ids,
            lines=[
                LineOut(index=i, text=line.text, from_file=line.added_id is None, added_by=line.added_by, added_at=line.added_at, edited_at=line.edited_at)
                for i, line in enumerate(m.lines, start=1)
            ],
        )
        for m in found
    ]


# --- Actions ---------------------------------------------------------------------------


class WisdomText(BaseModel):
    text: str = Field(min_length=1, max_length=1000)  # The same limit as /manifesto add


def _clean(body: WisdomText) -> str:
    text = body.text.strip()
    if not text:
        raise bad_request("Blank wisdom is still blank. Write something.")
    return text


async def _edit_or_remove(call) -> str:
    try:
        return await call
    except NoManifesto:
        raise not_found("They don't have a manifesto.")
    except NoSuchWisdom as e:
        raise conflict(f"Their manifesto only has {e.most} pieces of wisdom.")
    except WrittenInStone:
        raise conflict("That line came from their manifesto file. It's written in stone; change wisdom.json instead.")
    except NotYourWisdom:
        raise conflict("Someone else sharing that manifesto added that line, so it's theirs to change.")


@router.post("/{user_id}/wisdom")
async def add_wisdom(guild_id: int, user_id: int, body: WisdomText, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "manifesto")
    text = _clean(body)
    logger.info(f"Web {actor}: add wisdom to the manifesto of {user_id} in guild {guild_id}")
    wisdom = await ctx.services.manifesto.add(guild_id, user_id, text)
    return Done(message=f"Added as wisdom #{wisdom.index}. Nobody was told.")


@router.patch("/{user_id}/wisdom/{index}")
async def edit_wisdom(guild_id: int, user_id: int, index: int, body: WisdomText, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "manifesto")
    text = _clean(body)
    logger.info(f"Web {actor}: edit wisdom {index} of {user_id} in guild {guild_id}")
    await _edit_or_remove(ctx.services.manifesto.edit(guild_id, user_id, index, text))
    return Done(message=f"Wisdom #{index} rewritten.")


@router.delete("/{user_id}/wisdom/{index}")
async def remove_wisdom(guild_id: int, user_id: int, index: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "manifesto")
    logger.info(f"Web {actor}: remove wisdom {index} of {user_id} in guild {guild_id}")
    await _edit_or_remove(ctx.services.manifesto.remove(guild_id, user_id, index))
    return Done(message=f"Wisdom #{index} taken back. Later lines moved up a number.")
