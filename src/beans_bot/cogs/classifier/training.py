"""
Retraining a classifier while the bot runs, and scoring it.

- A retrain uses every photo in the dataset that isn't removed (see services/classifier.py). The model
  the bot uses is trained on all of them.
- It's scored by cross validation: the photos are dealt into FOLDS folds, and each fold is guessed by a
  model trained on the other folds. So every photo is scored once, by a model that never saw it. That gives
  accuracy, log loss, a confusion matrix, precision and recall per label, and every photo's probabilities.
- Folds are made per label with k-means on the embeddings, one cluster per fold. Near-identical photos (a
  burst from one drop) land in the same fold, so one can't vouch for the other. With FOLDS clusters, the
  other folds always hold at least FOLDS - 1 photos of the label. A label with fewer photos than FOLDS
  gets one photo per fold. A label with a single photo is never scored: it only trains, as a distractor.
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
from sklearn.cluster import KMeans
from sklearn.metrics import log_loss as sklearn_log_loss

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.services.classifier import RetrainFailed

logger = logging.getLogger(__name__)

register_heif_opener()  # Phones send HEIC

FOLDS = 10
METHOD = "cross_validation"  # Stored on each run's results. Runs from before folds have no method: they scored a held-out val set.


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
    content_hash: str


@dataclass
class RunResults:
    train_count: int  # Photos the model in use was trained on: all of them
    val_count: int  # Photos that were scored (their label has more than one photo)
    accuracy: float | None  # None when no photo could be scored
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


def assign_folds(images: list[TrainingImage], embeddings: dict[int, np.ndarray], folds: int = FOLDS) -> dict[int, int]:
    """
    Which fold each photo is scored in, by photo id. Photos of single-photo labels are left out: they're
    never scored. The same photos always get the same folds.
    """
    by_label: dict[str, list[TrainingImage]] = {}
    for image in sorted(images, key=lambda i: i.content_hash):
        by_label.setdefault(image.label, []).append(image)
    assigned: dict[int, int] = {}
    sizes = [0] * folds
    # Biggest labels first, each label's biggest cluster into the emptiest fold, so the folds come out even.
    for label, members in sorted(by_label.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(members) < 2:
            continue
        if len(members) <= folds:
            clusters = [[image] for image in members]
        else:
            found = KMeans(n_clusters=folds, n_init=10, random_state=0).fit_predict(np.array([embeddings[i.id] for i in members]))
            clusters = [[image for image, cluster in zip(members, found) if cluster == k] for k in range(folds)]
            clusters = sorted((c for c in clusters if c), key=lambda c: (-len(c), c[0].content_hash))
        emptiest = sorted(range(folds), key=lambda fold: (sizes[fold], fold))
        for cluster, fold in zip(clusters, emptiest):
            sizes[fold] += len(cluster)
            for image in cluster:
                assigned[image.id] = fold
    return assigned


def train_and_evaluate(
    extractor: FeatureExtractor,
    images: list[TrainingImage],
    output_path: Path,
    cache_path: Path,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Classifier, RunResults]:
    """
    Score the dataset by cross validation, then train on every photo and save that model at `output_path`.
    Slow, so call it from a thread. Raises RetrainFailed when there's nothing to learn from.
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

    usable = [i for i in images if i.id in embeddings]
    labels = sorted({i.label for i in usable})
    if not usable or len(labels) < 2:
        raise RetrainFailed(f"I need photos of at least two labels to learn anything. I have {len(usable)} photos of {len(labels)}.")
    index = {label: n for n, label in enumerate(labels)}
    X = np.array([embeddings[i.id] for i in usable])
    y = np.array([index[i.label] for i in usable])

    # Every fold's model still sees every label: a scored label has photos in at least two folds.
    folds = assign_folds(usable, embeddings)
    fold_of = np.array([folds.get(i.id, -1) for i in usable])
    n = len(labels)
    confusion = [[0] * n for _ in range(n)]
    predictions = []
    truth: list[int] = []
    scored_probabilities: list[np.ndarray] = []
    for fold in sorted(set(folds.values())):
        held_out = fold_of == fold
        model = Classifier()
        model.train(X[~held_out], y[~held_out], index)
        for row, probs in zip(np.flatnonzero(held_out), model.predict_proba(X[held_out])):
            image, true, guess = usable[row], int(y[row]), int(np.argmax(probs))
            confusion[true][guess] += 1
            truth.append(true)
            scored_probabilities.append(probs)
            predictions.append(
                {
                    "image_id": image.id,
                    "label": image.label,
                    "predicted": labels[guess],
                    "correct": true == guess,
                    "fold": fold,
                    "probabilities": {label: round(float(probs[k]), 4) for k, label in enumerate(labels)},
                }
            )
    accuracy = loss = None
    if truth:
        accuracy = sum(confusion[k][k] for k in range(n)) / len(truth)
        loss = float(sklearn_log_loss(truth, np.array(scored_probabilities), labels=list(range(n))))

    photo_counts = Counter(i.label for i in usable)
    scored_counts = Counter(labels[t] for t in truth)
    per_label = {}
    for k, label in enumerate(labels):
        hits = confusion[k][k]
        predicted = sum(confusion[row][k] for row in range(n))
        per_label[label] = {
            "train": photo_counts[label],
            "val": scored_counts[label],
            "precision": hits / predicted if predicted else None,
            "recall": hits / scored_counts[label] if scored_counts[label] else None,
        }

    classifier = Classifier()
    classifier.train(X, y, index)
    classifier.save(output_path)
    return classifier, RunResults(
        train_count=len(usable),
        val_count=len(truth),
        accuracy=accuracy,
        log_loss=loss,
        results={
            "method": METHOD,
            "folds": FOLDS,
            "labels": labels,
            "per_label": per_label,
            "confusion": confusion,
            "predictions": predictions,
            "skipped": skipped,
            "unscored_labels": sorted(label for label in labels if not scored_counts[label]),
        },
    )
