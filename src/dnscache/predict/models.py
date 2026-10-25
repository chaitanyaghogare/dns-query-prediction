"""Refresh predictors: will this entry be used again if we refresh it?

Three small, explainable models are compared offline (precision / recall /
F1 / ROC-AUC). The decision tree is used in the cache because its rules can
be printed and read, and it is fast: ``TreePredictor`` walks the fitted tree
directly instead of calling scikit-learn for every decision.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.naive_bayes import GaussianNB
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

from .features import FEATURES


def make_models(seed: int = 0) -> dict:
    return {
        "decision_tree": DecisionTreeClassifier(max_depth=5, min_samples_leaf=50, random_state=seed),
        "naive_bayes": GaussianNB(),
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
    }


def _log_features(X: np.ndarray) -> np.ndarray:
    # NB and LR cope badly with heavy-tailed counts/times; the tree does not care.
    return np.log1p(np.clip(X, 0, None))


def train_and_evaluate(X_tr, y_tr, X_va, y_va, threshold: float = 0.5, seed: int = 0) -> tuple[dict, dict]:
    models, report = make_models(seed), {}
    for name, m in models.items():
        f = (lambda a: a) if name == "decision_tree" else _log_features
        m.fit(f(X_tr), y_tr)
        p = m.predict_proba(f(X_va))[:, 1]
        yhat = (p >= threshold).astype(int)
        report[name] = {
            "precision": float(precision_score(y_va, yhat, zero_division=0)),
            "recall": float(recall_score(y_va, yhat, zero_division=0)),
            "f1": float(f1_score(y_va, yhat, zero_division=0)),
            "roc_auc": float(roc_auc_score(y_va, p)) if len(set(y_va)) > 1 else None,
            "positive_rate": float(yhat.mean()),
        }
    return models, report


class TreePredictor:
    """Pure-Python evaluation of a fitted sklearn DecisionTreeClassifier (~µs per call)."""

    def __init__(self, tree: DecisionTreeClassifier):
        t = tree.tree_
        self.left = t.children_left.tolist()
        self.right = t.children_right.tolist()
        self.feat = t.feature.tolist()
        self.thr = t.threshold.tolist()
        v = t.value[:, 0, :]
        v = v / v.sum(axis=1, keepdims=True)
        self.prob = v[:, 1].tolist() if v.shape[1] > 1 else [0.0] * len(v)

    def __call__(self, x) -> float:
        n = 0
        left, right, feat, thr = self.left, self.right, self.feat, self.thr
        while left[n] != -1:
            n = left[n] if x[feat[n]] <= thr[n] else right[n]
        return self.prob[n]


def tree_rules(tree: DecisionTreeClassifier) -> str:
    return export_text(tree, feature_names=FEATURES, show_weights=False, decimals=2)


def feature_importance(tree: DecisionTreeClassifier) -> list[tuple[str, float]]:
    return sorted(((f, float(i)) for f, i in zip(FEATURES, tree.feature_importances_)), key=lambda x: -x[1])


def save(model, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: str | Path):
    with open(path, "rb") as f:
        return pickle.load(f)
