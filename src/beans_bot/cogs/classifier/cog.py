"""
Replies to images with a script picked by what an image classifier sees
(which cat it is, what mood it's in). Runs automatically in configured
channels, or anywhere when someone reacts with the model's trigger emoji.

- Models with a `corrections` section get two buttons under each reply. Any member can confirm the
  guess, or pick what the photo really shows. The picker can also add a name the model doesn't know.
- The verdict is noted on the reply, and the photo joins the model's dataset under the right label so
  the model can be retrained on it. See services/classifier.py.
- Once someone has decided, the buttons go away. Anyone can still overrule the decision from the
  message's menu: Apps > Override the guess, on the photo or on the bot's reply.
- Models with a `drop` section take photos by DM. /photos drop name:<cat> opens a session, the member
  DMs as many photos as they like, and each message gets a tally. The bot opens the DM itself, so the
  member has somewhere to put them. It ends when they run /photos done (in the DM or the server) or go
  quiet for the idle timeout, and the bot DMs a summary. A DM that just says "done" ends it too.
- A retrain (from /admin retrain, the website, or auto retrain) scores a dataset by cross validation, trains
  its model on every photo and records both, in a background thread, then swaps the new model in. See
  training.py. Models with an `auto_retrain` section are retrained by a timer once photos changed and things went quiet.
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
from discord.ext import tasks
import torch
from PIL import Image
from sqlmodel import select

from beans_bot.cogs.base import BeansCog, CheckMessage, requires_feature
from beans_bot.cogs.classifier import training
from beans_bot.cogs.classifier.dino import Classifier, DinoFeatureExtractor
from beans_bot.config.schema import ClassifierAutoRetrainConfig, ClassifierDropConfig, ClassifierModelConfig
from beans_bot.db.models import ClassifierDropSession, ClassifierProcessedMessage, ClassifierScriptLastUsed
from beans_bot.db.types import utcnow
from beans_bot.services import emoji, scripts
from beans_bot.db.models import ClassifierTrainingRun
from beans_bot.services.classifier import (
    AlreadyLabeled,
    DropLimitReached,
    DropOutcome,
    InvalidLabel,
    NotClassified,
    RetrainFailed,
    RetrainInProgress,
    UnknownLabel,
)
from beans_bot.services.errors import UserFacingError
from beans_bot.services.photos import MAX_UPLOAD

EMBEDDING_CACHE_SIZE = 100
NEVER = datetime.datetime.min.replace(tzinfo=datetime.UTC)
OVERRIDE_MENU = "Override the guess"
OVERRIDE_HINT = f"Changed your mind? Open the menu on my guess, then Apps, then **{OVERRIDE_MENU}**."
NO_CORRECTIONS = "I've stopped taking notes on this one. I was probably right anyway."
BAD_NAME = "That's not a name, that's a cry for help. Up to 32 letters, numbers, spaces and dashes, starting with a letter or number."
DROP_DONE = "done"  # DMing this ends a photo drop too. /photos done is the way members are told about.


def describe_wait(length: datetime.timedelta) -> str:
    """ "15 minutes", "1 hour", "30 seconds"."""
    seconds = int(length.total_seconds())
    for unit, size in (("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds} second{'' if seconds == 1 else 's'}"


def in_a_server():
    """/photos drop needs a server to know which models and cats are on offer. /photos done works in DMs too."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            raise CheckMessage("Start the drop in the server, so I know whose cats we're talking about. Then bring the photos back here.")
        return True

    return app_commands.check(predicate)


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


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
    # Not guild_only: the photos go in a DM, so /photos done has to work there too.
    photos = app_commands.Group(name="photos", description="Hand me photos to learn from, by DM.")

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
        self.model_sources: dict[str, str] = {}  # "retrained" or "shipped"
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
                self.model_sources[model_path] = "retrained" if base_path == retrained else "shipped"
                self.logger.info(f"Loaded classifier {model_path} from {base_path}")
            except Exception as e:
                self.logger.error(f"Failed to load classifier {model_path}: {e}")
        self.embedding_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        ConfirmButton.cog = CorrectButton.cog = self
        self.bot.add_dynamic_items(ConfirmButton, CorrectButton)
        self.retrain_lock = asyncio.Lock()
        stale = await self.services.classifier.fail_stale_runs()
        if stale:
            self.logger.warning(f"Marked {stale} retrain(s) interrupted by a restart as failed")
        self.services.classifier.retrainer = self.retrain
        self.services.classifier.model_info = self.model_info
        self.bot.tree.add_command(self.override_menu)
        self.auto_retrain_settings = self.collect_auto_retrain()
        self.close_idle_drops.start()
        self.auto_retrain.start()

    async def cog_unload(self):
        self.close_idle_drops.cancel()
        self.auto_retrain.cancel()
        self.bot.tree.remove_command(self.override_menu.name, type=self.override_menu.type)
        self.services.classifier.retrainer = None
        self.services.classifier.model_info = None
        self.bot.remove_dynamic_items(ConfirmButton, CorrectButton)
        ConfirmButton.cog = CorrectButton.cog = None

    def retrained_path(self, model_path: str) -> Path:
        return self.config.classifier.retrained_dir / model_path

    def models_for(self, guild_id: int | None) -> dict[str, ClassifierModelConfig]:
        config = self.guild_config(guild_id)
        return config.classifier.models if config and config.classifier else {}

    @BeansCog.listener()
    async def on_message(self, message: discord.Message):
        if self.is_own_user(message.author.id):
            return
        if message.guild is None:
            if not message.author.bot:
                await self.handle_dm(message)
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
        labels = await self.services.classifier.known_labels(model.model_path, list(classifier.index_to_label.values()))
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
        labels = await self.services.classifier.known_labels(models[guess.classifier_name].model_path, list(classifier.index_to_label.values()))
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

    # --- Photo drops by DM ---------------------------------------------------------------

    def drop_models(self, guild_id: int | None) -> dict[str, ClassifierModelConfig]:
        return {name: model for name, model in self.models_for(guild_id).items() if model.drop is not None}

    def pick_drop_model(self, guild_id: int | None, name: str | None) -> tuple[str, ClassifierModelConfig, ClassifierDropConfig]:
        """The model a /photos drop is for. Raises UserFacingError when there's none, or it's ambiguous."""
        models = self.drop_models(guild_id)
        if not models:
            raise UserFacingError("Nobody here is allowed to slide photos into my DMs. Not a single model in this server takes drops.")
        if name is None:
            if len(models) > 1:
                raise UserFacingError(f"Which of my brains is this for? Pick a model: {scripts.join_names(sorted(models))}.")
            name = next(iter(models))
        model = models.get(name)
        if model is None or model.drop is None:
            raise UserFacingError(f"I have no model called {name} that takes drops. The ones that do: {scripts.join_names(sorted(models))}.")
        return name, model, model.drop

    def model_labels(self, model: ClassifierModelConfig) -> list[str]:
        classifier = self.classifiers.get(model.model_path)
        return list(classifier.index_to_label.values()) if classifier is not None else []

    def drop_summary(self, drop: ClassifierDropSession) -> str:
        """What the member hears when a drop ends. Says when the photos start to count."""
        model = self.models_for(drop.guild_id).get(drop.classifier_name)
        config = model.drop if model is not None else None
        values = {"label": drop.label, "kept": drop.kept, "duplicates": drop.duplicates, "failed": drop.failed}
        text = scripts.pick(config.summary_scripts, **values) if config is not None else None
        text = text or (
            f"That's a wrap on {drop.label}. I kept {drop.kept}, already had {drop.duplicates}, and couldn't make sense of {drop.failed}."
        )
        if drop.kept == 0:
            return f"{text}\n-# Nothing new, so I have nothing new to study."
        if model is not None and model.auto_retrain is not None:
            return f"{text}\n-# I'll study them soon, once things go quiet. No need to bother an admin."
        return f"{text}\n-# They count from my next retrain, whenever an admin gets round to it."

    async def send_dm(self, user_id: int, text: str) -> None:
        """DMs a member, or logs that they can't be reached."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.logger.warning(f"Could not DM user {user_id}")

    @photos.command(name="drop", description="DM me a pile of photos of one cat, and I'll learn from them.")
    @app_commands.describe(name="Who's in the photos", model="Which of my models they're for (only needed if there's more than one)")
    @requires_feature("classifier")
    @in_a_server()
    async def drop(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 32], model: str | None = None):
        assert interaction.guild_id is not None
        model_name, model_config, config = self.pick_drop_model(interaction.guild_id, model)
        try:
            started = await self.services.classifier.start_drop(
                interaction.guild_id,
                interaction.user.id,
                model_name,
                model_config.model_path,
                name,
                model_labels=self.model_labels(model_config),
                allow_new=config.allow_new_labels,
                idle_timeout=config.idle_timeout,
                daily_limit=config.daily_limit,
            )
        except InvalidLabel:
            raise UserFacingError(BAD_NAME)
        except UnknownLabel:
            raise UserFacingError(f"I've never heard of {name}, and I'm not taking new cats right now. Pick one I know.")
        except DropLimitReached as e:
            text = scripts.pick(config.limit_scripts, limit=e.limit)
            raise UserFacingError(text or f"You've already given me {e.limit} photos today. My eyes need a rest. Try again tomorrow.")
        drop = started.session
        idle = describe_wait(config.idle_timeout)
        values = {"label": drop.label, "idle": idle}
        if started.new_label:
            text = scripts.pick(config.new_label_scripts, **values)
            text = text or f"A cat called {drop.label}? Never met them. DM me every photo you have. I stop listening after {idle} of silence. Use `/photos done` when you're finished."
        else:
            text = scripts.pick(config.start_scripts, **values)
            text = text or f"DM me every photo of {drop.label} you have. I stop listening after {idle} of silence. Use `/photos done` when you're finished."
        text += f"\n-# Up to {config.daily_limit} photos a day."
        # The opening line goes in a DM, so the member has a conversation to put the photos in.
        await interaction.response.defer(ephemeral=True)
        try:
            opener = await interaction.user.send(text)
            reply = f"I've slid into your DMs. Put the photos of {drop.label} there: {opener.jump_url}"
        except discord.HTTPException:
            self.logger.warning(f"Could not DM user {interaction.user.id} to open their photo drop")
            reply = f"{text}\n-# I tried to DM you first and got the door slammed in my face. Message me yourself and send them there."
        if started.ended is not None:
            reply = f"{self.drop_summary(started.ended)}\n\n{reply}"
        await interaction.followup.send(reply, ephemeral=True)

    @drop.autocomplete("model")
    async def drop_model_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        names = sorted(self.drop_models(interaction.guild_id))
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    @drop.autocomplete("name")
    async def drop_name_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            _, model, _ = self.pick_drop_model(interaction.guild_id, getattr(interaction.namespace, "model", None))
        except UserFacingError:
            return []
        labels = await self.services.classifier.known_labels(model.model_path, self.model_labels(model))
        return [app_commands.Choice(name=label, value=label) for label in labels if current.lower() in label.lower()][:25]

    @photos.command(name="done", description="Stop sending me photos, and hear how it went.")
    async def done(self, interaction: discord.Interaction):
        service = self.services.classifier
        drop = await service.open_drop(interaction.user.id)
        ended = await service.end_drop(drop.id) if drop is not None and drop.id is not None else None
        if ended is None:
            raise UserFacingError("You weren't sending me anything. Start with /photos drop, and then you can be done.")
        await interaction.response.send_message(self.drop_summary(ended), ephemeral=True)

    async def no_drop_hint(self, message: discord.Message) -> None:
        """Photos in a DM with no drop open. Only answered if a server we share takes drops."""
        shared = [guild.id for guild in message.author.mutual_guilds]
        configs = [model.drop for guild_id in shared for model in self.drop_models(guild_id).values() if model.drop is not None]
        if not configs:
            return
        text = scripts.pick([line for config in configs for line in config.no_session_scripts])
        text = text or "Lovely, but I don't know who this is. Run /photos drop in the server first and tell me."
        try:
            await message.reply(text)
        except discord.HTTPException:
            self.logger.warning(f"Could not answer DM photos from {message.author.id}")

    async def handle_dm(self, message: discord.Message) -> None:
        service = self.services.classifier
        images = [attachment for attachment in message.attachments if is_image(attachment)]
        done = message.content.strip().lower() == DROP_DONE
        if not images and not done:
            return  # Other DMs are none of this feature's business
        drop = await service.open_drop(message.author.id)
        if drop is None or drop.id is None:
            if images:
                await self.no_drop_hint(message)
            return
        model = self.models_for(drop.guild_id).get(drop.classifier_name)
        if model is None or model.drop is None:
            # Drops were turned off for this model since the session started.
            ended = await service.end_drop(drop.id)
            if ended is not None:
                await self.send_dm(message.author.id, self.drop_summary(ended))
            return
        if images:
            await self.take_photos(message, drop, images, model.drop)
        if done and (ended := await service.end_drop(drop.id)) is not None:
            try:
                await message.reply(self.drop_summary(ended))
            except discord.HTTPException:
                self.logger.warning(f"Could not send the drop summary to {message.author.id}")

    async def take_photos(
        self, message: discord.Message, drop: ClassifierDropSession, images: list[discord.Attachment], config: ClassifierDropConfig
    ) -> None:
        """Stores a DM's photos in the drop and answers with a tally. One bad photo never stops the rest."""
        assert drop.id is not None
        service = self.services.classifier
        counts = {outcome: 0 for outcome in DropOutcome}
        latest = drop
        for attachment in images:
            try:
                if attachment.size > MAX_UPLOAD:
                    result = await service.drop_unreadable(drop.id)
                else:
                    data = await attachment.read()
                    result = await service.drop_photo(drop.id, data, attachment.filename, daily_limit=config.daily_limit)
            except discord.HTTPException:
                self.logger.warning(f"Could not download attachment {attachment.id} from {message.author.id}'s drop {drop.id}")
                result = await service.drop_unreadable(drop.id)
            except Exception:
                self.logger.exception(f"Could not store attachment {attachment.id} from {message.author.id}'s drop {drop.id}")
                result = await service.drop_unreadable(drop.id)
            counts[result.outcome] += 1
            latest = result.session or latest
            if result.outcome == DropOutcome.CLOSED:
                break
        if counts[DropOutcome.CLOSED]:
            text = "Too slow. I'd already stopped listening. Run /photos drop again if you've got more."
        else:
            parts = [f"kept {counts[DropOutcome.KEPT]}"]
            if counts[DropOutcome.DUPLICATE]:
                parts.append(f"already had {counts[DropOutcome.DUPLICATE]}")
            if counts[DropOutcome.FAILED]:
                parts.append(f"couldn't read {counts[DropOutcome.FAILED]}")
            text = f"{latest.label}: {', '.join(parts)}. {plural(latest.kept, 'photo')} kept this drop so far."
            if counts[DropOutcome.OVER_LIMIT]:
                limit = scripts.pick(config.limit_scripts, limit=config.daily_limit)
                limit = limit or f"That's your {config.daily_limit} for today. My eyes need a rest."
                text += f"\n{limit} I turned away {plural(counts[DropOutcome.OVER_LIMIT], 'photo')}."
        try:
            if not counts[DropOutcome.CLOSED]:
                await message.add_reaction("✅")
            await message.reply(text)
        except discord.HTTPException:
            self.logger.warning(f"Could not answer {message.author.id}'s drop photos")

    @tasks.loop(seconds=60)
    async def close_idle_drops(self):
        """Ends drop sessions that went quiet, and DMs each member their summary."""
        try:
            expired = await self.services.classifier.expired_drops()
        except Exception:
            self.logger.exception("Could not look up idle photo drops")
            return
        for drop in expired:
            try:
                assert drop.id is not None
                ended = await self.services.classifier.end_drop(drop.id)
                if ended is not None:
                    await self.send_dm(ended.user_id, self.drop_summary(ended))
            except Exception:
                self.logger.exception(f"Could not close photo drop {drop.id}")

    @close_idle_drops.before_loop
    async def before_close_idle_drops(self):
        await self.bot.wait_until_ready()

    # --- Retraining ----------------------------------------------------------------------

    def model_info(self, dataset: str) -> dict:
        """What the bot is using for a dataset: whether a model is loaded, where it came from, and its labels."""
        classifier = self.classifiers.get(dataset)
        return {
            "loaded": classifier is not None,
            "source": self.model_sources.get(dataset),
            "labels": list(classifier.index_to_label.values()) if classifier is not None else [],
        }

    async def retrain(self, dataset: str, triggered_by: str) -> ClassifierTrainingRun:
        """
        Retrains one dataset's model on its photos, scores it by cross validation, and starts using it.
        The work happens in a thread, so the bot keeps answering (and guessing) meanwhile. One at a time.
        """
        if self.retrain_lock.locked():
            raise RetrainInProgress()
        async with self.retrain_lock:
            service = self.services.classifier
            run = await service.start_run(dataset, triggered_by)
            assert run.id is not None
            try:
                rows = await service.training_images(dataset)
                if not rows:
                    raise RetrainFailed(f"No photos have been imported for {dataset} yet. Run scripts/import_classifier_dataset.py.")
                images = [
                    training.TrainingImage(id=row.id or 0, path=service.image_path(row), label=row.label, content_hash=row.content_hash)
                    for row in rows
                ]
                service.progress[dataset] = (0, len(images))

                def progress(done: int, total: int) -> None:
                    service.progress[dataset] = (done, total)  # A plain dict write is safe from the thread

                classifier, results = await asyncio.to_thread(
                    training.train_and_evaluate, self.feature_extractor, images, self.retrained_path(dataset), service.cache_path, progress
                )
                self.classifiers[dataset] = classifier
                self.model_sources[dataset] = "retrained"
                finished = await service.finish_run(
                    run.id,
                    train_count=results.train_count,
                    val_count=results.val_count,
                    accuracy=results.accuracy,
                    log_loss=results.log_loss,
                    results=results.results,
                )
            except Exception as e:
                await service.fail_run(run.id, str(e)[:500] if isinstance(e, RetrainFailed) else f"{type(e).__name__}: {e}"[:500])
                raise
            finally:
                service.progress.pop(dataset, None)
            self.logger.info(f"Retrained {dataset} on {results.train_count} photos, cross validation accuracy {results.accuracy}")
            return finished

    # --- Auto retrain ----------------------------------------------------------------------

    def collect_auto_retrain(self) -> dict[str, ClassifierAutoRetrainConfig]:
        """One auto retrain setting per dataset, across active guilds. If guilds disagree, the first wins."""
        found: dict[str, ClassifierAutoRetrainConfig] = {}
        for guild in self.config.guilds.values():
            if not guild.active or guild.classifier is None:
                continue
            for model in guild.classifier.models.values():
                if model.auto_retrain is None:
                    continue
                first = found.setdefault(model.model_path, model.auto_retrain)
                if first != model.auto_retrain:
                    self.logger.warning(f"Guilds set different auto retrain settings for {model.model_path}. Using the first: {first}")
        return found

    @tasks.loop(seconds=60)
    async def auto_retrain(self):
        for dataset, settings in self.auto_retrain_settings.items():
            try:
                due = await self.services.classifier.retrain_due(
                    dataset, quiet_period=settings.quiet_period, min_interval=settings.min_interval
                )
                if due:
                    self.logger.info(f"Auto retraining {dataset}")
                    await self.retrain(dataset, "auto")
            except RetrainInProgress:
                pass
            except RetrainFailed as e:
                self.logger.warning(f"Auto retrain of {dataset} failed: {e}")
            except Exception:
                self.logger.exception(f"Auto retrain of {dataset} failed")

    @auto_retrain.before_loop
    async def before_auto_retrain(self):
        await self.bot.wait_until_ready()
