"""
The image classifier.

- Per guild: the bot's recent guesses, and what members said about them.
- Per dataset (global, since several guilds can share a model): the photos, their labels and train/val
  split, uploads, retraining and each retrain's scores. Photo files are served as they're stored, or as
  thumbnails made on first request.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from beans_bot.db.models import ClassifierImage, ClassifierImageSource, ClassifierSplit, ClassifierTrainingRun
from beans_bot.services.classifier import InvalidLabel, NotAnImage, RetrainFailed, RetrainInProgress
from beans_bot.web.actions import logger
from beans_bot.web.context import WebContext
from beans_bot.web.deps import Actor, Ctx, Guild, Paged
from beans_bot.web.errors import bad_request, conflict, not_found, unavailable
from beans_bot.web.images import make_thumbnail
from beans_bot.web.schemas import Page, Snowflake

guild_router = APIRouter(prefix="/api/guilds/{guild_id}/classifier", tags=["classifier"])
router = APIRouter(prefix="/api/classifier", tags=["classifier"])

MAX_UPLOAD = 50 * 1024 * 1024
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


class GuessOut(BaseModel):
    message_id: Snowflake
    channel_id: Snowflake
    classifier_name: str
    predicted_label: str | None
    reply_message_id: Snowflake | None
    processed_at: datetime.datetime
    corrected_label: str | None  # The newest correction or confirmation
    corrected_by: Snowflake | None
    corrected_at: datetime.datetime | None


@guild_router.get("/guesses")
async def guesses(guild_id: int, ctx: Ctx, _: Guild, page: Paged) -> Page[GuessOut]:
    rows, total = await ctx.services.reports.classifier_guesses(guild_id, page.limit, page.offset)
    return Page(
        items=[
            GuessOut(
                message_id=g.guess.message_id,
                channel_id=g.guess.channel_id,
                classifier_name=g.guess.classifier_name,
                predicted_label=g.guess.predicted_label,
                reply_message_id=g.guess.reply_message_id,
                processed_at=g.guess.processed_at,
                corrected_label=g.correction.label if g.correction else None,
                corrected_by=g.correction.corrected_by if g.correction else None,
                corrected_at=g.correction.corrected_at if g.correction else None,
            )
            for g in rows
        ],
        total=total,
    )


# --- Datasets ----------------------------------------------------------------------------


class GuildModel(BaseModel):
    guild_id: Snowflake
    guild_name: str
    model_name: str
    corrections: bool


class LabelCount(BaseModel):
    label: str
    train: int
    val: int
    removed: int


class ModelInfo(BaseModel):
    loaded: bool
    source: str | None  # "retrained" or "shipped"
    labels: list[str]


class RunSummary(BaseModel):
    id: int
    dataset: str
    status: str
    triggered_by: str
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    error: str | None
    train_count: int | None
    val_count: int | None
    accuracy: float | None
    log_loss: float | None


class RunDetail(RunSummary):
    results: dict[str, Any] | None


class DatasetOut(BaseModel):
    name: str
    labels: list[LabelCount]
    model: ModelInfo | None  # None while the classifier cog isn't loaded
    latest_run: RunSummary | None
    changed_since_latest_run: int | None
    running: bool
    guilds: list[GuildModel]


class ImageOut(BaseModel):
    id: int
    dataset: str
    content_hash: str
    original_name: str | None
    label: str
    split: str
    source: str
    guild_id: Snowflake | None
    channel_id: Snowflake | None  # Where the bot saw it, for corrections. Links to the Discord message.
    message_id: Snowflake | None
    added_by: Snowflake | None
    added_at: datetime.datetime
    updated_at: datetime.datetime
    removed_at: datetime.datetime | None


class Uploaded(BaseModel):
    image: ImageOut
    duplicate: bool


class ImageChange(BaseModel):
    label: str | None = None
    split: Literal["train", "val"] | None = None
    removed: bool | None = None


class BulkChange(ImageChange):
    ids: list[int]


class Changed(BaseModel):
    changed: int


class Started(BaseModel):
    run_id: int
    message: str


class Progress(BaseModel):
    running: bool
    done: int
    total: int


def _summary(run: ClassifierTrainingRun) -> RunSummary:
    return RunSummary(**run.model_dump(exclude={"results"}))


def configured_datasets(ctx: WebContext) -> dict[str, list[GuildModel]]:
    """Every dataset a guild's config names, with the guilds and models that use it."""
    found: dict[str, list[GuildModel]] = {}
    for guild_id, guild in ctx.config.guilds.items():
        for name, model in (guild.classifier.models.items() if guild.classifier else []):
            found.setdefault(model.model_path, []).append(
                GuildModel(guild_id=guild_id, guild_name=guild.name, model_name=name, corrections=model.corrections is not None)
            )
    return found


async def _dataset(ctx: WebContext, dataset: str) -> str:
    if dataset not in configured_datasets(ctx) and dataset not in await ctx.services.classifier.datasets():
        raise not_found(f"There's no classifier dataset called {dataset}.")
    return dataset


async def _images_out(ctx: WebContext, rows: list[ClassifierImage]) -> list[ImageOut]:
    keys: list[tuple[int, int]] = [(r.guild_id, r.message_id) for r in rows if r.guild_id is not None and r.message_id is not None]
    channels = await ctx.services.classifier.message_channels(keys)
    out = []
    for r in rows:
        channel_id = channels.get((r.guild_id, r.message_id)) if r.guild_id is not None and r.message_id is not None else None
        out.append(ImageOut(**r.model_dump(exclude={"id"}), id=r.id or 0, channel_id=channel_id))
    return out


@router.get("/datasets")
async def datasets(ctx: Ctx) -> list[DatasetOut]:
    service = ctx.services.classifier
    configured = configured_datasets(ctx)
    running = await service.running_run()
    out = []
    for name in sorted(set(configured) | set(await service.datasets())):
        latest = await service.latest_run(name)
        info = service.model_info(name) if service.model_info is not None else None
        out.append(
            DatasetOut(
                name=name,
                labels=[LabelCount(label=label, **counts) for label, counts in (await service.label_counts(name)).items()],
                model=ModelInfo(**info) if info is not None else None,
                latest_run=_summary(latest) if latest else None,
                changed_since_latest_run=await service.changed_since(name, latest.finished_at or latest.started_at) if latest else None,
                running=running is not None and running.dataset == name,
                guilds=configured.get(name, []),
            )
        )
    return out


@router.get("/datasets/{dataset}/images")
async def images(
    dataset: str,
    ctx: Ctx,
    label: str | None = None,
    split: Literal["train", "val"] | None = None,
    source: Literal["seed", "correction", "upload"] | None = None,
    removed: bool | None = False,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[ImageOut]:
    await _dataset(ctx, dataset)
    rows, total = await ctx.services.classifier.images(dataset, label=label, split=split, source=source, removed=removed, limit=limit, offset=offset)
    return Page(items=await _images_out(ctx, rows), total=total)


@router.get("/images/{image_id}/file", response_class=FileResponse)
async def image_file(image_id: int, ctx: Ctx, size: Literal["thumb", "large"] = "thumb"):
    service = ctx.services.classifier
    image = await service.image(image_id)
    if image is None:
        raise not_found(f"There's no photo {image_id}.")
    path = service.image_path(image)
    if not path.exists():
        raise not_found("That photo's file is missing.")
    if size == "large":
        return FileResponse(path, media_type="image/jpeg", headers=IMMUTABLE)
    thumb = await asyncio.to_thread(make_thumbnail, path, service.thumb_path(image))
    return FileResponse(thumb, media_type="image/webp", headers=IMMUTABLE)


@router.post("/datasets/{dataset}/images")
async def upload(
    dataset: str,
    ctx: Ctx,
    actor: Actor,
    file: UploadFile = File(),
    label: str = Form(),
    split: Literal["auto", "train", "val"] = Form("auto"),
) -> Uploaded:
    """One photo per request: Cloudflare caps a request at 100 MB."""
    await _dataset(ctx, dataset)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise bad_request("That photo is over 50 MB. Even a fat cat isn't that big.")
    try:
        image, added = await ctx.services.classifier.add_image(
            dataset,
            data,
            label=label,
            original_name=file.filename,
            source=ClassifierImageSource.UPLOAD,
            split=None if split == "auto" else ClassifierSplit(split),
        )
    except NotAnImage:
        raise bad_request(f"{file.filename or 'That file'} isn't a photo I can open.")
    except InvalidLabel:
        raise bad_request("Labels are up to 32 lowercase letters, numbers, dashes and underscores, starting with a letter or number.")
    logger.info(f"Web {actor}: upload {file.filename!r} to {dataset} as {image.label} (photo {image.id}, {'new' if added else 'duplicate'})")
    (out,) = await _images_out(ctx, [image])
    return Uploaded(image=out, duplicate=not added)


async def _change(ctx: WebContext, ids: list[int], change: ImageChange) -> int:
    try:
        return await ctx.services.classifier.update_images(ids, label=change.label, split=change.split, removed=change.removed)
    except InvalidLabel:
        raise bad_request("Labels are up to 32 lowercase letters, numbers, dashes and underscores, starting with a letter or number.")


@router.patch("/images/{image_id}")
async def change_image(image_id: int, body: ImageChange, ctx: Ctx, actor: Actor) -> ImageOut:
    if await ctx.services.classifier.image(image_id) is None:
        raise not_found(f"There's no photo {image_id}.")
    logger.info(f"Web {actor}: change classifier photo {image_id}: {body.model_dump(exclude_none=True)}")
    await _change(ctx, [image_id], body)
    image = await ctx.services.classifier.image(image_id)
    assert image is not None
    (out,) = await _images_out(ctx, [image])
    return out


@router.post("/images/bulk")
async def change_images(body: BulkChange, ctx: Ctx, actor: Actor) -> Changed:
    logger.info(f"Web {actor}: change {len(body.ids)} classifier photos: {body.model_dump(exclude_none=True, exclude={'ids'})}")
    return Changed(changed=await _change(ctx, body.ids, body))


# --- Retraining ---------------------------------------------------------------------------


@router.post("/datasets/{dataset}/retrain", status_code=202)
async def retrain(dataset: str, request: Request, ctx: Ctx, actor: Actor) -> Started:
    await _dataset(ctx, dataset)
    service = ctx.services.classifier
    retrainer = service.retrainer
    if retrainer is None:
        raise unavailable("The classifier isn't running in the bot, so there's no brain here to retrain.")
    running = await service.running_run()
    if running is not None:
        raise conflict(f"{running.dataset} is already being retrained. One education at a time.")
    logger.info(f"Web {actor}: retrain {dataset}")

    async def retraining() -> ClassifierTrainingRun:
        return await retrainer(dataset, f"web:{actor}")

    task = asyncio.create_task(retraining())
    # Keep a reference, or the task can be garbage collected before it finishes.
    tasks: set[asyncio.Task] = request.app.state.retrain_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    task.add_done_callback(_finished)
    # The run's record is the retrainer's first step. Wait for it, so the page can follow that run.
    for _ in range(100):
        if task.done():
            break
        if (started := await service.running_run()) is not None and started.dataset == dataset and started.id is not None:
            return Started(run_id=started.id, message="Back to school. The old brain keeps guessing until the new one is ready.")
        await asyncio.sleep(0.05)
    if task.done() and (error := task.exception()) is not None:
        if isinstance(error, RetrainInProgress):
            raise conflict("A retrain is already running. One education at a time.")
        if isinstance(error, RetrainFailed):
            raise conflict(str(error))
        raise error
    latest, _ = await service.runs(dataset, limit=1)
    return Started(run_id=(latest[0].id or 0) if latest else 0, message="Retrain started.")


def _finished(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    if (error := task.exception()) is not None and not isinstance(error, (RetrainFailed, RetrainInProgress)):
        logger.error("A retrain started from the website failed", exc_info=error)


@router.get("/datasets/{dataset}/progress")
async def progress(dataset: str, ctx: Ctx) -> Progress:
    service = ctx.services.classifier
    running = await service.running_run()
    done, total = service.progress.get(dataset, (0, 0))
    return Progress(running=running is not None and running.dataset == dataset, done=done, total=total)


@router.get("/datasets/{dataset}/runs")
async def runs(dataset: str, ctx: Ctx, page: Paged) -> Page[RunSummary]:
    rows, total = await ctx.services.classifier.runs(dataset, page.limit, page.offset)
    return Page(items=[_summary(r) for r in rows], total=total)


@router.get("/runs/{run_id}")
async def run(run_id: int, ctx: Ctx) -> RunDetail:
    found = await ctx.services.classifier.run(run_id)
    if found is None:
        raise not_found(f"There's no training run {run_id}.")
    return RunDetail(**found.model_dump())
