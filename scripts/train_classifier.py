"""
Train and test a classifier for every dataset in the datasets directory.

    uv run --extra classifier scripts/train_classifier.py [--models-dir models/classifier]

Folder layout:
    <models-dir>/datasets/<dataset_name>/{train,test}/<label>/<image>

Embeddings are cached by file hash in <models-dir>/cache/embeddings.pkl, so
retraining only embeds new images. Each dataset produces <models-dir>/<dataset_name>.pkl
and <dataset_name>_labels.pkl, plus a confusion matrix and annotated test images
in <dataset>/test_output.
"""

import argparse
import hashlib
import pickle
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


def load_split(split_dir: Path, labels: list[str], extractor, cache, skip_hidden: bool):
    X, y, paths = [], [], []
    for index, label in enumerate(labels):
        print(f"Extracting embeddings for {split_dir.name}/{label}...")
        for path in tqdm.tqdm(sorted((split_dir / label).iterdir())):
            if not path.is_file() or (skip_hidden and path.stem[0] in "_."):
                continue
            X.append(embed(path, extractor, cache).squeeze())
            y.append(index)
            paths.append(path)
    return np.array(X), np.array(y), paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models-dir", type=Path, default=Path("models/classifier"))
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    datasets_dir = args.models_dir / "datasets"
    cache_file = args.models_dir / "cache" / "embeddings.pkl"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, np.ndarray] = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}

    extractor = DinoFeatureExtractor(device=args.device)
    for dataset in sorted(p for p in datasets_dir.iterdir() if p.is_dir()):
        labels = sorted(p.name for p in (dataset / "train").iterdir() if p.is_dir())
        print(f"\n--- Training {dataset.name} ({len(labels)} labels: {', '.join(labels)}) ---")
        X_train, y_train, _ = load_split(dataset / "train", labels, extractor, cache, skip_hidden=True)
        cache_file.write_bytes(pickle.dumps(cache))

        classifier = Classifier()
        classifier.train(X_train, y_train, {label: i for i, label in enumerate(labels)})
        classifier.save(args.models_dir / dataset.name)

        print(f"\n--- Testing {dataset.name} ---")
        output_dir = dataset / "test_output"
        X_test, y_test, test_paths = load_split(dataset / "test", labels, extractor, cache, skip_hidden=False)
        cache_file.write_bytes(pickle.dumps(cache))
        probs = classifier.predict_proba(X_test)
        for path, label_index, image_probs in zip(test_paths, y_test, probs):
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
