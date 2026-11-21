"""
Corrections to the image classifier's guesses.

- Any member can say what a classified photo really shows, or confirm that the bot got it right.
- Either way the photo is saved to disk under the right label, laid out like a training dataset, so the
  model can be retrained on it.
- A member can also name a cat the model has never heard of. That starts a new label folder, and the
  name is offered to everyone from then on.
- A post can be corrected again. The saved photo moves to the new label and the newest correction counts.
- An admin can retrain the model on the saved photos. The training itself needs torch, so the classifier
  cog does it and registers itself here as `retrainer`. That's how /admin retrain reaches it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.db.engine import Database
from beans_bot.db.models import ClassifierCorrection, ClassifierProcessedMessage

logger = logging.getLogger(__name__)

SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")
# Labels become folder names, so new ones are kept this plain.
NEW_LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


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


@dataclass
class Correction:
    previous_label: str | None  # The bot's guess, or the last correction
    label: str
    paths: list[Path]  # Where the photos were saved
    new_label: bool = False  # Nobody had used this label before

    @property
    def confirmed(self) -> bool:
        """The member agreed with the bot instead of correcting it."""
        return self.previous_label == self.label


class RetrainInProgress(Exception):
    """A retrain is already running."""


class RetrainFailed(Exception):
    """A retrain that couldn't run. The message is for the admin who asked."""


@dataclass
class RetrainResult:
    name: str  # The model, as named in the guild's config
    counts: dict[str, int]  # Photos per label the new model was trained on
    added: int  # How many of them came from confirmations and corrections
    skipped: int  # Saved photos that couldn't be read


def normalize_label(text: str) -> str:
    """Turns what a member typed ("Mr Whiskers") into a label ("mr_whiskers"), or raises InvalidLabel."""
    label = re.sub(r"\s+", "_", text.strip().lower())
    if not NEW_LABEL.fullmatch(label):
        raise InvalidLabel(text)
    return label


class ClassifierService:
    def __init__(self, db: Database, corrections_dir: Path):
        self.db = db
        self.corrections_dir = corrections_dir
        # Retrains every model in a guild that takes corrections. None while the classifier cog isn't loaded.
        self.retrainer: Callable[[int], Awaitable[list[RetrainResult]]] | None = None

    # --- Building blocks ---------------------------------------------------------------

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

    def _label_dir(self, dataset: str, label: str) -> Path:
        return self.corrections_dir / dataset / label

    def _move_photos(self, dataset: str, message_id: int, old_label: str | None, label: str, images: list[tuple[str, bytes]]) -> list[Path]:
        """Writes the photos under the new label, then removes this message's photos from the old one."""
        directory = self._label_dir(dataset, label)
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, (filename, data) in enumerate(images, start=1):
            # The name comes from a member, so keep only a plain extension from it.
            suffix = Path(filename).suffix.lower()
            path = directory / f"{message_id}_{index}{suffix if SAFE_SUFFIX.fullmatch(suffix) else '.img'}"
            path.write_bytes(data)
            paths.append(path)
        if old_label is not None:
            for stale in self._label_dir(dataset, old_label).glob(f"{message_id}_*"):
                stale.unlink()
        return paths

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
            # Only an earlier correction left photos on disk. The bot's own guess didn't save any.
            # Done inside the transaction, so the row is rolled back if the disk is full.
            old_label = latest.label if latest is not None else None
            paths = await asyncio.to_thread(self._move_photos, dataset, message_id, old_label, label, images)
        logger.info(f"Guild {guild_id}: user {corrected_by} labeled {classifier_name} message {message_id} as {label} (was {previous_label})")
        return Correction(previous_label=previous_label, label=label, paths=paths)

    # --- Operations --------------------------------------------------------------------

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

    def known_labels(self, dataset: str, model_labels: list[str]) -> list[str]:
        """The model's labels, then the ones members have added since it was trained."""
        root = self.corrections_dir / dataset
        if not root.is_dir():
            return list(model_labels)
        # A folder emptied by a later correction doesn't count.
        added = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in model_labels and any(p.iterdir()))
        return [*model_labels, *added]

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
        Records that the photos in a classified message really show `label`, and saves them under it.
        `dataset` is the folder for this model, `model_labels` are the labels it was trained on, and
        `images` are (filename, bytes) pairs. With `allow_new`, `label` is free text from a member and
        may start a new label.
        """
        known = self.known_labels(dataset, model_labels)
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
        """Records that the post's current label is right, and saves the photos under it."""
        return await self._record(guild_id, message_id, classifier_name, dataset, None, corrected_by, images)
