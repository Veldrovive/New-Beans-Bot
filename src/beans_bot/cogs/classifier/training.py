"""
Retraining a classifier while the bot runs, and scoring it.

- A retrain uses every photo in the dataset that isn't removed (see services/classifier.py). It trains
  on the "train" photos and scores the new model on the "val" photos: accuracy, log loss, a confusion
  matrix, precision and recall per label, and every val photo's probabilities.
- Embeddings are cached by the photo's content hash, so a retrain only embeds new photos.
- The new model is saved apart from the one that ships with the bot, which is left untouched.
- scripts/train_classifier.py still saves <model>_training.pkl with TrainingSet; the bot no longer reads it.
"""

from __future__ import annotations

import logging
import os
import pickle
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image
from pillow_heif import register_heif_opener
from sklearn.metrics import log_loss as sklearn_log_loss

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.services.classifier import RetrainFailed

logger = logging.getLogger(__name__)

register_heif_opener()  # Phones send HEIC


class FeatureExtractor(Protocol):
    def extract_features(self, images: Image.Image) -> np.ndarray: ...


@dataclass
class TrainingSet:
    """What a model was trained on: one embedding, label and file hash per photo."""

    X: np.ndarray
    labels: list[str]
    hashes: list[str]


def training_set_path(base_path: Path) -> Path:
    return Path(f"{base_path}_training.pkl")


def save_training_set(base_path: Path, training_set: TrainingSet) -> None:
    with open(training_set_path(base_path), "wb") as f:
        pickle.dump({"X": training_set.X, "labels": training_set.labels, "hashes": training_set.hashes}, f)


def load_training_set(base_path: Path) -> TrainingSet:
    """Raises FileNotFoundError if the model was trained before training sets were saved."""
    with open(training_set_path(base_path), "rb") as f:
        return TrainingSet(**pickle.load(f))


@dataclass
class TrainingImage:
    """One stored photo, without the database model, so this module doesn't need the DB."""

    id: int
    path: Path
    label: str
    split: str  # "train" or "val"
    content_hash: str


@dataclass
class RunResults:
    train_count: int
    val_count: int  # Val photos that were scored (their label has train photos)
    accuracy: float | None  # None when there were no val photos to score
    log_loss: float | None
    results: dict[str, Any] = field(default_factory=dict)  # Plain JSON, stored on the training run


def load_cache(cache_path: Path) -> dict[str, np.ndarray]:
    if not cache_path.exists():
        return {}
    cache = pickle.loads(cache_path.read_bytes())
    return {key: np.asarray(value).squeeze() for key, value in cache.items()}


def save_cache(cache_path: Path, cache: dict[str, np.ndarray]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    partial = cache_path.with_suffix(".tmp")
    partial.write_bytes(pickle.dumps(cache))
    os.replace(partial, cache_path)  # A crash mid-write never leaves half a cache


def train_and_evaluate(
    extractor: FeatureExtractor,
    images: list[TrainingImage],
    output_path: Path,
    cache_path: Path,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Classifier, RunResults]:
    """
    Train on the train photos, score on the val photos and save the model at `output_path`. Slow, so call
    it from a thread. Raises RetrainFailed when there's nothing to learn from.
    """
    cache = load_cache(cache_path)
    embeddings: dict[int, np.ndarray] = {}
    skipped: list[int] = []
    for done, image in enumerate(images, start=1):
        try:
            if image.content_hash not in cache:
                with Image.open(image.path) as opened:
                    cache[image.content_hash] = np.asarray(extractor.extract_features(opened.convert("RGB"))).squeeze()
            embeddings[image.id] = cache[image.content_hash]
        except Exception:
            logger.exception(f"Skipping unreadable photo {image.id} ({image.path})")
            skipped.append(image.id)
        if progress is not None:
            progress(done, len(images))
    save_cache(cache_path, cache)

    train = [i for i in images if i.split == "train" and i.id in embeddings]
    labels = sorted({i.label for i in train})
    if not train or len(labels) < 2:
        raise RetrainFailed(f"I need train photos of at least two labels to learn anything. I have {len(train)} photos of {len(labels)}.")
    index = {label: n for n, label in enumerate(labels)}
    classifier = Classifier()
    classifier.train(np.array([embeddings[i.id] for i in train]), np.array([index[i.label] for i in train]), index)

    val = [i for i in images if i.split == "val" and i.id in embeddings]
    scored = [i for i in val if i.label in index]
    unseen = sorted({i.label for i in val if i.label not in index})
    n = len(labels)
    confusion = [[0] * n for _ in range(n)]
    predictions = []
    accuracy = loss = None
    if scored:
        probabilities = classifier.predict_proba(np.array([embeddings[i.id] for i in scored]))
        truth = [index[i.label] for i in scored]
        guesses = [int(np.argmax(p)) for p in probabilities]
        for image, true, guess, probs in zip(scored, truth, guesses, probabilities):
            confusion[true][guess] += 1
            predictions.append(
                {
                    "image_id": image.id,
                    "label": image.label,
                    "predicted": labels[guess],
                    "correct": true == guess,
                    "probabilities": {label: round(float(probs[k]), 4) for k, label in enumerate(labels)},
                }
            )
        accuracy = sum(t == g for t, g in zip(truth, guesses)) / len(scored)
        loss = float(sklearn_log_loss(truth, probabilities, labels=list(range(n))))

    train_counts = Counter(i.label for i in train)
    val_counts = Counter(i.label for i in scored)
    per_label = {}
    for k, label in enumerate(labels):
        hits = confusion[k][k]
        predicted = sum(confusion[row][k] for row in range(n))
        per_label[label] = {
            "train": train_counts[label],
            "val": val_counts[label],
            "precision": hits / predicted if predicted else None,
            "recall": hits / val_counts[label] if val_counts[label] else None,
        }
    classifier.save(output_path)
    return classifier, RunResults(
        train_count=len(train),
        val_count=len(scored),
        accuracy=accuracy,
        log_loss=loss,
        results={
            "labels": labels,
            "per_label": per_label,
            "confusion": confusion,
            "predictions": predictions,
            "skipped": skipped,
            "unseen_val_labels": unseen,
        },
    )
