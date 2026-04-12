from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import pickle
from statistics import pstdev
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from app.models import Trade


@dataclass(slots=True)
class MLModelState:
    version: int
    trained_at: str
    samples: int
    accuracy: float
    feature_names: list[str]
    weights: dict[str, float]
    bias: float
    model_type: str = "logistic_regression"
    scaler_path: str | None = None
    model_bin_path: str | None = None
    validation_accuracy: float = 0.0
    validation_f1: float = 0.0
    validation_precision: float = 0.0
    validation_recall: float = 0.0
    validation_auc: float = 0.0


class MLSignalService:
    FEATURE_NAMES = [
        "rsi_norm",
        "confidence",
        "momentum_5",
        "momentum_20",
        "volatility_20",
        "atr_pct",
        "strategy_count_norm",
    ]

    def __init__(self, model_path: str):
        self.model_path = Path(model_path)
        self.artifacts_dir = self.model_path.parent
        self.model_bin_path = self.artifacts_dir / "ml_model.pkl"
        self.scaler_path = self.artifacts_dir / "ml_scaler.pkl"
        self.state: MLModelState | None = None
        self._model: LogisticRegression | None = None
        self._scaler: StandardScaler | None = None
        self.last_train_metrics: dict[str, Any] = {
            "status": "not-trained",
            "trained": False,
        }
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            return
        try:
            payload = json.loads(self.model_path.read_text(encoding="utf-8"))
            self.state = MLModelState(
                version=int(payload.get("version", 1)),
                trained_at=str(payload.get("trained_at", "")),
                samples=int(payload.get("samples", 0)),
                accuracy=float(payload.get("accuracy", 0.0)),
                feature_names=list(payload.get("feature_names", self.FEATURE_NAMES)),
                weights={str(k): float(v) for k, v in dict(payload.get("weights", {})).items()},
                bias=float(payload.get("bias", 0.0)),
                model_type=str(payload.get("model_type", "logistic_regression")),
                scaler_path=str(payload.get("scaler_path", str(self.scaler_path))),
                model_bin_path=str(payload.get("model_bin_path", str(self.model_bin_path))),
                validation_accuracy=float(payload.get("validation_accuracy", 0.0)),
                validation_f1=float(payload.get("validation_f1", 0.0)),
                validation_precision=float(payload.get("validation_precision", 0.0)),
                validation_recall=float(payload.get("validation_recall", 0.0)),
                validation_auc=float(payload.get("validation_auc", 0.0)),
            )
            self._try_load_binary_artifacts()
            self.last_train_metrics = {
                "status": "loaded",
                "trained": True,
                "samples": self.state.samples,
                "accuracy": self.state.accuracy,
                "validation_accuracy": self.state.validation_accuracy,
                "validation_f1": self.state.validation_f1,
                "validation_precision": self.state.validation_precision,
                "validation_recall": self.state.validation_recall,
                "validation_auc": self.state.validation_auc,
                "trained_at": self.state.trained_at,
            }
        except Exception:
            self.state = None
            self.last_train_metrics = {
                "status": "load-failed",
                "trained": False,
            }

    def _save(self) -> None:
        if self.state is None:
            return
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.state.version,
            "trained_at": self.state.trained_at,
            "samples": self.state.samples,
            "accuracy": self.state.accuracy,
            "feature_names": self.state.feature_names,
            "weights": self.state.weights,
            "bias": self.state.bias,
            "model_type": self.state.model_type,
            "scaler_path": self.state.scaler_path,
            "model_bin_path": self.state.model_bin_path,
            "validation_accuracy": self.state.validation_accuracy,
            "validation_f1": self.state.validation_f1,
            "validation_precision": self.state.validation_precision,
            "validation_recall": self.state.validation_recall,
            "validation_auc": self.state.validation_auc,
        }
        self.model_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _try_load_binary_artifacts(self) -> None:
        if not self.model_bin_path.exists() or not self.scaler_path.exists():
            return
        try:
            with self.model_bin_path.open("rb") as model_file:
                self._model = pickle.load(model_file)
            with self.scaler_path.open("rb") as scaler_file:
                self._scaler = pickle.load(scaler_file)
        except Exception:
            self._model = None
            self._scaler = None

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = math.exp(-value)
            return 1.0 / (1.0 + z)
        z = math.exp(value)
        return z / (1.0 + z)

    def status(self) -> dict[str, Any]:
        ready = bool(self.state and self.state.samples > 0)
        return {
            "ready": ready,
            "model_path": str(self.model_path),
            "model": {
                "trained_at": self.state.trained_at if self.state else None,
                "samples": self.state.samples if self.state else 0,
                "accuracy": self.state.accuracy if self.state else 0.0,
                "validation_accuracy": self.state.validation_accuracy if self.state else 0.0,
                "validation_f1": self.state.validation_f1 if self.state else 0.0,
                "validation_precision": self.state.validation_precision if self.state else 0.0,
                "validation_recall": self.state.validation_recall if self.state else 0.0,
                "validation_auc": self.state.validation_auc if self.state else 0.0,
                "features": self.state.feature_names if self.state else self.FEATURE_NAMES,
                "model_type": self.state.model_type if self.state else "logistic_regression",
            },
            "last_train": self.last_train_metrics,
        }

    def _to_feature_matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        matrix = [[float(row.get(name, 0.0)) for name in self.FEATURE_NAMES] for row in rows]
        return np.asarray(matrix, dtype=float)

    def build_live_features(
        self,
        series: dict[str, list[float]],
        confidence: float,
        rsi: float,
        strategy_count: int,
    ) -> dict[str, float]:
        closes = series.get("closes", [])
        highs = series.get("highs", [])
        lows = series.get("lows", [])
        if len(closes) < 25:
            return {name: 0.0 for name in self.FEATURE_NAMES}

        momentum_5 = (closes[-1] / closes[-6] - 1.0) if closes[-6] > 0 else 0.0
        momentum_20 = (closes[-1] / closes[-21] - 1.0) if closes[-21] > 0 else 0.0
        returns = [(curr / prev - 1.0) for prev, curr in zip(closes[-21:-1], closes[-20:]) if prev > 0]
        volatility_20 = pstdev(returns) if len(returns) >= 2 else 0.0

        ranges = [
            max(high - low, abs(high - prev_close), abs(low - prev_close))
            for high, low, prev_close in zip(highs[-14:], lows[-14:], closes[-15:-1])
        ]
        atr = (sum(ranges) / len(ranges)) if ranges else 0.0
        atr_pct = atr / max(closes[-1], 1e-9)

        return {
            "rsi_norm": max(0.0, min(1.0, rsi / 100.0)),
            "confidence": max(0.0, min(1.0, confidence)),
            "momentum_5": momentum_5,
            "momentum_20": momentum_20,
            "volatility_20": volatility_20,
            "atr_pct": atr_pct,
            "strategy_count_norm": max(0.0, min(1.0, strategy_count / 10.0)),
        }

    def predict_up_probability(self, feature_map: dict[str, float]) -> float | None:
        if self.state is None:
            return None
        if self._model is not None and self._scaler is not None:
            vector = np.asarray([[float(feature_map.get(name, 0.0)) for name in self.FEATURE_NAMES]], dtype=float)
            scaled = self._scaler.transform(vector)
            return float(self._model.predict_proba(scaled)[0, 1])
        score = self.state.bias
        for name in self.state.feature_names:
            score += float(self.state.weights.get(name, 0.0)) * float(feature_map.get(name, 0.0))
        return self._sigmoid(score)

    @staticmethod
    def action_from_probability(
        probability_up: float | None,
        buy_threshold: float,
        sell_threshold: float,
    ) -> str:
        if probability_up is None:
            return "HOLD"
        if probability_up >= buy_threshold:
            return "BUY"
        if probability_up <= sell_threshold:
            return "SELL"
        return "HOLD"

    def train_from_trades(
        self,
        trades: list[Trade],
        min_samples: int,
        epochs: int,
        learning_rate: float,
        validation_size: float = 0.2,
        random_state: int = 42,
    ) -> dict[str, Any]:
        ordered = sorted(trades, key=lambda item: item.created_at)
        by_symbol: dict[str, list[Trade]] = {}
        for trade in ordered:
            symbol = trade.execution_symbol or trade.signal_symbol or trade.symbol
            by_symbol.setdefault(symbol, []).append(trade)

        samples_x: list[dict[str, float]] = []
        samples_y: list[int] = []

        for sequence in by_symbol.values():
            open_buys: list[Trade] = []
            for trade in sequence:
                if trade.side == "BUY":
                    open_buys.append(trade)
                    continue
                if trade.side != "SELL" or not open_buys:
                    continue
                buy = open_buys.pop(0)
                buy_price = float(buy.fill_price or buy.price or 0.0)
                sell_price = float(trade.fill_price or trade.price or 0.0)
                if buy_price <= 0 or sell_price <= 0:
                    continue
                pnl = sell_price - buy_price
                if buy.fee_amount:
                    pnl -= float(buy.fee_amount) / max(float(buy.quantity or 0.0), 1e-9)
                if trade.fee_amount:
                    pnl -= float(trade.fee_amount) / max(float(trade.quantity or 0.0), 1e-9)
                label = 1 if pnl > 0 else 0

                strategy_count = 0
                if buy.strategy_weights:
                    try:
                        strategy_count = len(json.loads(buy.strategy_weights))
                    except Exception:
                        strategy_count = 0

                feature_row = {
                    "rsi_norm": max(0.0, min(1.0, float(buy.rsi_value or 50.0) / 100.0)),
                    "confidence": max(0.0, min(1.0, float(buy.confidence or 0.5))),
                    "momentum_5": 0.0,
                    "momentum_20": 0.0,
                    "volatility_20": 0.0,
                    "atr_pct": abs((float(buy.entry_take_profit or buy_price) - float(buy.entry_stop_loss or buy_price)) / max(buy_price, 1e-9)),
                    "strategy_count_norm": max(0.0, min(1.0, strategy_count / 10.0)),
                }
                samples_x.append(feature_row)
                samples_y.append(label)

        if len(samples_x) < min_samples:
            self.last_train_metrics = {
                "status": "insufficient-samples",
                "trained": False,
                "samples": len(samples_x),
                "required": min_samples,
            }
            return self.last_train_metrics

        x = self._to_feature_matrix(samples_x)
        y = np.asarray(samples_y, dtype=int)
        total = int(x.shape[0])

        stratify = y if len(set(y.tolist())) > 1 else None
        test_size = min(max(validation_size, 0.1), 0.4)
        x_train, x_val, y_train, y_val = train_test_split(
            x,
            y,
            test_size=test_size,
            random_state=random_state,
            stratify=stratify,
        )

        scaler = StandardScaler()
        x_train_scaled = scaler.fit_transform(x_train)
        x_val_scaled = scaler.transform(x_val)

        inv_reg_strength = max(0.001, min(1000.0, 1.0 / max(learning_rate, 1e-6)))
        model = LogisticRegression(
            max_iter=max(100, epochs),
            solver="lbfgs",
            C=inv_reg_strength,
            random_state=random_state,
        )
        model.fit(x_train_scaled, y_train)

        train_probs = model.predict_proba(x_train_scaled)[:, 1]
        val_probs = model.predict_proba(x_val_scaled)[:, 1]
        train_preds = (train_probs >= 0.5).astype(int)
        val_preds = (val_probs >= 0.5).astype(int)

        accuracy = float(accuracy_score(train_preds, y_train))
        validation_accuracy = float(accuracy_score(val_preds, y_val))
        validation_f1 = float(f1_score(y_val, val_preds, zero_division=0))
        validation_precision = float(precision_score(y_val, val_preds, zero_division=0))
        validation_recall = float(recall_score(y_val, val_preds, zero_division=0))
        validation_auc = float(roc_auc_score(y_val, val_probs)) if len(set(y_val.tolist())) > 1 else 0.0

        weights = {name: float(model.coef_[0][idx]) for idx, name in enumerate(self.FEATURE_NAMES)}
        bias = float(model.intercept_[0])

        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        with self.model_bin_path.open("wb") as model_file:
            pickle.dump(model, model_file)
        with self.scaler_path.open("wb") as scaler_file:
            pickle.dump(scaler, scaler_file)

        self.state = MLModelState(
            version=1,
            trained_at=datetime.now(UTC).isoformat(),
            samples=total,
            accuracy=accuracy,
            feature_names=list(self.FEATURE_NAMES),
            weights=weights,
            bias=bias,
            model_type="logistic_regression",
            scaler_path=str(self.scaler_path),
            model_bin_path=str(self.model_bin_path),
            validation_accuracy=validation_accuracy,
            validation_f1=validation_f1,
            validation_precision=validation_precision,
            validation_recall=validation_recall,
            validation_auc=validation_auc,
        )
        self._model = model
        self._scaler = scaler
        self._save()
        self.last_train_metrics = {
            "status": "trained",
            "trained": True,
            "samples": total,
            "accuracy": round(accuracy, 4),
            "validation_accuracy": round(validation_accuracy, 4),
            "validation_f1": round(validation_f1, 4),
            "validation_precision": round(validation_precision, 4),
            "validation_recall": round(validation_recall, 4),
            "validation_auc": round(validation_auc, 4),
            "trained_at": self.state.trained_at,
        }
        return self.last_train_metrics
