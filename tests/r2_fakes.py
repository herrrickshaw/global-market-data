"""Offline stand-ins for sklearn / scipy.optimize / the Excel writer (CI lacks all three)."""

import types

import numpy as np
import pandas as pd

from tests.w4_helpers import fake_module


class StandardScaler:
    def fit_transform(self, X):
        X = np.asarray(X, dtype=float)
        sd = X.std(axis=0)
        sd[sd == 0] = 1.0
        return (X - X.mean(axis=0)) / sd


class KMeans:
    last = None

    def __init__(self, n_clusters=8, random_state=None, n_init=10):
        self.n_clusters, self.random_state, self.n_init = n_clusters, random_state, n_init
        KMeans.last = self

    def fit_predict(self, X):
        return np.arange(len(X)) % self.n_clusters


class PCA:
    last = None

    def __init__(self, n_components=2):
        self.n_components = n_components
        PCA.last = self

    def fit_transform(self, X):
        k = self.n_components
        self.explained_variance_ratio_ = np.full(k, 1.0 / k)
        return np.asarray(X)[:, :k]


class DBSCAN:
    last = None

    def __init__(self, eps=0.5, min_samples=5):
        self.eps, self.min_samples = eps, min_samples
        DBSCAN.last = self

    def fit(self, X):
        n = len(X)
        self.labels_ = np.array([-1 if i % 7 == 0 else 0 for i in range(n)])
        return self


class GradientBoostingRegressor:
    last = None
    mode = "mean"  # "mean" -> r2 ~ 0 ; "perfect" -> predicts feature 0

    def __init__(self, **kw):
        self.kw = kw
        GradientBoostingRegressor.last = self

    def fit(self, X, y):
        self.mean_ = float(np.mean(y))
        self.k_ = np.asarray(X).shape[1]
        self.y_ = np.asarray(y)
        return self

    def predict(self, X):
        n = len(X)
        if GradientBoostingRegressor.mode == "perfect":
            return np.asarray(X)[:, 0]
        return np.full(n, self.mean_)

    @property
    def feature_importances_(self):
        v = np.arange(1, self.k_ + 1, dtype=float)
        return v / v.sum()


def train_test_split(X, y, test_size=0.25, random_state=None):
    n = len(X)
    nt = int(np.ceil(test_size * n))
    return X[nt:], X[:nt], y[nt:], y[:nt]


def r2_score(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    den = ((y - y.mean()) ** 2).sum()
    return 1 - ((y - p) ** 2).sum() / den if den else 0.0


def mean_absolute_error(y, p):
    return float(np.mean(np.abs(np.asarray(y, float) - np.asarray(p, float))))


def install_sklearn(monkeypatch):
    fake_module(monkeypatch, "sklearn")
    fake_module(monkeypatch, "sklearn.cluster", DBSCAN=DBSCAN, KMeans=KMeans)
    fake_module(monkeypatch, "sklearn.decomposition", PCA=PCA)
    fake_module(
        monkeypatch, "sklearn.ensemble", GradientBoostingRegressor=GradientBoostingRegressor
    )
    fake_module(
        monkeypatch,
        "sklearn.metrics",
        mean_absolute_error=mean_absolute_error,
        r2_score=r2_score,
    )
    fake_module(monkeypatch, "sklearn.model_selection", train_test_split=train_test_split)
    fake_module(monkeypatch, "sklearn.preprocessing", StandardScaler=StandardScaler)
    GradientBoostingRegressor.mode = "mean"


class FakeExcel:
    """Records every to_excel call instead of needing openpyxl."""

    def __init__(self):
        self.writers = []  # list of (path, engine, {sheet: DataFrame})

    def install(self, monkeypatch):
        outer = self

        class Writer:
            def __init__(self, path, engine=None, **kw):
                self.path, self.engine, self.sheets = path, engine, {}
                outer.writers.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                from pathlib import Path

                Path(self.path).write_bytes(b"xlsx")
                return False

        def to_excel(df, w, sheet_name=None, index=True, **kw):
            w.sheets[sheet_name] = df.copy()

        monkeypatch.setattr(pd, "ExcelWriter", Writer)
        monkeypatch.setattr(pd.DataFrame, "to_excel", to_excel)
        return self


def install_scipy_minimize(monkeypatch, fn):
    fake_module(monkeypatch, "scipy")
    opt = fake_module(monkeypatch, "scipy.optimize", minimize=fn)
    return types.SimpleNamespace(optimize=opt)
