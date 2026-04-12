from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.config import _to_coinbase_product_id, _to_dash_symbol, _to_kraken_symbol
from app.models import BrokerFillJournal, BrokerPositionJournal, ExecutionRequest, Mt5ExecutionJob, Mt5TradeCycle, Trade
from app.services.binance import BinanceClient, SymbolRules, decimal_to_string, round_step_size
from app.services.coinbase import CoinbaseMarketDataClient
from app.services.indicators import calculate_rsi
from app.services.kraken import KrakenMarketDataClient
from app.services.ml import MLSignalService
from app.services.mt5_execution import Mt5ExecutionService
from app.services.mt5 import Mt5TradingClient
from app.services.news_calendar import NewsCalendarClient
from app.services.okx import OkxMarketDataClient
from app.services.pro_analysis import ProfessionalAnalysisService
from app.services.strategy_engine import StrategyDecision, StrategyEngine


@dataclass(slots=True)
class MarketSnapshot:
    signal_symbol: str
    market_data_symbol: str
    execution_symbol: str
    market_data_provider: str
    latest_price: float
    rsi: float
    position_quantity: float
    suggested_action: str
    rules: SymbolRules
    strategy_vote_action: str = "HOLD"
    last_candle_open_time: int | None = None
    confidence: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    regime: str = ""
    strategy_names: list[str] = field(default_factory=list)
    strategy_details: list[dict[str, Any]] = field(default_factory=list)
    strategy_weights: dict[str, float] = field(default_factory=dict)
    ml_probability_up: float | None = None
    ml_action: str | None = None
    pre_candle_close_action: str | None = None
    candle_close_gate_blocked: bool = False
    candle_close_gate_enabled: bool = False
    seconds_until_candle_close: int | None = None
    pre_confidence_action: str | None = None
    confidence_gate_blocked: bool = False
    confidence_gate_threshold: float = 0.0
    pro_analysis_vote_action: str | None = None
    pro_analysis_final_action: str | None = None
    pro_analysis_gate_blocked: bool = False
    pro_analysis_gate_reasons: list[str] = field(default_factory=list)
    pro_analysis_session_name: str | None = None
    pro_analysis_session_allowed: bool = True
    pro_analysis_quality_gate_passed: bool = True
    pro_analysis_rr: float | None = None
    atr_pct: float = 0.0
    atr_value: float | None = None
    atr_recovery_active: bool = False
    atr_recovery_symbol_enabled: bool = False
    atr_recovery_profile: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return self.signal_symbol


class ExecutionBlockedError(RuntimeError):
    pass


class TradingService:
    def __init__(
        self,
        settings: Settings,
        binance_client: BinanceClient | None = None,
        market_data_client: Any | None = None,
        mt5_client: Any | None = None,
        mt5_execution_service: Mt5ExecutionService | None = None,
        news_calendar_client: NewsCalendarClient | None = None,
    ):
        self.settings = settings
        self.binance = binance_client or BinanceClient(settings)
        self.mt5 = mt5_client or Mt5TradingClient(settings)
        self.mt5_execution = mt5_execution_service or Mt5ExecutionService(settings, mt5_client=self.mt5)
        self.news_calendar = news_calendar_client or NewsCalendarClient(settings)
        self.strategy_engine = StrategyEngine(settings)
        self._market_data_client = market_data_client
        self._market_data_clients: dict[str, Any] = {}
        self._daily_equity_anchor_date: date | None = None
        self._daily_equity_anchor_value: float | None = None
        self._symbol_locks: dict[str, asyncio.Lock] = {}
        self._broker_positions: dict[str, float] = {}
        self._broker_open_orders: dict[str, list[dict[str, Any]]] = {}
        self._last_equity_anchor_by_day: dict[date, float] = {}
        self.ml_service = MLSignalService(settings.ml_model_path)
        self._ml_last_retrain_ts: float | None = None
        self._mt5_live_order_enabled_symbols: set[str] = set(settings.effective_mt5_live_order_enabled_symbols)
        self._mt5_atr_recovery_enabled_symbols: set[str] = set(settings.effective_atr_recovery_enabled_symbols)

    def _symbol_currencies(self, symbol: str) -> set[str]:
        letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
        if len(letters) >= 6:
            return {letters[:3], letters[3:6]}
        return set()

    def _inline_news_events(self) -> list[dict[str, Any]]:
        raw = (self.settings.news_filter_events_utc or "").strip()
        if not raw:
            return []
        items: list[dict[str, Any]] = []
        entries = [item.strip() for item in raw.split(";") if item.strip()]
        for entry in entries:
            parts = [part.strip() for part in entry.split("|")]
            if len(parts) < 3:
                continue
            ts_raw, currency, impact = parts[0], parts[1].upper(), parts[2].lower()
            title = parts[3] if len(parts) > 3 else "high-impact event"
            normalized_ts = ts_raw.replace("Z", "+00:00")
            try:
                event_ts = datetime.fromisoformat(normalized_ts)
            except ValueError:
                continue
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=UTC)
            items.append(
                {
                    "timestamp": event_ts,
                    "currency": currency,
                    "impact": impact,
                    "title": title,
                }
            )
        return items

    async def _news_filter_block_reason(self, snapshot: MarketSnapshot) -> str | None:
        if not self.settings.news_filter_enabled:
            return None

        impact_rank = {"low": 1, "medium": 2, "high": 3}
        min_impact = self.settings.news_filter_min_impact.strip().lower() or "high"
        min_rank = impact_rank.get(min_impact, 3)
        now = datetime.now(UTC)
        pre_window = timedelta(minutes=max(0, int(self.settings.news_filter_pre_event_minutes)))
        post_window = timedelta(minutes=max(0, int(self.settings.news_filter_post_event_minutes)))
        symbol_currencies = self._symbol_currencies(snapshot.execution_symbol)

        provider_events: list[dict[str, Any]] = []
        try:
            provider_events = await self.news_calendar.fetch_events()
        except Exception:
            provider_events = []

        events = provider_events + self._inline_news_events()
        for event in events:
            currency = str(event.get("currency") or "").upper()
            impact = str(event.get("impact") or "").lower()
            title = str(event.get("title") or "high-impact event")
            if impact_rank.get(impact, 0) < min_rank:
                continue
            if symbol_currencies and currency not in symbol_currencies:
                continue

            event_ts = event.get("timestamp")
            if not isinstance(event_ts, datetime):
                continue
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=UTC)

            if (event_ts - pre_window) <= now <= (event_ts + post_window):
                return (
                    "Execution block: NEWS_HIGH_IMPACT_WINDOW "
                    f"({currency} {impact.upper()} {title} at {event_ts.isoformat()})."
                )
        return None

    def _should_apply_pro_analysis_execution_plan(self, execution_symbol: str, selected_provider: str) -> bool:
        if not self.settings.pro_analysis_execution_enabled:
            return False
        if execution_symbol not in set(self.settings.effective_mt5_symbols):
            return False
        if self.settings.effective_execution_provider == "mt5":
            return True
        if self.settings.effective_market_data_provider == "mt5":
            return True
        return selected_provider == "mt5"

    async def _generate_pro_analysis_execution_plan(self, execution_symbol: str) -> dict[str, Any] | None:
        if execution_symbol not in set(self.settings.effective_mt5_symbols):
            return None
        service = ProfessionalAnalysisService(self.settings, self.mt5, self.strategy_engine)
        return await service.generate_execution_plan(symbol=execution_symbol)

    def register_market_data_client(self, provider: str, client: Any) -> None:
        self._market_data_clients[provider] = client

    def get_strategy_catalog(self) -> list[dict[str, str | bool]]:
        return self.strategy_engine.strategy_catalog()

    def _strategy_note(self, snapshot: MarketSnapshot) -> str:
        return ", ".join(snapshot.strategy_names) if snapshot.strategy_names else "none"

    def _strategy_weights_json(self, snapshot: MarketSnapshot) -> str:
        return json.dumps(snapshot.strategy_weights or {}, default=str)

    def _is_mt5_readiness_na(self, payload: dict[str, Any]) -> bool:
        values = [payload.get("comment"), payload.get("reason"), payload.get("retcode")]
        text = " ".join(str(item) for item in values if item is not None).strip().lower()
        return "n/a" in text

    def get_mt5_live_order_toggle_states(self) -> list[dict[str, Any]]:
        if not self.settings.mt5_live_order_symbol_toggle_enabled:
            return []
        selected = self.settings.effective_mt5_live_order_toggle_symbols
        if not selected:
            return []
        return [
            {
                "symbol": symbol,
                "enabled": symbol in self._mt5_live_order_enabled_symbols,
            }
            for symbol in selected
        ]

    def get_mt5_atr_recovery_toggle_states(self) -> list[dict[str, Any]]:
        if self.settings.effective_execution_provider != "mt5":
            return []
        selected = self.settings.effective_atr_recovery_toggle_symbols
        if not selected:
            return []
        return [
            {
                "symbol": symbol,
                "enabled": symbol in self._mt5_atr_recovery_enabled_symbols,
            }
            for symbol in selected
        ]

    def set_mt5_live_order_symbol_enabled(self, symbol: str, enabled: bool) -> dict[str, Any]:
        if not self.settings.mt5_live_order_symbol_toggle_enabled:
            raise ValueError("MT5 live-order symbol toggle feature is disabled.")

        selected = set(self.settings.effective_mt5_live_order_toggle_symbols)
        if not selected:
            raise ValueError("No MT5 toggle symbols are configured.")
        if symbol not in selected:
            raise ValueError(f"Symbol {symbol} is not configured for MT5 live-order toggles.")

        if enabled:
            self._mt5_live_order_enabled_symbols.add(symbol)
        else:
            self._mt5_live_order_enabled_symbols.discard(symbol)

        return {
            "symbol": symbol,
            "enabled": symbol in self._mt5_live_order_enabled_symbols,
        }

    def set_mt5_atr_recovery_symbol_enabled(self, symbol: str, enabled: bool) -> dict[str, Any]:
        if self.settings.effective_execution_provider != "mt5":
            raise ValueError("ATR recovery symbol toggles are only available for MT5 execution.")
        if not self.settings.atr_recovery_enabled:
            raise ValueError("ATR recovery module is disabled.")

        selected = set(self.settings.effective_atr_recovery_toggle_symbols)
        if not selected:
            raise ValueError("No MT5 ATR recovery symbols are configured.")
        if symbol not in selected:
            raise ValueError(f"Symbol {symbol} is not configured for MT5 ATR recovery toggles.")

        if enabled:
            self._mt5_atr_recovery_enabled_symbols.add(symbol)
        else:
            self._mt5_atr_recovery_enabled_symbols.discard(symbol)

        return {
            "symbol": symbol,
            "enabled": symbol in self._mt5_atr_recovery_enabled_symbols,
        }

    def _is_atr_recovery_symbol_enabled(self, execution_symbol: str) -> bool:
        if not self.settings.atr_recovery_enabled:
            return False
        if self.settings.atr_recovery_mt5_only and self.settings.effective_execution_provider != "mt5":
            return False
        selected = set(self.settings.effective_atr_recovery_toggle_symbols)
        if selected and execution_symbol not in selected:
            return False
        return execution_symbol in self._mt5_atr_recovery_enabled_symbols

    def _ema_value(self, closes: list[float], period: int) -> float:
        if not closes:
            return 0.0
        period = max(1, int(period))
        alpha = 2.0 / (period + 1.0)
        ema = float(closes[0])
        for price in closes[1:]:
            ema += (float(price) - ema) * alpha
        return ema

    def _atr_pct_from_series(self, highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
        if len(closes) < 2:
            return 0.0
        lookback_period = max(2, int(period))
        true_ranges: list[float] = []
        start_index = max(1, len(closes) - lookback_period)
        for index in range(start_index, len(closes)):
            high = float(highs[index])
            low = float(lows[index])
            prev_close = float(closes[index - 1])
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if not true_ranges:
            return 0.0
        return mean(true_ranges) / max(float(closes[-1]), 1e-9)

    def _structure_bias(self, highs: list[float], lows: list[float]) -> str:
        if len(highs) < 6 or len(lows) < 6:
            return "mixed"
        recent_high = max(float(item) for item in highs[-3:])
        prior_high = max(float(item) for item in highs[-6:-3])
        recent_low = min(float(item) for item in lows[-3:])
        prior_low = min(float(item) for item in lows[-6:-3])
        if recent_high > prior_high and recent_low > prior_low:
            return "bullish"
        if recent_high < prior_high and recent_low < prior_low:
            return "bearish"
        return "mixed"

    def _build_atr_recovery_profile(
        self,
        *,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        symbol_key: str,
        action: str,
        latest_price: float,
    ) -> dict[str, Any]:
        atr_period = max(2, int(self.settings.atr_recovery_atr_period))
        atr_pct = self._atr_pct_from_series(highs, lows, closes, atr_period)
        atr_value = latest_price * atr_pct if latest_price > 0 and atr_pct > 0 else 0.0
        symbol_enabled = self._is_atr_recovery_symbol_enabled(symbol_key)
        provider_supported = self.settings.effective_execution_provider == "mt5" or not self.settings.atr_recovery_mt5_only

        profile: dict[str, Any] = {
            "primary_signal_source": "weighted-multi-engine",
            "provider_supported": provider_supported,
            "symbol_enabled": symbol_enabled,
            "active": bool(self.settings.atr_recovery_enabled and provider_supported and symbol_enabled),
            "execution_overlay_active": False,
            "atr_period": atr_period,
            "atr_pct": round(atr_pct, 6),
            "atr_value": round(atr_value, 6) if atr_value > 0 else None,
            "trend_bias": "mixed",
            "structure_bias": self._structure_bias(highs, lows),
            "stop_loss": None,
            "take_profit": None,
            "hedge_trigger": None,
            "trailing_activation_price": None,
            "reversal_confirmation_price": None,
            "note": "ATR recovery overlay inactive.",
        }

        if not profile["active"]:
            if not self.settings.atr_recovery_enabled:
                profile["note"] = "ATR recovery module disabled."
            elif not provider_supported:
                profile["note"] = "ATR recovery is limited to MT5 execution."
            elif not symbol_enabled:
                profile["note"] = "ATR recovery toggle is OFF for this symbol."
            return profile

        min_atr_pct = max(0.0, float(self.settings.atr_recovery_min_atr_pct))
        if min_atr_pct > 0 and atr_pct < min_atr_pct:
            profile["note"] = f"ATR recovery skipped because ATR pct {atr_pct:.4f} is below minimum {min_atr_pct:.4f}."
            return profile

        ema50 = self._ema_value(closes, 50)
        ema200 = self._ema_value(closes, 200)
        if ema50 > ema200:
            profile["trend_bias"] = "bullish"
        elif ema50 < ema200:
            profile["trend_bias"] = "bearish"

        sl_multiplier = max(0.1, float(self.settings.atr_recovery_stop_loss_multiplier))
        tp_multiplier = max(0.1, float(self.settings.atr_recovery_take_profit_multiplier))

        if action == "BUY" and atr_value > 0:
            stop_loss = latest_price - (atr_value * sl_multiplier)
            take_profit = latest_price + (atr_value * tp_multiplier)
            hedge_multiplier = max(0.05, float(self.settings.atr_recovery_hedge_trigger_multiplier))
            trailing_activation = latest_price - (atr_value * max(0.05, float(self.settings.atr_recovery_trailing_activation_atr)))
            reversal_confirmation = (latest_price - (atr_value * hedge_multiplier)) + (atr_value * max(0.05, float(self.settings.atr_recovery_reversal_atr_threshold)))
            profile.update(
                {
                    "execution_overlay_active": True,
                    "stop_loss": round(stop_loss, 5),
                    "take_profit": round(take_profit, 5),
                    "hedge_trigger": round(max(stop_loss + (atr_value * 0.05), latest_price - (atr_value * hedge_multiplier)), 5),
                    "trailing_activation_price": round(trailing_activation, 5),
                    "reversal_confirmation_price": round(reversal_confirmation, 5),
                    "note": "ATR recovery overlay ready for MT5 BUY-cycle management; directional source remains weighted multi-engine.",
                }
            )
            return profile

        if action == "SELL" and atr_value > 0:
            stop_loss = latest_price + (atr_value * sl_multiplier)
            take_profit = latest_price - (atr_value * tp_multiplier)
            profile.update(
                {
                    "execution_overlay_active": True,
                    "stop_loss": round(stop_loss, 5),
                    "take_profit": round(take_profit, 5),
                    "note": "ATR execution overlay active for SELL-cycle exits; BUY-side recovery remains disabled for SELL trades.",
                }
            )
            return profile

        profile["note"] = "ATR recovery overlay is idle because no directional trade is active."
        return profile

    def _mt5_live_order_toggle_block_reason(self, execution_symbol: str, action: str) -> str | None:
        if action != "BUY":
            return None
        if self.settings.effective_execution_provider != "mt5":
            return None
        if not self.settings.mt5_live_order_symbol_toggle_enabled:
            return None

        selected = self.settings.effective_mt5_live_order_toggle_symbols
        if not selected:
            return None
        if execution_symbol not in selected:
            return None
        if execution_symbol in self._mt5_live_order_enabled_symbols:
            return None

        return (
            f"Execution block: live-order toggle is OFF for {execution_symbol}. "
            "Enable it in dashboard before opening new entries."
        )

    async def _estimate_equity(self, snapshot: MarketSnapshot) -> float | None:
        try:
            if self.settings.can_place_live_orders:
                if self.settings.effective_execution_provider == "mt5":
                    account = await self.mt5.get_account_info()
                    return float(account.get("equity") or account.get("balance") or 0.0)
                account = await self.binance.get_account_info()
                rules = await self.binance.get_exchange_info(snapshot.execution_symbol)
                base_free = float(self._extract_free_balance(account, rules.base_asset))
                quote_free = float(self._extract_free_balance(account, rules.quote_asset))
                return quote_free + (base_free * snapshot.latest_price)
            return None
        except Exception:
            return None

    def _slippage_pct(self, intended_price: float, fill_price: float) -> float:
        if intended_price <= 0:
            return 0.0
        return abs((fill_price - intended_price) / intended_price) * 100.0

    def _interval_seconds(self) -> int:
        interval = self.settings.effective_strategy_candle_interval
        if interval == "4h":
            return 4 * 60 * 60
        return 60 * 60

    def _to_epoch_seconds(self, timestamp: int | None) -> int | None:
        if timestamp is None:
            return None
        value = int(timestamp)
        # Some feeds return milliseconds while others return seconds.
        if value > 10_000_000_000:
            return int(value / 1000)
        return value

    def _seconds_until_candle_close(self, last_candle_open_time: int | None) -> int | None:
        open_ts = self._to_epoch_seconds(last_candle_open_time)
        if open_ts is None:
            return None
        close_ts = open_ts + self._interval_seconds()
        remaining = close_ts - int(datetime.now(UTC).timestamp())
        return max(0, remaining)

    def _confidence_threshold_for_action(self, action: str) -> float:
        if not self.settings.strategy_side_confidence_thresholds_enabled:
            return self.settings.effective_strategy_min_confidence_threshold
        if action == "BUY":
            return self.settings.effective_strategy_min_confidence_threshold_buy
        if action == "SELL":
            return self.settings.effective_strategy_min_confidence_threshold_sell
        return self.settings.effective_strategy_min_confidence_threshold

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        lock = self._symbol_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            self._symbol_locks[symbol] = lock
        return lock

    def _get_execution_symbol(self, symbol_override: str | None = None) -> str:
        if self.settings.can_place_live_orders and self.settings.effective_execution_provider == "mt5":
            return symbol_override or self.settings.mt5_symbol
        return self.settings.trading_symbol

    def _get_signal_symbol(self, symbol_override: str | None = None) -> str:
        if self.settings.effective_market_data_provider == "mt5":
            return symbol_override or self.settings.mt5_symbol
        if self.settings.can_place_live_orders and self.settings.effective_execution_provider == "mt5":
            return symbol_override or self.settings.mt5_symbol
        return self.settings.trading_symbol

    def _current_execution_account_scope(self) -> str:
        provider = self.settings.effective_execution_provider
        if provider == "mt5":
            login = int(self.settings.mt5_login or 0)
            server = self.settings.mt5_server.strip().lower() or "unknown"
            return f"mt5:{login}:{server}"
        if provider == "binance":
            api_key = (self.settings.binance_api_key or "").strip()
            key_fingerprint = api_key[-8:] if api_key else "default"
            return f"binance:{key_fingerprint}"
        return provider or "default"

    def _pending_execution_request(self, db: Session, execution_symbol: str, account_scope: str) -> ExecutionRequest | None:
        statement = (
            select(ExecutionRequest)
            .where(
                ExecutionRequest.account_scope == account_scope,
                ExecutionRequest.execution_symbol == execution_symbol,
                ExecutionRequest.status.in_(["PENDING", "QUEUED"]),
            )
            .order_by(desc(ExecutionRequest.created_at))
            .limit(1)
        )
        return db.scalar(statement)

    def _build_idempotency_key(
        self,
        snapshot: MarketSnapshot,
        action: str,
        request_id: str | None = None,
    ) -> str:
        account_scope = self._current_execution_account_scope()
        if request_id and request_id.strip():
            return f"{account_scope}:{request_id.strip()}"
        raw = (
            f"{account_scope}:{action}:{snapshot.execution_symbol}:{snapshot.market_data_provider}:"
            f"{snapshot.market_data_symbol}:{snapshot.last_candle_open_time or 'na'}"
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _build_client_order_id(self, idempotency_key: str, action: str) -> str:
        digest = hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:22]
        return f"v2-{action.lower()}-{digest}"

    def _register_execution_request(
        self,
        db: Session,
        snapshot: MarketSnapshot,
        action: str,
        account_scope: str,
        idempotency_key: str,
        client_order_id: str,
    ) -> ExecutionRequest:
        existing = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.account_scope == account_scope,
                ExecutionRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            raise ExecutionBlockedError(
                f"Execution block: duplicate idempotency key already recorded with status {existing.status}."
            )

        request = ExecutionRequest(
            account_scope=account_scope,
            idempotency_key=idempotency_key,
            client_order_id=client_order_id,
            signal_symbol=snapshot.signal_symbol,
            execution_symbol=snapshot.execution_symbol,
            action=action,
            status="PENDING",
        )
        db.add(request)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ExecutionBlockedError("Execution block: duplicate idempotency key already recorded.") from exc
        db.refresh(request)
        return request

    def _complete_execution_request(
        self,
        db: Session,
        request: ExecutionRequest,
        status: str,
        broker_order_id: str | None = None,
        error: str | None = None,
    ) -> None:
        request.status = status
        request.broker_order_id = broker_order_id
        request.error = error[:255] if error else None
        request.completed_at = datetime.now(UTC)
        db.add(request)
        db.commit()
        db.refresh(request)

    async def _reconciliation_block_reason(self, db: Session, snapshot: MarketSnapshot) -> str | None:
        if not self.settings.can_place_live_orders:
            return None

        broker_position = Decimal(str(await self._get_authoritative_live_position_quantity(snapshot.execution_symbol)))

        # Strict replay/idempotency policy: never re-enter BUY while a live position is already open.
        if (
            self.settings.strict_no_reentry_enabled
            and snapshot.suggested_action == "BUY"
            and broker_position > Decimal("0")
        ):
            return (
                "Execution block: active broker position already open; "
                "strict idempotency policy prevents duplicate re-entry."
            )

        if snapshot.suggested_action == "SELL" and broker_position <= 0:
            return "Execution block: broker reports no free balance to sell."

        return None

    async def _enforced_risk_block_reason(self, snapshot: MarketSnapshot) -> str | None:
        if snapshot.suggested_action not in {"BUY", "SELL"}:
            return None

        market_guard = await self._market_guard_block_reason(snapshot)
        if market_guard:
            return market_guard

        kill_switch = await self._daily_loss_kill_switch_block_reason(snapshot)
        if kill_switch:
            return kill_switch

        if snapshot.suggested_action != "BUY":
            return None

        max_position_quote = float(self.settings.risk_max_position_size_quote)
        max_portfolio_exposure = float(self.settings.risk_max_portfolio_exposure_quote)
        max_concurrent_positions = int(self.settings.risk_max_concurrent_positions)

        current_qty = float(await self._get_authoritative_live_position_quantity(snapshot.execution_symbol))
        current_position_quote = current_qty * snapshot.latest_price
        projected_position_quote = current_position_quote + float(self.settings.trade_amount_usdt)
        if max_position_quote > 0 and projected_position_quote > max_position_quote:
            return (
                f"Risk block: projected position {projected_position_quote:.2f} exceeds max "
                f"position size {max_position_quote:.2f}."
            )

        if max_portfolio_exposure > 0:
            current_portfolio = await self._current_portfolio_exposure_quote(snapshot)
            projected_portfolio = current_portfolio + float(self.settings.trade_amount_usdt)
            if projected_portfolio > max_portfolio_exposure:
                return (
                    f"Risk block: projected portfolio exposure {projected_portfolio:.2f} exceeds "
                    f"max {max_portfolio_exposure:.2f}."
                )

        if max_concurrent_positions > 0:
            open_positions = await self._count_open_positions(snapshot)
            if current_qty <= 0 and open_positions >= max_concurrent_positions:
                return (
                    f"Risk block: open positions {open_positions} exceeds max concurrent "
                    f"positions {max_concurrent_positions}."
                )

        return None

    async def _current_portfolio_exposure_quote(self, snapshot: MarketSnapshot) -> float:
        if self.settings.effective_execution_provider == "mt5" and len(self.settings.effective_mt5_symbols) > 1:
            symbols = self.settings.effective_mt5_symbols
        else:
            symbols = [snapshot.execution_symbol]
        total = 0.0
        for symbol in symbols:
            quantity = float(await self._get_authoritative_live_position_quantity(symbol))
            if quantity <= 0:
                continue
            price = snapshot.latest_price if symbol == snapshot.execution_symbol else await self._latest_price_for_symbol(symbol)
            total += abs(quantity) * price
        return total

    async def _count_open_positions(self, snapshot: MarketSnapshot) -> int:
        if self.settings.effective_execution_provider == "mt5" and len(self.settings.effective_mt5_symbols) > 1:
            symbols = self.settings.effective_mt5_symbols
        else:
            symbols = [snapshot.execution_symbol]
        count = 0
        for symbol in symbols:
            quantity = float(await self._get_authoritative_live_position_quantity(symbol))
            if quantity > 0:
                count += 1
        return count

    async def _market_guard_block_reason(self, snapshot: MarketSnapshot) -> str | None:
        max_spread_pct = float(self.settings.risk_max_spread_pct)
        max_slippage_pct = float(self.settings.risk_max_slippage_pct)
        if max_spread_pct <= 0 and max_slippage_pct <= 0:
            return None

        market_state: dict[str, float]
        if self.settings.effective_execution_provider == "mt5":
            market_state = await self.mt5.get_symbol_market_state(snapshot.execution_symbol)
            bid = float(market_state.get("bid") or 0.0)
            ask = float(market_state.get("ask") or 0.0)
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
            spread_pct = ((ask - bid) / mid * 100.0) if mid > 0 else 0.0
        else:
            market_state = await self.binance.get_symbol_market_state(snapshot.execution_symbol)
            mid = float(market_state.get("mid") or 0.0)
            spread_pct = float(market_state.get("spread_pct") or 0.0)

        if max_spread_pct > 0 and spread_pct > max_spread_pct:
            return f"Risk block: spread {spread_pct:.4f}% exceeds limit {max_spread_pct:.4f}%."

        if max_slippage_pct > 0 and mid > 0 and snapshot.latest_price > 0:
            slippage_pct = abs((snapshot.latest_price - mid) / snapshot.latest_price) * 100.0
            if slippage_pct > max_slippage_pct:
                return f"Risk block: slippage {slippage_pct:.4f}% exceeds limit {max_slippage_pct:.4f}%."

        return None

    async def _daily_loss_kill_switch_block_reason(self, snapshot: MarketSnapshot) -> str | None:
        kill_switch_pct = float(self.settings.risk_daily_loss_kill_switch_pct or self.settings.risk_daily_loss_limit_pct)
        if kill_switch_pct <= 0:
            return None

        if self.settings.effective_execution_provider == "mt5":
            account = await self.mt5.get_account_info()
            equity = float(account.get("equity") or account.get("balance") or 0.0)
        else:
            account = await self.binance.get_account_info()
            rules = await self.binance.get_exchange_info(snapshot.execution_symbol)
            base_free = float(self._extract_free_balance(account, rules.base_asset))
            quote_free = float(self._extract_free_balance(account, rules.quote_asset))
            equity = quote_free + (base_free * snapshot.latest_price)

        if equity <= 0:
            return None

        today = datetime.now(UTC).date()
        anchor = self._last_equity_anchor_by_day.get(today)
        if anchor is None:
            self._last_equity_anchor_by_day[today] = equity
            return None

        drawdown_pct = ((anchor - equity) / anchor) * 100 if anchor > 0 else 0.0
        if drawdown_pct >= kill_switch_pct:
            return f"Risk block: daily loss kill switch triggered at {drawdown_pct:.2f}% >= {kill_switch_pct:.2f}%"
        return None

    async def _execution_block_reason(
        self,
        db: Session,
        snapshot: MarketSnapshot,
        request_id: str | None = None,
    ) -> str | None:
        if snapshot.suggested_action not in {"BUY", "SELL"}:
            return None

        news_reason = await self._news_filter_block_reason(snapshot)
        if news_reason:
            return news_reason

        account_scope = self._current_execution_account_scope()

        pending = self._pending_execution_request(db, snapshot.execution_symbol, account_scope=account_scope)
        if pending is not None:
            return (
                f"Execution block: pending request {pending.client_order_id} for "
                f"{snapshot.execution_symbol} requires reconciliation."
            )

        idempotency_key = self._build_idempotency_key(snapshot, snapshot.suggested_action, request_id=request_id)
        existing = db.scalar(
            select(ExecutionRequest).where(
                ExecutionRequest.account_scope == account_scope,
                ExecutionRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return f"Execution block: duplicate idempotency key already recorded with status {existing.status}."

        min_seconds_between = int(self.settings.risk_min_seconds_between_trades)
        if min_seconds_between > 0:
            latest_trade = db.scalar(
                select(Trade)
                .where(
                    func.coalesce(Trade.execution_symbol, Trade.symbol) == snapshot.execution_symbol,
                    Trade.side.in_(["BUY", "SELL"]),
                    Trade.status.in_(["FILLED", "SIMULATED"]),
                )
                .order_by(desc(Trade.created_at))
                .limit(1)
            )
            if latest_trade and latest_trade.created_at:
                trade_time = latest_trade.created_at
                if trade_time.tzinfo is None:
                    trade_time = trade_time.replace(tzinfo=UTC)
                elapsed = (datetime.now(UTC) - trade_time).total_seconds()
                if elapsed < min_seconds_between:
                    remaining = int(max(1, min_seconds_between - elapsed))
                    return (
                        "Risk block: cooldown active for "
                        f"{snapshot.execution_symbol}; wait {remaining}s before the next trade."
                    )

        if not self.settings.can_place_live_orders:
            return None
        if not self.settings.live_trading_enabled:
            return "Execution block: live trading is disarmed."

        symbol_toggle_reason = self._mt5_live_order_toggle_block_reason(
            snapshot.execution_symbol,
            snapshot.suggested_action,
        )
        if symbol_toggle_reason:
            return symbol_toggle_reason

        mt5_limit_reason = await self._mt5_active_positions_block_reason(snapshot)
        if mt5_limit_reason:
            return mt5_limit_reason

        reconcile_reason = await self._reconciliation_block_reason(db, snapshot)
        if reconcile_reason:
            return reconcile_reason

        portfolio_reason = await self._portfolio_risk_block_reason(db, snapshot)
        if portfolio_reason:
            return portfolio_reason

        enforced_risk_reason = await self._enforced_risk_block_reason(snapshot)
        if enforced_risk_reason:
            return enforced_risk_reason

        if self.settings.effective_execution_provider != "mt5":
            return None

        daily_limit = float(self.settings.risk_daily_loss_limit_pct)
        if daily_limit > 0:
            account = await self.mt5.get_account_info()
            equity = float(account.get("equity") or account.get("balance") or 0.0)
            if equity > 0:
                today = datetime.now(UTC).date()
                if self._daily_equity_anchor_date != today or self._daily_equity_anchor_value is None:
                    self._daily_equity_anchor_date = today
                    self._daily_equity_anchor_value = equity
                anchor = float(self._daily_equity_anchor_value or 0.0)
                if anchor > 0:
                    drawdown_pct = ((anchor - equity) / anchor) * 100
                    if drawdown_pct >= daily_limit:
                        return (
                            f"Risk block: daily drawdown {drawdown_pct:.2f}% exceeds limit {daily_limit:.2f}%"
                        )

        max_spread_pips = float(self.settings.risk_max_spread_pips)
        if max_spread_pips > 0:
            market_state = await self.mt5.get_symbol_market_state(snapshot.execution_symbol)
            spread_pips = float(market_state.get("spread_pips") or 0.0)
            if spread_pips > max_spread_pips:
                return (
                    f"Risk block: spread {spread_pips:.2f} pips exceeds limit {max_spread_pips:.2f} pips"
                )

        return None

    async def _mt5_active_positions_block_reason(self, snapshot: MarketSnapshot) -> str | None:
        # Account-wide MT5 cap: block opening new entries once active positions hit limit.
        if snapshot.suggested_action not in {"BUY", "SELL"}:
            return None
        if self.settings.effective_execution_provider != "mt5":
            return None

        cap = max(0, int(self.settings.risk_mt5_max_active_positions))
        if cap <= 0:
            return None

        active_positions = int(await self.mt5.get_active_positions_count())
        if active_positions >= cap:
            return (
                f"Risk block: MT5 active positions {active_positions} reached cap {cap}. "
                "Close positions before opening new trades."
            )
        return None

    async def _portfolio_risk_block_reason(self, db: Session, snapshot: MarketSnapshot) -> str | None:
        corr_cap = float(self.settings.risk_correlation_cap)
        var_limit = float(self.settings.risk_portfolio_var_limit_pct)
        es_limit = float(self.settings.risk_portfolio_es_limit_pct)
        if corr_cap <= 0 and var_limit <= 0 and es_limit <= 0:
            return None

        exposure_map = await self._build_portfolio_exposure_map(db, snapshot)
        active_symbols = [symbol for symbol, notional in exposure_map.items() if notional > 0]
        if not active_symbols:
            return None

        lookback = max(30, int(self.settings.risk_var_lookback_candles))
        returns_map = await self._build_symbol_returns_map(active_symbols, lookback)
        if snapshot.execution_symbol not in returns_map:
            return None

        # Correlation cap applies when the current signal can increase gross exposure.
        if corr_cap > 0 and snapshot.suggested_action == "BUY":
            target_returns = returns_map.get(snapshot.execution_symbol, [])
            if len(target_returns) >= 5:
                for symbol in active_symbols:
                    if symbol == snapshot.execution_symbol:
                        continue
                    corr = self._pearson_corr(target_returns, returns_map.get(symbol, []))
                    if corr is not None and abs(corr) > corr_cap:
                        return (
                            "Risk block: correlation cap exceeded for "
                            f"{snapshot.execution_symbol} vs {symbol} ({corr:.2f} > {corr_cap:.2f})."
                        )

        portfolio_returns = self._build_portfolio_return_series(exposure_map, returns_map)
        if len(portfolio_returns) < 10:
            return None

        confidence = min(max(float(self.settings.risk_var_confidence), 0.5), 0.999)
        var_pct, es_pct = self._portfolio_loss_metrics(portfolio_returns, confidence)

        if var_limit > 0 and var_pct > var_limit:
            return f"Risk block: portfolio VaR {var_pct:.2f}% exceeds limit {var_limit:.2f}%"
        if es_limit > 0 and es_pct > es_limit:
            return f"Risk block: portfolio ES {es_pct:.2f}% exceeds limit {es_limit:.2f}%"

        return None

    async def _build_portfolio_exposure_map(self, db: Session, snapshot: MarketSnapshot) -> dict[str, float]:
        symbols: list[str]
        if (
            self.settings.effective_execution_provider == "mt5"
            and len(self.settings.effective_mt5_symbols) > 1
        ):
            symbols = list(self.settings.effective_mt5_symbols)
        else:
            symbols = [snapshot.execution_symbol]

        exposures: dict[str, float] = {}
        for symbol in symbols:
            if symbol == snapshot.execution_symbol:
                latest_price = snapshot.latest_price
            else:
                latest_price = await self._latest_price_for_symbol(symbol)

            signal_symbol = self._get_signal_symbol(symbol)
            quantity = abs(self.get_position_quantity_for_symbol(db, signal_symbol))
            exposures[symbol] = max(0.0, quantity * latest_price)

        # Project impact of the pending action for conservative pre-trade checks.
        if snapshot.suggested_action == "BUY":
            exposures[snapshot.execution_symbol] = exposures.get(snapshot.execution_symbol, 0.0) + float(
                self.settings.trade_amount_usdt
            )
        elif snapshot.suggested_action == "SELL":
            exposures[snapshot.execution_symbol] = max(
                0.0,
                exposures.get(snapshot.execution_symbol, 0.0) - (snapshot.position_quantity * snapshot.latest_price),
            )

        return exposures

    async def _latest_price_for_symbol(self, symbol: str) -> float:
        errors: list[str] = []
        for provider, candidate_symbol, client in self._market_data_candidates(symbol_override=symbol):
            try:
                klines = await client.get_klines(
                    symbol=candidate_symbol,
                    interval=self.settings.effective_strategy_candle_interval,
                    limit=2,
                )
                return float(klines[-1][4])
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                errors.append(f"{provider}: {detail}")
        raise RuntimeError(" | ".join(errors) or f"Unable to fetch latest price for {symbol}")

    async def _build_symbol_returns_map(self, symbols: list[str], lookback: int) -> dict[str, list[float]]:
        returns_map: dict[str, list[float]] = {}
        for symbol in symbols:
            prices: list[float] | None = None
            for provider, candidate_symbol, client in self._market_data_candidates(symbol_override=symbol):
                try:
                    klines = await client.get_klines(
                        symbol=candidate_symbol,
                        interval=self.settings.effective_strategy_candle_interval,
                        limit=lookback,
                    )
                    prices = [float(item[4]) for item in klines]
                    break
                except Exception:
                    continue
            if not prices or len(prices) < 3:
                continue
            returns = [((curr / prev) - 1.0) for prev, curr in zip(prices[:-1], prices[1:]) if prev > 0]
            if len(returns) >= 2:
                returns_map[symbol] = returns
        return returns_map

    def _build_portfolio_return_series(
        self,
        exposure_map: dict[str, float],
        returns_map: dict[str, list[float]],
    ) -> list[float]:
        supported = [(symbol, notional) for symbol, notional in exposure_map.items() if symbol in returns_map and notional > 0]
        if not supported:
            return []
        min_len = min(len(returns_map[symbol]) for symbol, _ in supported)
        if min_len < 2:
            return []

        total_notional = sum(notional for _, notional in supported)
        if total_notional <= 0:
            return []

        weighted: list[float] = []
        for index in range(-min_len, 0):
            portfolio_return = 0.0
            for symbol, notional in supported:
                weight = notional / total_notional
                portfolio_return += returns_map[symbol][index] * weight
            weighted.append(portfolio_return)
        return weighted

    def _portfolio_loss_metrics(self, portfolio_returns: list[float], confidence: float) -> tuple[float, float]:
        losses = sorted(max(0.0, -value) for value in portfolio_returns)
        if not losses:
            return 0.0, 0.0

        quantile_index = min(len(losses) - 1, max(0, int(confidence * (len(losses) - 1))))
        var = losses[quantile_index]
        tail = [loss for loss in losses if loss >= var]
        es = mean(tail) if tail else var
        return var * 100.0, es * 100.0

    def _pearson_corr(self, left: list[float], right: list[float]) -> float | None:
        size = min(len(left), len(right))
        if size < 2:
            return None
        xs = left[-size:]
        ys = right[-size:]
        mean_x = sum(xs) / size
        mean_y = sum(ys) / size
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den_left = sum((x - mean_x) ** 2 for x in xs) ** 0.5
        den_right = sum((y - mean_y) ** 2 for y in ys) ** 0.5
        denom = den_left * den_right
        if denom <= 0:
            return None
        return num / denom

    async def run_startup_self_check(self, db: Session) -> dict[str, Any]:
        self.settings.validate_live_trading_configuration()

        pending = db.scalar(
            select(func.count()).select_from(ExecutionRequest).where(ExecutionRequest.status == "PENDING")
        )
        if pending:
            raise RuntimeError("Startup self-check failed: pending execution requests require reconciliation.")

        providers_checked: list[str] = []
        for provider, candidate_symbol, client in self._market_data_candidates():
            try:
                await client.get_klines(
                    symbol=candidate_symbol,
                    interval=self.settings.effective_strategy_candle_interval,
                    limit=max(2, min(self.settings.candle_limit, 5)),
                )
                providers_checked.append(provider)
                break
            except Exception:
                continue

        if not providers_checked:
            raise RuntimeError("Startup self-check failed: could not fetch market data from any configured provider.")

        result: dict[str, Any] = {
            "market_data_provider": providers_checked[0],
            "execution_provider": self.settings.effective_execution_provider,
            "live_trading_enabled": self.settings.live_trading_enabled,
        }

        if self.settings.can_place_live_orders:
            if self.settings.effective_execution_provider == "binance":
                await self.binance.get_account_info()
                await self.binance.get_exchange_info(self.settings.trading_symbol)
            else:
                await self.mt5.get_account_info()
                await self.mt5.get_exchange_info(self.settings.mt5_symbol)
                mt5_ready = await self.mt5.check_auto_execution_ready(self.settings.mt5_symbol)
                result["mt5_auto_execution_check"] = mt5_ready
                if not bool(mt5_ready.get("ready", False)):
                    if self._is_mt5_readiness_na(mt5_ready):
                        result["mt5_auto_execution_check_ignored"] = True
                        return result
                    raise RuntimeError(
                        "Startup self-check failed: MT5 auto execution is not ready "
                        f"for {self.settings.mt5_symbol}."
                    )

        return result

    def _get_market_data_client(self, provider: str) -> Any:
        if provider == "binance":
            return self.binance
        if provider in self._market_data_clients:
            return self._market_data_clients[provider]
        if self._market_data_client and self.settings.effective_market_data_provider == provider:
            return self._market_data_client
        if provider == "coinbase":
            return CoinbaseMarketDataClient(self.settings)
        if provider == "kraken":
            return KrakenMarketDataClient(self.settings)
        if provider == "okx":
            return OkxMarketDataClient(self.settings)
        if provider == "mt5":
            return self.mt5
        raise ValueError(f"Unsupported market data provider: {provider}")

    def _get_market_data_symbol_for_provider(self, provider: str, symbol_override: str | None = None) -> str:
        if symbol_override:
            return symbol_override
        if provider == "mt5":
            return self.settings.mt5_symbol
        if self.settings.market_data_symbol:
            return self.settings.market_data_symbol
        if provider == "coinbase":
            return _to_coinbase_product_id(self.settings.trading_symbol)
        if provider == "kraken":
            return _to_kraken_symbol(self.settings.trading_symbol)
        if provider == "okx":
            return _to_dash_symbol(self.settings.trading_symbol)
        return self.settings.trading_symbol

    def _market_data_candidates(self, symbol_override: str | None = None) -> list[tuple[str, str, Any]]:
        provider = self.settings.effective_market_data_provider
        if provider == "auto":
            candidates: list[tuple[str, str, Any]] = []
            for item in self.settings.auto_market_data_priority:
                candidates.append(
                    (item, self._get_market_data_symbol_for_provider(item, symbol_override), self._get_market_data_client(item))
                )
            return candidates
        return [
            (provider, self._get_market_data_symbol_for_provider(provider, symbol_override), self._get_market_data_client(provider))
        ]

    def _extract_series(self, klines: list[list[Any]]) -> dict[str, list[float]]:
        timestamps = [int(item[0]) for item in klines]
        closes = [float(item[4]) for item in klines]
        highs = [float(item[2]) for item in klines]
        lows = [float(item[3]) for item in klines]
        volumes = [float(item[5]) for item in klines]
        return {"timestamps": timestamps, "closes": closes, "highs": highs, "lows": lows, "volumes": volumes}

    async def build_snapshot_for_symbol(self, db: Session, market_data_symbol: str) -> MarketSnapshot:
        return await self._build_snapshot(db, symbol_override=market_data_symbol)

    async def build_snapshot_with_runtime_settings(
        self,
        db: Session,
        runtime_settings: Settings,
        market_data_symbol: str | None = None,
    ) -> MarketSnapshot:
        preview_service = TradingService(runtime_settings)
        if market_data_symbol:
            return await preview_service.build_snapshot_for_symbol(db, market_data_symbol)
        return await preview_service.build_snapshot(db)

    async def build_snapshot(self, db: Session) -> MarketSnapshot:
        return await self._build_snapshot(db)

    async def _build_snapshot(self, db: Session, symbol_override: str | None = None) -> MarketSnapshot:
        errors: list[str] = []
        selected_provider = self.settings.effective_market_data_provider
        market_data_symbol = symbol_override or self.settings.effective_market_data_symbol
        signal_symbol = self._get_signal_symbol(symbol_override)
        execution_symbol = self._get_execution_symbol(symbol_override)
        series: dict[str, list[float]] | None = None
        rules: SymbolRules | None = None

        for provider, candidate_symbol, client in self._market_data_candidates(symbol_override=symbol_override):
            try:
                klines = await client.get_klines(
                    symbol=candidate_symbol,
                    interval=self.settings.effective_strategy_candle_interval,
                    limit=self.settings.candle_limit,
                )
                series = self._extract_series(klines)
                rules = await self._get_sizing_rules(
                    provider,
                    candidate_symbol,
                    client,
                    execution_symbol_override=symbol_override,
                )
                selected_provider = provider
                market_data_symbol = candidate_symbol
                break
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                errors.append(f"{provider}: {detail}")

        if series is None or rules is None:
            raise RuntimeError(" | ".join(errors) or "No market data providers succeeded.")

        closes = series["closes"]
        latest_price = closes[-1]
        rsi = calculate_rsi(closes, self.settings.rsi_period)
        atr_pct_value = self._atr_pct_from_series(series["highs"], series["lows"], closes, self.settings.atr_recovery_atr_period)
        atr_value = latest_price * atr_pct_value if latest_price > 0 and atr_pct_value > 0 else None

        if self.settings.can_place_live_orders:
            position_quantity = await self._get_authoritative_live_position_quantity(execution_symbol)
        else:
            position_quantity = self._get_journal_position_quantity_for_symbol(db, signal_symbol)

        decision: StrategyDecision = self.strategy_engine.evaluate(series, self.settings.effective_strategy_candle_interval)
        strategy_vote_action = decision.action
        suggested_action = strategy_vote_action
        ml_probability_up: float | None = None
        ml_action: str | None = None
        pro_analysis_vote_action: str | None = None
        pro_analysis_final_action: str | None = None
        pro_analysis_gate_blocked = False
        pro_analysis_gate_reasons: list[str] = []
        pro_analysis_session_name: str | None = None
        pro_analysis_session_allowed = True
        pro_analysis_quality_gate_passed = True
        pro_analysis_rr: float | None = None

        if self.settings.ml_enabled:
            live_features = self.ml_service.build_live_features(
                series=series,
                confidence=decision.confidence,
                rsi=rsi,
                strategy_count=len(decision.selected_strategies),
            )
            ml_probability_up = self.ml_service.predict_up_probability(live_features)
            ml_action = self.ml_service.action_from_probability(
                ml_probability_up,
                buy_threshold=float(self.settings.ml_buy_probability_threshold),
                sell_threshold=float(self.settings.ml_sell_probability_threshold),
            )
            if ml_probability_up is not None:
                confirmation = float(self.settings.ml_confirmation_threshold)
                if self.settings.ml_override_strategy:
                    suggested_action = ml_action
                else:
                    if suggested_action == "BUY" and ml_probability_up < confirmation:
                        suggested_action = "HOLD"
                    elif suggested_action == "SELL" and ml_probability_up > (1.0 - confirmation):
                        suggested_action = "HOLD"

        if self._should_apply_pro_analysis_execution_plan(execution_symbol, selected_provider):
            try:
                plan = await self._generate_pro_analysis_execution_plan(execution_symbol)
            except Exception:
                plan = None
            if plan is not None:
                pro_analysis_vote_action = str(plan.get("weighted_vote_action", "HOLD")).upper()
                pro_analysis_final_action = str(plan.get("final_action", "HOLD")).upper()
                pro_analysis_session_name = str(plan.get("session_name", "") or "")
                pro_analysis_session_allowed = bool(plan.get("session_allowed", True))
                pro_analysis_quality_gate_passed = bool(plan.get("quality_gate_passed", True))
                pro_analysis_gate_reasons = [str(item) for item in (plan.get("quality_gate_reasons") or []) if str(item).strip()]

                trade_idea = plan.get("trade_idea") or {}
                rr_value = trade_idea.get("risk_to_reward_tp2")
                pro_analysis_rr = float(rr_value) if rr_value is not None else None

                if suggested_action in {"BUY", "SELL"}:
                    if pro_analysis_vote_action not in {"BUY", "SELL"}:
                        pro_analysis_gate_reasons.append("Upgraded multi-timeframe vote did not produce a directional bias.")
                    elif pro_analysis_vote_action != suggested_action:
                        pro_analysis_gate_reasons.append(
                            f"Upgraded multi-timeframe vote is {pro_analysis_vote_action}, which opposes the base signal {suggested_action}."
                        )
                    elif pro_analysis_final_action != suggested_action:
                        if not pro_analysis_gate_reasons:
                            pro_analysis_gate_reasons.append("Upgraded multi-timeframe quality gate rejected the setup.")
                    else:
                        if trade_idea.get("stop_loss") is not None:
                            decision.stop_loss = float(trade_idea["stop_loss"])
                        if trade_idea.get("take_profit_2") is not None:
                            decision.take_profit = float(trade_idea["take_profit_2"])

                    if pro_analysis_vote_action != suggested_action or pro_analysis_final_action != suggested_action:
                        suggested_action = "HOLD"
                        pro_analysis_gate_blocked = True
                        pro_analysis_quality_gate_passed = False

        pre_confidence_action = suggested_action
        confidence_gate_threshold = self._confidence_threshold_for_action(pre_confidence_action)
        confidence_gate_blocked = False
        if suggested_action in {"BUY", "SELL"} and float(decision.confidence) < confidence_gate_threshold:
            suggested_action = "HOLD"
            confidence_gate_blocked = True

        last_candle_open_time = series["timestamps"][-1] if series.get("timestamps") else None
        pre_candle_close_action = suggested_action
        candle_close_gate_enabled = bool(self.settings.strict_candle_close_enabled)
        seconds_until_candle_close = self._seconds_until_candle_close(last_candle_open_time)
        candle_close_gate_blocked = False
        if candle_close_gate_enabled and suggested_action in {"BUY", "SELL"}:
            if seconds_until_candle_close is not None and seconds_until_candle_close > 0:
                suggested_action = "HOLD"
                candle_close_gate_blocked = True

        if position_quantity <= 0 and suggested_action == "SELL":
            suggested_action = "HOLD"

        atr_recovery_profile = self._build_atr_recovery_profile(
            highs=series["highs"],
            lows=series["lows"],
            closes=closes,
            symbol_key=(execution_symbol if execution_symbol in set(self.settings.effective_mt5_symbols) else signal_symbol),
            action=suggested_action,
            latest_price=latest_price,
        )
        if atr_recovery_profile.get("execution_overlay_active") and suggested_action in {"BUY", "SELL"}:
            overlay_stop = atr_recovery_profile.get("stop_loss")
            overlay_take = atr_recovery_profile.get("take_profit")
            if overlay_stop is not None:
                decision.stop_loss = float(overlay_stop)
            if overlay_take is not None:
                decision.take_profit = float(overlay_take)

        return MarketSnapshot(
            signal_symbol=signal_symbol,
            market_data_symbol=market_data_symbol,
            execution_symbol=execution_symbol,
            market_data_provider=selected_provider,
            latest_price=latest_price,
            rsi=rsi,
            position_quantity=position_quantity,
            suggested_action=suggested_action,
            strategy_vote_action=strategy_vote_action,
            rules=rules,
            last_candle_open_time=last_candle_open_time,
            confidence=decision.confidence,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            regime=decision.regime,
            strategy_names=[item.name for item in decision.selected_strategies],
            strategy_details=[
                {
                    "name": item.name,
                    "action": item.action,
                    "confidence": item.confidence,
                    "entry_price": item.entry_price,
                    "stop_loss": item.stop_loss,
                    "take_profit": item.take_profit,
                    "entry_rule": item.entry_rule,
                    "exit_rule": item.exit_rule,
                }
                for item in decision.all_strategies
            ],
            strategy_weights={
                item.name: round(float(getattr(item, "confidence", decision.confidence)), 4)
                for item in decision.selected_strategies
            },
            ml_probability_up=ml_probability_up,
            ml_action=ml_action,
            pre_candle_close_action=pre_candle_close_action,
            candle_close_gate_blocked=candle_close_gate_blocked,
            candle_close_gate_enabled=candle_close_gate_enabled,
            seconds_until_candle_close=seconds_until_candle_close,
            pre_confidence_action=pre_confidence_action,
            confidence_gate_blocked=confidence_gate_blocked,
            confidence_gate_threshold=confidence_gate_threshold,
            pro_analysis_vote_action=pro_analysis_vote_action,
            pro_analysis_final_action=pro_analysis_final_action,
            pro_analysis_gate_blocked=pro_analysis_gate_blocked,
            pro_analysis_gate_reasons=pro_analysis_gate_reasons,
            pro_analysis_session_name=pro_analysis_session_name,
            pro_analysis_session_allowed=pro_analysis_session_allowed,
            pro_analysis_quality_gate_passed=pro_analysis_quality_gate_passed,
            pro_analysis_rr=pro_analysis_rr,
            atr_pct=atr_pct_value,
            atr_value=atr_value,
            atr_recovery_active=bool(atr_recovery_profile.get("active")),
            atr_recovery_symbol_enabled=bool(atr_recovery_profile.get("symbol_enabled")),
            atr_recovery_profile=atr_recovery_profile,
        )

    async def _get_authoritative_live_position_quantity(self, execution_symbol: str) -> float:
        if self.settings.effective_execution_provider == "mt5":
            # Always refresh from broker for live checks to avoid stale cache allowing replay buys.
            quantity = float(await self.mt5.get_open_position_volume(execution_symbol))
            self._broker_positions[execution_symbol] = quantity
            return quantity
        if execution_symbol in self._broker_positions:
            return float(self._broker_positions.get(execution_symbol, 0.0))
        if self.settings.effective_execution_provider == "binance":
            account = await self.binance.get_account_info()
            rules = await self.binance.get_exchange_info(execution_symbol)
            quantity = float(self._extract_free_balance(account, rules.base_asset))
            self._broker_positions[execution_symbol] = quantity
            return quantity
        return 0.0

    async def _get_sizing_rules(
        self,
        provider: str,
        market_data_symbol: str,
        client: Any,
        execution_symbol_override: str | None = None,
    ) -> SymbolRules:
        if self.settings.can_place_live_orders and self.settings.effective_execution_provider == "binance":
            return await self.binance.get_exchange_info(self.settings.trading_symbol)
        if self.settings.can_place_live_orders and self.settings.effective_execution_provider == "mt5":
            return await self.mt5.get_exchange_info(execution_symbol_override or self.settings.mt5_symbol)
        return await client.get_exchange_info(market_data_symbol)

    def get_position_quantity(self, db: Session) -> float:
        return self.get_position_quantity_for_symbol(db, self._get_signal_symbol())

    def get_position_quantity_for_symbol(self, db: Session, symbol: str) -> float:
        if self.settings.can_place_live_orders:
            execution_symbol = self._get_execution_symbol(symbol)
            return round(float(self._broker_positions.get(execution_symbol, 0.0)), 8)
        return self._get_journal_position_quantity_for_symbol(db, symbol)

    def _get_journal_position_quantity_for_symbol(self, db: Session, symbol: str) -> float:
        buy_quantity = db.scalar(
            select(func.coalesce(func.sum(Trade.quantity), 0.0)).where(
                func.coalesce(Trade.signal_symbol, Trade.symbol) == symbol,
                Trade.side == "BUY",
                Trade.status.in_(["FILLED", "SIMULATED"]),
            )
        )
        sell_quantity = db.scalar(
            select(func.coalesce(func.sum(Trade.quantity), 0.0)).where(
                func.coalesce(Trade.signal_symbol, Trade.symbol) == symbol,
                Trade.side == "SELL",
                Trade.status.in_(["FILLED", "SIMULATED"]),
            )
        )
        return round(float(buy_quantity or 0.0) - float(sell_quantity or 0.0), 8)

    def _resolve_trade_outcome_label(self, realized_pnl: float | None) -> str:
        if realized_pnl is None:
            return "OPEN"
        if realized_pnl > 0:
            return "PROFIT"
        if realized_pnl < 0:
            return "LOSS"
        return "BREAKEVEN"

    def _open_mt5_trade_cycle(self, db: Session, snapshot: MarketSnapshot, trade: Trade) -> Mt5TradeCycle | None:
        if self.settings.effective_execution_provider != "mt5":
            return None
        if trade.side != "BUY":
            return None

        mt5_symbols = set(self.settings.effective_mt5_symbols)
        effective_symbol = (
            snapshot.execution_symbol
            if snapshot.execution_symbol in mt5_symbols
            else snapshot.signal_symbol
        )

        cycle = db.scalar(
            select(Mt5TradeCycle)
            .where(
                Mt5TradeCycle.owner_id == self.settings.mt5_runtime_owner_id,
                Mt5TradeCycle.execution_symbol == effective_symbol,
                Mt5TradeCycle.status == "OPEN",
            )
            .order_by(desc(Mt5TradeCycle.opened_at), desc(Mt5TradeCycle.id))
            .limit(1)
        )

        profile = snapshot.atr_recovery_profile or {}
        if cycle is None:
            cycle = Mt5TradeCycle(
                owner_id=self.settings.mt5_runtime_owner_id,
                signal_symbol=snapshot.signal_symbol,
                execution_symbol=effective_symbol,
                cycle_type="ATR_RECOVERY",
                base_direction="BUY",
                status="OPEN",
                atr_recovery_enabled=bool(snapshot.atr_recovery_active),
                entry_price=trade.fill_price or trade.price,
                latest_price=snapshot.latest_price,
                atr_pct=snapshot.atr_pct,
                atr_value=snapshot.atr_value,
                stop_loss=trade.entry_stop_loss,
                take_profit=trade.entry_take_profit,
                hedge_trigger=profile.get("hedge_trigger"),
                trailing_activation_price=profile.get("trailing_activation_price"),
                reversal_confirmation_price=profile.get("reversal_confirmation_price"),
                overlay_active=bool(profile.get("execution_overlay_active")),
                planned_hedge_only=not self.settings.atr_recovery_live_hedge_enabled,
                hedge_position_ticket=None,
                hedge_placed_at=None,
                hedge_sl_last_modified=None,
                hedge_cooldown_until=None,
                hedge_attempt_count=0,
                hedge_last_action_at=None,
                hedge_last_action_price=None,
                linked_trade_id=trade.id,
                notes=profile.get("note"),
            )
            db.add(cycle)
        else:
            cycle.signal_symbol = snapshot.signal_symbol
            cycle.atr_recovery_enabled = bool(snapshot.atr_recovery_active)
            cycle.latest_price = snapshot.latest_price
            cycle.atr_pct = snapshot.atr_pct
            cycle.atr_value = snapshot.atr_value
            cycle.stop_loss = trade.entry_stop_loss
            cycle.take_profit = trade.entry_take_profit
            cycle.hedge_trigger = profile.get("hedge_trigger")
            cycle.trailing_activation_price = profile.get("trailing_activation_price")
            cycle.reversal_confirmation_price = profile.get("reversal_confirmation_price")
            cycle.overlay_active = bool(profile.get("execution_overlay_active"))
            cycle.linked_trade_id = trade.id
            cycle.notes = profile.get("note")
            db.add(cycle)

        db.commit()
        db.refresh(cycle)
        return cycle

    def _close_mt5_trade_cycle(self, db: Session, snapshot: MarketSnapshot, trade: Trade) -> Mt5TradeCycle | None:
        if self.settings.effective_execution_provider != "mt5":
            return None
        if trade.side != "SELL":
            return None

        mt5_symbols = set(self.settings.effective_mt5_symbols)
        effective_symbol = (
            snapshot.execution_symbol
            if snapshot.execution_symbol in mt5_symbols
            else snapshot.signal_symbol
        )

        cycle = db.scalar(
            select(Mt5TradeCycle)
            .where(
                Mt5TradeCycle.owner_id == self.settings.mt5_runtime_owner_id,
                Mt5TradeCycle.execution_symbol == effective_symbol,
                Mt5TradeCycle.status == "OPEN",
            )
            .order_by(desc(Mt5TradeCycle.opened_at), desc(Mt5TradeCycle.id))
            .limit(1)
        )
        if cycle is None:
            return None

        cycle.latest_price = snapshot.latest_price
        cycle.status = "CLOSED"
        cycle.closed_at = datetime.now(UTC)
        cycle.close_reason = "SELL_EXIT"
        cycle.notes = f"{cycle.notes or ''} | closed_via_sell_exit".strip(" |")
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        return cycle

    def list_mt5_trade_cycles(self, db: Session, owner_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self.settings.effective_execution_provider != "mt5":
            return []
        effective_owner = (owner_id or self.settings.mt5_runtime_owner_id).strip() or "local"
        cycles = list(
            db.scalars(
                select(Mt5TradeCycle)
                .where(Mt5TradeCycle.owner_id == effective_owner)
                .order_by(desc(Mt5TradeCycle.updated_at), desc(Mt5TradeCycle.id))
                .limit(max(1, min(limit, 100)))
            )
        )
        return [
            {
                "id": row.id,
                "owner_id": row.owner_id,
                "signal_symbol": row.signal_symbol,
                "execution_symbol": row.execution_symbol,
                "cycle_type": row.cycle_type,
                "base_direction": row.base_direction,
                "status": row.status,
                "atr_recovery_enabled": row.atr_recovery_enabled,
                "entry_price": row.entry_price,
                "latest_price": row.latest_price,
                "atr_pct": row.atr_pct,
                "atr_value": row.atr_value,
                "stop_loss": row.stop_loss,
                "take_profit": row.take_profit,
                "hedge_trigger": row.hedge_trigger,
                "trailing_activation_price": row.trailing_activation_price,
                "reversal_confirmation_price": row.reversal_confirmation_price,
                "overlay_active": row.overlay_active,
                "planned_hedge_only": row.planned_hedge_only,
                "hedge_position_ticket": row.hedge_position_ticket,
                "hedge_placed_at": row.hedge_placed_at.isoformat() if row.hedge_placed_at else None,
                "hedge_sl_last_modified": row.hedge_sl_last_modified,
                "hedge_cooldown_until": row.hedge_cooldown_until.isoformat() if row.hedge_cooldown_until else None,
                "hedge_attempt_count": row.hedge_attempt_count,
                "hedge_last_action_at": row.hedge_last_action_at.isoformat() if row.hedge_last_action_at else None,
                "hedge_last_action_price": row.hedge_last_action_price,
                "linked_trade_id": row.linked_trade_id,
                "close_reason": row.close_reason,
                "notes": row.notes,
                "opened_at": row.opened_at.isoformat() if row.opened_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            }
            for row in cycles
        ]

    def list_startup_pending_execution_jobs(
        self,
        db: Session,
        owner_id: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_owner = (owner_id or self.settings.mt5_runtime_owner_id).strip() or "local"
        jobs = list(
            db.scalars(
                select(Mt5ExecutionJob)
                .where(
                    Mt5ExecutionJob.owner_id == effective_owner,
                    Mt5ExecutionJob.status.in_(["QUEUED", "CLAIMED"]),
                )
                .order_by(Mt5ExecutionJob.submitted_at.asc(), Mt5ExecutionJob.id.asc())
            )
        )
        return [
            {
                "job_id": row.id,
                "client_order_id": row.client_order_id,
                "signal_symbol": row.signal_symbol,
                "execution_symbol": row.execution_symbol,
                "action": row.action,
                "volume": row.volume,
                "stop_loss": row.stop_loss,
                "take_profit": row.take_profit,
                "status": row.status,
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
                "source": "queued_job",
            }
            for row in jobs
        ]

    def cancel_startup_pending_execution_jobs(
        self,
        db: Session,
        owner_id: str | None = None,
    ) -> list[dict[str, Any]]:
        effective_owner = (owner_id or self.settings.mt5_runtime_owner_id).strip() or "local"
        jobs = list(
            db.scalars(
                select(Mt5ExecutionJob)
                .where(
                    Mt5ExecutionJob.owner_id == effective_owner,
                    Mt5ExecutionJob.status.in_(["QUEUED", "CLAIMED"]),
                )
                .order_by(Mt5ExecutionJob.submitted_at.asc(), Mt5ExecutionJob.id.asc())
            )
        )
        cancelled: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for row in jobs:
            row.status = "CANCELLED"
            row.error = "Cancelled by startup execution review."
            row.completed_at = now
            db.add(row)
            if row.execution_request_id is not None:
                request = db.get(ExecutionRequest, row.execution_request_id)
                if request is not None:
                    request.status = "CANCELLED"
                    request.error = row.error
                    request.completed_at = now
                    db.add(request)
            cancelled.append(
                {
                    "job_id": row.id,
                    "execution_symbol": row.execution_symbol,
                    "action": row.action,
                    "client_order_id": row.client_order_id,
                }
            )
        if jobs:
            db.commit()
        return cancelled

    async def build_startup_execution_review(self, db: Session) -> dict[str, Any]:
        generated_at = datetime.now(UTC).isoformat()
        items: list[dict[str, Any]] = []

        queued_jobs = self.list_startup_pending_execution_jobs(db)
        items.extend(
            {
                "source": "queued_job",
                "symbol": item.get("execution_symbol"),
                "signal_symbol": item.get("signal_symbol"),
                "execution_symbol": item.get("execution_symbol"),
                "action": item.get("action"),
                "confidence": None,
                "latest_price": None,
                "stop_loss": item.get("stop_loss"),
                "take_profit": item.get("take_profit"),
                "execution_block": None,
                "analysis_generated_at": item.get("submitted_at"),
                "analysis_age": "past",
                "client_order_id": item.get("client_order_id"),
                "volume": item.get("volume"),
                "status": item.get("status"),
                "message": "Previously queued MT5 execution waiting for confirmation.",
            }
            for item in queued_jobs
        )

        for symbol in self.settings.effective_mt5_symbols:
            try:
                snapshot = await self.build_snapshot_for_symbol(db, symbol)
                block_reason = None
                if snapshot.suggested_action in {"BUY", "SELL"}:
                    block_reason = await self._execution_block_reason(
                        db,
                        snapshot,
                        request_id=f"startup-review:{symbol}:{int(datetime.now(UTC).timestamp())}",
                    )
                items.append(
                    {
                        "source": "startup_analysis",
                        "symbol": symbol,
                        "signal_symbol": snapshot.signal_symbol,
                        "execution_symbol": snapshot.execution_symbol,
                        "action": snapshot.suggested_action,
                        "strategy_vote_action": snapshot.strategy_vote_action,
                        "confidence": snapshot.confidence,
                        "latest_price": snapshot.latest_price,
                        "stop_loss": snapshot.stop_loss,
                        "take_profit": snapshot.take_profit,
                        "execution_block": block_reason,
                        "analysis_generated_at": generated_at,
                        "analysis_age": "current",
                        "message": "Current first-boot analysis preview.",
                    }
                )
            except Exception as exc:
                items.append(
                    {
                        "source": "startup_analysis",
                        "symbol": symbol,
                        "signal_symbol": symbol,
                        "execution_symbol": symbol,
                        "action": "N/A",
                        "strategy_vote_action": None,
                        "confidence": None,
                        "latest_price": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "execution_block": str(exc),
                        "analysis_generated_at": generated_at,
                        "analysis_age": "current",
                        "message": "Current first-boot analysis failed.",
                    }
                )

        actionable_count = sum(
            1
            for item in items
            if item.get("source") == "startup_analysis"
            and item.get("action") in {"BUY", "SELL"}
            and not item.get("execution_block")
        )
        queued_job_count = len(queued_jobs)
        requires_confirmation = bool(actionable_count or queued_job_count)
        message = None
        if requires_confirmation:
            message = (
                "First boot review is holding MT5 automation. Review current intended actions and any carried-over queued jobs, "
                "then allow execution or cancel."
            )

        return {
            "status": "pending_confirmation" if requires_confirmation else "not_required",
            "generated_at": generated_at,
            "message": message,
            "requires_confirmation": requires_confirmation,
            "items": items,
            "actionable_count": actionable_count,
            "queued_job_count": queued_job_count,
        }

    async def _execute_hedge_sell(
        self,
        db: Session,
        cycle: Mt5TradeCycle,
        current_bid: float,
    ) -> Mt5TradeCycle | None:
        """Place a live SELL hedge order for an open BUY cycle.

        Only runs when ``atr_recovery_live_hedge_enabled`` is True and the
        cycle does not already have a hedge ticket.  Volume is matched to the
        linked BUY trade when possible; falls back to ``mt5_volume_lots``.
        """
        if not self.settings.atr_recovery_live_hedge_enabled:
            return None
        if cycle.hedge_position_ticket is not None:
            return cycle

        symbol = cycle.execution_symbol
        volume = Decimal(str(self.settings.mt5_volume_lots))

        if cycle.linked_trade_id is not None:
            linked = db.scalar(select(Trade).where(Trade.id == cycle.linked_trade_id))
            if linked is not None and linked.quantity and float(linked.quantity) > 0:
                volume = Decimal(str(linked.quantity))

        try:
            result = await self.mt5.place_market_sell(
                symbol,
                volume,
                client_order_id=f"hedge-{cycle.id}",
            )
        except Exception as exc:
            logging.error("[hedge] Failed to place SELL hedge for cycle %s on %s: %s", cycle.id, symbol, exc)
            return None

        ticket_raw = result.get("order") or result.get("position")
        try:
            ticket = int(ticket_raw)
        except (TypeError, ValueError):
            logging.warning("[hedge] Placed hedge for cycle %s but could not parse ticket: %s", cycle.id, ticket_raw)
            ticket = None

        cycle.hedge_position_ticket = ticket
        now = datetime.now(UTC)
        cycle.hedge_placed_at = now
        cycle.planned_hedge_only = False
        cycle.hedge_attempt_count = int(cycle.hedge_attempt_count or 0) + 1
        cycle.hedge_last_action_at = now
        cycle.hedge_last_action_price = current_bid
        cooldown_seconds = max(0, int(self.settings.atr_recovery_hedge_cooldown_seconds))
        cycle.hedge_cooldown_until = now + timedelta(seconds=cooldown_seconds) if cooldown_seconds > 0 else None
        cycle.notes = f"{cycle.notes or ''} | live_hedge_placed".strip(" |")
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        logging.info("[hedge] Live SELL hedge placed for cycle %s on %s (ticket=%s)", cycle.id, symbol, ticket)
        return cycle

    def _evaluate_reversal_confirmation(
        self,
        cycle: Mt5TradeCycle,
        current_bid: float,
        highs: list[float] | None = None,
        lows: list[float] | None = None,
        closes: list[float] | None = None,
    ) -> bool:
        """Return True when a bullish reversal is confirmed strongly enough to
        justify closing the SELL hedge.

        Primary gate (always required):
          - Price has risen to or above ``reversal_confirmation_price``, which
            represents ATR-extension above the hedge stress zone.

        Secondary gates (applied when OHLC data is provided):
          - Market structure is bullish (HH/HL pattern).
          - EMA50 > EMA200 on the provided close series.
        """
        if cycle.reversal_confirmation_price is None:
            return False
        if current_bid < float(cycle.reversal_confirmation_price):
            return False

        if closes and len(closes) >= 2:
            ema50 = self._ema_value(closes, 50)
            ema200 = self._ema_value(closes, 200)
            if ema50 < ema200:
                return False

        if highs and lows and len(highs) >= 6 and len(lows) >= 6:
            if self._structure_bias(highs, lows) == "bearish":
                return False

        return True

    async def run_cycle_hedge_monitor(self, db: Session) -> dict[str, Any]:
        """Monitor all open BUY trade cycles and automate hedge lifecycle.

        For each open cycle this method:
        1. Refreshes ``latest_price`` from the MT5 broker.
        2. **Area 1** — places a live SELL hedge when price touches
           ``hedge_trigger`` and ``atr_recovery_live_hedge_enabled`` is True.
        3. **Area 2** — trails the hedge SL downward as price falls when
           ``atr_recovery_trailing_monitor_enabled`` is True.
        4. **Area 3** — closes the hedge automatically when reversal is
           confirmed and ``atr_recovery_auto_reversal_close`` is True.

        All three paths are gated by their own feature flag so they can be
        enabled independently.
        """
        if self.settings.effective_execution_provider != "mt5":
            return {"skipped": True, "reason": "non-MT5 execution provider"}

        owner_id = self.settings.mt5_runtime_owner_id
        open_cycles = list(
            db.scalars(
                select(Mt5TradeCycle)
                .where(
                    Mt5TradeCycle.owner_id == owner_id,
                    Mt5TradeCycle.status == "OPEN",
                    Mt5TradeCycle.hedge_trigger.is_not(None),
                )
                .order_by(Mt5TradeCycle.opened_at.asc())
            )
        )

        processed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for cycle in open_cycles:
            symbol = cycle.execution_symbol
            result: dict[str, Any] = {"cycle_id": cycle.id, "symbol": symbol}
            try:
                state = await self.mt5.get_symbol_market_state(symbol)
                current_bid = float(state.get("bid") or state.get("ask") or 0.0)
                if current_bid <= 0:
                    result["skipped"] = "invalid bid price"
                    processed.append(result)
                    continue

                cycle.latest_price = current_bid
                result["current_bid"] = current_bid

                # ── Area 1: place hedge when trigger is hit ─────────────────
                if (
                    self.settings.atr_recovery_live_hedge_enabled
                    and cycle.hedge_position_ticket is None
                    and cycle.hedge_trigger is not None
                    and current_bid <= float(cycle.hedge_trigger)
                ):
                    now = datetime.now(UTC)
                    max_hedges = max(0, int(self.settings.atr_recovery_max_hedges_per_cycle))
                    if max_hedges > 0 and int(cycle.hedge_attempt_count or 0) >= max_hedges:
                        result["hedge_block"] = "max_hedges_per_cycle"
                    elif cycle.hedge_cooldown_until and now < cycle.hedge_cooldown_until:
                        result["hedge_block"] = "cooldown_active"
                    else:
                        min_rehedge_delta_atr = max(0.0, float(self.settings.atr_recovery_min_price_delta_for_rehedge_atr))
                        if (
                            min_rehedge_delta_atr > 0
                            and cycle.atr_value is not None
                            and cycle.hedge_last_action_price is not None
                            and abs(current_bid - float(cycle.hedge_last_action_price))
                            < (float(cycle.atr_value) * min_rehedge_delta_atr)
                        ):
                            result["hedge_block"] = "min_rehedge_delta_not_met"
                        else:
                            updated = await self._execute_hedge_sell(db, cycle, current_bid)
                            result["hedge_placed"] = updated is not None and updated.hedge_position_ticket is not None
                            result["hedge_ticket"] = cycle.hedge_position_ticket

                # ── Area 2: trail SL on live hedge ─────────────────────────
                if (
                    self.settings.atr_recovery_trailing_monitor_enabled
                    and cycle.hedge_position_ticket is not None
                    and cycle.trailing_activation_price is not None
                    and cycle.atr_value is not None
                    and float(cycle.atr_value) > 0
                    and current_bid <= float(cycle.trailing_activation_price)
                ):
                    trailing_distance = float(cycle.atr_value) * max(
                        0.01, float(self.settings.atr_recovery_trailing_multiplier)
                    )
                    new_sl = current_bid + trailing_distance
                    current_tracked_sl = float(cycle.hedge_sl_last_modified or cycle.stop_loss or 0.0)
                    if current_tracked_sl <= 0 or new_sl < current_tracked_sl:
                        try:
                            await self.mt5.modify_position_sl(
                                int(cycle.hedge_position_ticket), symbol, new_sl
                            )
                            cycle.hedge_sl_last_modified = new_sl
                            result["trailing_sl_updated"] = round(new_sl, 5)
                            logging.info(
                                "[hedge] Trailing SL updated for cycle %s to %.5f", cycle.id, new_sl
                            )
                        except Exception as exc:
                            logging.warning("[hedge] Trailing SL failed for cycle %s: %s", cycle.id, exc)
                            result["trailing_sl_error"] = str(exc)

                # ── Area 3: auto-close hedge on reversal confirmation ───────
                if (
                    self.settings.atr_recovery_auto_reversal_close
                    and cycle.hedge_position_ticket is not None
                    and self._evaluate_reversal_confirmation(cycle, current_bid)
                ):
                    hedge_volume = float(self.settings.mt5_volume_lots)
                    if cycle.linked_trade_id is not None:
                        linked = db.scalar(select(Trade).where(Trade.id == cycle.linked_trade_id))
                        if linked is not None and linked.quantity and float(linked.quantity) > 0:
                            hedge_volume = float(linked.quantity)
                    try:
                        await self.mt5.close_position_by_ticket(
                            int(cycle.hedge_position_ticket), symbol, hedge_volume
                        )
                        now = datetime.now(UTC)
                        cooldown_seconds = max(0, int(self.settings.atr_recovery_hedge_cooldown_seconds))
                        cycle.hedge_cooldown_until = now + timedelta(seconds=cooldown_seconds) if cooldown_seconds > 0 else None
                        cycle.hedge_last_action_at = now
                        cycle.hedge_last_action_price = current_bid
                        cycle.status = "CLOSED"
                        cycle.closed_at = now
                        cycle.close_reason = "REVERSAL_CONFIRMED"
                        cycle.notes = f"{cycle.notes or ''} | auto_closed_reversal_confirmed".strip(" |")
                        result["hedge_closed"] = True
                        result["close_reason"] = "REVERSAL_CONFIRMED"
                        logging.info(
                            "[hedge] Hedge auto-closed on reversal for cycle %s on %s", cycle.id, symbol
                        )
                    except Exception as exc:
                        logging.warning(
                            "[hedge] Auto reversal close failed for cycle %s: %s", cycle.id, exc
                        )
                        result["reversal_close_error"] = str(exc)

                db.add(cycle)
                db.commit()
                db.refresh(cycle)
                result["cycle_status"] = cycle.status
                processed.append(result)

            except Exception as exc:
                logging.error("[hedge] Monitor error for cycle %s on %s: %s", cycle.id, symbol, exc)
                errors.append({"cycle_id": cycle.id, "symbol": symbol, "error": str(exc)})

        return {
            "cycles_checked": len(open_cycles),
            "processed": processed,
            "errors": errors,
        }

    def _estimate_realized_pnl_for_sell(
        self,
        db: Session,
        signal_symbol: str,
        execution_symbol: str,
        sell_quantity: float,
        sell_quote_amount: float,
        sell_fee_amount: float,
    ) -> tuple[float | None, float | None]:
        if sell_quantity <= 0:
            return None, None

        historical_trades = list(
            db.scalars(
                select(Trade)
                .where(
                    func.coalesce(Trade.signal_symbol, Trade.symbol) == signal_symbol,
                    func.coalesce(Trade.execution_symbol, Trade.symbol) == execution_symbol,
                    Trade.side.in_(["BUY", "SELL"]),
                    Trade.status.in_(["FILLED", "SIMULATED"]),
                )
                .order_by(Trade.created_at.asc(), Trade.id.asc())
            )
        )

        lots: list[dict[str, float]] = []
        for trade in historical_trades:
            quantity = float(trade.quantity or 0.0)
            if quantity <= 0:
                continue

            if trade.side == "BUY":
                quote = float(trade.quote_amount or 0.0)
                fee = float(trade.fee_amount or 0.0)
                unit_cost = (quote + fee) / quantity
                lots.append({"remaining": quantity, "unit_cost": unit_cost})
                continue

            remaining_to_match = quantity
            for lot in lots:
                if remaining_to_match <= 0:
                    break
                available = float(lot["remaining"])
                if available <= 0:
                    continue
                matched = min(available, remaining_to_match)
                lot["remaining"] = available - matched
                remaining_to_match -= matched

        sell_unit_proceeds = (max(0.0, float(sell_quote_amount)) - max(0.0, float(sell_fee_amount))) / sell_quantity
        remaining_sell_qty = sell_quantity
        matched_qty = 0.0
        matched_cost_basis = 0.0
        realized_proceeds = 0.0

        for lot in lots:
            if remaining_sell_qty <= 0:
                break
            available = float(lot["remaining"])
            if available <= 0:
                continue
            matched = min(available, remaining_sell_qty)
            matched_qty += matched
            matched_cost_basis += matched * float(lot["unit_cost"])
            realized_proceeds += matched * sell_unit_proceeds
            remaining_sell_qty -= matched

        if matched_qty <= 0:
            return None, None

        realized_pnl = realized_proceeds - matched_cost_basis
        realized_pnl_pct = (realized_pnl / matched_cost_basis * 100.0) if matched_cost_basis > 0 else None
        return round(realized_pnl, 8), (round(realized_pnl_pct, 6) if realized_pnl_pct is not None else None)

    def _build_cycle_result(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        return {
            "symbol": snapshot.signal_symbol,
            "signal_symbol": snapshot.signal_symbol,
            "execution_symbol": snapshot.execution_symbol,
            "market_data_symbol": snapshot.market_data_symbol,
            "market_data_provider": snapshot.market_data_provider,
            "price": snapshot.latest_price,
            "rsi": snapshot.rsi,
            "action": snapshot.suggested_action,
            "strategy_vote_action": snapshot.strategy_vote_action,
            "confidence": snapshot.confidence,
            "regime": snapshot.regime,
            "strategies": snapshot.strategy_names,
            "ml_probability_up": snapshot.ml_probability_up,
            "ml_action": snapshot.ml_action,
            "stop_loss": snapshot.stop_loss,
            "take_profit": snapshot.take_profit,
            "position_quantity": snapshot.position_quantity,
            "pre_candle_close_action": snapshot.pre_candle_close_action,
            "candle_close_gate_blocked": snapshot.candle_close_gate_blocked,
            "candle_close_gate_enabled": snapshot.candle_close_gate_enabled,
            "seconds_until_candle_close": snapshot.seconds_until_candle_close,
            "pre_confidence_action": snapshot.pre_confidence_action,
            "confidence_gate_blocked": snapshot.confidence_gate_blocked,
            "confidence_gate_threshold": snapshot.confidence_gate_threshold,
            "pro_analysis_vote_action": snapshot.pro_analysis_vote_action,
            "pro_analysis_final_action": snapshot.pro_analysis_final_action,
            "pro_analysis_gate_blocked": snapshot.pro_analysis_gate_blocked,
            "pro_analysis_gate_reasons": snapshot.pro_analysis_gate_reasons,
            "pro_analysis_session_name": snapshot.pro_analysis_session_name,
            "pro_analysis_session_allowed": snapshot.pro_analysis_session_allowed,
            "pro_analysis_quality_gate_passed": snapshot.pro_analysis_quality_gate_passed,
            "pro_analysis_rr": snapshot.pro_analysis_rr,
            "atr_pct": snapshot.atr_pct,
            "atr_value": snapshot.atr_value,
            "atr_recovery_active": snapshot.atr_recovery_active,
            "atr_recovery_symbol_enabled": snapshot.atr_recovery_symbol_enabled,
            "atr_recovery_profile": snapshot.atr_recovery_profile,
            "trade": None,
        }

    async def run_cycle_for_symbol(self, db: Session, symbol: str, request_id: str | None = None) -> dict[str, Any]:
        snapshot = await self.build_snapshot_for_symbol(db, symbol)
        result = self._build_cycle_result(snapshot)

        if snapshot.suggested_action in {"BUY", "SELL"}:
            async with self._get_symbol_lock(snapshot.execution_symbol):
                block_reason = await self._execution_block_reason(db, snapshot, request_id=request_id)
                if block_reason:
                    result["execution_block"] = block_reason
                    return result

                try:
                    if snapshot.suggested_action == "BUY":
                        trade = await self._execute_buy(db, snapshot, request_id=request_id)
                        result["trade"] = trade_to_dict(trade)
                    elif snapshot.suggested_action == "SELL":
                        trade = await self._execute_sell(db, snapshot, request_id=request_id)
                        result["trade"] = trade_to_dict(trade)
                except ExecutionBlockedError as exc:
                    result["execution_block"] = str(exc)

        return result

    async def run_professional_analysis_execution(
        self,
        db: Session,
        symbol: str,
        account_size: float | None = None,
        risk_tolerance: str = "MEDIUM",
        trading_style: str = "DAY TRADING",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("A symbol is required for professional analysis execution.")

        service = ProfessionalAnalysisService(self.settings, self.mt5, self.strategy_engine)
        plan = await service.generate_execution_plan(
            symbol=normalized_symbol,
            account_size=account_size,
            risk_tolerance=risk_tolerance,
            trading_style=trading_style,
        )
        snapshot = await self.build_snapshot_for_symbol(db, normalized_symbol)
        forced_action = str(plan.get("final_action", "HOLD")).upper()
        weighted_vote_action = str(plan.get("weighted_vote_action", snapshot.strategy_vote_action or "HOLD")).upper()
        gate_reasons = [str(item) for item in plan.get("quality_gate_reasons", [])]
        trade_idea = plan.get("trade_idea") or {}
        execution_snapshot = replace(
            snapshot,
            suggested_action=forced_action,
            strategy_vote_action=weighted_vote_action,
            pro_analysis_vote_action=weighted_vote_action,
            pro_analysis_final_action=forced_action,
            pro_analysis_gate_blocked=bool(gate_reasons) or forced_action not in {"BUY", "SELL"},
            pro_analysis_gate_reasons=gate_reasons,
            pro_analysis_session_name=plan.get("session_name"),
            pro_analysis_session_allowed=bool(plan.get("session_allowed", True)),
            pro_analysis_quality_gate_passed=bool(plan.get("quality_gate_passed", False)),
            pro_analysis_rr=float(trade_idea.get("risk_to_reward_tp2", 0.0) or 0.0),
        )

        result = self._build_cycle_result(execution_snapshot)
        result["analysis_plan"] = plan
        result["requested_trading_style"] = trading_style
        result["requested_risk_tolerance"] = risk_tolerance

        if forced_action not in {"BUY", "SELL"}:
            result["execution_block"] = "; ".join(gate_reasons) if gate_reasons else "Professional analysis did not approve execution."
            return result

        async with self._get_symbol_lock(execution_snapshot.execution_symbol):
            block_reason = await self._execution_block_reason(db, execution_snapshot, request_id=request_id)
            if block_reason:
                result["execution_block"] = block_reason
                return result

            try:
                if forced_action == "BUY":
                    trade = await self._execute_buy(db, execution_snapshot, request_id=request_id)
                else:
                    trade = await self._execute_sell(db, execution_snapshot, request_id=request_id)
                result["trade"] = trade_to_dict(trade)
            except ExecutionBlockedError as exc:
                result["execution_block"] = str(exc)

        return result

    async def run_auto_cycle(self, db: Session, request_id: str | None = None) -> dict[str, Any]:
        await self._maybe_retrain_ml_model(db)
        if (
            self.settings.effective_market_data_provider == "mt5"
            and self.settings.effective_execution_provider == "mt5"
            and len(self.settings.effective_mt5_symbols) > 1
        ):
            results: list[dict[str, Any]] = []
            errors: dict[str, str] = {}
            for symbol in self.settings.effective_mt5_symbols:
                symbol_request_id = f"{request_id}:{symbol}" if request_id else None
                try:
                    results.append(await self.run_cycle_for_symbol(db, symbol, request_id=symbol_request_id))
                except Exception as exc:
                    errors[symbol] = str(exc)
            return {
                "mode": "multi-symbol",
                "symbols": self.settings.effective_mt5_symbols,
                "results": results,
                "errors": errors,
            }

        return await self.run_cycle(db, request_id=request_id)

    async def _maybe_retrain_ml_model(self, db: Session) -> None:
        if not (self.settings.ml_enabled and self.settings.ml_auto_retrain_enabled):
            return
        now_ts = time.time()
        interval = max(60, int(self.settings.ml_retrain_interval_seconds))
        if self._ml_last_retrain_ts is not None and (now_ts - self._ml_last_retrain_ts) < interval:
            return
        self.train_ml_model(db)
        self._ml_last_retrain_ts = now_ts

    async def run_cycle(self, db: Session, request_id: str | None = None) -> dict[str, Any]:
        snapshot = await self.build_snapshot(db)
        result = self._build_cycle_result(snapshot)

        if snapshot.suggested_action in {"BUY", "SELL"}:
            async with self._get_symbol_lock(snapshot.execution_symbol):
                block_reason = await self._execution_block_reason(db, snapshot, request_id=request_id)
                if block_reason:
                    result["execution_block"] = block_reason
                    return result

                try:
                    if snapshot.suggested_action == "BUY":
                        trade = await self._execute_buy(db, snapshot, request_id=request_id)
                        result["trade"] = trade_to_dict(trade)
                    elif snapshot.suggested_action == "SELL":
                        trade = await self._execute_sell(db, snapshot, request_id=request_id)
                        result["trade"] = trade_to_dict(trade)
                except ExecutionBlockedError as exc:
                    result["execution_block"] = str(exc)

        return result

    async def run_backtest(
        self,
        history_limit: int = 1000,
        initial_balance: float = 1000.0,
        trade_amount: float | None = None,
        fee_rate: float | None = None,
        market_data_symbol: str | None = None,
    ) -> dict[str, Any]:
        if history_limit <= max(self.settings.rsi_period, 60):
            raise ValueError("history_limit must be greater than 60 candles.")
        if initial_balance <= 0:
            raise ValueError("initial_balance must be greater than zero.")

        per_trade_amount = float(trade_amount if trade_amount is not None else self.settings.trade_amount_usdt)
        if per_trade_amount <= 0:
            raise ValueError("trade_amount must be greater than zero.")

        effective_fee_rate = float(fee_rate if fee_rate is not None else self.settings.fee_rate)
        if effective_fee_rate < 0:
            raise ValueError("fee_rate must be zero or greater.")

        symbol_override = market_data_symbol.strip() if market_data_symbol else None
        signal_symbol = self._get_signal_symbol(symbol_override)
        execution_symbol = self._get_execution_symbol(symbol_override)

        series: dict[str, list[float]] | None = None
        selected_provider = self.settings.effective_market_data_provider
        market_data_symbol = self.settings.effective_market_data_symbol
        selected_client: Any | None = None
        errors: list[str] = []

        for provider, candidate_symbol, client in self._market_data_candidates(symbol_override=symbol_override):
            try:
                klines = await client.get_klines(
                    symbol=candidate_symbol,
                    interval=self.settings.effective_strategy_candle_interval,
                    limit=history_limit,
                )
                series = self._extract_series(klines)
                selected_provider = provider
                market_data_symbol = candidate_symbol
                selected_client = client
                break
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                errors.append(f"{provider}: {detail}")

        if series is None:
            raise RuntimeError(" | ".join(errors) or "No market data providers succeeded.")

        if selected_client is None:
            raise RuntimeError("No market data client selected for backtest run.")

        rules = await self._get_sizing_rules(
            selected_provider,
            market_data_symbol,
            selected_client,
            execution_symbol_override=symbol_override,
        )

        closes = series["closes"]
        highs = series["highs"]
        lows = series["lows"]
        volumes = series["volumes"]

        warmup = max(60, self.settings.rsi_period + 1)
        usable = len(closes) - warmup
        if usable < 60:
            raise ValueError("Not enough candles after warmup to create train/validation/oos windows.")

        train_pct = min(max(float(self.settings.backtest_train_pct), 10.0), 80.0)
        validation_pct = min(max(float(self.settings.backtest_validation_pct), 5.0), 40.0)
        train_end = warmup + int(usable * (train_pct / 100.0))
        validation_end = train_end + int(usable * (validation_pct / 100.0))
        train_end = min(max(train_end, warmup + 10), len(closes) - 20)
        validation_end = min(max(validation_end, train_end + 10), len(closes) - 5)

        spread_bps = max(0.0, float(self.settings.backtest_spread_bps))
        slippage_bps = max(0.0, float(self.settings.backtest_slippage_bps))
        latency_bars = max(0, int(self.settings.backtest_latency_bars))
        partial_fill_min = min(max(float(self.settings.backtest_partial_fill_min_pct) / 100.0, 0.1), 1.0)
        min_notional = float(rules.min_notional)

        def simulate_window(start: int, end: int, start_equity: float) -> dict[str, Any]:
            cash_balance = start_equity
            open_quantity = 0.0
            entry_cost = 0.0
            stop_loss = 0.0
            take_profit = 0.0
            completed_trades = 0
            winning_trades = 0
            buy_signals = 0
            sell_signals = 0
            cumulative_confidence = 0.0
            signal_count = 0
            peak_equity = start_equity
            max_drawdown_pct = 0.0
            order_events = 0
            partial_fill_events = 0
            trade_returns: list[float] = []

            for index in range(start, end):
                price = closes[index]
                window_series = {
                    "closes": closes[: index + 1],
                    "highs": highs[: index + 1],
                    "lows": lows[: index + 1],
                    "volumes": volumes[: index + 1],
                }
                decision = self.strategy_engine.evaluate(window_series, self.settings.effective_strategy_candle_interval)
                cumulative_confidence += decision.confidence
                signal_count += 1

                latency_index = min(end - 1, index + latency_bars)
                market_reference = closes[latency_index]

                if open_quantity > 0:
                    exit_signal = decision.action == "SELL"
                    hit_sl = bool(stop_loss and lows[index] <= stop_loss)
                    hit_tp = bool(take_profit and highs[index] >= take_profit)
                    if hit_sl or hit_tp or exit_signal:
                        sell_signals += 1
                        order_events += 1
                        slip_bps = random.uniform(0.0, slippage_bps)
                        fill_ratio = random.uniform(partial_fill_min, 1.0)
                        if fill_ratio < 0.999:
                            partial_fill_events += 1
                        fill_price = market_reference * (1.0 - (spread_bps / 20000.0) - (slip_bps / 10000.0))
                        fill_quantity = max(0.0, open_quantity * fill_ratio)
                        fill_notional = fill_quantity * fill_price
                        if fill_notional >= min_notional and fill_quantity > 0:
                            cost_basis = (entry_cost / open_quantity) if open_quantity > 0 else 0.0
                            realized_cost = cost_basis * fill_quantity
                            sell_fee = fill_notional * effective_fee_rate
                            net_value = fill_notional - sell_fee
                            cash_balance += net_value
                            open_quantity = max(0.0, open_quantity - fill_quantity)
                            entry_cost = max(0.0, entry_cost - realized_cost)
                            completed_trades += 1
                            pnl = net_value - realized_cost
                            if pnl > 0:
                                winning_trades += 1
                            if realized_cost > 0:
                                trade_returns.append(pnl / realized_cost)
                            if open_quantity <= 0:
                                stop_loss = 0.0
                                take_profit = 0.0

                if open_quantity <= 0 and decision.action == "BUY" and cash_balance > 0:
                    buy_signals += 1
                    order_events += 1
                    allocation = min(per_trade_amount, cash_balance)
                    if allocation >= min_notional:
                        slip_bps = random.uniform(0.0, slippage_bps)
                        fill_ratio = random.uniform(partial_fill_min, 1.0)
                        if fill_ratio < 0.999:
                            partial_fill_events += 1
                        fill_price = market_reference * (1.0 + (spread_bps / 20000.0) + (slip_bps / 10000.0))
                        raw_quantity = (allocation / fill_price) if fill_price > 0 else 0.0
                        fill_quantity = raw_quantity * fill_ratio
                        fill_notional = fill_quantity * fill_price
                        if fill_notional >= min_notional and fill_quantity > 0:
                            buy_fee = fill_notional * effective_fee_rate
                            total_spend = fill_notional + buy_fee
                            if total_spend <= cash_balance:
                                cash_balance -= total_spend
                                open_quantity = fill_quantity
                                entry_cost = total_spend
                                stop_loss = float(decision.stop_loss or (price * 0.995))
                                take_profit = float(decision.take_profit or (price * 1.01))

                current_equity = cash_balance + (open_quantity * price)
                peak_equity = max(peak_equity, current_equity)
                if peak_equity > 0:
                    drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100
                    max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

            latest_price = closes[end - 1]
            ending_equity = cash_balance + (open_quantity * latest_price)
            net_pnl = ending_equity - start_equity
            roi_pct = (net_pnl / start_equity) * 100 if start_equity > 0 else 0.0
            win_rate_pct = (winning_trades / completed_trades * 100) if completed_trades else 0.0
            avg_confidence = (cumulative_confidence / signal_count) if signal_count else 0.0
            partial_fill_rate = (partial_fill_events / order_events * 100) if order_events else 0.0
            return {
                "start": start,
                "end": end,
                "candles": max(0, end - start),
                "starting_equity": round(start_equity, 2),
                "ending_equity": round(ending_equity, 2),
                "net_pnl": round(net_pnl, 2),
                "roi_pct": round(roi_pct, 2),
                "completed_trades": completed_trades,
                "winning_trades": winning_trades,
                "win_rate_pct": round(win_rate_pct, 2),
                "buy_signals": buy_signals,
                "sell_signals": sell_signals,
                "avg_confidence": round(avg_confidence, 4),
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "open_position_quantity": round(open_quantity, 8),
                "partial_fill_rate_pct": round(partial_fill_rate, 2),
                "trade_returns": trade_returns,
            }

        train_metrics = simulate_window(warmup, train_end, initial_balance)
        validation_metrics = simulate_window(train_end, validation_end, float(train_metrics["ending_equity"]))
        oos_metrics = simulate_window(validation_end, len(closes), float(validation_metrics["ending_equity"]))

        walk_forward_steps = max(1, int(self.settings.backtest_walk_forward_steps))
        step_span = max(10, (len(closes) - warmup) // (walk_forward_steps + 1))
        walk_forward_runs: list[dict[str, Any]] = []
        for step in range(walk_forward_steps):
            test_start = warmup + (step * step_span)
            test_end = min(len(closes), test_start + step_span)
            if test_end - test_start < 10:
                continue
            wf_metrics = simulate_window(test_start, test_end, initial_balance)
            walk_forward_runs.append(
                {
                    "step": step + 1,
                    "test_start": test_start,
                    "test_end": test_end,
                    "roi_pct": wf_metrics["roi_pct"],
                    "win_rate_pct": wf_metrics["win_rate_pct"],
                    "max_drawdown_pct": wf_metrics["max_drawdown_pct"],
                }
            )

        monte_carlo_paths = max(10, int(self.settings.backtest_monte_carlo_paths))
        source_returns = list(oos_metrics["trade_returns"])
        monte_carlo: dict[str, Any]
        if source_returns:
            simulated_rois: list[float] = []
            sample_size = len(source_returns)
            for _ in range(monte_carlo_paths):
                equity = initial_balance
                for _ in range(sample_size):
                    sampled_return = random.choice(source_returns)
                    equity *= (1.0 + sampled_return)
                simulated_rois.append(((equity - initial_balance) / initial_balance) * 100.0)
            sorted_rois = sorted(simulated_rois)
            p5 = sorted_rois[max(0, int(0.05 * (len(sorted_rois) - 1)))]
            p50 = sorted_rois[max(0, int(0.50 * (len(sorted_rois) - 1)))]
            p95 = sorted_rois[max(0, int(0.95 * (len(sorted_rois) - 1)))]
            monte_carlo = {
                "paths": monte_carlo_paths,
                "p5_roi_pct": round(p5, 2),
                "p50_roi_pct": round(p50, 2),
                "p95_roi_pct": round(p95, 2),
            }
        else:
            monte_carlo = {
                "paths": monte_carlo_paths,
                "status": "insufficient-trade-returns",
            }

        wf_avg_roi = 0.0
        if walk_forward_runs:
            wf_avg_roi = sum(float(item.get("roi_pct") or 0.0) for item in walk_forward_runs) / len(walk_forward_runs)
        mc_p5 = float(monte_carlo.get("p5_roi_pct", -9999.0)) if isinstance(monte_carlo, dict) else -9999.0
        trust_gate_passed = bool(
            oos_metrics["roi_pct"] > 0
            and oos_metrics["max_drawdown_pct"] <= max(train_metrics["max_drawdown_pct"] * 1.5, 10.0)
            and wf_avg_roi > 0
            and (mc_p5 > -15.0 or monte_carlo.get("status") == "insufficient-trade-returns")
        )

        latest_price = closes[-1]
        latest_rsi = calculate_rsi(closes, self.settings.rsi_period)

        return {
            "symbol": signal_symbol,
            "signal_symbol": signal_symbol,
            "execution_symbol": execution_symbol,
            "market_data_symbol": market_data_symbol,
            "market_data_provider": selected_provider,
            "interval": self.settings.effective_strategy_candle_interval,
            "candles": len(closes),
            "strategy_mode": "adaptive-combined-10",
            "simulation_mode": "event-driven-simulator",
            "initial_balance": round(initial_balance, 2),
            "trade_amount": round(per_trade_amount, 2),
            "fee_rate": effective_fee_rate,
            "latest_price": latest_price,
            "latest_rsi": latest_rsi,
            "open_position_quantity": oos_metrics["open_position_quantity"],
            "completed_trades": oos_metrics["completed_trades"],
            "winning_trades": oos_metrics["winning_trades"],
            "win_rate_pct": oos_metrics["win_rate_pct"],
            "buy_signals": oos_metrics["buy_signals"],
            "sell_signals": oos_metrics["sell_signals"],
            "avg_confidence": oos_metrics["avg_confidence"],
            "ending_equity": oos_metrics["ending_equity"],
            "net_pnl": oos_metrics["net_pnl"],
            "roi_pct": oos_metrics["roi_pct"],
            "max_drawdown_pct": oos_metrics["max_drawdown_pct"],
            "windows": {
                "train": {k: v for k, v in train_metrics.items() if k != "trade_returns"},
                "validation": {k: v for k, v in validation_metrics.items() if k != "trade_returns"},
                "out_of_sample": {k: v for k, v in oos_metrics.items() if k != "trade_returns"},
            },
            "walk_forward": {
                "steps": len(walk_forward_runs),
                "runs": walk_forward_runs,
            },
            "monte_carlo": monte_carlo,
            "trust_assessment": {
                "parameter_set_trusted": trust_gate_passed,
                "oos_roi_pct": oos_metrics["roi_pct"],
                "walk_forward_avg_roi_pct": round(wf_avg_roi, 2),
                "monte_carlo_p5_roi_pct": round(mc_p5, 2) if mc_p5 > -9000 else None,
            },
            "simulation_assumptions": {
                "spread_bps": spread_bps,
                "slippage_bps": slippage_bps,
                "latency_bars": latency_bars,
                "partial_fill_min_pct": round(partial_fill_min * 100.0, 2),
                "minimum_notional": min_notional,
            },
        }

    async def _execute_buy(self, db: Session, snapshot: MarketSnapshot, request_id: str | None = None) -> Trade:
        rules = snapshot.rules
        quote_amount = await self._resolve_buy_quote_amount(snapshot, rules)
        if self.settings.effective_execution_provider != "mt5" and quote_amount < rules.min_notional:
            raise ValueError(
                f"Configured trade amount {quote_amount} is below Binance minimum notional {rules.min_notional}."
            )

        quantity = round_step_size(quote_amount / Decimal(str(snapshot.latest_price)), rules.step_size)
        if self.settings.effective_execution_provider != "mt5" and quantity < rules.min_qty:
            raise ValueError(
                f"Computed quantity {decimal_to_string(quantity)} is below minimum lot size {decimal_to_string(rules.min_qty)}."
            )

        idempotency_key = self._build_idempotency_key(snapshot, "BUY", request_id=request_id)
        client_order_id = self._build_client_order_id(idempotency_key, "BUY")
        account_scope = self._current_execution_account_scope()
        execution_request = self._register_execution_request(
            db,
            snapshot,
            action="BUY",
            account_scope=account_scope,
            idempotency_key=idempotency_key,
            client_order_id=client_order_id,
        )
        equity_before = await self._estimate_equity(snapshot)

        try:
            if self.settings.can_place_live_orders:
                if self.settings.effective_execution_provider == "mt5":
                    lot_volume = Decimal(str(self.settings.mt5_volume_lots))
                    order = await self.mt5_execution.place_market_buy(
                        db,
                        snapshot.execution_symbol,
                        lot_volume,
                        stop_loss=snapshot.stop_loss,
                        take_profit=snapshot.take_profit,
                        client_order_id=client_order_id,
                        owner_id=self.settings.mt5_runtime_owner_id,
                        signal_symbol=snapshot.signal_symbol,
                        execution_request_id=execution_request.id,
                    )
                    executed_quantity = float(order.get("volume", lot_volume))
                    average_price = float(order.get("price", snapshot.latest_price) or snapshot.latest_price)
                    cummulative_quote_qty = average_price * executed_quantity
                    if order.get("is_queued"):
                        status = "QUEUED"
                        exchange_order_id = str(order.get("order") or order.get("job_id"))
                        notes = (
                            f"Queued MT5 market buy on {snapshot.execution_symbol} | "
                            f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                        )
                    else:
                        status = "FILLED"
                        exchange_order_id = str(order.get("order") or order.get("deal"))
                        notes = (
                            f"Live MT5 market buy on {snapshot.execution_symbol} | "
                            f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                        )
                else:
                    order = await self.binance.place_market_buy(
                        snapshot.execution_symbol,
                        quote_amount,
                        client_order_id=client_order_id,
                    )
                    executed_quantity = float(order.get("executedQty", quantity))
                    cummulative_quote_qty = float(order.get("cummulativeQuoteQty", quote_amount))
                    average_price = (
                        cummulative_quote_qty / executed_quantity if executed_quantity else snapshot.latest_price
                    )
                    status = order.get("status", "FILLED")
                    exchange_order_id = str(order.get("orderId"))
                    notes = (
                        f"Live Binance market buy on {snapshot.execution_symbol} | "
                        f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                    )
                    protection_result = await self._ensure_binance_protection_orders(
                        snapshot.execution_symbol,
                        Decimal(str(executed_quantity)),
                        snapshot.stop_loss,
                    )
                    if protection_result:
                        notes = f"{notes} | protection={protection_result}"
                is_dry_run = False
            else:
                executed_quantity = float(quantity)
                average_price = snapshot.latest_price
                cummulative_quote_qty = average_price * executed_quantity
                status = "SIMULATED"
                exchange_order_id = None
                notes = (
                    f"Dry run market buy using {snapshot.market_data_provider} market data on {snapshot.execution_symbol} | "
                    f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                )
                is_dry_run = True

            if snapshot.atr_recovery_profile.get("execution_overlay_active"):
                atr_note = (
                    f"atr_overlay=on atr_pct={snapshot.atr_pct:.4f} "
                    f"sl={snapshot.atr_recovery_profile.get('stop_loss')} tp={snapshot.atr_recovery_profile.get('take_profit')}"
                )
                hedge_trigger = snapshot.atr_recovery_profile.get("hedge_trigger")
                if hedge_trigger is not None:
                    atr_note = f"{atr_note} hedge_trigger={hedge_trigger} planned_only=true"
                notes = f"{notes} | {atr_note}"

            trade = Trade(
                symbol=snapshot.signal_symbol,
                signal_symbol=snapshot.signal_symbol,
                execution_symbol=snapshot.execution_symbol,
                side="BUY",
                quantity=executed_quantity,
                price=average_price,
                quote_amount=cummulative_quote_qty,
                rsi_value=snapshot.rsi,
                signal="BUY",
                status=status,
                exchange_order_id=exchange_order_id,
                broker_position_id=exchange_order_id,
                intended_price=snapshot.latest_price,
                fill_price=average_price,
                slippage_pct=self._slippage_pct(snapshot.latest_price, average_price),
                fee_amount=float(cummulative_quote_qty) * float(self.settings.fee_rate),
                entry_stop_loss=snapshot.stop_loss,
                entry_take_profit=snapshot.take_profit,
                strategy_weights=self._strategy_weights_json(snapshot),
                confidence=snapshot.confidence,
                equity_before=equity_before,
                equity_after=await self._estimate_equity(snapshot),
                realized_pnl=None,
                realized_pnl_pct=None,
                outcome="OPEN",
                reconciliation_status=("QUEUED" if status == "QUEUED" else ("PENDING" if self.settings.can_place_live_orders else "SKIPPED")),
                is_dry_run=is_dry_run,
                notes=notes,
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            self._open_mt5_trade_cycle(db, snapshot, trade)
            self._complete_execution_request(
                db,
                execution_request,
                status=status,
                broker_order_id=exchange_order_id,
            )
            return trade
        except Exception as exc:
            db.rollback()
            self._complete_execution_request(db, execution_request, status="FAILED", error=str(exc))
            raise

    async def _resolve_buy_quote_amount(self, snapshot: MarketSnapshot, rules: SymbolRules) -> Decimal:
        configured_quote = Decimal(str(self.settings.trade_amount_usdt))
        if configured_quote <= 0:
            raise ValueError("Configured trade amount must be greater than zero.")

        if not (self.settings.can_place_live_orders and self.settings.effective_execution_provider == "binance"):
            return configured_quote

        account = await self.binance.get_account_info()
        free_quote = self._extract_free_balance(account, rules.quote_asset)
        capped_quote = configured_quote

        max_exposure_pct = float(self.settings.risk_max_quote_exposure_pct)
        if max_exposure_pct > 0 and free_quote > 0:
            exposure_cap = free_quote * Decimal(str(max_exposure_pct)) / Decimal("100")
            capped_quote = min(capped_quote, exposure_cap)

        max_loss_pct = float(self.settings.risk_max_loss_per_trade_pct)
        if (
            max_loss_pct > 0
            and free_quote > 0
            and snapshot.stop_loss is not None
            and snapshot.stop_loss < snapshot.latest_price
        ):
            entry_price = Decimal(str(snapshot.latest_price))
            stop_loss = Decimal(str(snapshot.stop_loss))
            per_unit_loss = entry_price - stop_loss
            risk_budget = free_quote * Decimal(str(max_loss_pct)) / Decimal("100")
            if per_unit_loss > 0 and risk_budget > 0:
                max_quantity = risk_budget / per_unit_loss
                max_quote_from_loss = max_quantity * entry_price
                capped_quote = min(capped_quote, max_quote_from_loss)

        vol_target_pct = float(self.settings.risk_volatility_target_pct)
        vol_lookback = int(self.settings.risk_vol_lookback_candles)
        if vol_target_pct > 0 and free_quote > 0:
            try:
                klines = await self.binance.get_klines(
                    symbol=snapshot.market_data_symbol,
                    interval=self.settings.effective_strategy_candle_interval,
                    limit=vol_lookback + 1,
                )
                prices = [float(item[4]) for item in klines if item[4]]
                if len(prices) >= 3:
                    returns = [(curr / prev) - 1.0 for prev, curr in zip(prices[:-1], prices[1:]) if prev > 0]
                    realized_vol = pstdev(returns) if len(returns) >= 2 else 0.0
                    if realized_vol > 0:
                        vol_cap = free_quote * Decimal(str(vol_target_pct)) / Decimal("100") / Decimal(str(realized_vol))
                        capped_quote = min(capped_quote, vol_cap)
            except Exception:
                pass

        if capped_quote <= 0:
            raise ExecutionBlockedError("Risk block: quote amount resolved to zero after risk limits.")
        return capped_quote

    async def _execute_sell(self, db: Session, snapshot: MarketSnapshot, request_id: str | None = None) -> Trade:
        rules = snapshot.rules
        target_quantity = Decimal(str(snapshot.position_quantity))
        idempotency_key = self._build_idempotency_key(snapshot, "SELL", request_id=request_id)
        client_order_id = self._build_client_order_id(idempotency_key, "SELL")
        account_scope = self._current_execution_account_scope()
        execution_request = self._register_execution_request(
            db,
            snapshot,
            action="SELL",
            account_scope=account_scope,
            idempotency_key=idempotency_key,
            client_order_id=client_order_id,
        )
        equity_before = await self._estimate_equity(snapshot)

        try:
            if self.settings.can_place_live_orders:
                if self.settings.effective_execution_provider == "mt5":
                    lot_volume = Decimal(str(self.settings.mt5_volume_lots))
                    order = await self.mt5_execution.place_market_sell(
                        db,
                        snapshot.execution_symbol,
                        lot_volume,
                        stop_loss=snapshot.stop_loss,
                        take_profit=snapshot.take_profit,
                        client_order_id=client_order_id,
                        owner_id=self.settings.mt5_runtime_owner_id,
                        signal_symbol=snapshot.signal_symbol,
                        execution_request_id=execution_request.id,
                    )
                    executed_quantity = float(order.get("volume", lot_volume))
                    average_price = float(order.get("price", snapshot.latest_price) or snapshot.latest_price)
                    cummulative_quote_qty = average_price * executed_quantity
                    if order.get("is_queued"):
                        status = "QUEUED"
                        exchange_order_id = str(order.get("order") or order.get("job_id"))
                        notes = (
                            f"Queued MT5 market sell on {snapshot.execution_symbol} | "
                            f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                        )
                    else:
                        status = "FILLED"
                        exchange_order_id = str(order.get("order") or order.get("deal"))
                        notes = (
                            f"Live MT5 market sell on {snapshot.execution_symbol} | "
                            f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                        )
                else:
                    account = await self.binance.get_account_info()
                    free_balance = self._extract_free_balance(account, rules.base_asset)
                    sell_quantity = round_step_size(min(target_quantity, free_balance), rules.step_size)
                    if sell_quantity < rules.min_qty:
                        raise ValueError(
                            f"Free balance {decimal_to_string(sell_quantity)} is below minimum sell quantity {decimal_to_string(rules.min_qty)}."
                        )
                    order = await self.binance.place_market_sell(
                        snapshot.execution_symbol,
                        sell_quantity,
                        client_order_id=client_order_id,
                    )
                    executed_quantity = float(order.get("executedQty", sell_quantity))
                    cummulative_quote_qty = float(order.get("cummulativeQuoteQty", snapshot.latest_price * executed_quantity))
                    average_price = (
                        cummulative_quote_qty / executed_quantity if executed_quantity else snapshot.latest_price
                    )
                    status = order.get("status", "FILLED")
                    exchange_order_id = str(order.get("orderId"))
                    notes = (
                        f"Live Binance market sell on {snapshot.execution_symbol} | "
                        f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                    )
                is_dry_run = False
            else:
                sell_quantity = round_step_size(target_quantity, rules.step_size)
                if sell_quantity < rules.min_qty:
                    raise ValueError(
                        f"Position {decimal_to_string(sell_quantity)} is below minimum sell quantity {decimal_to_string(rules.min_qty)}."
                    )
                executed_quantity = float(sell_quantity)
                average_price = snapshot.latest_price
                cummulative_quote_qty = average_price * executed_quantity
                status = "SIMULATED"
                exchange_order_id = None
                notes = (
                    f"Dry run market sell using {snapshot.market_data_provider} market data on {snapshot.execution_symbol} | "
                    f"signal={snapshot.signal_symbol} | strategies: {self._strategy_note(snapshot)}"
                )
                is_dry_run = True

            if snapshot.atr_recovery_profile.get("execution_overlay_active"):
                atr_note = (
                    f"atr_overlay=on atr_pct={snapshot.atr_pct:.4f} "
                    f"sl={snapshot.atr_recovery_profile.get('stop_loss')} tp={snapshot.atr_recovery_profile.get('take_profit')}"
                )
                notes = f"{notes} | {atr_note}"

            fee_amount = float(cummulative_quote_qty) * float(self.settings.fee_rate)
            realized_pnl, realized_pnl_pct = self._estimate_realized_pnl_for_sell(
                db,
                signal_symbol=snapshot.signal_symbol,
                execution_symbol=snapshot.execution_symbol,
                sell_quantity=float(executed_quantity),
                sell_quote_amount=float(cummulative_quote_qty),
                sell_fee_amount=fee_amount,
            )

            trade = Trade(
                symbol=snapshot.signal_symbol,
                signal_symbol=snapshot.signal_symbol,
                execution_symbol=snapshot.execution_symbol,
                side="SELL",
                quantity=executed_quantity,
                price=average_price,
                quote_amount=cummulative_quote_qty,
                rsi_value=snapshot.rsi,
                signal="SELL",
                status=status,
                exchange_order_id=exchange_order_id,
                broker_position_id=exchange_order_id,
                intended_price=snapshot.latest_price,
                fill_price=average_price,
                slippage_pct=self._slippage_pct(snapshot.latest_price, average_price),
                fee_amount=fee_amount,
                entry_stop_loss=snapshot.stop_loss,
                entry_take_profit=snapshot.take_profit,
                strategy_weights=self._strategy_weights_json(snapshot),
                confidence=snapshot.confidence,
                equity_before=equity_before,
                equity_after=await self._estimate_equity(snapshot),
                realized_pnl=realized_pnl,
                realized_pnl_pct=realized_pnl_pct,
                outcome=self._resolve_trade_outcome_label(realized_pnl),
                reconciliation_status=("QUEUED" if status == "QUEUED" else ("PENDING" if self.settings.can_place_live_orders else "SKIPPED")),
                is_dry_run=is_dry_run,
                notes=notes,
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            self._close_mt5_trade_cycle(db, snapshot, trade)
            self._complete_execution_request(
                db,
                execution_request,
                status=status,
                broker_order_id=exchange_order_id,
            )
            return trade
        except Exception as exc:
            db.rollback()
            self._complete_execution_request(db, execution_request, status="FAILED", error=str(exc))
            raise

    def _extract_free_balance(self, account_payload: dict[str, Any], asset: str) -> Decimal:
        for balance in account_payload.get("balances", []):
            if balance["asset"] == asset:
                return Decimal(balance["free"])
        return Decimal("0")

    async def _ensure_binance_protection_orders(
        self,
        execution_symbol: str,
        position_quantity: Decimal,
        stop_loss: float | None,
    ) -> str:
        if position_quantity <= 0:
            return "skipped"
        try:
            open_orders = await self.binance.get_open_orders(execution_symbol)
            has_stop = any(str(item.get("type", "")).upper() in {"STOP_LOSS", "STOP_LOSS_LIMIT"} for item in open_orders)
            if has_stop:
                return "existing-stop"

            if stop_loss is None:
                market_state = await self.binance.get_symbol_market_state(execution_symbol)
                reference = float(market_state.get("mid") or market_state.get("bid") or 0.0)
                if reference <= 0:
                    return "skipped"
                stop_price = Decimal(str(reference * 0.98))
            else:
                stop_price = Decimal(str(stop_loss))
            limit_price = stop_price * Decimal("0.999")
            await self.binance.place_stop_loss_limit_sell(
                symbol=execution_symbol,
                quantity=position_quantity,
                stop_price=stop_price,
                limit_price=limit_price,
                client_order_id=self._build_client_order_id(f"{execution_symbol}:stop:{datetime.now(UTC).timestamp()}", "SELL"),
            )
            return "stop-placed"
        except Exception as exc:
            return f"stop-watchdog-error:{str(exc)[:80]}"

    def list_recent_trades(self, db: Session, limit: int = 20) -> list[Trade]:
        statement = select(Trade).order_by(desc(Trade.created_at)).limit(limit)
        return list(db.scalars(statement))

    def get_ml_status(self) -> dict[str, Any]:
        status = self.ml_service.status()
        status["enabled"] = bool(self.settings.ml_enabled)
        status["override_strategy"] = bool(self.settings.ml_override_strategy)
        return status

    def train_ml_model(self, db: Session) -> dict[str, Any]:
        trades = list(
            db.scalars(
                select(Trade)
                .where(Trade.side.in_(["BUY", "SELL"]), Trade.status.in_(["FILLED", "SIMULATED"]))
                .order_by(desc(Trade.created_at))
                .limit(max(100, int(self.settings.ml_training_trade_limit)))
            )
        )
        metrics = self.ml_service.train_from_trades(
            trades=trades,
            min_samples=max(5, int(self.settings.ml_min_training_samples)),
            epochs=max(20, int(self.settings.ml_training_epochs)),
            learning_rate=max(0.001, float(self.settings.ml_learning_rate)),
        )
        return metrics

    def list_execution_journal(
        self,
        db: Session,
        limit: int = 100,
        symbol: str | None = None,
        reconciliation_status: str | None = None,
        since: datetime | None = None,
    ) -> list[Trade]:
        capped_limit = max(1, min(limit, 500))
        statement = select(Trade)

        if symbol:
            symbol_value = symbol.strip()
            statement = statement.where(
                or_(
                    Trade.symbol == symbol_value,
                    Trade.signal_symbol == symbol_value,
                    Trade.execution_symbol == symbol_value,
                )
            )

        if reconciliation_status:
            statement = statement.where(Trade.reconciliation_status == reconciliation_status.strip().upper())

        if since is not None:
            statement = statement.where(Trade.created_at >= since)

        statement = statement.order_by(desc(Trade.created_at)).limit(capped_limit)
        return list(db.scalars(statement))

    async def reconcile_broker_state(self, db: Session) -> dict[str, Any]:
        if not self.settings.can_place_live_orders:
            return {
                "status": "skipped",
                "reason": "live-orders-disabled",
            }

        if self.settings.effective_execution_provider == "binance":
            return await self._reconcile_binance_state(db)
        if self.settings.effective_execution_provider == "mt5":
            return await self._reconcile_mt5_state(db)
        raise RuntimeError(f"Unsupported execution provider for reconciliation: {self.settings.effective_execution_provider}")

    async def _reconcile_binance_state(self, db: Session) -> dict[str, Any]:
        account = await self.binance.get_account_info()
        symbol_rules = await self.binance.get_exchange_info(self.settings.trading_symbol)
        open_orders = await self.binance.get_open_orders(self.settings.trading_symbol)
        fills = await self.binance.get_recent_fills(self.settings.trading_symbol, limit=50)

        base_free = self._extract_free_balance(account, symbol_rules.base_asset)
        quote_free = self._extract_free_balance(account, symbol_rules.quote_asset)
        self._broker_positions[self.settings.trading_symbol] = float(base_free)
        self._broker_open_orders[self.settings.trading_symbol] = open_orders

        stop_types = {"STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"}
        orphaned_stops = [
            {
                "symbol": order.get("symbol"),
                "order_id": order.get("orderId"),
                "type": order.get("type"),
                "status": order.get("status"),
            }
            for order in open_orders
            if order.get("type") in stop_types and base_free <= 0
        ]

        failed_requests = list(
            db.scalars(
                select(ExecutionRequest)
                .where(ExecutionRequest.status == "FAILED")
                .order_by(desc(ExecutionRequest.created_at))
                .limit(20)
            )
        )
        rejected_orders = [
            {
                "client_order_id": item.client_order_id,
                "execution_symbol": item.execution_symbol,
                "error": item.error,
                "created_at": item.created_at.isoformat(),
            }
            for item in failed_requests
        ]

        self._persist_position_journal(
            db,
            provider="binance",
            execution_symbol=self.settings.trading_symbol,
            quantity=float(base_free),
            raw_payload={
                "balances": {
                    symbol_rules.base_asset: decimal_to_string(base_free),
                    symbol_rules.quote_asset: decimal_to_string(quote_free),
                },
                "open_orders": open_orders,
            },
        )
        self._persist_fill_journal_entries(db, provider="binance", execution_symbol=self.settings.trading_symbol, fills=fills)
        self._update_trade_reconciliation_status(db, fills=fills, open_orders=open_orders)

        protection_status = "not-needed"
        if base_free > 0:
            protection_status = await self._ensure_binance_protection_orders(
                execution_symbol=self.settings.trading_symbol,
                position_quantity=base_free,
                stop_loss=None,
            )

        return {
            "status": "ok",
            "provider": "binance",
            "balances": {
                symbol_rules.base_asset: decimal_to_string(base_free),
                symbol_rules.quote_asset: decimal_to_string(quote_free),
            },
            "positions": [
                {
                    "symbol": self.settings.trading_symbol,
                    "broker_quantity": decimal_to_string(base_free),
                    "journal_quantity": decimal_to_string(Decimal(str(self._get_journal_position_quantity_for_symbol(db, self.settings.trading_symbol)))),
                    "mismatch": False,
                }
            ],
            "open_orders": open_orders,
            "fills": fills,
            "rejected_orders": rejected_orders,
            "orphaned_stops": orphaned_stops,
            "protection_watchdog": protection_status,
        }

    async def _reconcile_mt5_state(self, db: Session) -> dict[str, Any]:
        account = await self.mt5.get_account_info()
        symbol = self.settings.mt5_symbol
        open_orders = await self.mt5.get_open_orders(symbol)
        deals = await self.mt5.get_recent_deals(lookback_hours=24)
        open_volume = await self.mt5.get_open_position_volume(symbol)
        self._broker_positions[symbol] = float(open_volume)
        self._broker_open_orders[symbol] = open_orders

        local_position = Decimal(str(self._get_journal_position_quantity_for_symbol(db, self._get_signal_symbol(symbol))))
        broker_position = Decimal(str(open_volume))
        mismatch = abs(local_position - broker_position) > Decimal("0.000001")

        stop_markers = {"sl", "tp", "stop", "stoplimit"}
        orphaned_stops = [
            {
                "symbol": order.get("symbol"),
                "ticket": order.get("ticket"),
                "type": order.get("type"),
            }
            for order in open_orders
            if any(marker in str(order.get("type", "")).lower() for marker in stop_markers) and broker_position <= 0
        ]

        failed_requests = list(
            db.scalars(
                select(ExecutionRequest)
                .where(ExecutionRequest.status == "FAILED")
                .order_by(desc(ExecutionRequest.created_at))
                .limit(20)
            )
        )
        rejected_orders = [
            {
                "client_order_id": item.client_order_id,
                "execution_symbol": item.execution_symbol,
                "error": item.error,
                "created_at": item.created_at.isoformat(),
            }
            for item in failed_requests
        ]

        self._persist_position_journal(
            db,
            provider="mt5",
            execution_symbol=symbol,
            quantity=float(open_volume),
            raw_payload={"account": account, "open_orders": open_orders},
        )
        self._persist_fill_journal_entries(db, provider="mt5", execution_symbol=symbol, fills=deals)
        self._update_trade_reconciliation_status(db, fills=deals, open_orders=open_orders)

        return {
            "status": "ok",
            "provider": "mt5",
            "balances": {
                "equity": float(account.get("equity", 0.0) or 0.0),
                "balance": float(account.get("balance", 0.0) or 0.0),
            },
            "positions": [
                {
                    "symbol": symbol,
                    "broker_quantity": float(open_volume),
                    "local_quantity": float(local_position),
                    "mismatch": mismatch,
                }
            ],
            "open_orders": open_orders,
            "fills": deals,
            "rejected_orders": rejected_orders,
            "orphaned_stops": orphaned_stops,
        }

    def _persist_fill_journal_entries(
        self,
        db: Session,
        provider: str,
        execution_symbol: str,
        fills: list[dict[str, Any]],
    ) -> None:
        for item in fills:
            broker_fill_id = str(item.get("id") or item.get("ticket") or item.get("order") or item.get("time"))
            if not broker_fill_id:
                continue
            existing = db.scalar(
                select(BrokerFillJournal).where(
                    BrokerFillJournal.provider == provider,
                    BrokerFillJournal.execution_symbol == execution_symbol,
                    BrokerFillJournal.broker_fill_id == broker_fill_id,
                )
            )
            if existing is not None:
                continue
            row = BrokerFillJournal(
                provider=provider,
                execution_symbol=execution_symbol,
                broker_fill_id=broker_fill_id,
                side=str(item.get("side") or item.get("type") or "")[:8] or None,
                quantity=float(item.get("qty") or item.get("volume") or item.get("executedQty") or 0.0) or None,
                price=float(item.get("price") or 0.0) or None,
                raw_payload=json.dumps(item, default=str),
            )
            db.add(row)
        db.commit()

    def _persist_position_journal(
        self,
        db: Session,
        provider: str,
        execution_symbol: str,
        quantity: float,
        raw_payload: dict[str, Any],
    ) -> None:
        row = BrokerPositionJournal(
            provider=provider,
            execution_symbol=execution_symbol,
            quantity=float(quantity),
            raw_payload=json.dumps(raw_payload, default=str),
        )
        db.add(row)
        db.commit()

    async def hydrate_authoritative_state(self, db: Session) -> dict[str, Any]:
        if not self.settings.can_place_live_orders:
            return {"status": "skipped", "reason": "live-orders-disabled"}
        return await self.reconcile_broker_state(db)

    def _update_trade_reconciliation_status(
        self,
        db: Session,
        fills: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> None:
        fill_ids = {
            str(item.get("orderId") or item.get("order") or item.get("ticket") or item.get("id") or "")
            for item in fills
            if (item.get("orderId") or item.get("order") or item.get("ticket") or item.get("id"))
        }
        open_ids = {
            str(item.get("orderId") or item.get("order") or item.get("ticket") or "")
            for item in open_orders
            if (item.get("orderId") or item.get("order") or item.get("ticket"))
        }

        trades = list(
            db.scalars(
                select(Trade)
                .where(Trade.exchange_order_id.is_not(None), Trade.reconciliation_status == "PENDING")
                .order_by(desc(Trade.created_at))
                .limit(200)
            )
        )
        for trade in trades:
            order_id = str(trade.exchange_order_id or "")
            if order_id in fill_ids:
                trade.reconciliation_status = "MATCHED"
            elif order_id in open_ids:
                trade.reconciliation_status = "OPEN"
            else:
                trade.reconciliation_status = "UNMATCHED"
            db.add(trade)
        db.commit()


def trade_to_dict(trade: Trade | None) -> dict[str, Any] | None:
    if trade is None:
        return None
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "signal_symbol": trade.signal_symbol or trade.symbol,
        "execution_symbol": trade.execution_symbol or trade.symbol,
        "side": trade.side,
        "quantity": trade.quantity,
        "price": trade.price,
        "intended_price": trade.intended_price,
        "fill_price": trade.fill_price,
        "slippage_pct": trade.slippage_pct,
        "fee_amount": trade.fee_amount,
        "quote_amount": trade.quote_amount,
        "rsi_value": trade.rsi_value,
        "entry_stop_loss": trade.entry_stop_loss,
        "entry_take_profit": trade.entry_take_profit,
        "strategy_weights": trade.strategy_weights,
        "confidence": trade.confidence,
        "equity_before": trade.equity_before,
        "equity_after": trade.equity_after,
        "realized_pnl": trade.realized_pnl,
        "realized_pnl_pct": trade.realized_pnl_pct,
        "outcome": trade.outcome,
        "broker_position_id": trade.broker_position_id,
        "reconciliation_status": trade.reconciliation_status,
        "status": trade.status,
        "is_dry_run": trade.is_dry_run,
        "notes": trade.notes,
        "created_at": trade.created_at.isoformat(),
    }
