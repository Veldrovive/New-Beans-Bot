"""DINOv3 image embeddings plus a scikit-learn classifier on top of them."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import login
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from transformers import AutoImageProcessor, AutoModel

logger = logging.getLogger(__name__)

CHECKPOINT = "facebook/dinov3-vits16-pretrain-lvd1689m"


class DinoFeatureExtractor:
    def __init__(self, checkpoint: str = CHECKPOINT, device: str = "cpu", hf_token: str | None = None):
        self.device = device
        try:
            self._load(checkpoint)
        except OSError:
            # The model is gated, so log in and try again.
            if hf_token is None:
                raise
            logger.info("DINOv3 is gated; logging in to Hugging Face")
            login(hf_token)
            self._load(checkpoint)
        self.model.eval()

    def _load(self, checkpoint: str) -> None:
        self.processor = AutoImageProcessor.from_pretrained(checkpoint)
        self.model = AutoModel.from_pretrained(checkpoint, dtype=torch.bfloat16, device_map=self.device)

    def extract_features(self, images: Image.Image | list[Image.Image]) -> np.ndarray:
        if isinstance(images, Image.Image):
            images = [images]
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        return outputs.pooler_output.to(torch.float32).cpu().numpy()


class Classifier:
    def __init__(self):
        self.pipeline: Pipeline | None = None
        self.label_to_index: dict[str, int] = {}
        self.index_to_label: dict[int, str] = {}

    def train(self, X, y, label_to_index: dict[str, int]) -> Pipeline:
        self.label_to_index = label_to_index
        self.index_to_label = {v: k for k, v in label_to_index.items()}
        logger.info(f"Training on {len(X)} samples with {len(label_to_index)} classes.")
        classifier = LogisticRegression(solver="lbfgs", class_weight="balanced", C=0.1, max_iter=1000, random_state=42)
        self.pipeline = Pipeline([("scaler", StandardScaler()), ("classifier", classifier)])
        self.pipeline.fit(X, y)
        return self.pipeline

    def predict(self, embeddings):
        if self.pipeline is None:
            raise ValueError("Model not trained or loaded.")
        return self.pipeline.predict(embeddings)

    def predict_proba(self, embeddings):
        if self.pipeline is None:
            raise ValueError("Model not trained or loaded.")
        return self.pipeline.predict_proba(embeddings)

    def save(self, base_path: Path) -> None:
        """Writes <base_path>.pkl (the pipeline) and <base_path>_labels.pkl (labels in index order)."""
        if self.pipeline is None:
            raise ValueError("No model to save.")
        base_path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{base_path}.pkl", "wb") as f:
            pickle.dump(self.pipeline, f)
        labels = [label for label, _ in sorted(self.label_to_index.items(), key=lambda item: item[1])]
        with open(f"{base_path}_labels.pkl", "wb") as f:
            pickle.dump(labels, f)
        logger.info(f"Model saved to {base_path}")

    def load(self, base_path: Path) -> None:
        pipeline_path = Path(f"{base_path}.pkl")
        labels_path = Path(f"{base_path}_labels.pkl")
        if not pipeline_path.exists() or not labels_path.exists():
            raise FileNotFoundError(f"Model files not found at {base_path}")
        with open(pipeline_path, "rb") as f:
            self.pipeline = pickle.load(f)
        with open(labels_path, "rb") as f:
            labels = pickle.load(f)
        self.index_to_label = dict(enumerate(labels))
        self.label_to_index = {label: i for i, label in enumerate(labels)}
