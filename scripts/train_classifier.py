"""
Train and test a classifier for every dataset in the datasets directory.

    uv run --extra classifier scripts/train_classifier.py [--models-dir models/classifier] [--test-fraction 0.2]

Folder layout:
    <models-dir>/datasets/<dataset_name>/<label>/<image>
Images whose names start with "_" or "." are skipped (Finder litters folders with .DS_Store).

The train/test split is automatic. Within each label, images are ordered by file hash and the
first --test-fraction of them are held out for testing (at least one, if the label has two or more).
The split is the same on every run, so scores are comparable, and nobody balances folders by hand.
Duplicate images count once, so a copy can't sit in both sets.

Embeddings are cached by file hash in <models-dir>/cache/embeddings.pkl, so
retraining only embeds new images. Each dataset produces <models-dir>/<dataset_name>.pkl
and <dataset_name>_labels.pkl, plus a confusion matrix and annotated test images
in <models-dir>/test_output/<dataset_name>.
"""

import argparse
import hashlib
import math
import pickle
import shutil
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import tqdm
from PIL import Image
from pillow_heif import register_heif_opener
from sklearn.metrics import ConfusionMatrixDisplay, accuracy_score, confusion_matrix, log_loss

from beans_bot.cogs.classifier.dino import Classifier, DinoFeatureExtractor

register_heif_opener()


def file_hash(path: Path) -> str:
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def embed(path: Path, extractor: DinoFeatureExtractor, cache: dict[str, np.ndarray]) -> np.ndarray:
    key = file_hash(path)
    if key not in cache:
        cache[key] = extractor.extract_features(Image.open(path).convert("RGB"))
    return cache[key]


def annotate_image(image_path: Path, probs: np.ndarray, labels: list[str], output_dir: Path) -> None:
    img = cv2.cvtColor(np.array(Image.open(image_path).convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    img = cv2.resize(img, (1920, int(h * 1920 / w)))

    best = int(np.argmax(probs))
    lines = [f"Prediction: {labels[best]} ({probs[best]:.2%})"]
    lines += [f"{labels[i]}: {probs[i]:.2%}" for i in np.argsort(probs)[::-1]]

    font, scale, thickness = cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3
    for i, line in enumerate(lines):
        y = 50 + i * 50
        (tw, th), _ = cv2.getTextSize(line, font, scale, thickness)
        cv2.rectangle(img, (5, y - th - 10), (5 + tw + 20, y + 10), (0, 0, 0), -1)
        cv2.putText(img, line, (15, y), font, scale, (0, 255, 0), thickness, cv2.LINE_AA)
    cv2.imwrite(str(output_dir / f"{image_path.stem}_annotated.png"), img)


def split_dataset(dataset: Path, test_fraction: float):
    """Labels, plus (train, test) lists of (path, label index), split per label by file hash."""
    images: dict[str, list[Path]] = {}
    for label_dir in sorted(p for p in dataset.iterdir() if p.is_dir()):
        by_hash: dict[str, Path] = {}
        for path in sorted(label_dir.iterdir()):
            if path.is_file() and path.name[0] not in "_.":
                by_hash.setdefault(file_hash(path), path)
        if by_hash:
            images[label_dir.name] = [by_hash[key] for key in sorted(by_hash)]
        else:
            # The model can't learn a label with no images, and the saved labels must match its classes.
            print(f"  Skipping {label_dir.name}: no images")
    labels = list(images)
    train, test = [], []
    for index, label in enumerate(labels):
        paths = images[label]
        # Each label with two or more images gets at least one test image and keeps at least one for training.
        n_test = min(max(math.ceil(len(paths) * test_fraction), 1), len(paths) - 1) if len(paths) > 1 else 0
        print(f"  {label}: {len(paths) - n_test} train, {n_test} test")
        test += [(path, index) for path in paths[:n_test]]
        train += [(path, index) for path in paths[n_test:]]
    return labels, train, test


def embed_all(items: list[tuple[Path, int]], extractor, cache):
    X = [embed(path, extractor, cache).squeeze() for path, _ in tqdm.tqdm(items)]
    return np.array(X), np.array([index for _, index in items])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models-dir", type=Path, default=Path("models/classifier"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--test-fraction", type=float, default=0.2)
    args = parser.parse_args()

    datasets_dir = args.models_dir / "datasets"
    cache_file = args.models_dir / "cache" / "embeddings.pkl"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, np.ndarray] = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}

    extractor = DinoFeatureExtractor(device=args.device)
    for dataset in sorted(p for p in datasets_dir.iterdir() if p.is_dir()):
        if (dataset / "train").is_dir() and (dataset / "test").is_dir():
            raise SystemExit(f"{dataset} still has train/ and test/ folders. Merge them into one folder per label.")
        print(f"\n--- Training {dataset.name} ---")
        labels, train, test = split_dataset(dataset, args.test_fraction)
        X_train, y_train = embed_all(train, extractor, cache)
        cache_file.write_bytes(pickle.dumps(cache))

        classifier = Classifier()
        classifier.train(X_train, y_train, {label: i for i, label in enumerate(labels)})
        classifier.save(args.models_dir / dataset.name)

        print(f"\n--- Testing {dataset.name} ---")
        output_dir = args.models_dir / "test_output" / dataset.name
        shutil.rmtree(output_dir, ignore_errors=True)  # Images can move between splits, so start clean
        output_dir.mkdir(parents=True)
        X_test, y_test = embed_all(test, extractor, cache)
        cache_file.write_bytes(pickle.dumps(cache))
        probs = classifier.predict_proba(X_test)
        for (path, _), label_index, image_probs in zip(test, y_test, probs):
            annotated_dir = output_dir / "annotated_imgs" / labels[label_index]
            annotated_dir.mkdir(parents=True, exist_ok=True)
            annotate_image(path, image_probs, labels, annotated_dir)

        predictions = classifier.predict(X_test)
        print(f"Accuracy: {accuracy_score(y_test, predictions)}")
        print(f"Log loss: {log_loss(y_test, probs, labels=range(len(labels)))}")
        fig, ax = plt.subplots(figsize=(10, 8))
        ConfusionMatrixDisplay(confusion_matrix(y_test, predictions, labels=range(len(labels))), display_labels=labels).plot(ax=ax)
        plt.title(f"Test Set Confusion Matrix for {dataset.name}")
        plt.savefig(output_dir / "confusion_matrix.png")
        plt.close(fig)


if __name__ == "__main__":
    main()
