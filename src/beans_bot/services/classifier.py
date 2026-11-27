"""
The image classifier's photos, corrections and retraining records.

- Each model (a dataset, named by its `model_path`: "cats", "emotions") has a set of photos in the
  classifier_image table, each with a label and a split: "train" photos teach the model, "val" photos
  score it. The files are shrunk JPEGs under `datasets_dir`. The admin website manages them: upload,
  relabel, move between train and val, remove and restore.
- A new photo's split is decided by its hash alone, so it never changes by accident.
- Any member can say what a classified photo really shows, or confirm that the bot got it right. Either
  way its photos join the dataset under that label. Correcting a post again moves them to the new label.
- A member can also name a cat the model has never heard of. That starts a new label, and the name is
  offered to everyone from then on.
- A member can hand the bot a pile of photos of one cat by DM: a drop session, started with /photos drop.
  Each photo joins the dataset under the session's label. A member can add only so many a day, and the
  session ends when they say done or go quiet.
- Retraining needs torch, so the classifier cog does it and registers itself here as `retrainer`. That's
  how /admin retrain and the website reach it. Each retrain is recorded with its scores on the val photos.
- Models with auto retrain on are retrained by the cog once `retrain_due` says enough photos changed, the
  changes have stopped, no drop is open and the last run was a while ago.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.db.engine import Database
from beans_bot.db.models import (
    ClassifierCorrection,
    ClassifierDropSession,
    ClassifierImage,
    ClassifierImageSource,
    ClassifierProcessedMessage,
    ClassifierSplit,
    ClassifierTrainingRun,
)
from beans_bot.db.types import utcnow
from beans_bot.services import photos

logger = logging.getLogger(__name__)

# Labels and dataset names become folder and file names, so they're kept this plain.
NEW_LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DATASET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# A drop's daily limit counts the photos a member added in this window.
DROP_LIMIT_WINDOW = datetime.timedelta(hours=24)


class NotClassified(Exception):
    """The classifier never replied to this message, so there's no guess to correct."""


class UnknownLabel(Exception):
    """The label isn't one the model knows, and new labels weren't asked for."""


class InvalidLabel(Exception):
    """A new label that wouldn't make a safe folder name."""


class AlreadyLabeled(Exception):
    """The post was already confirmed or corrected to this label, so its photos are saved there."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


class NotAnImage(Exception):
    """The bytes couldn't be opened as a photo."""


@dataclass
class Correction:
    previous_label: str | None  # The bot's guess, or the last correction
    label: str
    paths: list[Path]  # Where the photos are kept
    new_label: bool = False  # Nobody had used this label before

    @property
    def confirmed(self) -> bool:
        """The member agreed with the bot instead of correcting it."""
        return self.previous_label == self.label


class RetrainInProgress(Exception):
    """A retrain is already running."""


class RetrainFailed(Exception):
    """A retrain that couldn't run. The message is for the admin who asked."""


class DropLimitReached(Exception):
    """The member already added their daily limit of photos to this dataset."""

    def __init__(self, limit: int):
        super().__init__(limit)
        self.limit = limit


class DropOutcome(StrEnum):
    KEPT = "kept"
    DUPLICATE = "duplicate"  # Already in the dataset
    FAILED = "failed"  # Couldn't be read
    OVER_LIMIT = "over_limit"  # The member's daily limit is used up. Nothing was stored.
    CLOSED = "closed"  # The session ended or went quiet too long. Nothing changed.


@dataclass
class DropResult:
    outcome: DropOutcome
    session: ClassifierDropSession | None  # As it is after this photo. None if there's no such session.


@dataclass
class DropStart:
    session: ClassifierDropSession
    ended: ClassifierDropSession | None  # The member's previous drop, ended to make way for this one
    new_label: bool  # Nobody had used this label before


@dataclass
class Photo:
    """A photo ready to store: its hash as it arrived, and the shrunk JPEG that is kept."""

    content_hash: str
    jpeg: bytes
    original_name: str | None


def normalize_label(text: str) -> str:
    """Turns what a member typed ("Mr Whiskers") into a label ("mr_whiskers"), or raises InvalidLabel."""
    label = re.sub(r"\s+", "_", text.strip().lower())
    if not NEW_LABEL.fullmatch(label):
        raise InvalidLabel(text)
    return label


def prepare_photo(data: bytes, original_name: str | None = None) -> Photo:
    """Hash and shrink a photo. Slow for big photos, so call it from a thread. Raises NotAnImage."""
    try:
        jpeg = photos.normalize_photo(data)
    except Exception as e:  # Pillow raises a zoo of errors for broken files
        raise NotAnImage(original_name or "photo") from e
    return Photo(content_hash=photos.content_hash(data), jpeg=jpeg, original_name=original_name)


class ClassifierService:
    def __init__(self, db: Database, datasets_dir: Path, val_fraction: float = 0.2):
        self.db = db
        self.datasets_dir = datasets_dir
        self.val_fraction = val_fraction
        # Retrains one dataset: (dataset, triggered_by). None while the classifier cog isn't loaded.
        self.retrainer: Callable[[str, str], Awaitable[ClassifierTrainingRun]] | None = None
        # What model the bot is using for a dataset: loaded, source ("retrained" or "shipped"), labels.
        self.model_info: Callable[[str], dict[str, Any]] | None = None
        # (done, total) photos embedded, per dataset, while a retrain runs.
        self.progress: dict[str, tuple[int, int]] = {}

    # --- Files ---------------------------------------------------------------------------

    @staticmethod
    def check_dataset(dataset: str) -> str:
        if not DATASET_NAME.fullmatch(dataset):
            raise ValueError(f"Not a dataset name: {dataset!r}")
        return dataset

    def dataset_dir(self, dataset: str) -> Path:
        return self.datasets_dir / self.check_dataset(dataset)

    def image_path(self, image: ClassifierImage) -> Path:
        return self.dataset_dir(image.dataset) / "images" / image.filename

    def thumb_path(self, image: ClassifierImage) -> Path:
        return self.dataset_dir(image.dataset) / "thumbs" / f"{image.content_hash}.webp"

    @property
    def cache_path(self) -> Path:
        """Embeddings of the stored photos, keyed by content_hash. Written by the classifier cog."""
        return self.datasets_dir / "embeddings.pkl"

    def default_split(self, content_hash: str) -> str:
        """Decided by the photo alone, so it never changes."""
        return ClassifierSplit.VAL if int(content_hash[:8], 16) / 0xFFFFFFFF < self.val_fraction else ClassifierSplit.TRAIN

    def _write(self, image: ClassifierImage, jpeg: bytes) -> None:
        path = self.image_path(image)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)

    # --- Building blocks (run inside a caller's transaction) --------------------------

    async def _by_hash(self, session: AsyncSession, dataset: str, content_hash: str) -> ClassifierImage | None:
        result = await session.exec(select(ClassifierImage).where(ClassifierImage.dataset == dataset, ClassifierImage.content_hash == content_hash))
        return result.first()

    async def _add(
        self,
        session: AsyncSession,
        dataset: str,
        photo: Photo,
        *,
        label: str,
        source: str,
        split: str | None,
        guild_id: int | None = None,
        message_id: int | None = None,
        added_by: int | None = None,
    ) -> tuple[ClassifierImage, bool]:
        """
        Store a photo under `label`. A photo already in the dataset is returned as it is with False,
        unless it was removed: then it comes back under the new label. The file is written here, inside
        the caller's transaction, so a full disk rolls the row back.
        """
        existing = await self._by_hash(session, dataset, photo.content_hash)
        if existing is not None:
            if existing.removed_at is None:
                return existing, False
            existing.label = label
            existing.removed_at = None
            existing.updated_at = utcnow()
            if split is not None:
                existing.split = split
            session.add(existing)
            if not self.image_path(existing).exists():
                await asyncio.to_thread(self._write, existing, photo.jpeg)
            return existing, True
        image = ClassifierImage(
            dataset=dataset,
            content_hash=photo.content_hash,
            filename=f"{photo.content_hash}.jpg",
            original_name=photo.original_name,
            label=label,
            split=split or self.default_split(photo.content_hash),
            source=source,
            guild_id=guild_id,
            message_id=message_id,
            added_by=added_by,
        )
        session.add(image)
        await session.flush()
        await asyncio.to_thread(self._write, image, photo.jpeg)
        return image, True

    async def _latest(self, session: AsyncSession, guild_id: int, message_id: int, classifier_name: str) -> ClassifierCorrection | None:
        return (
            await session.exec(
                select(ClassifierCorrection)
                .where(
                    ClassifierCorrection.guild_id == guild_id,
                    ClassifierCorrection.message_id == message_id,
                    ClassifierCorrection.classifier_name == classifier_name,
                )
                .order_by(col(ClassifierCorrection.id).desc())
                .limit(1)
            )
        ).first()

    async def _record(
        self,
        guild_id: int,
        message_id: int,
        classifier_name: str,
        dataset: str,
        label: str | None,  # None means whatever the post is labeled now
        corrected_by: int,
        images: list[tuple[str, bytes]],
    ) -> Correction:
        self.check_dataset(dataset)
        # Shrinking big photos is slow, so it happens before the transaction (and its write lock) starts.
        prepared = []
        for filename, data in images:
            try:
                prepared.append(await asyncio.to_thread(prepare_photo, data, filename))
            except NotAnImage:
                # Members can attach anything with an image content type.
                logger.warning(f"Skipping an unreadable photo ({filename!r}) in message {message_id}")
        async with self.db.transaction() as session:
            processed = await session.get(ClassifierProcessedMessage, (guild_id, message_id, classifier_name))
            if processed is None:
                raise NotClassified(message_id)
            latest = await self._latest(session, guild_id, message_id, classifier_name)
            previous_label = latest.label if latest is not None else processed.predicted_label
            if label is None:
                if previous_label is None:
                    raise NotClassified(message_id)
                label = previous_label
            # Agreeing with the bot's guess is fine once: that saves the photo. After that it's settled.
            if latest is not None and latest.label == label:
                raise AlreadyLabeled(label)
            session.add(
                ClassifierCorrection(
                    guild_id=guild_id,
                    message_id=message_id,
                    classifier_name=classifier_name,
                    previous_label=previous_label,
                    label=label,
                    corrected_by=corrected_by,
                )
            )
            # Photos an earlier correction saved move to the new label, and come back if they were removed.
            saved = await session.exec(
                select(ClassifierImage).where(ClassifierImage.dataset == dataset, ClassifierImage.message_id == message_id)
            )
            rows = {row.content_hash: row for row in saved.all()}
            for row in rows.values():
                row.label = label
                row.removed_at = None
                row.updated_at = utcnow()
                session.add(row)
            # Photos not saved yet join the dataset.
            for photo in prepared:
                if photo.content_hash in rows:
                    continue
                row, _ = await self._add(
                    session,
                    dataset,
                    photo,
                    label=label,
                    source=ClassifierImageSource.CORRECTION,
                    split=None,
                    guild_id=guild_id,
                    message_id=message_id,
                    added_by=corrected_by,
                )
                rows[photo.content_hash] = row
            paths = [self.image_path(row) for row in rows.values()]
        logger.info(f"Guild {guild_id}: user {corrected_by} labeled {classifier_name} message {message_id} as {label} (was {previous_label})")
        return Correction(previous_label=previous_label, label=label, paths=paths)

    # --- Corrections from Discord ------------------------------------------------------------

    async def guesses_for(self, guild_id: int, message_ids: list[int]) -> list[ClassifierProcessedMessage]:
        """The guesses where any of these messages is the photo or the bot's reply. Replies match first."""
        async with self.db.transaction() as session:
            rows = (
                await session.exec(
                    select(ClassifierProcessedMessage).where(
                        ClassifierProcessedMessage.guild_id == guild_id,
                        or_(
                            col(ClassifierProcessedMessage.message_id).in_(message_ids),
                            col(ClassifierProcessedMessage.reply_message_id).in_(message_ids),
                        ),
                    )
                )
            ).all()
        return sorted(rows, key=lambda row: row.reply_message_id not in message_ids)

    async def known_labels(self, dataset: str, model_labels: list[str]) -> list[str]:
        """The model's labels, then the other labels that have photos, sorted."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierImage.label).where(ClassifierImage.dataset == dataset, col(ClassifierImage.removed_at).is_(None)).distinct()
            )
            labels = set(result.all())
        return [*model_labels, *sorted(labels - set(model_labels))]

    async def correct(
        self,
        guild_id: int,
        message_id: int,
        classifier_name: str,
        dataset: str,
        model_labels: list[str],
        label: str,
        corrected_by: int,
        images: list[tuple[str, bytes]],
        allow_new: bool = False,
    ) -> Correction:
        """
        Records that the photos in a classified message really show `label`, and keeps them under it.
        `dataset` is the model's model_path, `model_labels` are the labels it was trained on, and
        `images` are (filename, bytes) pairs. With `allow_new`, `label` is free text from a member and
        may start a new label.
        """
        known = await self.known_labels(dataset, model_labels)
        new_label = False
        if label not in known:
            if not allow_new:
                raise UnknownLabel(label)
            label = normalize_label(label)
            new_label = label not in known
        correction = await self._record(guild_id, message_id, classifier_name, dataset, label, corrected_by, images)
        correction.new_label = new_label
        return correction

    async def confirm(
        self, guild_id: int, message_id: int, classifier_name: str, dataset: str, corrected_by: int, images: list[tuple[str, bytes]]
    ) -> Correction:
        """Records that the post's current label is right, and keeps the photos under it."""
        return await self._record(guild_id, message_id, classifier_name, dataset, None, corrected_by, images)

    # --- Photos ----------------------------------------------------------------------------

    async def add_image(
        self,
        dataset: str,
        data: bytes,
        *,
        label: str,
        original_name: str | None,
        source: str,
        split: str | None = None,
        guild_id: int | None = None,
        message_id: int | None = None,
        added_by: int | None = None,
    ) -> tuple[ClassifierImage, bool]:
        """
        Shrink and store a photo. Returns the row and whether it was added (False: it was already in the
        dataset). Raises NotAnImage or InvalidLabel.
        """
        self.check_dataset(dataset)
        label = normalize_label(label)
        photo = await asyncio.to_thread(prepare_photo, data, original_name)
        return await self.add_prepared(
            dataset, photo, label=label, source=source, split=split, guild_id=guild_id, message_id=message_id, added_by=added_by
        )

    async def add_prepared(
        self,
        dataset: str,
        photo: Photo,
        *,
        label: str,
        source: str,
        split: str | None = None,
        guild_id: int | None = None,
        message_id: int | None = None,
        added_by: int | None = None,
    ) -> tuple[ClassifierImage, bool]:
        """add_image for a photo that's already been through prepare_photo."""
        self.check_dataset(dataset)
        label = normalize_label(label)
        async with self.db.transaction() as session:
            return await self._add(
                session, dataset, photo, label=label, source=source, split=split, guild_id=guild_id, message_id=message_id, added_by=added_by
            )

    async def has_hash(self, dataset: str, content_hash: str) -> bool:
        async with self.db.transaction() as session:
            return await self._by_hash(session, dataset, content_hash) is not None

    async def images(
        self,
        dataset: str,
        *,
        label: str | None = None,
        split: str | None = None,
        source: str | None = None,
        removed: bool | None = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ClassifierImage], int]:
        """A page of a dataset's photos, newest first. `removed` None means removed or not."""
        query = select(ClassifierImage).where(ClassifierImage.dataset == dataset)
        if label is not None:
            query = query.where(ClassifierImage.label == label)
        if split is not None:
            query = query.where(ClassifierImage.split == split)
        if source is not None:
            query = query.where(ClassifierImage.source == source)
        if removed is True:
            query = query.where(col(ClassifierImage.removed_at).is_not(None))
        elif removed is False:
            query = query.where(col(ClassifierImage.removed_at).is_(None))
        async with self.db.transaction() as session:
            total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
            rows = await session.exec(query.order_by(col(ClassifierImage.added_at).desc(), col(ClassifierImage.id).desc()).limit(limit).offset(offset))
            return list(rows.all()), total

    async def image(self, image_id: int) -> ClassifierImage | None:
        async with self.db.transaction() as session:
            return await session.get(ClassifierImage, image_id)

    async def update_images(self, ids: list[int], *, label: str | None = None, split: str | None = None, removed: bool | None = None) -> int:
        """Relabel, move between train and val, remove or restore photos. Returns how many changed. Raises InvalidLabel."""
        values: dict[str, Any] = {}
        if label is not None:
            values["label"] = normalize_label(label)
        if split is not None:
            values["split"] = ClassifierSplit(split).value
        if not ids or (not values and removed is None):
            return 0
        now = utcnow()
        query = update(ClassifierImage).where(col(ClassifierImage.id).in_(ids))
        if removed is True:
            values["removed_at"] = now
            query = query.where(col(ClassifierImage.removed_at).is_(None))  # Keep when it was first removed
        elif removed is False:
            values["removed_at"] = None
        async with self.db.transaction() as session:
            result = await session.exec(query.values(**values, updated_at=now))
            changed = result.rowcount
        logger.info(f"Updated {changed} classifier photos {ids[:20]}{'...' if len(ids) > 20 else ''}: {values}")
        return changed

    async def label_counts(self, dataset: str) -> dict[str, dict[str, int]]:
        """Per label: train and val photos, and how many were removed."""
        counts: dict[str, dict[str, int]] = {}
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierImage.label, ClassifierImage.split, col(ClassifierImage.removed_at).is_not(None), func.count())
                .where(ClassifierImage.dataset == dataset)
                .group_by(col(ClassifierImage.label), col(ClassifierImage.split), col(ClassifierImage.removed_at).is_not(None))
            )
            for label, split, removed, count in result.all():
                row = counts.setdefault(label, {"train": 0, "val": 0, "removed": 0})
                row["removed" if removed else split] = row.get("removed" if removed else split, 0) + count
        return dict(sorted(counts.items()))

    async def datasets(self) -> list[str]:
        async with self.db.transaction() as session:
            return sorted((await session.exec(select(ClassifierImage.dataset).distinct())).all())

    async def training_images(self, dataset: str) -> list[ClassifierImage]:
        """Every photo the next retrain uses: all but the removed ones."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierImage)
                .where(ClassifierImage.dataset == dataset, col(ClassifierImage.removed_at).is_(None))
                .order_by(col(ClassifierImage.id))
            )
            return list(result.all())

    async def message_channels(self, guild_message_ids: list[tuple[int, int]]) -> dict[tuple[int, int], int]:
        """(guild, message) -> the channel the bot saw it in, for linking corrected photos to Discord."""
        if not guild_message_ids:
            return {}
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierProcessedMessage).where(col(ClassifierProcessedMessage.message_id).in_([m for _, m in guild_message_ids]))
            )
            return {(row.guild_id, row.message_id): row.channel_id for row in result.all()}

    # --- Photo drops by DM -----------------------------------------------------------------

    async def _drops_used(self, session: AsyncSession, user_id: int, dataset: str, now: datetime.datetime) -> int:
        """Photos the member dropped into this dataset in the last 24 hours."""
        result = await session.exec(
            select(func.count())
            .select_from(ClassifierImage)
            .where(
                ClassifierImage.dataset == dataset,
                ClassifierImage.source == ClassifierImageSource.DROP,
                ClassifierImage.added_by == user_id,
                col(ClassifierImage.added_at) > now - DROP_LIMIT_WINDOW,
            )
        )
        return result.one()

    async def _end_open_drops(self, session: AsyncSession, user_id: int, now: datetime.datetime) -> list[ClassifierDropSession]:
        result = await session.exec(
            select(ClassifierDropSession)
            .where(ClassifierDropSession.user_id == user_id, col(ClassifierDropSession.ended_at).is_(None))
            .order_by(col(ClassifierDropSession.id).desc())
        )
        ended = list(result.all())
        for row in ended:
            row.ended_at = now
            session.add(row)
        return ended

    async def start_drop(
        self,
        guild_id: int,
        user_id: int,
        classifier_name: str,
        dataset: str,
        label: str,
        *,
        model_labels: list[str],
        allow_new: bool,
        idle_timeout: datetime.timedelta,
        daily_limit: int,
        now: datetime.datetime | None = None,
    ) -> DropStart:
        """
        Opens a drop session for the member, ending any they already had open. `label` is what the member
        typed. Raises InvalidLabel, UnknownLabel (a new name when `allow_new` is off) or DropLimitReached.
        """
        self.check_dataset(dataset)
        now = now or utcnow()
        known = await self.known_labels(dataset, model_labels)
        if label not in known:
            label = normalize_label(label)
        new_label = label not in known
        if new_label and not allow_new:
            raise UnknownLabel(label)
        async with self.db.transaction() as session:
            if await self._drops_used(session, user_id, dataset, now) >= daily_limit:
                raise DropLimitReached(daily_limit)
            ended = await self._end_open_drops(session, user_id, now)
            drop = ClassifierDropSession(
                guild_id=guild_id,
                user_id=user_id,
                classifier_name=classifier_name,
                dataset=dataset,
                label=label,
                idle_timeout=int(idle_timeout.total_seconds()),
                started_at=now,
                last_photo_at=now,
            )
            session.add(drop)
        logger.info(f"Guild {guild_id}: user {user_id} started a {classifier_name} photo drop of {label} (session {drop.id})")
        return DropStart(session=drop, ended=ended[0] if ended else None, new_label=new_label)

    async def open_drop(self, user_id: int, now: datetime.datetime | None = None) -> ClassifierDropSession | None:
        """The member's open drop session. One past its idle timeout counts as closed, even before the timer ends it."""
        now = now or utcnow()
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierDropSession)
                .where(ClassifierDropSession.user_id == user_id, col(ClassifierDropSession.ended_at).is_(None))
                .order_by(col(ClassifierDropSession.id).desc())
            )
            return next((row for row in result.all() if row.is_open(now)), None)

    async def drop_photo(
        self, session_id: int, data: bytes, original_name: str | None, *, daily_limit: int, now: datetime.datetime | None = None
    ) -> DropResult:
        """Stores one photo from a drop under the session's label, and counts it."""
        try:
            # Shrinking big photos is slow, so it happens before the transaction (and its write lock) starts.
            photo: Photo | None = await asyncio.to_thread(prepare_photo, data, original_name)
        except NotAnImage:
            photo = None
        now = now or utcnow()
        async with self.db.transaction() as session:
            drop = await session.get(ClassifierDropSession, session_id)
            if drop is None or not drop.is_open(now):
                return DropResult(DropOutcome.CLOSED, drop)
            if photo is None:
                outcome = DropOutcome.FAILED
                drop.failed += 1
            elif (existing := await self._by_hash(session, drop.dataset, photo.content_hash)) is not None and existing.removed_at is None:
                outcome = DropOutcome.DUPLICATE  # Duplicates don't use up the daily limit
                drop.duplicates += 1
            elif await self._drops_used(session, drop.user_id, drop.dataset, now) >= daily_limit:
                return DropResult(DropOutcome.OVER_LIMIT, drop)
            else:
                await self._add(
                    session,
                    drop.dataset,
                    photo,
                    label=drop.label,
                    source=ClassifierImageSource.DROP,
                    split=None,
                    guild_id=drop.guild_id,
                    added_by=drop.user_id,
                )
                outcome = DropOutcome.KEPT
                drop.kept += 1
            drop.last_photo_at = now
            session.add(drop)
        if outcome == DropOutcome.KEPT:
            logger.info(f"Guild {drop.guild_id}: user {drop.user_id} dropped a photo of {drop.label} into {drop.dataset} (session {session_id})")
        return DropResult(outcome, drop)

    async def drop_unreadable(self, session_id: int, now: datetime.datetime | None = None) -> DropResult:
        """Counts an attachment the bot couldn't download, or that was too big, as failed."""
        now = now or utcnow()
        async with self.db.transaction() as session:
            drop = await session.get(ClassifierDropSession, session_id)
            if drop is None or not drop.is_open(now):
                return DropResult(DropOutcome.CLOSED, drop)
            drop.failed += 1
            drop.last_photo_at = now
            session.add(drop)
        return DropResult(DropOutcome.FAILED, drop)

    async def end_drop(self, session_id: int, now: datetime.datetime | None = None) -> ClassifierDropSession | None:
        """Ends a drop session. Returns it, or None if it had already ended, so only one caller sends the summary."""
        async with self.db.transaction() as session:
            drop = await session.get(ClassifierDropSession, session_id)
            if drop is None or drop.ended_at is not None:
                return None
            drop.ended_at = now or utcnow()
            session.add(drop)
        logger.info(f"Guild {drop.guild_id}: user {drop.user_id}'s photo drop {session_id} ended with {drop.kept} kept")
        return drop

    async def expired_drops(self, now: datetime.datetime | None = None) -> list[ClassifierDropSession]:
        """Sessions that haven't ended but went quiet past their idle timeout, for the timer to close."""
        now = now or utcnow()
        async with self.db.transaction() as session:
            result = await session.exec(select(ClassifierDropSession).where(col(ClassifierDropSession.ended_at).is_(None)))
            return [row for row in result.all() if not row.is_open(now)]

    async def open_drops(self, dataset: str, now: datetime.datetime | None = None) -> int:
        """How many drop sessions are open for this dataset."""
        now = now or utcnow()
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierDropSession).where(ClassifierDropSession.dataset == dataset, col(ClassifierDropSession.ended_at).is_(None))
            )
            return sum(1 for row in result.all() if row.is_open(now))

    # --- Training runs ------------------------------------------------------------------------

    async def start_run(self, dataset: str, triggered_by: str) -> ClassifierTrainingRun:
        async with self.db.transaction() as session:
            run = ClassifierTrainingRun(dataset=dataset, status="running", triggered_by=triggered_by)
            session.add(run)
        logger.info(f"Retraining {dataset} (run {run.id}), started by {triggered_by}")
        return run

    async def finish_run(
        self, run_id: int, *, train_count: int, val_count: int, accuracy: float | None, log_loss: float | None, results: dict[str, Any]
    ) -> ClassifierTrainingRun:
        async with self.db.transaction() as session:
            run = await session.get(ClassifierTrainingRun, run_id)
            assert run is not None
            run.status = "succeeded"
            run.finished_at = utcnow()
            run.train_count, run.val_count, run.accuracy, run.log_loss, run.results = train_count, val_count, accuracy, log_loss, results
            session.add(run)
        logger.info(f"Retrained {run.dataset} (run {run_id}): {train_count} train, {val_count} val, accuracy {accuracy}")
        return run

    async def fail_run(self, run_id: int, error: str) -> None:
        async with self.db.transaction() as session:
            run = await session.get(ClassifierTrainingRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = utcnow()
                run.error = error
                session.add(run)
        logger.warning(f"Retrain run {run_id} failed: {error}")

    async def fail_stale_runs(self) -> int:
        """Runs still "running" from before a restart never finished. Returns how many there were."""
        async with self.db.transaction() as session:
            result = await session.exec(
                update(ClassifierTrainingRun)
                .where(col(ClassifierTrainingRun.status) == "running")
                .values(status="failed", finished_at=utcnow(), error="Interrupted by a restart")
            )
            return result.rowcount

    async def runs(self, dataset: str, limit: int = 50, offset: int = 0) -> tuple[list[ClassifierTrainingRun], int]:
        query = select(ClassifierTrainingRun).where(ClassifierTrainingRun.dataset == dataset)
        async with self.db.transaction() as session:
            total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
            rows = await session.exec(query.order_by(col(ClassifierTrainingRun.id).desc()).limit(limit).offset(offset))
            return list(rows.all()), total

    async def run(self, run_id: int) -> ClassifierTrainingRun | None:
        async with self.db.transaction() as session:
            return await session.get(ClassifierTrainingRun, run_id)

    async def latest_run(self, dataset: str) -> ClassifierTrainingRun | None:
        """The newest succeeded run: the model the bot is using, if it was retrained since the last deploy."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(ClassifierTrainingRun)
                .where(ClassifierTrainingRun.dataset == dataset, ClassifierTrainingRun.status == "succeeded")
                .order_by(col(ClassifierTrainingRun.id).desc())
                .limit(1)
            )
            return result.first()

    async def running_run(self) -> ClassifierTrainingRun | None:
        """The retrain in progress, of any dataset. Only one runs at a time."""
        async with self.db.transaction() as session:
            result = await session.exec(select(ClassifierTrainingRun).where(ClassifierTrainingRun.status == "running").limit(1))
            return result.first()

    async def changed_since(self, dataset: str, when) -> int:
        """Photos added, changed or removed after `when`."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(func.count())
                .select_from(ClassifierImage)
                .where(ClassifierImage.dataset == dataset, or_(col(ClassifierImage.updated_at) > when, col(ClassifierImage.added_at) > when))
            )
            return result.one()

    async def retrain_due(
        self,
        dataset: str,
        *,
        min_changes: int,
        quiet_period: datetime.timedelta,
        min_interval: datetime.timedelta,
        now: datetime.datetime | None = None,
    ) -> bool:
        """
        Whether auto retrain should retrain this dataset now: enough photos changed since the newest run of
        any status started (so a failed run isn't retried until more change), the changes have stopped for
        `quiet_period`, no drop is open, the newest run started `min_interval` ago, and nothing is retraining.
        """
        now = now or utcnow()
        if await self.running_run() is not None:
            return False
        async with self.db.transaction() as session:
            newest = (
                await session.exec(
                    select(ClassifierTrainingRun).where(ClassifierTrainingRun.dataset == dataset).order_by(col(ClassifierTrainingRun.id).desc()).limit(1)
                )
            ).first()
            total, last_added, last_updated = (
                await session.exec(
                    select(func.count(), func.max(col(ClassifierImage.added_at)), func.max(col(ClassifierImage.updated_at))).where(
                        ClassifierImage.dataset == dataset
                    )
                )
            ).one()
        if newest is not None and now - newest.started_at < min_interval:
            return False
        # With no run yet, every photo counts.
        changed = total if newest is None else await self.changed_since(dataset, newest.started_at)
        if changed < min_changes:
            return False
        newest_change = max((t for t in (last_added, last_updated) if t is not None), default=None)
        if newest_change is not None and now - newest_change < quiet_period:
            return False
        return await self.open_drops(dataset, now) == 0
