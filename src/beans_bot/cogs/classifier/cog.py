"""
Replies to images with a script picked by what an image classifier sees
(which cat it is, what mood it's in). Runs automatically in configured
channels, or anywhere when someone reacts with the model's trigger emoji.

- Models with a `corrections` section get two buttons under each reply. Any member can confirm the
  guess, or pick what the photo really shows. The picker can also add a name the model doesn't know.
- The verdict is noted on the reply, and the photo is saved to disk under the right label so the
  model can be retrained on it. See services/classifier.py.
- Once someone has decided, the buttons go away. Anyone can still overrule the decision from the
  message's menu: Apps > Override the guess, on the photo or on the bot's reply.
- /admin retrain rebuilds those models from their original training set plus the saved photos, in a
  background thread, and swaps the new model in when it's done. See training.py.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import io
import random
import re
from pathlib import Path
from collections import OrderedDict

import discord
import numpy as np
from discord import app_commands
import torch
from PIL import Image
from sqlmodel import select

from beans_bot.cogs.base import BeansCog
from beans_bot.cogs.classifier import training
from beans_bot.cogs.classifier.dino import Classifier, DinoFeatureExtractor
from beans_bot.config.schema import ClassifierModelConfig
from beans_bot.db.models import ClassifierProcessedMessage, ClassifierScriptLastUsed
from beans_bot.db.types import utcnow
from beans_bot.services import emoji, scripts
from beans_bot.services.classifier import AlreadyLabeled, InvalidLabel, NotClassified, RetrainFailed, RetrainInProgress, RetrainResult, UnknownLabel

EMBEDDING_CACHE_SIZE = 100
NEVER = datetime.datetime.min.replace(tzinfo=datetime.UTC)
OVERRIDE_MENU = "Override the guess"
OVERRIDE_HINT = f"Changed your mind? Open the menu on my guess, then Apps, then **{OVERRIDE_MENU}**."
NO_CORRECTIONS = "I've stopped taking notes on this one. I was probably right anyway."


def confidence_level(thresholds: dict[str, float], probability: float) -> str | None:
    for level, threshold in sorted(thresholds.items(), key=lambda item: item[1], reverse=True):
        if probability >= threshold:
            return level
    return None


def is_image(attachment: discord.Attachment) -> bool:
    return bool(attachment.content_type) and attachment.content_type.startswith("image/")


NEW_LABEL_VALUE = "+new"  # Not a valid label, so it can't clash with one


class ConfirmButton(discord.ui.DynamicItem[discord.ui.Button], template=r"classifier:confirm:(?P<name>.+)"):
    """The "you got it right" button under a reply. Matched by custom_id, so old replies still work after a restart."""

    cog: ClassifierCog | None = None  # Set while the cog is loaded

    def __init__(self, name: str, label: str = "Yep, that's the one"):
        super().__init__(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.success, custom_id=f"classifier:confirm:{name}"))
        self.name = name

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /) -> ConfirmButton:
        return cls(match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.cog is not None and interaction.message is not None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.cog.label_photo(interaction, self.name, interaction.message, None)


class CorrectButton(discord.ui.DynamicItem[discord.ui.Button], template=r"classifier:correct:(?P<name>.+)"):
    """The "you got it wrong" button under a reply. Matched by custom_id, like ConfirmButton."""

    cog: ClassifierCog | None = None  # Set while the cog is loaded

    def __init__(self, name: str, label: str = "Wrong?"):
        super().__init__(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary, custom_id=f"classifier:correct:{name}"))
        self.name = name

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /) -> CorrectButton:
        return cls(match["name"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.cog is not None:
            await self.cog.offer_correction(interaction, self.name)


class NewLabelModal(discord.ui.Modal, title="Someone new"):
    """Asks for the name of a cat the model doesn't know."""

    label: discord.ui.TextInput = discord.ui.TextInput(label="Who is it?", placeholder="Their name", min_length=1, max_length=32)

    def __init__(self, picker: CorrectionPicker):
        super().__init__()
        self.picker = picker

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.picker.stop()
        await interaction.response.defer()
        await self.picker.cog.label_photo(interaction, self.picker.name, self.picker.reply, self.label.value, new=True)


class LabelSelect(discord.ui.Select):
    def __init__(self, picker: CorrectionPicker, labels: list[str]):
        # Discord allows 25 options, and the last one is for adding a new label.
        options = [discord.SelectOption(label=label[:100], value=label) for label in labels[:24]]
        options.append(discord.SelectOption(label="Someone new...", value=NEW_LABEL_VALUE, description="Add a name I don't know yet", emoji="➕"))
        super().__init__(placeholder="Who is it really?", options=options)
        self.picker = picker

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == NEW_LABEL_VALUE:
            await interaction.response.send_modal(NewLabelModal(self.picker))
            return
        self.picker.stop()
        await interaction.response.defer()
        await self.picker.cog.label_photo(interaction, self.picker.name, self.picker.reply, self.values[0])


class CorrectionPicker(discord.ui.View):
    """Shown privately to whoever pressed the wrong button. Lists the labels, plus a way to add one."""

    def __init__(self, cog: ClassifierCog, name: str, reply: discord.Message, labels: list[str]):
        super().__init__(timeout=300)
        self.cog = cog
        self.name = name
        self.reply = reply
        self.add_item(LabelSelect(self, labels))


class ClassifierCog(BeansCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.override_menu = app_commands.ContextMenu(name=OVERRIDE_MENU, callback=self.override_guess)

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
                # A model retrained on members' photos replaces the one that ships with the bot.
                retrained = self.retrained_path(model_path)
                base_path = retrained if Path(f"{retrained}.pkl").exists() else global_config.models_dir / model_path
                classifier = Classifier()
                classifier.load(base_path)
                self.classifiers[model_path] = classifier
                self.logger.info(f"Loaded classifier {model_path} from {base_path}")
            except Exception as e:
                self.logger.error(f"Failed to load classifier {model_path}: {e}")
        self.embedding_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        ConfirmButton.cog = CorrectButton.cog = self
        self.bot.add_dynamic_items(ConfirmButton, CorrectButton)
        self.retrain_lock = asyncio.Lock()
        self.services.classifier.retrainer = self.retrain
        self.bot.tree.add_command(self.override_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.override_menu.name, type=self.override_menu.type)
        self.services.classifier.retrainer = None
        self.bot.remove_dynamic_items(ConfirmButton, CorrectButton)
        ConfirmButton.cog = CorrectButton.cog = None

    def retrained_path(self, model_path: str) -> Path:
        return self.config.classifier.retrained_dir / model_path

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
            if not is_image(attachment):
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
            text = chosen.format(**label_names)
            if model.corrections is not None:
                view = discord.ui.View(timeout=None)
                view.add_item(ConfirmButton(name, random.choice(model.corrections.confirm_labels or ["Yep, that's the one"])))
                view.add_item(CorrectButton(name, model.corrections.button_label))
                reply = await message.reply(text, view=view)
            else:
                reply = await message.reply(text)
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
                    guild_id=guild_id,
                    message_id=message.id,
                    classifier_name=name,
                    channel_id=message.channel.id,
                    predicted_label=top_label,
                    reply_message_id=reply.id,
                )
            )
        self.logger.info(f"Replied to message {message.id} with script: {chosen}")

    # --- Corrections ---------------------------------------------------------------------

    async def offer_correction(self, interaction: discord.Interaction, name: str) -> None:
        """Someone pressed the wrong button under a reply. Ask them, privately, what the photo really shows."""
        model = self.models_for(interaction.guild_id).get(name)
        classifier = self.classifiers.get(model.model_path) if model is not None else None
        if model is None or model.corrections is None or classifier is None or interaction.message is None:
            await interaction.response.send_message(NO_CORRECTIONS, ephemeral=True)
            return
        labels = self.services.classifier.known_labels(model.model_path, list(classifier.index_to_label.values()))
        await interaction.response.send_message("Fine. Enlighten me.", view=CorrectionPicker(self, name, interaction.message, labels), ephemeral=True)

    async def override_guess(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """
        The message menu entry. It opens the same picker as the wrong button, and it's the only way in once
        a decision has taken the buttons away. Works on the photo or on the bot's reply.
        """
        models = self.models_for(interaction.guild_id)
        if interaction.guild_id is None or not any(model.corrections for model in models.values()):
            await interaction.response.send_message("I don't take corrections around here. I'm never wrong in this server.", ephemeral=True)
            return
        # Older guesses didn't record their reply, so also look up the photo a reply points at.
        message_ids = [message.id]
        if message.reference is not None and message.reference.message_id is not None:
            message_ids.append(message.reference.message_id)
        guesses = await self.services.classifier.guesses_for(interaction.guild_id, message_ids)
        guess = next((g for g in guesses if (model := models.get(g.classifier_name)) is not None and model.corrections is not None), None)
        reply = None
        if guess is not None:
            if self.is_own_user(message.author.id):
                reply = message
            elif guess.reply_message_id is not None:
                reply = await self.fetch_message(message.channel.id, guess.reply_message_id)
        classifier = self.classifiers.get(models[guess.classifier_name].model_path) if guess is not None else None
        if guess is None or reply is None or classifier is None:
            await interaction.response.send_message(
                "There's no guess of mine here to overrule. Try it on one of my cat guesses, if you must.", ephemeral=True
            )
            return
        labels = self.services.classifier.known_labels(models[guess.classifier_name].model_path, list(classifier.index_to_label.values()))
        picker = CorrectionPicker(self, guess.classifier_name, reply, labels)
        await interaction.response.send_message("Overruling the record, are we? Go on then.", view=picker, ephemeral=True)

    async def label_photo(self, interaction: discord.Interaction, name: str, reply: discord.Message, label: str | None, new: bool = False) -> None:
        """
        Records what a member says a photo shows. `reply` is the bot's guess, and the photo is the message it
        replied to. A `label` of None confirms the guess. With `new`, the label is a name the member typed.
        The interaction is already deferred, so every outcome edits the member's private message.
        """

        async def finish(text: str) -> None:
            await interaction.edit_original_response(content=text, view=None)

        model = self.models_for(interaction.guild_id).get(name)
        classifier = self.classifiers.get(model.model_path) if model is not None else None
        if interaction.guild_id is None or model is None or model.corrections is None or classifier is None:
            await finish(NO_CORRECTIONS)
            return

        photo_id = reply.reference.message_id if reply.reference is not None else None
        photo = await self.fetch_message(reply.channel.id, photo_id) if photo_id is not None else None
        images: list[tuple[str, bytes]] = []
        for attachment in photo.attachments if photo is not None else []:
            if not is_image(attachment):
                continue
            try:
                images.append((attachment.filename, await attachment.read()))
            except discord.HTTPException:
                self.logger.exception(f"Failed to download attachment {attachment.id} for a correction")
        if photo is None or not images:
            await finish("The photo is gone, so there's nothing left for me to be right or wrong about.")
            return

        service = self.services.classifier
        try:
            if label is None:
                correction = await service.confirm(interaction.guild_id, photo.id, name, model.model_path, interaction.user.id, images)
            else:
                correction = await service.correct(
                    guild_id=interaction.guild_id,
                    message_id=photo.id,
                    classifier_name=name,
                    dataset=model.model_path,
                    model_labels=list(classifier.index_to_label.values()),
                    label=label,
                    corrected_by=interaction.user.id,
                    images=images,
                    allow_new=new,
                )
        except AlreadyLabeled as e:
            await finish(f"This one's already settled as {e.label}, and the photo is in my files. The case is closed.")
            return
        except InvalidLabel:
            await finish("That's not a name, that's a cry for help. Up to 32 letters, numbers, spaces and dashes, starting with a letter or number.")
            return
        except (NotClassified, UnknownLabel):
            await finish("My records say I never guessed at that one. You can't grade what I didn't say.")
            return
        except OSError:
            self.logger.exception(f"Failed to save photos for message {photo.id}")
            await finish("I couldn't save the photo, so that didn't stick. Tell an admin my disk is sulking.")
            return

        # Note the verdict under the guess, replacing the note from any earlier one.
        member = interaction.user.mention
        config = model.corrections
        old_label = correction.previous_label or "what I said"
        if correction.confirmed:
            note = scripts.pick(config.confirmed_scripts, confirmer=member, label=correction.label) or f"{member} confirms this is {correction.label}."
            done = f"Of course it's {correction.label}. I've saved the photo as proof. {OVERRIDE_HINT}"
        elif correction.new_label:
            values = {"corrector": member, "old_label": old_label, "new_label": correction.label}
            note = scripts.pick(config.new_label_scripts, **values) or f"{member} says this is {correction.label}, who I've never met."
            done = f"A new face! The photo is filed under {correction.label}. I won't recognise them until I'm retrained, so keep correcting me."
        else:
            values = {"corrector": member, "old_label": old_label, "new_label": correction.label}
            note = scripts.pick(config.corrected_scripts, **values) or f"{member} says this is {correction.label}, not {old_label}."
            done = f"Noted: it's {correction.label}. I've saved the photo so I can study my mistake. {OVERRIDE_HINT}"
        guess = reply.content.split("\n-# ")[0]
        try:
            # The decision is made, so the buttons go. The message menu can still overrule it.
            await reply.edit(content=f"{guess}\n-# {note}", view=None, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.logger.exception(f"Failed to note the verdict on reply {reply.id}")
        await finish(done)

    # --- Retraining ----------------------------------------------------------------------

    async def retrain(self, guild_id: int) -> list[RetrainResult]:
        """
        Retrains every model in the guild that takes corrections, and starts using the new ones.
        The work happens in a thread, so the bot keeps answering (and guessing) meanwhile.
        """
        if self.retrain_lock.locked():
            raise RetrainInProgress()
        async with self.retrain_lock:
            global_config = self.config.classifier
            models = {name: model for name, model in self.models_for(guild_id).items() if model.corrections is not None}
            if not models:
                raise RetrainFailed("No model here takes corrections, so I have nothing new to learn.")
            results = []
            for name, model in models.items():
                try:
                    classifier, result = await asyncio.to_thread(
                        training.retrain,
                        name,
                        self.feature_extractor,
                        global_config.models_dir / model.model_path,
                        global_config.corrections_dir / model.model_path,
                        self.retrained_path(model.model_path),
                        global_config.retrained_dir / "embeddings.pkl",
                    )
                except FileNotFoundError as e:
                    self.logger.exception(f"No training set for {name}")
                    raise RetrainFailed(
                        f"I can't find my old homework for {name} (`{model.model_path}_training.pkl`). "
                        "Run `scripts/train_classifier.py` once and redeploy me."
                    ) from e
                self.classifiers[model.model_path] = classifier
                self.logger.info(f"Guild {guild_id}: retrained {name} on {sum(result.counts.values())} photos ({result.added} from members)")
                results.append(result)
            return results
