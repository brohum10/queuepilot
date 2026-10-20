"""Portable TF-IDF + logistic classifiers. JSON/NPZ only; never load pickle."""

from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits


def softmax(logits, temperature=1):
    logits = np.asarray(logits, dtype=np.float64) / temperature
    logits -= logits.max(axis=1, keepdims=True)
    probabilities = np.exp(logits)
    return probabilities / probabilities.sum(axis=1, keepdims=True)


class TextModel:
    def __init__(self, kind):
        if kind not in ("baseline", "candidate"):
            raise ValueError("Unknown model kind.")
        self.kind, self.temperature = kind, 1.0
        self.word = TfidfVectorizer(
            ngram_range=(1, 1 if kind == "baseline" else 2),
            max_features=16000,
            sublinear_tf=True,
            dtype=np.float64,
        )
        self.char = (
            TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(3, 5),
                max_features=14000,
                sublinear_tf=True,
                dtype=np.float64,
            )
            if kind == "candidate"
            else None
        )

    def features(self, texts, fit=False):
        word = self.word.fit_transform(texts) if fit else self.word.transform(texts)
        if self.char is None:
            return word
        char = self.char.fit_transform(texts) if fit else self.char.transform(texts)
        return hstack([word * 0.7, char * 0.3], format="csr")

    def fit(self, texts, labels):
        x = self.features(texts, fit=True)
        with threadpool_limits(limits=1):
            classifier = LogisticRegression(
                C=6, max_iter=300, tol=1e-4, random_state=17
            ).fit(x, labels)
        if classifier.n_iter_.max() >= 300:
            raise RuntimeError(
                "Model did not converge; do not publish incomplete training."
            )
        # Evaluation uses the exact portable float32 weights that inference loads.
        self.coef = classifier.coef_.astype(np.float32)
        self.intercept = classifier.intercept_.astype(np.float32)
        self.labels = classifier.classes_.tolist()
        return self

    def logits(self, texts):
        with threadpool_limits(limits=1):
            return np.asarray(self.features(texts) @ self.coef.T + self.intercept)

    def calibrate(self, texts, labels):
        logits = self.logits(texts)
        actual = np.array([self.labels.index(y) for y in labels])
        grid = np.linspace(0.35, 2.5, 120)
        losses = [
            -np.log(
                np.clip(softmax(logits, t)[np.arange(len(actual)), actual], 1e-15, 1)
            ).mean()
            for t in grid
        ]
        self.temperature = float(grid[int(np.argmin(losses))])
        return {
            "temperature": self.temperature,
            "nll_before": float(
                -np.log(
                    np.clip(softmax(logits)[np.arange(len(actual)), actual], 1e-15, 1)
                ).mean()
            ),
            "nll_after": float(min(losses)),
            "grid": [0.35, 2.5, 120],
        }

    def predict(self, texts):
        if not texts:
            return []
        started = time.perf_counter()
        x = self.features(texts)
        with threadpool_limits(limits=1):
            p = softmax(np.asarray(x @ self.coef.T + self.intercept), self.temperature)
        elapsed = (time.perf_counter() - started) * 1000
        result = []
        for i, row in enumerate(p):
            ranked = np.argsort(-row, kind="stable")[:3]
            result.append(
                {
                    "label": self.labels[int(ranked[0])],
                    "confidence": float(row[ranked[0]]),
                    "margin": float(row[ranked[0]] - row[ranked[1]]),
                    "known_features": int(x[i].nnz),
                    "alternatives": [
                        {"label": self.labels[int(k)], "confidence": float(row[k])}
                        for k in ranked
                    ],
                    "batch_amortized_ms": elapsed / len(texts),
                }
            )
        return result

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        config = {
            "kind": self.kind,
            "temperature": self.temperature,
            "labels": self.labels,
            "word": {
                "vocabulary": {k: int(v) for k, v in self.word.vocabulary_.items()},
                "idf": self.word.idf_.tolist(),
            },
            "char": (
                {
                    "vocabulary": {k: int(v) for k, v in self.char.vocabulary_.items()},
                    "idf": self.char.idf_.tolist(),
                }
                if self.char
                else None
            ),
        }
        (directory / (self.kind + ".json")).write_text(
            json.dumps(config, sort_keys=True) + "\n"
        )
        np.savez_compressed(
            directory / (self.kind + ".npz"), coef=self.coef, intercept=self.intercept
        )

    @classmethod
    def load(cls, directory, kind):
        directory = Path(directory)
        config = json.loads((directory / (kind + ".json")).read_text())
        model = cls(kind)
        model.temperature, model.labels = config["temperature"], config["labels"]
        if not 0.1 <= model.temperature <= 10 or len(model.labels) != len(
            set(model.labels)
        ):
            raise ValueError("Invalid model metadata.")
        for name in ("word", "char"):
            if config[name] is None:
                continue
            vectorizer = getattr(model, name)
            vectorizer.set_params(vocabulary=config[name]["vocabulary"])
            vectorizer.idf_ = np.asarray(config[name]["idf"])
        with np.load(directory / (kind + ".npz"), allow_pickle=False) as weights:
            model.coef, model.intercept = weights["coef"], weights["intercept"]
        expected = len(config["word"]["idf"]) + (
            len(config["char"]["idf"]) if config["char"] else 0
        )
        if (
            model.coef.shape != (len(model.labels), expected)
            or model.intercept.shape != (len(model.labels),)
            or not np.isfinite(model.coef).all()
            or not np.isfinite(model.intercept).all()
        ):
            raise ValueError("Invalid model weights.")
        return model
