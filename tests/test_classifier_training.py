"""Retraining and scoring, with a fake feature extractor so no model has to be downloaded."""

import io
import pickle

import pytest

pytest.importorskip("torch")  # Only in the classifier extra

import numpy as np
from PIL import Image

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.cogs.classifier.training import TrainingImage, TrainingSet, load_training_set, save_training_set, train_and_evaluate
from beans_bot.services.classifier import RetrainFailed

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


class ColorExtractor:
    """Embeds an image as its average color, shaped (1, 3) like the real one."""

    def __init__(self):
        self.calls = 0

    def extract_features(self, images: Image.Image) -> np.ndarray:
        self.calls += 1
        return np.asarray(images, dtype=np.float32).mean(axis=(0, 1))[None, :]


class Photos:
    """Writes photos to disk and describes them the way the cog does."""

    def __init__(self, root):
        self.root = root
        self.images: list[TrainingImage] = []

    def add(self, color, label, split="train", data=None) -> TrainingImage:
        n = len(self.images) + 1
        path = self.root / f"{n}.png"
        if data is None:
            buffer = io.BytesIO()
            Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
            data = buffer.getvalue()
        path.write_bytes(data)
        image = TrainingImage(id=n, path=path, label=label, split=split, content_hash=f"hash{n}")
        self.images.append(image)
        return image

    def several(self, color, label, count, split="train"):
        for i in range(count):
            self.add(tuple(min(255, c + i) for c in color), label, split)


@pytest.fixture
def photos(tmp_path):
    (tmp_path / "photos").mkdir()
    return Photos(tmp_path / "photos")


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "retrained" / "cats", tmp_path / "datasets" / "embeddings.pkl"


def predict(classifier, color) -> str:
    return classifier.index_to_label[int(classifier.predict(np.array([color], dtype=np.float32))[0])]


def test_training_set_round_trips(tmp_path):
    # Still used by scripts/train_classifier.py.
    save_training_set(tmp_path / "cats", TrainingSet(X=np.zeros((2, 3)), labels=["a", "b"], hashes=["h1", "h2"]))
    assert load_training_set(tmp_path / "cats").labels == ["a", "b"]


def test_learns_a_new_label_and_saves_the_model(photos, paths):
    photos.several(RED, "red", 6)
    photos.several(GREEN, "green", 6)
    photos.several(BLUE, "blue", 3)
    output, cache = paths
    progress = []
    classifier, results = train_and_evaluate(ColorExtractor(), photos.images, output, cache, lambda done, total: progress.append((done, total)))

    assert [predict(classifier, c) for c in (RED, GREEN, BLUE)] == ["red", "green", "blue"]
    assert (results.train_count, results.val_count, results.accuracy, results.log_loss) == (15, 0, None, None)
    assert results.results["labels"] == ["blue", "green", "red"]
    assert progress[-1] == (15, 15) and len(progress) == 15
    loaded = Classifier()
    loaded.load(output)
    assert predict(loaded, BLUE) == "blue"


def test_scores_a_known_val_set_exactly(photos, paths):
    photos.several(RED, "red", 6)
    photos.several(GREEN, "green", 6)
    photos.add(RED, "red", "val")
    photos.add(GREEN, "green", "val")
    wrong = photos.add(GREEN, "red", "val")  # Labeled red, but it's green: the model will say green
    _, results = train_and_evaluate(ColorExtractor(), photos.images, *paths)

    assert (results.train_count, results.val_count) == (12, 3)
    assert results.accuracy == pytest.approx(2 / 3)
    assert results.log_loss is not None and results.log_loss > 0
    r = results.results
    assert r["labels"] == ["green", "red"]
    assert r["confusion"] == [[1, 0], [1, 1]]  # Rows are the truth, columns the guess
    assert r["per_label"]["green"] == {"train": 6, "val": 1, "precision": 0.5, "recall": 1.0}
    assert r["per_label"]["red"] == {"train": 6, "val": 2, "precision": 1.0, "recall": 0.5}
    mistake = next(p for p in r["predictions"] if not p["correct"])
    assert (mistake["image_id"], mistake["label"], mistake["predicted"]) == (wrong.id, "red", "green")
    assert set(mistake["probabilities"]) == {"green", "red"} and mistake["probabilities"]["green"] > 0.5


def test_skips_unreadable_photos(photos, paths):
    photos.several(RED, "red", 3)
    photos.several(GREEN, "green", 3)
    broken = photos.add(None, "red", data=b"not an image")
    _, results = train_and_evaluate(ColorExtractor(), photos.images, *paths)
    assert results.results["skipped"] == [broken.id] and results.train_count == 6


def test_only_embeds_new_photos_and_caches_flat_vectors(photos, paths):
    photos.several(RED, "red", 3)
    photos.several(GREEN, "green", 3)
    extractor = ColorExtractor()
    train_and_evaluate(extractor, photos.images, *paths)
    photos.add(BLUE, "red")
    train_and_evaluate(extractor, photos.images, *paths)
    assert extractor.calls == 7
    cache = pickle.loads(paths[1].read_bytes())
    assert len(cache) == 7 and all(v.shape == (3,) for v in cache.values())


def test_needs_two_labels(photos, paths):
    photos.several(RED, "red", 3)
    photos.several(GREEN, "green", 2, split="val")
    with pytest.raises(RetrainFailed):
        train_and_evaluate(ColorExtractor(), photos.images, *paths)
    assert not paths[0].parent.exists()


def test_val_only_labels_are_reported_not_scored(photos, paths):
    photos.several(RED, "red", 4)
    photos.several(GREEN, "green", 4)
    photos.add(RED, "red", "val")
    photos.add(BLUE, "blue", "val")  # Nobody taught it blue
    _, results = train_and_evaluate(ColorExtractor(), photos.images, *paths)
    assert results.results["unseen_val_labels"] == ["blue"]
    assert (results.val_count, results.accuracy) == (1, 1.0)
    assert "blue" not in results.results["labels"]
