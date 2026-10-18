"""
Replies to images with a script picked by what an image classifier sees
(which cat it is, what mood it's in). Runs automatically in configured
channels, or anywhere when someone reacts with the model's trigger emoji.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import io
import random
from collections import OrderedDict

import discord
import numpy as np
import torch
from PIL import Image
from sqlmodel import select

from beans_bot.cogs.base import BeansCog
from beans_bot.cogs.classifier.dino import Classifier, DinoFeatureExtractor
from beans_bot.config.schema import ClassifierModelConfig
from beans_bot.db.models import ClassifierProcessedMessage, ClassifierScriptLastUsed
from beans_bot.db.types import utcnow
from beans_bot.services import emoji

EMBEDDING_CACHE_SIZE = 100
NEVER = datetime.datetime.min.replace(tzinfo=datetime.UTC)


def confidence_level(thresholds: dict[str, float], probability: float) -> str | None:
    for level, threshold in sorted(thresholds.items(), key=lambda item: item[1], reverse=True):
        if probability >= threshold:
            return level
    return None


class ClassifierCog(BeansCog):
    async def cog_load(self):
        global_config = self.config.classifier
        torch.set_num_threads(global_config.torch_cpu_threads)
        hf_token = global_config.hf_token.get_secret_value() if global_config.hf_token else None
        self.logger.info("Loading DINOv3 feature extractor...")
        self.feature_extractor = await asyncio.to_thread(
            DinoFeatureExtractor, device=global_config.device, hf_token=hf_token
        )
        self.classifiers: dict[str, Classifier] = {}
        model_paths = {
            model.model_path
            for guild in self.config.guilds.values()
            if guild.active and guild.classifier
            for model in guild.classifier.models.values()
        }
        for model_path in model_paths:
            try:
                classifier = Classifier()
                classifier.load(global_config.models_dir / model_path)
                self.classifiers[model_path] = classifier
                self.logger.info(f"Loaded classifier {model_path}")
            except Exception as e:
                self.logger.error(f"Failed to load classifier {model_path}: {e}")
        self.embedding_cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def models_for(self, guild_id: int | None) -> dict[str, ClassifierModelConfig]:
        config = self.guild_config(guild_id)
        return config.classifier.models if config and config.classifier else {}

    @BeansCog.listener()
    async def on_message(self, message: discord.Message):
        if self.is_own_user(message.author.id) or message.guild is None:
            return
        for name, model in self.models_for(message.guild.id).items():
            if message.channel.id in model.auto_respond_channels:
                await self.run_classifier(message, name, model)

    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if self.is_own_user(payload.user_id) or payload.guild_id is None:
            return
        for name, model in self.models_for(payload.guild_id).items():
            if model.manual_trigger_reaction and emoji.matches(payload.emoji, model.manual_trigger_reaction):
                message = await self.fetch_message(payload.channel_id, payload.message_id)
                if message is not None:
                    await self.run_classifier(message, name, model)

    async def embed(self, image_bytes: bytes) -> np.ndarray:
        key = hashlib.md5(image_bytes).hexdigest()
        if key in self.embedding_cache:
            self.embedding_cache.move_to_end(key)
            return self.embedding_cache[key]

        def compute() -> np.ndarray:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            return self.feature_extractor.extract_features(image)

        embedding = await asyncio.to_thread(compute)
        self.embedding_cache[key] = embedding
        if len(self.embedding_cache) > EMBEDDING_CACHE_SIZE:
            self.embedding_cache.popitem(last=False)
        return embedding

    async def probabilities(self, message: discord.Message, classifier: Classifier) -> np.ndarray | None:
        """Label probabilities for all images in a message combined, or None if it has no images."""
        per_image = []
        for attachment in message.attachments:
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                continue
            try:
                embedding = await self.embed(await attachment.read())
                per_image.append(classifier.predict_proba(embedding)[0])
            except Exception as e:
                self.logger.error(f"Failed to process attachment: {e}")
        if not per_image:
            return None
        combined = np.prod(per_image, axis=0)
        return combined / np.sum(combined)

    async def run_classifier(self, message: discord.Message, name: str, model: ClassifierModelConfig) -> None:
        assert message.guild is not None
        guild_id = message.guild.id
        if not message.attachments:
            return
        classifier = self.classifiers.get(model.model_path)
        if classifier is None:
            self.logger.error(f"Model {model.model_path} not loaded.")
            return

        async with self.services.db.transaction() as session:
            if await session.get(ClassifierProcessedMessage, (guild_id, message.id, name)) is not None:
                self.logger.info(f"Message {message.id} already processed by {name}.")
                return

        probs = await self.probabilities(message, classifier)
        if probs is None:
            return
        ranked = np.argsort(probs)[::-1]
        top_label = classifier.index_to_label[int(ranked[0])]
        label_names = {f"{name}_label_{rank + 1}": classifier.index_to_label[int(i)] for rank, i in enumerate(ranked)}
        self.logger.info(f"{name} probabilities: {dict(zip(label_names.values(), probs[ranked].round(3)))}")

        level = confidence_level(model.confidence_thresholds, float(probs[ranked[0]]))
        if level is None:
            self.logger.error(f"No confidence threshold matched for {name}.")
            return
        candidates = [s for s in model.scripts if s.label in ("ANY", top_label) and s.confidence == level]
        if not candidates:
            self.logger.error(f"No candidate scripts for {name}.")
            return
        top_priority = max(s.priority for s in candidates)
        texts = [s.text for s in candidates if s.priority == top_priority]

        async with self.services.db.transaction() as session:
            if random.random() < model.select_random_prob:
                chosen = random.choice(texts)
            else:
                # Least recently used, with never-used scripts first.
                used = {
                    row.script_text: row.last_used_at
                    for row in (
                        await session.exec(
                            select(ClassifierScriptLastUsed).where(
                                ClassifierScriptLastUsed.guild_id == guild_id,
                                ClassifierScriptLastUsed.classifier_name == name,
                            )
                        )
                    ).all()
                }
                chosen = min(texts, key=lambda t: used.get(t, NEVER))

        try:
            await message.reply(chosen.format(**label_names))
        except (discord.HTTPException, KeyError, IndexError):
            self.logger.exception(f"Failed to send classifier reply for message {message.id}")
            return

        async with self.services.db.transaction() as session:
            last_used = await session.get(ClassifierScriptLastUsed, (guild_id, name, chosen))
            if last_used is None:
                session.add(ClassifierScriptLastUsed(guild_id=guild_id, classifier_name=name, script_text=chosen))
            else:
                last_used.last_used_at = utcnow()
            session.add(
                ClassifierProcessedMessage(
                    guild_id=guild_id, message_id=message.id, classifier_name=name, channel_id=message.channel.id
                )
            )
        self.logger.info(f"Replied to message {message.id} with script: {chosen}")
