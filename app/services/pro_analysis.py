from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from textwrap import dedent
from typing import Any

import numpy as np

from app.config import Settings
from app.services.indicators import calculate_rsi
from app.services.strategy_engine import StrategyDecision, StrategyEngine


@dataclass(slots=True)
class TimeframeAnalysis:
    timeframe: str
    price: float
    structure: str
    vote: str
    confidence: float
    ema_fast: float
    ema_slow: float
    rsi: float
    macd_hist: float
    vwap: float
    support: float
    resistance: float
    atr_pct: float
    volume_ratio: float


class ProfessionalAnalysisService:
    TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
    TIMEFRAME_WEIGHTS: dict[str, float] = {
        "1m": 0.35,
        "5m": 0.55,
        "15m": 0.9,
        "1h": 1.35,
        "4h": 1.8,
        "1d": 2.2,
    }
    STYLE_SESSION_MAP: dict[str, list[str]] = {
        "SCALPING": ["London open", "New York open"],
        "DAY TRADING": ["London session", "London/New York overlap", "New York morning"],
        "SWING": ["Daily close review", "London session confirmation"],
    }
    STYLE_TIMEFRAME_MAP: dict[str, tuple[str, ...]] = {
        "SCALPING": ("1m", "5m", "15m"),
        "DAY TRADING": ("15m", "1h", "4h"),
        "SWING": ("1h", "4h", "1d"),
    }
    STYLE_ANCHOR_TIMEFRAME: dict[str, str] = {
        "SCALPING": "5m",
        "DAY TRADING": "1h",
        "SWING": "4h",
    }
    STYLE_ALIGNMENT_TIMEFRAMES: dict[str, tuple[str, str]] = {
        "SCALPING": ("15m", "5m"),
        "DAY TRADING": ("4h", "1h"),
        "SWING": ("1d", "4h"),
    }
    STYLE_ENTRY_CONFIRMATION_TIMEFRAME: dict[str, str] = {
        "SCALPING": "1m",
        "DAY TRADING": "15m",
        "SWING": "1h",
    }
    STYLE_VWAP_TIMEFRAME: dict[str, str] = {
        "SCALPING": "5m",
        "DAY TRADING": "15m",
        "SWING": "1h",
    }
    RISK_MAP: dict[str, float] = {
        "LOW": 0.01,
        "MEDIUM": 0.015,
        "HIGH": 0.02,
    }
    SESSION_WINDOWS_UTC: dict[str, tuple[int, int]] = {
        "London open": (7, 10),
        "London session": (7, 16),
        "London/New York overlap": (12, 16),
        "New York open": (13, 16),
        "New York morning": (13, 17),
        "Daily close review": (20, 22),
        "London session confirmation": (8, 11),
    }

    def __init__(self, settings: Settings, mt5_client: Any, strategy_engine: StrategyEngine):
        self.settings = settings
        self.mt5 = mt5_client
        self.strategy_engine = strategy_engine

    async def generate_report(
        self,
        symbols: list[str] | None = None,
        account_size: float | None = None,
        risk_tolerance: str = "LOW",
        trading_style: str = "DAY TRADING",
    ) -> dict[str, Any]:
        risk_label = self._normalize_risk_tolerance(risk_tolerance)
        style_label = self._normalize_trading_style(trading_style)
        allowed_symbols = set(self.settings.effective_mt5_symbols)
        target_symbols = symbols or [self.settings.mt5_symbol]
        normalized_symbols = []
        for symbol in target_symbols:
            normalized = symbol.strip()
            if not normalized:
                continue
            if normalized not in allowed_symbols:
                raise ValueError(
                    f"Unsupported symbol '{normalized}'. Use only configured MT5 symbols: {', '.join(self.settings.effective_mt5_symbols)}"
                )
            normalized_symbols.append(normalized)
        if not normalized_symbols:
            normalized_symbols = [self.settings.mt5_symbol]

        effective_account_size = float(account_size) if account_size and account_size > 0 else await self._infer_account_size()

        reports = [
            await self._analyze_symbol(symbol, effective_account_size, risk_label, style_label)
            for symbol in normalized_symbols
        ]

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "symbols": normalized_symbols,
            "account_size": effective_account_size,
            "risk_tolerance": risk_label,
            "trading_style": style_label,
            "reports": reports,
        }

    async def generate_execution_plan(
        self,
        symbol: str,
        account_size: float | None = None,
        risk_tolerance: str | None = None,
        trading_style: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        effective_risk = self._normalize_risk_tolerance(risk_tolerance or self.settings.effective_pro_analysis_execution_risk_tolerance)
        effective_style = self._normalize_trading_style(trading_style or self.settings.effective_pro_analysis_execution_trading_style)
        payload = await self.generate_report(
            symbols=[symbol],
            account_size=account_size,
            risk_tolerance=effective_risk,
            trading_style=effective_style,
        )
        report = payload["reports"][0]
        timeframes = {item["timeframe"]: item for item in report["market_overview"]["timeframes"]}
        weighted_vote = report["strategy_logic"]["weighted_vote"]
        trade_idea = report["trade_ideas"][0]
        smart_money = report["market_overview"]["smart_money"]
        allowed_sessions = self.STYLE_SESSION_MAP[effective_style]
        active_sessions = self._active_session_names(as_of)
        session_name = self._resolve_session_name(active_sessions, allowed_sessions)
        session_allowed = (not self.settings.pro_analysis_execution_require_session_filter) or any(
            session in allowed_sessions for session in active_sessions
        )

        action = str(weighted_vote.get("action", "HOLD")).upper()
        reasons: list[str] = []
        if action not in {"BUY", "SELL"}:
            reasons.append("Weighted multi-timeframe vote is HOLD.")

        min_confidence = float(self.settings.pro_analysis_execution_min_vote_confidence)
        weighted_confidence = float(weighted_vote.get("confidence", 0.0))
        if action in {"BUY", "SELL"} and weighted_confidence < min_confidence:
            reasons.append(
                f"Weighted vote confidence {weighted_confidence * 100:.1f}% is below upgraded threshold {min_confidence * 100:.1f}%."
            )

        if not session_allowed:
            current_windows = ", ".join(active_sessions) if active_sessions else "Off session"
            reasons.append(f"Session filter blocked trading during {current_windows}; allowed windows: {', '.join(allowed_sessions)}.")

        alignment_timeframes = self.STYLE_ALIGNMENT_TIMEFRAMES[effective_style]
        alignment_labels = " and ".join(item.upper() for item in alignment_timeframes)
        higher_frame = timeframes.get(alignment_timeframes[0], {})
        trend_frame = timeframes.get(alignment_timeframes[1], {})
        entry_timeframe = self.STYLE_ENTRY_CONFIRMATION_TIMEFRAME[effective_style]
        entry_frame = timeframes.get(entry_timeframe, {})
        vwap_timeframe = self.STYLE_VWAP_TIMEFRAME[effective_style]
        vwap_frame = timeframes.get(vwap_timeframe, {})

        higher_alignment = higher_frame.get("vote") == trend_frame.get("vote") == action and action in {"BUY", "SELL"}
        if self.settings.pro_analysis_execution_require_higher_timeframe_alignment and not higher_alignment:
            reasons.append(f"{alignment_labels} are not aligned with the weighted vote.")

        entry_vote_confirmed = entry_frame.get("vote") == action and action in {"BUY", "SELL"}
        if not entry_vote_confirmed and action in {"BUY", "SELL"}:
            reasons.append(f"{entry_timeframe.upper()} entry timeframe does not confirm the weighted vote.")

        vwap_alignment = True
        if action == "BUY":
            vwap_alignment = float(vwap_frame.get("price", 0.0)) >= float(vwap_frame.get("vwap", 0.0))
        elif action == "SELL":
            vwap_alignment = float(vwap_frame.get("price", 0.0)) <= float(vwap_frame.get("vwap", 0.0))
        if self.settings.pro_analysis_execution_require_vwap_alignment and action in {"BUY", "SELL"} and not vwap_alignment:
            reasons.append(f"{vwap_timeframe.upper()} VWAP alignment does not support the proposed direction.")

        rr_tp2 = float(trade_idea.get("risk_to_reward_tp2", 0.0))
        if action in {"BUY", "SELL"} and rr_tp2 < float(self.settings.pro_analysis_execution_min_rr):
            reasons.append(
                f"Trade quality failed because TP2 reward-to-risk {rr_tp2:.2f} is below minimum {float(self.settings.pro_analysis_execution_min_rr):.2f}."
            )

        if action == "BUY" and smart_money.get("order_block_tone") == "bearish":
            reasons.append("4H order-block tone is bearish against the proposed BUY.")
        if action == "SELL" and smart_money.get("order_block_tone") == "bullish":
            reasons.append("4H order-block tone is bullish against the proposed SELL.")

        final_action = action if not reasons else "HOLD"
        return {
            "symbol": symbol,
            "weighted_vote_action": action,
            "weighted_vote_confidence": weighted_confidence,
            "weighted_vote_score": float(weighted_vote.get("score", 0.0)),
            "final_action": final_action,
            "session_name": session_name,
            "active_sessions": active_sessions,
            "session_allowed": session_allowed,
            "allowed_sessions": allowed_sessions,
            "higher_timeframe_alignment": higher_alignment,
            "entry_timeframe_confirmation": entry_vote_confirmed,
            "vwap_alignment": vwap_alignment,
            "quality_gate_passed": not reasons,
            "quality_gate_reasons": reasons,
            "trade_idea": trade_idea,
            "market_structure": report["market_overview"]["market_structure"],
            "report": report,
        }

    async def _infer_account_size(self) -> float:
        try:
            account = await self.mt5.get_account_info()
            return float(account.get("equity") or account.get("balance") or 10_000.0)
        except Exception:
            return 10_000.0

    async def _analyze_symbol(
        self,
        symbol: str,
        account_size: float,
        risk_tolerance: str,
        trading_style: str,
    ) -> dict[str, Any]:
        timeframe_payloads: list[TimeframeAnalysis] = []
        raw_series: dict[str, dict[str, list[float]]] = {}

        for timeframe in self.TIMEFRAMES:
            klines = await self.mt5.get_klines(symbol=symbol, interval=timeframe, limit=300)
            series = self._extract_series(klines)
            raw_series[timeframe] = series
            timeframe_payloads.append(self._analyze_timeframe(series, timeframe))

        market_state = await self.mt5.get_symbol_market_state(symbol)
        symbol_specs = await self.mt5.get_symbol_specifications(symbol)

        weighted_vote = self._weighted_vote(timeframe_payloads)
        primary_timeframes = [item for item in timeframe_payloads if item.timeframe in self.STYLE_TIMEFRAME_MAP[trading_style]]
        trade_idea = self._build_trade_idea(
            symbol=symbol,
            weighted_vote=weighted_vote,
            timeframes=primary_timeframes,
            account_size=account_size,
            risk_tolerance=risk_tolerance,
            symbol_specs=symbol_specs,
            trading_style=trading_style,
        )
        fundamental_bias = self._fundamental_bias_summary()
        smart_money = self._smart_money_summary(raw_series["15m"], raw_series["1h"], raw_series["4h"])
        market_overview = self._market_overview(symbol, timeframe_payloads, weighted_vote, fundamental_bias, smart_money)
        strategy_logic = self._strategy_logic(symbol, weighted_vote, trading_style)
        bot_code = self._bot_code(symbol, trading_style, risk_tolerance)
        risk_notes = self._risk_notes(symbol, account_size, risk_tolerance, trade_idea, symbol_specs, market_state)
        deployment_guide = self._deployment_guide(trading_style)

        report = {
            "symbol": symbol,
            "market_overview": market_overview,
            "trade_ideas": [trade_idea],
            "strategy_logic": strategy_logic,
            "bot_code": bot_code,
            "risk_notes": risk_notes,
            "deployment_guide": deployment_guide,
        }
        report["formatted_report"] = self._format_report(report)
        return report

    def _extract_series(self, klines: list[list[Any]]) -> dict[str, list[float]]:
        return {
            "timestamps": [int(item[0]) for item in klines],
            "opens": [float(item[1]) for item in klines],
            "highs": [float(item[2]) for item in klines],
            "lows": [float(item[3]) for item in klines],
            "closes": [float(item[4]) for item in klines],
            "volumes": [float(item[5]) for item in klines],
        }

    def _analyze_timeframe(self, series: dict[str, list[float]], timeframe: str) -> TimeframeAnalysis:
        closes = series["closes"]
        highs = series["highs"]
        lows = series["lows"]
        volumes = series["volumes"]

        decision: StrategyDecision = self.strategy_engine.evaluate(
            {
                "closes": closes,
                "highs": highs,
                "lows": lows,
                "volumes": volumes,
            },
            timeframe,
        )
        price = closes[-1]
        ema_fast = self._ema(closes, 20)
        ema_slow = self._ema(closes, 50)
        rsi = calculate_rsi(closes, 14)
        macd_hist = self._macd_histogram(closes)
        vwap = self._vwap(highs, lows, closes, volumes)
        support = min(lows[-20:])
        resistance = max(highs[-20:])
        atr_pct = self._atr_pct(highs, lows, closes)
        avg_volume = max(np.mean(volumes[-20:]), 1e-9)
        volume_ratio = volumes[-1] / avg_volume
        structure = self._structure_label(price, ema_fast, ema_slow, support, resistance, atr_pct)

        return TimeframeAnalysis(
            timeframe=timeframe,
            price=price,
            structure=structure,
            vote=decision.action,
            confidence=float(decision.confidence),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi,
            macd_hist=macd_hist,
            vwap=vwap,
            support=support,
            resistance=resistance,
            atr_pct=atr_pct,
            volume_ratio=volume_ratio,
        )

    def _weighted_vote(self, analyses: list[TimeframeAnalysis]) -> dict[str, Any]:
        score = 0.0
        explanations: list[str] = []
        for item in analyses:
            weight = self.TIMEFRAME_WEIGHTS[item.timeframe]
            direction = 1.0 if item.vote == "BUY" else -1.0 if item.vote == "SELL" else 0.0
            score += direction * item.confidence * weight
            explanations.append(
                f"{item.timeframe}: {item.vote} ({item.confidence * 100:.1f}%) structure={item.structure}"
            )

        if score > 1.2:
            action = "BUY"
        elif score < -1.2:
            action = "SELL"
        else:
            action = "HOLD"

        confidence = min(0.99, abs(score) / max(sum(self.TIMEFRAME_WEIGHTS.values()), 1.0) * 2.8)
        return {
            "action": action,
            "confidence": round(confidence, 4),
            "score": round(score, 4),
            "explanations": explanations,
        }

    def _build_trade_idea(
        self,
        symbol: str,
        weighted_vote: dict[str, Any],
        timeframes: list[TimeframeAnalysis],
        account_size: float,
        risk_tolerance: str,
        symbol_specs: dict[str, float | int | str],
        trading_style: str,
    ) -> dict[str, Any]:
        anchor_timeframe = self.STYLE_ANCHOR_TIMEFRAME[trading_style]
        anchor = next((item for item in timeframes if item.timeframe == anchor_timeframe), timeframes[-1])
        direction = weighted_vote["action"]
        risk_pct = self.RISK_MAP[risk_tolerance]
        risk_amount = round(account_size * risk_pct, 2)

        if direction == "BUY":
            entry = anchor.price
            stop_loss = min(anchor.support, entry * (1 - max(anchor.atr_pct * 1.3, 0.0015)))
            risk_per_unit = max(entry - stop_loss, entry * 0.001)
            tp1 = entry + (risk_per_unit * 2.0)
            tp2 = entry + (risk_per_unit * 3.0)
            tp3 = entry + (risk_per_unit * 4.0)
            invalidation = [
                "1H close below the protective stop.",
                "4H trend flips bearish with RSI below 45.",
                "London/New York volume expansion fails to confirm the move.",
            ]
        elif direction == "SELL":
            entry = anchor.price
            stop_loss = max(anchor.resistance, entry * (1 + max(anchor.atr_pct * 1.3, 0.0015)))
            risk_per_unit = max(stop_loss - entry, entry * 0.001)
            tp1 = entry - (risk_per_unit * 2.0)
            tp2 = entry - (risk_per_unit * 3.0)
            tp3 = entry - (risk_per_unit * 4.0)
            invalidation = [
                "1H close above the protective stop.",
                "4H trend flips bullish with RSI above 55.",
                "Breakdown occurs without session liquidity or momentum follow-through.",
            ]
        else:
            entry = anchor.price
            stop_loss = anchor.support
            tp1 = anchor.resistance
            tp2 = anchor.resistance
            tp3 = anchor.resistance
            risk_per_unit = max(abs(entry - stop_loss), entry * 0.001)
            invalidation = [
                "No clear multi-timeframe alignment.",
                "Session liquidity remains compressed.",
            ]

        rr_ratio = abs(tp2 - entry) / max(abs(entry - stop_loss), 1e-9)
        recommended_lots = self._recommended_lots(symbol_specs, risk_amount, abs(entry - stop_loss))
        rationale = [
            f"Primary vote is {direction} with weighted confidence {weighted_vote['confidence'] * 100:.1f}%.",
            f"Anchor timeframe {anchor.timeframe.upper()} structure is {anchor.structure} with RSI {anchor.rsi:.1f} and MACD histogram {anchor.macd_hist:.5f}.",
            f"Proposed setup keeps target reward-to-risk at {rr_ratio:.2f}R on TP2.",
            f"Recommended session focus: {', '.join(self.STYLE_SESSION_MAP[trading_style])}.",
        ]
        rationale = [item for item in rationale if item]

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profit_1": round(tp1, 5),
            "take_profit_2": round(tp2, 5),
            "take_profit_3": round(tp3, 5),
            "risk_to_reward_tp2": round(rr_ratio, 2),
            "risk_amount": risk_amount,
            "recommended_lots": recommended_lots,
            "reasoning": rationale,
            "invalidation_conditions": invalidation,
        }

    def _market_overview(
        self,
        symbol: str,
        analyses: list[TimeframeAnalysis],
        weighted_vote: dict[str, Any],
        fundamental_bias: dict[str, Any],
        smart_money: dict[str, Any],
    ) -> dict[str, Any]:
        higher = [item for item in analyses if item.timeframe in {"1h", "4h", "1d"}]
        lower = [item for item in analyses if item.timeframe in {"1m", "5m", "15m"}]
        key_support = min(item.support for item in higher)
        key_resistance = max(item.resistance for item in higher)
        market_structure = self._aggregate_structure(higher)

        return {
            "headline": f"{symbol} weighted vote is {weighted_vote['action']} with {weighted_vote['confidence'] * 100:.1f}% conviction.",
            "market_structure": market_structure,
            "trend_stack": weighted_vote["explanations"],
            "key_levels": {
                "support": round(key_support, 5),
                "resistance": round(key_resistance, 5),
                "liquidity_pool_high": round(max(item.resistance for item in lower), 5),
                "liquidity_pool_low": round(min(item.support for item in lower), 5),
            },
            "fundamental_bias": fundamental_bias,
            "smart_money": smart_money,
            "timeframes": [
                {
                    "timeframe": item.timeframe,
                    "vote": item.vote,
                    "confidence": round(item.confidence, 4),
                    "structure": item.structure,
                    "price": round(item.price, 5),
                    "ema_fast": round(item.ema_fast, 5),
                    "ema_slow": round(item.ema_slow, 5),
                    "rsi": round(item.rsi, 2),
                    "macd_hist": round(item.macd_hist, 6),
                    "vwap": round(item.vwap, 5),
                    "volume_ratio": round(item.volume_ratio, 2),
                }
                for item in analyses
            ],
        }

    def _strategy_logic(self, symbol: str, weighted_vote: dict[str, Any], trading_style: str) -> dict[str, Any]:
        sessions = self.STYLE_SESSION_MAP[trading_style]
        alignment_timeframes = self.STYLE_ALIGNMENT_TIMEFRAMES[trading_style]
        entry_timeframe = self.STYLE_ENTRY_CONFIRMATION_TIMEFRAME[trading_style]
        anchor_timeframe = self.STYLE_ANCHOR_TIMEFRAME[trading_style]
        return {
            "configured_strategies": self.settings.effective_enabled_strategies,
            "entry_conditions": [
                "Use only configured MT5 symbols from the existing bot universe.",
                f"Require {alignment_timeframes[0].upper()} and {alignment_timeframes[1].upper()} directional agreement with the weighted vote.",
                f"Require {entry_timeframe.upper()} confirmation from momentum, breakout, or smart-money structure.",
                "Reject setups where weighted confidence is below 60% for BUY or 30% for SELL, matching the existing live thresholds.",
                "Execute only during preferred sessions: " + ", ".join(sessions) + ".",
            ],
            "exit_conditions": [
                "Scale out at TP1, TP2, TP3.",
                "Move stop to breakeven after TP1.",
                f"Exit early when {entry_timeframe.upper()} and {anchor_timeframe.upper()} flip against the trade direction with momentum divergence.",
            ],
            "trade_filters": [
                f"Respect existing spread cap of {self.settings.risk_max_spread_pips:.1f} pips.",
                f"Respect MT5 active position cap of {self.settings.risk_mt5_max_active_positions}.",
                f"Respect duplicate re-entry lock and cooldown of {self.settings.risk_min_seconds_between_trades} seconds.",
            ],
            "weighted_vote": weighted_vote,
            "session_filters": sessions,
            "timeframe_stack": list(self.STYLE_TIMEFRAME_MAP[trading_style]),
            "symbol": symbol,
        }

    def _bot_code(self, symbol: str, trading_style: str, risk_tolerance: str) -> dict[str, str]:
        alignment_timeframes = self.STYLE_ALIGNMENT_TIMEFRAMES[trading_style]
        entry_timeframe = self.STYLE_ENTRY_CONFIRMATION_TIMEFRAME[trading_style]
        python_code = dedent(
            f"""
            import MetaTrader5 as mt5

            SYMBOL = \"{symbol}\"
            RISK_TOLERANCE = \"{risk_tolerance}\"
            TRADING_STYLE = \"{trading_style}\"

            def should_trade(vote_higher, vote_trend, vote_entry, spread_pips, session_name):
                if spread_pips > {self.settings.risk_max_spread_pips:.1f}:
                    return False
                if vote_higher != vote_trend:
                    return False
                if vote_entry == \"HOLD\":
                    return False
                if session_name not in {self.STYLE_SESSION_MAP[trading_style]!r}:
                    return False
                return True

            # For {trading_style}, higher={alignment_timeframes[0].upper()}, trend={alignment_timeframes[1].upper()}, entry={entry_timeframe.upper()}.

            def place_trade(direction, volume, sl, tp):
                request = {{
                    \"action\": mt5.TRADE_ACTION_DEAL,
                    \"symbol\": SYMBOL,
                    \"volume\": volume,
                    \"type\": mt5.ORDER_TYPE_BUY if direction == \"BUY\" else mt5.ORDER_TYPE_SELL,
                    \"sl\": sl,
                    \"tp\": tp,
                    \"deviation\": {self.settings.mt5_deviation},
                    \"magic\": {self.settings.mt5_magic},
                    \"comment\": \"v2-upgraded\",
                }}
                return mt5.order_send(request)
            """
        ).strip()

        mql5_pseudocode = dedent(
            """
            // 1. Read MTF votes: M15, H1, H4, D1.
            // 2. Skip trade if spread, cooldown, or active-position filters fail.
            // 3. Enter only when H4 and H1 align and M15 confirms momentum.
            // 4. Set SL beyond structure and stage TP1/TP2/TP3 at >= 2R profile.
            // 5. Move SL to breakeven after TP1 and trail beneath/above H1 swing structure.
            """
        ).strip()

        javascript_pseudocode = dedent(
            """
            const vote = aggregateVotes(timeframes);
            if (!isSessionAllowed(session) || spreadPips > maxSpread) return;
            if (!hasHigherTimeframeAlignment(vote.h4, vote.h1)) return;
            const setup = buildTradePlan(vote, structure, riskBudget);
            if (setup.rrTp2 < 2) return;
            executeMt5Order(setup);
            """
        ).strip()

        return {
            "python_mt5": python_code,
            "mql5_pseudocode": mql5_pseudocode,
            "javascript_pseudocode": javascript_pseudocode,
        }

    def _risk_notes(
        self,
        symbol: str,
        account_size: float,
        risk_tolerance: str,
        trade_idea: dict[str, Any],
        symbol_specs: dict[str, float | int | str],
        market_state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "account_size": round(account_size, 2),
            "risk_tolerance": risk_tolerance,
            "risk_per_trade_pct": round(self.RISK_MAP[risk_tolerance] * 100, 2),
            "risk_amount": trade_idea["risk_amount"],
            "recommended_lots": trade_idea["recommended_lots"],
            "spread_pips": round(float(market_state.get("spread_pips") or 0.0), 2),
            "position_cap": self.settings.risk_mt5_max_active_positions,
            "diversification_note": "Limit concurrent trades to uncorrelated majors or one metal-plus-one-major combination when trading multiple pairs.",
            "broker_step": symbol_specs.get("volume_step", 0.0),
        }

    def _deployment_guide(self, trading_style: str) -> list[str]:
        sessions = ", ".join(self.STYLE_SESSION_MAP[trading_style])
        return [
            "VPS: deploy near the broker server, keep NTP sync enabled, and monitor terminal restarts.",
            f"MetaTrader: bind the strategy to the existing MT5 account and limit execution to {sessions}.",
            "Web execution: expose a read-only analysis endpoint separately from order execution and require signed control requests.",
            "Fail-safe: halt trading on MT5 disconnect, stale candles, spread blowout, or daily loss kill-switch trigger.",
            "Monitoring: log weighted votes, order_check responses, fills, and Telegram alerts for every decision.",
        ]

    def _format_report(self, report: dict[str, Any]) -> str:
        trade = report["trade_ideas"][0]
        overview = report["market_overview"]
        strategy = report["strategy_logic"]
        risk = report["risk_notes"]
        deploy = report["deployment_guide"]
        code = report["bot_code"]

        lines = [
            "Market Overview",
            f"- {overview['headline']}",
            f"- Market structure: {overview['market_structure']}",
            f"- Key support: {overview['key_levels']['support']:.5f}",
            f"- Key resistance: {overview['key_levels']['resistance']:.5f}",
            f"- Fundamental bias: {overview['fundamental_bias']['summary']}",
            f"- Smart money: {overview['smart_money']['summary']}",
            "",
            "Trade Ideas",
            f"- Direction: {trade['direction']}",
            f"- Entry: {trade['entry']:.5f}",
            f"- Stop loss: {trade['stop_loss']:.5f}",
            f"- TP1: {trade['take_profit_1']:.5f}",
            f"- TP2: {trade['take_profit_2']:.5f}",
            f"- TP3: {trade['take_profit_3']:.5f}",
            f"- RR to TP2: {trade['risk_to_reward_tp2']:.2f}",
            "",
            "Strategy Logic",
        ]
        lines.extend(f"- {item}" for item in strategy["entry_conditions"])
        lines.append("")
        lines.append("Bot Code / Pseudocode")
        lines.append(code["python_mt5"])
        lines.append("")
        lines.append("Risk Notes")
        lines.append(f"- Account size: {risk['account_size']:.2f}")
        lines.append(f"- Risk per trade: {risk['risk_per_trade_pct']:.2f}%")
        lines.append(f"- Recommended lots: {risk['recommended_lots']}")
        lines.append("")
        lines.append("Deployment Guide")
        lines.extend(f"- {item}" for item in deploy)
        return "\n".join(lines)

    def _normalize_risk_tolerance(self, value: str) -> str:
        candidate = str(value or "LOW").strip().upper()
        return candidate if candidate in self.RISK_MAP else "LOW"

    def _normalize_trading_style(self, value: str) -> str:
        candidate = str(value or "DAY TRADING").strip().upper().replace("_", " ")
        return candidate if candidate in self.STYLE_SESSION_MAP else "DAY TRADING"

    def _current_session_name(self, as_of: datetime | None = None) -> str:
        active_sessions = self._active_session_names(as_of)
        if active_sessions:
            return active_sessions[0]
        return "Off session"

    def _active_session_names(self, as_of: datetime | None = None) -> list[str]:
        current = as_of.astimezone(UTC) if as_of else datetime.now(UTC)
        hour = current.hour
        active_sessions: list[str] = []
        for name, (start_hour, end_hour) in self.SESSION_WINDOWS_UTC.items():
            if start_hour <= hour < end_hour:
                active_sessions.append(name)
        return active_sessions

    def _resolve_session_name(self, active_sessions: list[str], allowed_sessions: list[str]) -> str:
        for session in allowed_sessions:
            if session in active_sessions:
                return session
        if active_sessions:
            return active_sessions[0]
        return "Off session"

    def _structure_label(
        self,
        price: float,
        ema_fast: float,
        ema_slow: float,
        support: float,
        resistance: float,
        atr_pct: float,
    ) -> str:
        if price > resistance * 0.998:
            return "breakout-pressure"
        if price < support * 1.002:
            return "breakdown-pressure"
        if abs(ema_fast - ema_slow) / max(price, 1e-9) < 0.0015 and atr_pct < 0.003:
            return "consolidation"
        if ema_fast > ema_slow:
            return "bullish-trend"
        if ema_fast < ema_slow:
            return "bearish-trend"
        return "range"

    def _aggregate_structure(self, analyses: list[TimeframeAnalysis]) -> str:
        labels = [item.structure for item in analyses]
        bullish = sum(1 for label in labels if "bullish" in label or "breakout" in label)
        bearish = sum(1 for label in labels if "bearish" in label or "breakdown" in label)
        if bullish > bearish:
            return "Higher timeframes are biased bullish with directional continuation risk."
        if bearish > bullish:
            return "Higher timeframes are biased bearish with continuation pressure."
        return "Higher timeframes are mixed and closer to consolidation than impulse trend."

    def _fundamental_bias_summary(self) -> dict[str, Any]:
        bias = float(self.settings.news_sentiment_bias or 0.0)
        if bias > 0.1:
            summary = "Configured macro/news bias is bullish; long setups get slight preference."
        elif bias < -0.1:
            summary = "Configured macro/news bias is bearish; short setups get slight preference."
        else:
            summary = "No external macro feed is configured; fundamental bias is neutral and manual."
        return {
            "score": round(bias, 2),
            "summary": summary,
        }

    def _smart_money_summary(
        self,
        series_15m: dict[str, list[float]],
        series_1h: dict[str, list[float]],
        series_4h: dict[str, list[float]],
    ) -> dict[str, Any]:
        highs = series_15m["highs"]
        lows = series_15m["lows"]
        closes = series_15m["closes"]

        liquidity_grab_up = highs[-1] > max(highs[-6:-1]) and closes[-1] < highs[-2]
        liquidity_grab_down = lows[-1] < min(lows[-6:-1]) and closes[-1] > lows[-2]
        bullish_fvg = series_1h["lows"][-1] > series_1h["highs"][-3]
        bearish_fvg = series_1h["highs"][-1] < series_1h["lows"][-3]
        order_block = "bullish" if series_4h["closes"][-1] > series_4h["opens"][-1] else "bearish"

        notes: list[str] = [f"4H order-block tone: {order_block}."]
        if liquidity_grab_up:
            notes.append("15M sweep above recent highs suggests buy-side liquidity grab.")
        if liquidity_grab_down:
            notes.append("15M sweep below recent lows suggests sell-side liquidity grab.")
        if bullish_fvg:
            notes.append("1H bullish fair value gap remains open.")
        if bearish_fvg:
            notes.append("1H bearish fair value gap remains open.")
        if len(notes) == 1:
            notes.append("No dominant smart-money displacement signal is present right now.")

        return {
            "liquidity_grab_up": liquidity_grab_up,
            "liquidity_grab_down": liquidity_grab_down,
            "bullish_fvg": bullish_fvg,
            "bearish_fvg": bearish_fvg,
            "order_block_tone": order_block,
            "summary": " ".join(notes),
        }

    def _recommended_lots(
        self,
        symbol_specs: dict[str, float | int | str],
        risk_amount: float,
        stop_distance: float,
    ) -> float:
        tick_size = float(symbol_specs.get("trade_tick_size") or 0.0)
        tick_value = float(
            symbol_specs.get("trade_tick_value_loss")
            or symbol_specs.get("trade_tick_value")
            or symbol_specs.get("trade_tick_value_profit")
            or 0.0
        )
        min_volume = float(symbol_specs.get("volume_min") or self.settings.mt5_volume_lots)
        max_volume = float(symbol_specs.get("volume_max") or max(min_volume, self.settings.mt5_volume_lots))
        volume_step = float(symbol_specs.get("volume_step") or 0.01)

        if tick_size <= 0 or tick_value <= 0 or stop_distance <= 0:
            return round(self.settings.mt5_volume_lots, 2)

        risk_per_lot = (stop_distance / tick_size) * tick_value
        if risk_per_lot <= 0:
            return round(self.settings.mt5_volume_lots, 2)

        raw_lots = risk_amount / risk_per_lot
        clipped = max(min_volume, min(max_volume, raw_lots))
        stepped = np.floor(clipped / volume_step) * volume_step
        return round(float(max(min_volume, stepped)), 2)

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        array = np.asarray(values[-max(period * 4, period):], dtype=float)
        alpha = 2.0 / (period + 1)
        result = array[0]
        for item in array[1:]:
            result = (alpha * item) + ((1 - alpha) * result)
        return float(result)

    def _macd_histogram(self, closes: list[float]) -> float:
        if len(closes) < 35:
            return 0.0
        macd_series: list[float] = []
        for index in range(26, len(closes) + 1):
            window = closes[:index]
            macd_series.append(self._ema(window, 12) - self._ema(window, 26))
        signal = self._ema(macd_series, 9)
        return macd_series[-1] - signal

    def _vwap(self, highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> float:
        typical = np.asarray([(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)], dtype=float)
        volume = np.asarray(volumes, dtype=float)
        denominator = float(np.sum(volume))
        if denominator <= 0:
            return float(closes[-1])
        return float(np.sum(typical * volume) / denominator)

    def _atr_pct(self, highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
        if len(closes) < 2:
            return 0.0
        trs: list[float] = []
        for index in range(1, len(closes)):
            high = highs[index]
            low = lows[index]
            prev_close = closes[index - 1]
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr = float(np.mean(trs[-period:])) if trs else 0.0
        return atr / max(closes[-1], 1e-9)