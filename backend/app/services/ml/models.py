"""Prediction models trained on real usage data.

- CapacityModel: expected occupancy (0-1) of a line at a given time, from
  utilisation data (passenger counts / APC exports).
- OnDemandWaitModel: expected pickup wait (minutes) in an on-demand zone, from
  the operator's trip data.

Both share one design: time-of-week features plus a target-encoded key
(route or zone), fitted with gradient boosting. Until a model has been trained
on real data, each falls back to a transparent heuristic so routing still works.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd


def _peak_profile(hour: float, weekday: int) -> float:
    """0-1 commuter peak shape: morning ~07:45, evening ~17:00, flatter at weekends."""
    am = math.exp(-((hour - 7.75) ** 2) / 1.2)
    pm = math.exp(-((hour - 17.0) ** 2) / 2.0)
    scale = 1.0 if weekday < 5 else 0.3
    return scale * max(am, 0.85 * pm)


@dataclass
class _TimeProfileModel:
    """Gradient-boosted regressor over (key, time-of-week) with a heuristic fallback."""

    key_column: ClassVar[str]
    target_column: ClassVar[str]

    estimator: Any = None
    key_means: dict[str, float] = field(default_factory=dict)
    global_mean: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    trained_rows: int = 0

    @property
    def trained(self) -> bool:
        return self.estimator is not None

    def _features(self, keys: list[str], when: pd.Series) -> np.ndarray:
        hour = when.dt.hour + when.dt.minute / 60.0
        weekday = when.dt.weekday
        key_mean = [self.key_means.get(k, self.global_mean) for k in keys]
        return np.column_stack(
            [
                key_mean,
                np.sin(2 * np.pi * hour / 24),
                np.cos(2 * np.pi * hour / 24),
                hour,
                weekday,
                (weekday >= 5).astype(float),
            ]
        )

    def _predict_trained(self, key: str, when: datetime) -> float:
        x = self._features([key], pd.Series([pd.Timestamp(when)]))
        return float(self.estimator.predict(x)[0])

    @classmethod
    def fit(cls, df: pd.DataFrame, timestamp_column: str = "timestamp") -> "_TimeProfileModel":
        """Fit on a DataFrame with key, timestamp and target columns.

        Holds out the most recent 20% of rows (by time) and reports MAE for the
        model and for the heuristic baseline, so a trained model is only trusted
        when it beats the fallback.
        """
        from sklearn.ensemble import HistGradientBoostingRegressor

        df = df.dropna(subset=[cls.key_column, timestamp_column, cls.target_column]).copy()
        df[timestamp_column] = pd.to_datetime(df[timestamp_column])
        df[cls.key_column] = df[cls.key_column].astype(str)
        df = df.sort_values(timestamp_column).reset_index(drop=True)
        if len(df) < 50:
            raise ValueError(f"Need at least 50 rows to train, got {len(df)}")
        split = int(len(df) * 0.8)
        train, test = df.iloc[:split], df.iloc[split:]

        model = cls(
            key_means=train.groupby(cls.key_column)[cls.target_column].mean().to_dict(),
            global_mean=float(train[cls.target_column].mean()),
        )
        estimator = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05)
        estimator.fit(
            model._features(train[cls.key_column].tolist(), train[timestamp_column]),
            train[cls.target_column].to_numpy(),
        )
        model.estimator = estimator

        y = test[cls.target_column].to_numpy()
        pred = estimator.predict(
            model._features(test[cls.key_column].tolist(), test[timestamp_column])
        )
        baseline = np.array(
            [
                model.heuristic(k, ts.to_pydatetime())
                for k, ts in zip(test[cls.key_column], test[timestamp_column], strict=True)
            ]
        )
        model.metrics = {
            "mae": float(np.mean(np.abs(pred - y))),
            "mae_heuristic": float(np.mean(np.abs(baseline - y))),
            "test_rows": float(len(test)),
        }
        # Refit on everything for deployment
        estimator.fit(
            model._features(df[cls.key_column].tolist(), df[timestamp_column]),
            df[cls.target_column].to_numpy(),
        )
        model.key_means = df.groupby(cls.key_column)[cls.target_column].mean().to_dict()
        model.global_mean = float(df[cls.target_column].mean())
        model.trained_rows = len(df)
        return model

    def heuristic(self, key: str, when: datetime) -> float:
        raise NotImplementedError

    def save(self, path: str | Path) -> None:
        import joblib

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @classmethod
    def load_or_default(cls, path: str | Path) -> "_TimeProfileModel":
        """Load a trained model if one exists and sklearn is installed; else the heuristic.

        joblib unpickles, so only point the model paths at files produced by
        `app.services.ml.train` — never at downloaded or user-supplied files.
        """
        path = Path(path)
        if path.exists():
            try:
                import joblib

                model = joblib.load(path)
                metrics = getattr(model, "metrics", {})
                if isinstance(model, cls) and metrics.get("mae", 0) < metrics.get(
                    "mae_heuristic", float("inf")
                ):
                    return model  # Only trust a model that beat the heuristic on held-out data
            except Exception:  # noqa: BLE001 — a bad model file falls back to the heuristic
                pass
        return cls()

    def describe(self) -> dict[str, Any]:
        return {
            "trained": self.trained,
            "trained_rows": self.trained_rows,
            "metrics": self.metrics,
        }


@dataclass
class CapacityModel(_TimeProfileModel):
    """Expected occupancy 0-1 (1 = vehicle full) for a route at a time.

    Training CSV columns: route_id, timestamp, occupancy (0-1).
    """

    key_column: ClassVar[str] = "route_id"
    target_column: ClassVar[str] = "occupancy"

    MODE_BASE: ClassVar[dict[str, float]] = {"train": 0.45, "tram": 0.4, "subway": 0.5, "bus": 0.35}

    def heuristic(self, key: str, when: datetime, mode: str = "bus") -> float:
        base = self.MODE_BASE.get(mode, 0.35)
        hour = when.hour + when.minute / 60
        return min(1.0, base + 0.4 * _peak_profile(hour, when.weekday()))

    def predict(self, route_id: str, when: datetime, mode: str = "bus") -> float:
        if self.trained:
            return float(np.clip(self._predict_trained(route_id, when), 0.0, 1.0))
        return self.heuristic(route_id, when, mode)


@dataclass
class OnDemandWaitModel(_TimeProfileModel):
    """Expected wait in minutes from booking to pickup in an on-demand zone.

    Training CSV columns: zone_id, timestamp (request time), wait_min.
    """

    key_column: ClassVar[str] = "zone_id"
    target_column: ClassVar[str] = "wait_min"

    def heuristic(self, key: str, when: datetime, base_wait: float = 10.0) -> float:
        hour = when.hour + when.minute / 60
        night = 1.4 if hour < 6 or hour >= 22 else 1.0  # Fewer vehicles on duty
        return base_wait * (1 + 0.6 * _peak_profile(hour, when.weekday())) * night

    def predict(self, zone_id: str, when: datetime, base_wait: float = 10.0) -> float:
        if self.trained:
            return max(0.0, self._predict_trained(zone_id, when))
        return self.heuristic(zone_id, when, base_wait)
