from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

from app.config import Settings
from app.services.indicators import calculate_rsi


@dataclass(slots=True)
class StrategySignal:
    name: str
    action: str
    confidence: float
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    entry_rule: str
    exit_rule: str


@dataclass(slots=True)
class StrategyDecision:
    action: str
    confidence: float
    stop_loss: float | None
    take_profit: float | None
    selected_strategies: list[StrategySignal]
    all_strategies: list[StrategySignal]
    regime: str


class StrategyEngine:
    LEGACY_STRATEGY_ALIASES: dict[str, str] = {
        "news-trading": "sentiment-bias",
        "ai-pattern-recognition": "pattern-heuristic",
    }

    STRATEGY_DEFINITIONS: tuple[dict[str, str], ...] = (
        {"name": "trend-following", "description": "EMA20/EMA50 trend alignment."},
        {"name": "breakout", "description": "Support and resistance breakout detection."},
        {"name": "scalping", "description": "Fast EMA momentum with volume confirmation."},
        {"name": "mean-reversion", "description": "RSI plus Bollinger-style reversion setup."},
        {"name": "momentum", "description": "MACD histogram and volume spike momentum."},
        {"name": "smart-money-concepts", "description": "Liquidity grab and rejection logic."},
        {"name": "grid", "description": "Grid boundary entries around fair value."},
        {"name": "sentiment-bias", "description": "Manual sentiment-bias directional filter."},
        {"name": "pattern-heuristic", "description": "Heuristic pattern score from drift, volatility, and volume."},
        {"name": "multi-timeframe-confluence", "description": "EMA stack and RSI agreement."},
        {"name": "pivot-breakout", "description": "LuxAlgo-style confirmed pivot S/R breakout with volume oscillator gate."},
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self._strategy_builders = {
            "trend-following": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._trend_following(closes, atr_pct),
            "breakout": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._breakout_strategy(closes, highs, lows, atr_pct),
            "scalping": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._scalping_strategy(closes, volumes, timeframe, atr_pct),
            "mean-reversion": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._mean_reversion(closes, atr_pct),
            "momentum": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._momentum_strategy(closes, volumes, atr_pct),
            "smart-money-concepts": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._smart_money_concepts(closes, highs, lows, atr_pct),
            "grid": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._grid_strategy(closes, atr_pct),
            "sentiment-bias": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._news_strategy(price, atr_pct),
            "pattern-heuristic": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._ai_pattern_strategy(closes, highs, lows, volumes, atr_pct),
            "multi-timeframe-confluence": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._multi_timeframe_confluence(closes, atr_pct),
            "pivot-breakout": lambda closes, highs, lows, volumes, timeframe, atr_pct, price: self._pivot_breakout(closes, highs, lows, volumes, atr_pct),
        }
        configured = [self.LEGACY_STRATEGY_ALIASES.get(name, name) for name in self.settings.effective_enabled_strategies]
        self._active_strategy_names = configured
        invalid = [name for name in self._active_strategy_names if name not in self._strategy_builders]
        if invalid:
            raise ValueError(f"Unsupported strategy names configured: {', '.join(invalid)}")

    def strategy_catalog(self) -> list[dict[str, str | bool]]:
        enabled = set(self.active_strategy_names)
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "enabled": item["name"] in enabled,
            }
            for item in self.STRATEGY_DEFINITIONS
        ]

    @property
    def active_strategy_names(self) -> list[str]:
        configured = self._active_strategy_names
        if configured:
            return configured
        return [item["name"] for item in self.STRATEGY_DEFINITIONS]

    def evaluate(self, series: dict[str, list[float]], timeframe: str) -> StrategyDecision:
        closes = series["closes"]
        highs = series["highs"]
        lows = series["lows"]
        volumes = series["volumes"]

        if len(closes) < 60:
            signal = self._build_signal(
                name="fallback-rsi",
                action="HOLD",
                confidence=0.2,
                price=closes[-1],
                atr_pct=self._atr_pct(highs, lows, closes),
                entry_rule="Need at least 60 candles for full strategy stack.",
                exit_rule="Wait for enough history.",
            )
            return StrategyDecision(
                action="HOLD",
                confidence=signal.confidence,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                selected_strategies=[signal],
                all_strategies=[signal],
                regime="insufficient-data",
            )

        price = closes[-1]
        atr_pct = self._atr_pct(highs, lows, closes)
        regime = self._detect_regime(closes, highs, lows, volumes)

        strategy_signals = [
            self._strategy_builders[name](closes, highs, lows, volumes, timeframe, atr_pct, price)
            for name in self.active_strategy_names
        ]

        weighted_signals = self._weight_by_regime(strategy_signals, regime)
        net_score = sum(weighted_signals)

        if net_score > 0.20:
            action = "BUY"
        elif net_score < -0.20:
            action = "SELL"
        else:
            action = "HOLD"

        selected = sorted(
            strategy_signals,
            key=lambda item: item.confidence,
            reverse=True,
        )[:4]

        confidence = min(0.99, abs(net_score))
        stop_loss, take_profit = self._combine_risk_levels(selected, action, price, atr_pct)

        return StrategyDecision(
            action=action,
            confidence=round(confidence, 4),
            stop_loss=stop_loss,
            take_profit=take_profit,
            selected_strategies=selected,
            all_strategies=strategy_signals,
            regime=regime,
        )

    def _detect_regime(self, closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> str:
        ema_fast = self._ema(closes, 20)
        ema_slow = self._ema(closes, 50)
        atr_pct = self._atr_pct(highs, lows, closes)
        volatility = self._volatility_pct(closes)
        volume_spike = volumes[-1] > (mean(volumes[-20:]) * 1.6)

        if volume_spike and atr_pct > 0.004:
            return "breakout"
        if abs(ema_fast - ema_slow) / max(closes[-1], 1e-9) > 0.002:
            return "trend"
        if volatility < 0.0025:
            return "range"
        if atr_pct > 0.006:
            return "volatile"
        return "balanced"

    def _weight_by_regime(self, signals: list[StrategySignal], regime: str) -> list[float]:
        weights_map: dict[str, dict[str, float]] = {
            "trend": {
                "trend-following": 1.4,
                "momentum": 1.3,
                "multi-timeframe-confluence": 1.2,
                "mean-reversion": 0.7,
                "grid": 0.6,
            },
            "breakout": {
                "breakout": 1.5,
                "pivot-breakout": 1.45,
                "smart-money-concepts": 1.25,
                "momentum": 1.2,
                "scalping": 1.0,
            },
            "range": {
                "mean-reversion": 1.5,
                "grid": 1.4,
                "scalping": 1.1,
                "trend-following": 0.6,
            },
            "volatile": {
                "smart-money-concepts": 1.3,
                "breakout": 1.2,
                "ai-pattern-recognition": 1.1,
                "grid": 0.7,
            },
            "balanced": {},
        }

        regime_weights = weights_map.get(regime, {})
        scores: list[float] = []
        for signal in signals:
            sign = 1.0 if signal.action == "BUY" else -1.0 if signal.action == "SELL" else 0.0
            weight = regime_weights.get(signal.name, 1.0)
            scores.append(sign * signal.confidence * weight)
        return scores

    def _combine_risk_levels(
        self,
        selected: list[StrategySignal],
        action: str,
        price: float,
        atr_pct: float,
    ) -> tuple[float | None, float | None]:
        if action not in {"BUY", "SELL"}:
            return None, None

        candidates = [
            item for item in selected if item.action == action and item.stop_loss is not None and item.take_profit is not None
        ]
        if candidates:
            stop_loss = mean([item.stop_loss for item in candidates if item.stop_loss is not None])
            take_profit = mean([item.take_profit for item in candidates if item.take_profit is not None])
            return round(stop_loss, 5), round(take_profit, 5)

        if action == "BUY":
            stop_loss = price * (1 - max(atr_pct * 1.2, 0.0015))
            take_profit = price * (1 + max(atr_pct * 2.4, 0.0030))
        else:
            stop_loss = price * (1 + max(atr_pct * 1.2, 0.0015))
            take_profit = price * (1 - max(atr_pct * 2.4, 0.0030))
        return round(stop_loss, 5), round(take_profit, 5)

    def _trend_following(self, closes: list[float], atr_pct: float) -> StrategySignal:
        ema_fast = self._ema(closes, 20)
        ema_slow = self._ema(closes, 50)
        trend_strength = abs(ema_fast - ema_slow) / max(closes[-1], 1e-9)
        confidence = min(0.95, 0.35 + (trend_strength * 90))
        action = "BUY" if ema_fast > ema_slow else "SELL" if ema_fast < ema_slow else "HOLD"
        return self._build_signal(
            "trend-following",
            action,
            confidence,
            closes[-1],
            atr_pct,
            "Entry on EMA20/EMA50 direction alignment.",
            "Exit on opposite EMA crossover or SL/TP.",
        )

    def _breakout_strategy(self, closes: list[float], highs: list[float], lows: list[float], atr_pct: float) -> StrategySignal:
        lookback = 40
        resistance = max(highs[-lookback:])
        support = min(lows[-lookback:])
        price = closes[-1]
        if price > resistance * 0.9997:
            action = "BUY"
            confidence = 0.65
        elif price < support * 1.0003:
            action = "SELL"
            confidence = 0.65
        else:
            action = "HOLD"
            confidence = 0.25
        return self._build_signal(
            "breakout",
            action,
            confidence,
            price,
            atr_pct,
            "Entry when price breaks support/resistance zone.",
            "Exit on failed breakout or SL/TP.",
        )

    def _scalping_strategy(self, closes: list[float], volumes: list[float], timeframe: str, atr_pct: float) -> StrategySignal:
        ema_fast = self._ema(closes, 5)
        ema_slow = self._ema(closes, 13)
        vol_ratio = volumes[-1] / max(mean(volumes[-20:]), 1e-9)
        fast_tf_bonus = 0.1 if timeframe in {"1m", "5m", "15m"} else 0.0
        confidence = min(0.9, 0.3 + fast_tf_bonus + max(0, vol_ratio - 1.0) * 0.15)
        action = "BUY" if ema_fast > ema_slow else "SELL" if ema_fast < ema_slow else "HOLD"
        return self._build_signal(
            "scalping",
            action,
            confidence,
            closes[-1],
            atr_pct,
            "Entry on micro EMA momentum with volume confirmation.",
            "Exit quickly on momentum loss or SL/TP.",
        )

    def _mean_reversion(self, closes: list[float], atr_pct: float) -> StrategySignal:
        price = closes[-1]
        rsi = self._safe_rsi(closes, period=14)
        basis = mean(closes[-20:])
        std = pstdev(closes[-20:]) if len(closes) >= 20 else 0.0
        upper = basis + (2 * std)
        lower = basis - (2 * std)

        if rsi <= 32 and price <= lower:
            action = "BUY"
            confidence = 0.72
        elif rsi >= 68 and price >= upper:
            action = "SELL"
            confidence = 0.72
        else:
            action = "HOLD"
            confidence = 0.3
        return self._build_signal(
            "mean-reversion",
            action,
            confidence,
            price,
            atr_pct,
            "Entry when RSI and Bollinger extremes signal reversion.",
            "Exit near mean reversion target or SL/TP.",
        )

    def _momentum_strategy(self, closes: list[float], volumes: list[float], atr_pct: float) -> StrategySignal:
        macd, signal_line = self._macd(closes)
        hist = macd - signal_line
        vol_ratio = volumes[-1] / max(mean(volumes[-20:]), 1e-9)
        confidence = min(0.92, 0.35 + abs(hist) * 20 + max(0, vol_ratio - 1.0) * 0.1)
        if hist > 0 and vol_ratio > 1.1:
            action = "BUY"
        elif hist < 0 and vol_ratio > 1.1:
            action = "SELL"
        else:
            action = "HOLD"
            confidence = 0.28
        return self._build_signal(
            "momentum",
            action,
            confidence,
            closes[-1],
            atr_pct,
            "Entry on MACD momentum and volume spike.",
            "Exit when MACD histogram flips or SL/TP.",
        )

    def _smart_money_concepts(self, closes: list[float], highs: list[float], lows: list[float], atr_pct: float) -> StrategySignal:
        price = closes[-1]
        prev_high = max(highs[-25:-1])
        prev_low = min(lows[-25:-1])
        grabbed_liquidity_down = lows[-1] < prev_low and closes[-1] > closes[-2]
        grabbed_liquidity_up = highs[-1] > prev_high and closes[-1] < closes[-2]
        if grabbed_liquidity_down:
            action = "BUY"
            confidence = 0.68
        elif grabbed_liquidity_up:
            action = "SELL"
            confidence = 0.68
        else:
            action = "HOLD"
            confidence = 0.25
        return self._build_signal(
            "smart-money-concepts",
            action,
            confidence,
            price,
            atr_pct,
            "Entry after liquidity grab and rejection.",
            "Exit at opposing liquidity zone or SL/TP.",
        )

    def _grid_strategy(self, closes: list[float], atr_pct: float) -> StrategySignal:
        price = closes[-1]
        center = mean(closes[-30:])
        grid_step = max(center * max(atr_pct, 0.001), center * 0.0008)
        lower_grid = center - grid_step
        upper_grid = center + grid_step

        if price <= lower_grid:
            action = "BUY"
            confidence = 0.55
        elif price >= upper_grid:
            action = "SELL"
            confidence = 0.55
        else:
            action = "HOLD"
            confidence = 0.22
        return self._build_signal(
            "grid",
            action,
            confidence,
            price,
            atr_pct,
            "Entry at grid boundaries around fair value.",
            "Exit at opposite grid level or SL/TP.",
        )

    def _news_strategy(self, price: float, atr_pct: float) -> StrategySignal:
        bias = 0.0
        if self.settings.news_sentiment_bias:
            try:
                bias = float(self.settings.news_sentiment_bias)
            except ValueError:
                bias = 0.0

        if bias > 0.25:
            action = "BUY"
            confidence = min(0.8, 0.4 + abs(bias) * 0.5)
        elif bias < -0.25:
            action = "SELL"
            confidence = min(0.8, 0.4 + abs(bias) * 0.5)
        else:
            action = "HOLD"
            confidence = 0.2

        return self._build_signal(
            "sentiment-bias",
            action,
            confidence,
            price,
            atr_pct,
            "Entry on strong positive/negative news sentiment input.",
            "Exit on sentiment fade or SL/TP.",
        )

    def _ai_pattern_strategy(
        self,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
        atr_pct: float,
    ) -> StrategySignal:
        returns = [((curr / prev) - 1) for prev, curr in zip(closes[-30:-1], closes[-29:]) if prev]
        drift = mean(returns) if returns else 0.0
        vol = pstdev(returns) if len(returns) > 2 else 0.0
        volume_impulse = volumes[-1] / max(mean(volumes[-20:]), 1e-9)
        pattern_score = (drift * 1000) + (volume_impulse - 1.0) - (vol * 200)

        if pattern_score > 0.45:
            action = "BUY"
        elif pattern_score < -0.45:
            action = "SELL"
        else:
            action = "HOLD"

        confidence = min(0.85, 0.3 + abs(pattern_score) * 0.4)

        return self._build_signal(
            "pattern-heuristic",
            action,
            confidence,
            closes[-1],
            atr_pct,
            "Entry when pattern score predicts directional continuation.",
            "Exit when pattern score weakens or SL/TP.",
        )

    def _multi_timeframe_confluence(self, closes: list[float], atr_pct: float) -> StrategySignal:
        price = closes[-1]
        short = self._ema(closes, 20)
        mid = self._ema(closes, 50)
        long = self._ema(closes, 200)
        rsi = self._safe_rsi(closes, period=14)

        if short > mid > long and rsi > 52:
            action = "BUY"
            confidence = 0.78
        elif short < mid < long and rsi < 48:
            action = "SELL"
            confidence = 0.78
        else:
            action = "HOLD"
            confidence = 0.3

        return self._build_signal(
            "multi-timeframe-confluence",
            action,
            confidence,
            price,
            atr_pct,
            "Entry when short/mid/long trend and RSI agree.",
            "Exit on confluence break or SL/TP.",
        )

    def _build_signal(
        self,
        name: str,
        action: str,
        confidence: float,
        price: float,
        atr_pct: float,
        entry_rule: str,
        exit_rule: str,
    ) -> StrategySignal:
        confidence = max(0.0, min(0.99, confidence))
        stop_loss: float | None = None
        take_profit: float | None = None

        if action == "BUY":
            stop_loss = price * (1 - max(atr_pct * 1.0, 0.0015))
            take_profit = price * (1 + max(atr_pct * 2.0, 0.0030))
        elif action == "SELL":
            stop_loss = price * (1 + max(atr_pct * 1.0, 0.0015))
            take_profit = price * (1 - max(atr_pct * 2.0, 0.0030))

        return StrategySignal(
            name=name,
            action=action,
            confidence=round(confidence, 4),
            entry_price=round(price, 5),
            stop_loss=round(stop_loss, 5) if stop_loss is not None else None,
            take_profit=round(take_profit, 5) if take_profit is not None else None,
            entry_rule=entry_rule,
            exit_rule=exit_rule,
        )

    def _safe_rsi(self, closes: list[float], period: int) -> float:
        if len(closes) <= period:
            return 50.0
        return float(calculate_rsi(closes, period))

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2 / (period + 1)
        ema_val = values[0]
        for value in values[1:]:
            ema_val = (value * alpha) + (ema_val * (1 - alpha))
        return ema_val

    def _macd(self, closes: list[float]) -> tuple[float, float]:
        fast = self._ema(closes, 12)
        slow = self._ema(closes, 26)
        macd = fast - slow

        macd_series: list[float] = []
        alpha_fast = 2 / (12 + 1)
        alpha_slow = 2 / (26 + 1)
        ema_fast = closes[0]
        ema_slow = closes[0]
        for value in closes:
            ema_fast = (value * alpha_fast) + (ema_fast * (1 - alpha_fast))
            ema_slow = (value * alpha_slow) + (ema_slow * (1 - alpha_slow))
            macd_series.append(ema_fast - ema_slow)

        signal = self._ema(macd_series, 9)
        return macd, signal

    def _atr_pct(self, highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
        if len(closes) < 2:
            return 0.002
        ranges = [
            max(high - low, abs(high - prev_close), abs(low - prev_close))
            for high, low, prev_close in zip(highs[1:], lows[1:], closes[:-1])
        ]
        lookback = ranges[-period:] if len(ranges) >= period else ranges
        atr = mean(lookback) if lookback else 0.0
        return atr / max(closes[-1], 1e-9)

    def _pivot_breakout(
        self,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
        atr_pct: float,
        left_bars: int = 15,
        right_bars: int = 15,
        vol_thresh: float = 20.0,
    ) -> StrategySignal:
        """LuxAlgo-style confirmed pivot S/R breakout with EMA volume oscillator gate."""
        min_bars = left_bars + right_bars + 1
        if len(closes) < min_bars:
            return self._build_signal(
                "pivot-breakout", "HOLD", 0.2, closes[-1], atr_pct,
                "Not enough bars for pivot confirmation.",
                "Wait for enough history.",
            )

        # --- volume oscillator (EMA5 vs EMA10 of tick volume, normalised) ---
        ema5 = self._ema(volumes, 5)
        ema10 = self._ema(volumes, 10)
        vol_osc = 100.0 * (ema5 - ema10) / max(ema10, 1e-9)

        # --- find most-recent confirmed pivot high and low ---
        # A pivot is confirmed only when right_bars candles have followed it.
        # We look at bar index -(right_bars+1) which is the last bar that has
        # right_bars confirmed bars after it.
        confirmed_end = len(highs) - right_bars  # exclusive upper bound
        confirmed_start = max(0, confirmed_end - 60)  # search window

        pivot_high: float | None = None
        pivot_low: float | None = None

        for i in range(confirmed_start, confirmed_end):
            if i < left_bars:
                continue
            window_high = highs[i - left_bars: i + right_bars + 1]
            window_low  = lows[i - left_bars: i + right_bars + 1]
            if highs[i] == max(window_high):
                pivot_high = highs[i]
            if lows[i] == min(window_low):
                pivot_low = lows[i]

        if pivot_high is None or pivot_low is None:
            return self._build_signal(
                "pivot-breakout", "HOLD", 0.2, closes[-1], atr_pct,
                "No confirmed pivots found in lookback window.",
                "Wait for pivot confirmation.",
            )

        price = closes[-1]
        last_high = highs[-1]
        last_low  = lows[-1]
        last_open = closes[-2] if len(closes) >= 2 else price
        last_close = closes[-1]

        volume_confirmed = vol_osc > vol_thresh

        if last_low < pivot_low and volume_confirmed:
            # Wick below support vs full candle break
            wick_down = last_open - last_close
            body_up   = last_high - last_open
            if wick_down > 0 and body_up > wick_down:
                # Wick rejection — bullish reversal (BEAR_WICK_SUPPORT)
                action = "BUY"
                confidence = 0.70
            else:
                # Support fully broken — bearish continuation
                action = "SELL"
                confidence = 0.67
        elif last_high > pivot_high and volume_confirmed:
            # Wick above resistance vs full candle break
            wick_up   = last_close - last_open
            body_down = last_open - last_low
            if wick_up < 0 and abs(body_down) > abs(wick_up):
                # Wick rejection — bearish reversal (BULL_WICK_RESISTANCE)
                action = "SELL"
                confidence = 0.70
            else:
                # Resistance fully broken — bullish continuation
                action = "BUY"
                confidence = 0.67
        else:
            action = "HOLD"
            confidence = 0.22

        return self._build_signal(
            "pivot-breakout",
            action,
            confidence,
            price,
            atr_pct,
            "Entry on confirmed pivot S/R break with volume oscillator confirmation.",
            "Exit on failed breakout or SL/TP.",
        )

    def _volatility_pct(self, closes: list[float], period: int = 20) -> float:
        if len(closes) <= period:
            return 0.0
        returns = [((curr / prev) - 1) for prev, curr in zip(closes[-period - 1:-1], closes[-period:]) if prev]
        return pstdev(returns) if len(returns) >= 2 else 0.0
