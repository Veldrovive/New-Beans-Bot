"""
Retraining a classifier while the bot runs.

- The original training photos aren't on the server, so scripts/train_classifier.py saves their
  embeddings next to each model as <model>_training.pkl.
- A retrain starts from those embeddings and adds every photo members have confirmed or corrected.
- The new model is saved apart from the one that ships with the bot, which is left untouched.
"""

from __future__ import annotations

import hashlib
import io
import logging
import pickle
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image
from pillow_heif import register_heif_opener

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.services.classifier import RetrainResult

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


def retrain(name: str, extractor: FeatureExtractor, base_path: Path, corrections_dir: Path, output_path: Path, cache_path: Path) -> tuple[Classifier, RetrainResult]:
    """
    Trains a model on the training set saved at `base_path` plus the photos in `corrections_dir/<label>/`,
    and saves it at `output_path`. Slow, so call it from a thread.
    """
    base = load_training_set(base_path)
    cache: dict[str, np.ndarray] = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}
    rows, labels = list(base.X), list(base.labels)
    # A corrected photo that was later copied into the dataset would otherwise count twice.
    seen = set(base.hashes)
    added = skipped = 0
    label_dirs = sorted(p for p in corrections_dir.iterdir() if p.is_dir()) if corrections_dir.is_dir() else []
    for label_dir in label_dirs:
        for path in sorted(label_dir.iterdir()):
            if not path.is_file() or path.name[0] in "_.":
                continue
            data = path.read_bytes()
            key = hashlib.md5(data).hexdigest()
            if key in seen:
                continue
            try:
                if key not in cache:
                    cache[key] = extractor.extract_features(Image.open(io.BytesIO(data)).convert("RGB")).squeeze()
            except Exception:
                # Members can upload anything with an image content type.
                logger.exception(f"Skipping unreadable photo {path}")
                skipped += 1
                continue
            seen.add(key)
            rows.append(cache[key])
            labels.append(label_dir.name)
            added += 1
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(pickle.dumps(cache))

    names = sorted(set(labels))
    label_to_index = {label: i for i, label in enumerate(names)}
    classifier = Classifier()
    classifier.train(np.array(rows), np.array([label_to_index[label] for label in labels]), label_to_index)
    classifier.save(output_path)
    counts = Counter(labels)
    return classifier, RetrainResult(name=name, counts={label: counts[label] for label in names}, added=added, skipped=skipped)
