"""Retraining, with a fake feature extractor so no model has to be downloaded."""

import io

import pytest

pytest.importorskip("torch")  # Only in the classifier extra

import numpy as np
from PIL import Image

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.cogs.classifier.training import TrainingSet, load_training_set, retrain, save_training_set

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


class ColorExtractor:
    """Embeds an image as its average color."""

    def __init__(self):
        self.calls = 0

    def extract_features(self, images: Image.Image) -> np.ndarray:
        self.calls += 1
        return np.asarray(images, dtype=np.float32).mean(axis=(0, 1))[None, :]


def png(color, size=4) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def near(color, n=6):
    return [[c + i for c in color] for i in range(n)]


@pytest.fixture
def paths(tmp_path):
    """A shipped model that knows red and green cats."""
    base = tmp_path / "models" / "cats"
    base.parent.mkdir()
    X = np.array(near(RED) + near(GREEN), dtype=np.float32)
    save_training_set(base, TrainingSet(X=X, labels=["red"] * 6 + ["green"] * 6, hashes=[f"h{i}" for i in range(12)]))
    return base, tmp_path / "corrections" / "cats", tmp_path / "retrained" / "cats", tmp_path / "retrained" / "embeddings.pkl"


def save(corrections, label, name, data):
    (corrections / label).mkdir(parents=True, exist_ok=True)
    (corrections / label / name).write_bytes(data)


def predict(classifier, color) -> str:
    return classifier.index_to_label[int(classifier.predict(np.array([color], dtype=np.float32))[0])]


def test_training_set_round_trips(paths):
    training_set = load_training_set(paths[0])
    assert training_set.X.shape == (12, 3) and training_set.labels[0] == "red" and training_set.hashes[-1] == "h11"


def test_retrain_learns_a_new_cat_from_saved_photos(paths):
    base, corrections, output, cache = paths
    for i in range(3):
        save(corrections, "blue", f"50{i}_1.png", png((0, 0, 250 + i)))
    save(corrections, "red", "600_1.png", png((250, 0, 0)))
    save(corrections, "red", ".DS_Store", b"finder litter")

    classifier, result = retrain("cats", ColorExtractor(), base, corrections, output, cache)

    assert result.name == "cats" and (result.added, result.skipped) == (4, 0)
    assert result.counts == {"blue": 3, "green": 6, "red": 7}
    assert [predict(classifier, c) for c in (RED, GREEN, BLUE)] == ["red", "green", "blue"]
    # The saved model is what the bot loads after a restart. The shipped one is untouched.
    loaded = Classifier()
    loaded.load(output)
    assert predict(loaded, BLUE) == "blue"
    assert not (base.parent / "cats.pkl").exists()


def test_retrain_with_no_saved_photos_rebuilds_the_shipped_model(paths):
    base, corrections, output, cache = paths
    classifier, result = retrain("cats", ColorExtractor(), base, corrections, output, cache)
    assert result.counts == {"green": 6, "red": 6} and result.added == 0
    assert predict(classifier, RED) == "red"


def test_retrain_skips_unreadable_and_duplicate_photos(paths):
    base, corrections, output, cache = paths
    save(corrections, "blue", "1_1.png", png(BLUE))
    save(corrections, "blue", "2_1.png", png(BLUE))  # The same photo posted twice
    save(corrections, "blue", "3_1.png", b"not an image")
    _, result = retrain("cats", ColorExtractor(), base, corrections, output, cache)
    assert (result.added, result.skipped, result.counts["blue"]) == (1, 1, 1)


def test_retrain_only_embeds_new_photos(paths):
    base, corrections, output, cache = paths
    extractor = ColorExtractor()
    save(corrections, "blue", "1_1.png", png(BLUE))
    retrain("cats", extractor, base, corrections, output, cache)
    save(corrections, "blue", "2_1.png", png((0, 0, 200)))
    retrain("cats", extractor, base, corrections, output, cache)
    assert extractor.calls == 2


def test_retrain_needs_the_shipped_training_set(tmp_path):
    with pytest.raises(FileNotFoundError):
        retrain("cats", ColorExtractor(), tmp_path / "cats", tmp_path / "c", tmp_path / "out" / "cats", tmp_path / "out" / "e.pkl")
    assert not (tmp_path / "out").exists()
