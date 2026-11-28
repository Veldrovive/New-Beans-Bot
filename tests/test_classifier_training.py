"""Retraining and scoring, with a fake feature extractor so no model has to be downloaded."""

import io
import pickle

import pytest

pytest.importorskip("torch")  # Only in the classifier extra

import numpy as np
from PIL import Image

from beans_bot.cogs.classifier.dino import Classifier
from beans_bot.cogs.classifier.training import FOLDS, TrainingImage, TrainingSet, assign_folds, load_training_set, save_training_set, train_and_evaluate
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

    def add(self, color, label, data=None) -> TrainingImage:
        n = len(self.images) + 1
        path = self.root / f"{n}.png"
        if data is None:
            buffer = io.BytesIO()
            Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
            data = buffer.getvalue()
        path.write_bytes(data)
        image = TrainingImage(id=n, path=path, label=label, content_hash=f"hash{n:03}")
        self.images.append(image)
        return image

    def several(self, color, label, count):
        for i in range(count):
            self.add(tuple(min(255, c + i) for c in color), label)


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


def test_trains_on_every_photo_and_saves_the_model(photos, paths):
    photos.several(RED, "red", 6)
    photos.several(GREEN, "green", 6)
    photos.several(BLUE, "blue", 3)
    output, cache = paths
    progress = []
    classifier, results = train_and_evaluate(ColorExtractor(), photos.images, output, cache, lambda done, total: progress.append((done, total)))

    assert [predict(classifier, c) for c in (RED, GREEN, BLUE)] == ["red", "green", "blue"]
    # Every photo trains the model, and every photo was scored by a model that hadn't seen it.
    assert (results.train_count, results.val_count, results.accuracy) == (15, 15, 1.0)
    assert results.results["labels"] == ["blue", "green", "red"]
    assert (results.results["method"], results.results["folds"]) == ("cross_validation", FOLDS)
    assert sorted(p["image_id"] for p in results.results["predictions"]) == [i.id for i in photos.images]
    assert progress[-1] == (15, 15) and len(progress) == 15
    loaded = Classifier()
    loaded.load(output)
    assert predict(loaded, BLUE) == "blue"


def test_scores_a_mislabeled_photo_as_a_mistake(photos, paths):
    photos.several(RED, "red", 6)
    photos.several(GREEN, "green", 6)
    wrong = photos.add(GREEN, "red")  # Labeled red, but it's green: a model that hasn't seen it says green
    _, results = train_and_evaluate(ColorExtractor(), photos.images, *paths)

    assert (results.train_count, results.val_count) == (13, 13)
    assert results.accuracy == pytest.approx(12 / 13)
    assert results.log_loss is not None and results.log_loss > 0
    r = results.results
    assert r["labels"] == ["green", "red"]
    assert r["confusion"] == [[6, 0], [1, 6]]  # Rows are the truth, columns the guess
    assert r["per_label"]["green"] == {"train": 6, "val": 6, "precision": pytest.approx(6 / 7), "recall": 1.0}
    assert r["per_label"]["red"] == {"train": 7, "val": 7, "precision": 1.0, "recall": pytest.approx(6 / 7)}
    (mistake,) = [p for p in r["predictions"] if not p["correct"]]
    assert (mistake["image_id"], mistake["label"], mistake["predicted"]) == (wrong.id, "red", "green")
    assert set(mistake["probabilities"]) == {"green", "red"} and mistake["probabilities"]["green"] > 0.5
    assert 0 <= mistake["fold"] < FOLDS


def test_a_single_photo_label_trains_but_is_never_scored(photos, paths):
    photos.several(RED, "red", 4)
    photos.several(GREEN, "green", 4)
    lonely = photos.add(BLUE, "blue")
    classifier, results = train_and_evaluate(ColorExtractor(), photos.images, *paths)

    assert predict(classifier, BLUE) == "blue"  # It still teaches the model
    assert (results.train_count, results.val_count) == (9, 8)
    r = results.results
    assert r["unscored_labels"] == ["blue"]
    assert lonely.id not in {p["image_id"] for p in r["predictions"]}
    assert r["per_label"]["blue"] == {"train": 1, "val": 0, "precision": None, "recall": None}
    # Every fold's model knew blue, so every scored photo has a probability for it.
    assert all(set(p["probabilities"]) == {"blue", "green", "red"} for p in r["predictions"])


def embedded(photos) -> dict[int, np.ndarray]:
    extractor = ColorExtractor()
    return {i.id: extractor.extract_features(Image.open(i.path).convert("RGB")).squeeze() for i in photos.images}


def test_folds_keep_near_duplicates_together(photos):
    # Twelve bursts of three near-identical photos each: more bursts than folds, so some folds hold two.
    bursts = [[photos.add((20 * b, 255 - 20 * b, shade), "cat") for shade in (0, 1, 2)] for b in range(12)]
    photos.several(BLUE, "other", 3)
    folds = assign_folds(photos.images, embedded(photos))

    for burst in bursts:
        assert len({folds[i.id] for i in burst}) == 1  # One of a burst never vouches for another
    cat_folds = [folds[i.id] for burst in bursts for i in burst]
    assert set(cat_folds) == set(range(FOLDS))  # Every fold holds some out, so the rest always has FOLDS - 1 or more
    assert folds == assign_folds(list(reversed(photos.images)), embedded(photos))  # The same photos, the same folds


def test_small_labels_get_one_photo_per_fold_and_folds_stay_even(photos):
    photos.several(RED, "red", 4)
    photos.several(GREEN, "green", 3)
    photos.add(BLUE, "blue")
    folds = assign_folds(photos.images, embedded(photos))

    by_label = {label: [folds[i.id] for i in photos.images if i.label == label and i.id in folds] for label in ("red", "green", "blue")}
    assert len(set(by_label["red"])) == 4 and len(set(by_label["green"])) == 3 and by_label["blue"] == []
    sizes = [list(folds.values()).count(f) for f in range(FOLDS)]
    assert max(sizes) == 1  # Seven photos over ten folds: green went to the folds red left empty


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
    with pytest.raises(RetrainFailed):
        train_and_evaluate(ColorExtractor(), photos.images, *paths)
    assert not paths[0].parent.exists()

