from decimal import Decimal
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base, BrokerPositionJournal, ExecutionRequest, Mt5ExecutionJob, Mt5Profile, Mt5TradeCycle, Mt5Worker
from app.services.binance import SymbolRules
from app.services.mt5_execution import Mt5ExecutionService
from app.services.pro_analysis import ProfessionalAnalysisService
from app.services.trading import MarketSnapshot, TradingService


class FakeBinanceClient:
    def __init__(
        self,
        closes: list[float],
        latest_price: float | None = None,
        base_free: str = "1.0",
        quote_free: str = "1000.0",
    ):
        self.closes = closes
        self.latest_price = latest_price or closes[-1]
        self.orders: list[dict[str, str]] = []
        self.stop_orders: list[dict[str, str]] = []
        self.base_free = base_free
        self.quote_free = quote_free

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float | str]]:
        return [[0, 0, 0, 0, price, 0] for price in self.closes]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        return SymbolRules(
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            step_size=Decimal("0.00000100"),
            min_qty=Decimal("0.00001000"),
            min_notional=Decimal("10.00"),
        )

    async def get_account_info(self) -> dict[str, list[dict[str, str]]]:
        return {
            "balances": [
                {"asset": "BTC", "free": self.base_free},
                {"asset": "USDT", "free": self.quote_free},
            ]
        }

    async def place_market_buy(
        self,
        symbol: str,
        quote_order_qty: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, str]:
        self.orders.append(
            {
                "side": "BUY",
                "symbol": symbol,
                "quote_order_qty": str(quote_order_qty),
                "client_order_id": client_order_id or "",
            }
        )
        return {
            "orderId": 111,
            "status": "FILLED",
            "executedQty": "0.01000000",
            "cummulativeQuoteQty": "500.00",
        }

    async def place_market_sell(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, str]:
        self.orders.append(
            {
                "side": "SELL",
                "symbol": symbol,
                "quantity": str(quantity),
                "client_order_id": client_order_id or "",
            }
        )
        return {
            "orderId": 222,
            "status": "FILLED",
            "executedQty": str(quantity),
            "cummulativeQuoteQty": str(float(quantity) * self.latest_price),
        }

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, str]]:
        return []

    async def get_recent_fills(self, symbol: str, limit: int = 50) -> list[dict[str, str]]:
        return []

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        return {
            "bid": self.latest_price - 0.5,
            "ask": self.latest_price + 0.5,
            "mid": self.latest_price,
            "spread": 1.0,
            "spread_pct": (1.0 / self.latest_price) * 100.0,
        }

    async def place_stop_loss_limit_sell(
        self,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        limit_price: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, str]:
        self.stop_orders.append(
            {
                "symbol": symbol,
                "quantity": str(quantity),
                "stop_price": str(stop_price),
                "limit_price": str(limit_price),
                "client_order_id": client_order_id or "",
            }
        )
        return {"orderId": "stop-1", "status": "NEW"}


class FakeCoinbaseClient:
    def __init__(self, closes: list[float]):
        self.closes = closes

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float]]:
        return [[1000 + index, price - 10, price + 10, price - 5, price, 100] for index, price in enumerate(self.closes)]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        return SymbolRules(
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USD",
            step_size=Decimal("0.00000001"),
            min_qty=Decimal("0.00000001"),
            min_notional=Decimal("1.00"),
        )


class InProgressCandleBinanceClient(FakeBinanceClient):
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float | str]]:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        open_ms = now_ms - (10 * 60 * 1000)
        return [[open_ms, 0, 0, 0, price, 0] for price in self.closes]


class FailingMarketDataClient:
    async def get_klines(self, symbol: str, interval: str, limit: int):
        raise RuntimeError("provider blocked")

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        raise RuntimeError("provider blocked")


class FakeNewsCalendarClient:
    def __init__(self, events: list[dict[str, object]]):
        self._events = events

    async def fetch_events(self) -> list[dict[str, object]]:
        return list(self._events)


class FakeMt5Client:
    def __init__(
        self,
        closes: list[float],
        open_volume: float = 0.0,
        spread_pips: float = 0.5,
        equity: float = 1000.0,
        balance: float = 1000.0,
        active_positions_count: int | None = None,
    ):
        self.closes = closes
        self.open_volume = open_volume
        self.position_by_symbol: dict[str, float] = {}
        if open_volume > 0:
            self.position_by_symbol["__default__"] = open_volume
        self.orders: list[dict[str, str | float | None]] = []
        self.spread_pips = spread_pips
        self.equity = equity
        self.balance = balance
        self.active_positions_count = active_positions_count
        self.sl_modifications: list[dict[str, object]] = []

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float]]:
        return [[1000 + index, price - 10, price + 10, price - 5, price, 100] for index, price in enumerate(self.closes)]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        return SymbolRules(
            symbol=symbol,
            base_asset=symbol,
            quote_asset="USD",
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("0"),
        )

    async def get_open_position_volume(self, symbol: str) -> float:
        if symbol in self.position_by_symbol:
            return self.position_by_symbol[symbol]
        return self.position_by_symbol.get("__default__", 0.0)

    async def get_active_positions_count(self) -> int:
        if self.active_positions_count is not None:
            return int(self.active_positions_count)
        return sum(1 for quantity in self.position_by_symbol.values() if quantity > 0)

    async def get_account_info(self) -> dict[str, float]:
        return {"equity": self.equity, "balance": self.balance}

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        return {
            "bid": self.closes[-1] - 0.001,
            "ask": self.closes[-1] + 0.001,
            "point": 0.0001,
            "spread_points": self.spread_pips * 10,
            "spread_pips": self.spread_pips,
        }

    async def get_symbol_specifications(self, symbol: str) -> dict[str, float | int | str]:
        return {
            "symbol": symbol,
            "point": 0.0001,
            "digits": 5,
            "volume_min": 0.01,
            "volume_max": 10.0,
            "volume_step": 0.01,
            "trade_contract_size": 100000.0,
            "trade_tick_size": 0.0001,
            "trade_tick_value": 10.0,
            "trade_tick_value_profit": 10.0,
            "trade_tick_value_loss": 10.0,
            "currency_profit": "USD",
        }

    async def place_market_buy(
        self,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, str | float]:
        current = self.position_by_symbol.pop("__default__", 0.0)
        current = self.position_by_symbol.get(symbol, current)
        updated = current + float(volume)
        self.position_by_symbol[symbol] = updated
        self.open_volume = sum(self.position_by_symbol.values())
        self.orders.append(
            {
                "side": "BUY",
                "symbol": symbol,
                "volume": float(volume),
                "sl": stop_loss,
                "tp": take_profit,
                "client_order_id": client_order_id,
            }
        )
        return {
            "order": "mt5-buy-1",
            "volume": float(volume),
            "price": self.closes[-1],
            "sl": stop_loss,
            "tp": take_profit,
        }

    async def place_market_sell(
        self,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, str | float]:
        current = self.position_by_symbol.pop("__default__", 0.0)
        current = self.position_by_symbol.get(symbol, current)
        updated = max(0.0, current - float(volume))
        self.position_by_symbol[symbol] = updated
        self.open_volume = sum(self.position_by_symbol.values())
        self.orders.append(
            {
                "side": "SELL",
                "symbol": symbol,
                "volume": float(volume),
                "sl": stop_loss,
                "tp": take_profit,
                "client_order_id": client_order_id,
            }
        )
        return {
            "order": 9001,
            "volume": float(volume),
            "price": self.closes[-1],
            "sl": stop_loss,
            "tp": take_profit,
        }

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, float | str]]:
        return []

    async def get_recent_deals(self, lookback_hours: int = 24) -> list[dict[str, float | str]]:
        return []

    async def check_auto_execution_ready(self, symbol: str) -> dict[str, float | str | bool]:
        return {
            "ready": True,
            "symbol": symbol,
            "retcode": 10009,
            "comment": "ok",
        }

    async def modify_position_sl(self, ticket: int, symbol: str, new_sl: float) -> dict[str, object]:
        self.sl_modifications.append({"ticket": ticket, "symbol": symbol, "new_sl": new_sl})
        return {"retcode": 10009, "order": ticket}

    async def close_position_by_ticket(self, ticket: int, symbol: str, volume: float) -> dict[str, object]:
        current = self.position_by_symbol.get(symbol, 0.0)
        self.position_by_symbol[symbol] = max(0.0, current - volume)
        self.open_volume = sum(self.position_by_symbol.values())
        self.orders.append({"side": "CLOSE_BY_TICKET", "ticket": ticket, "symbol": symbol, "volume": volume})
        return {"retcode": 10009, "order": ticket}


def build_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    return testing_session()


class AlwaysBuyEngine:
    def evaluate(self, series, timeframe):
        return SimpleNamespace(
            action="BUY",
            confidence=0.9,
            stop_loss=series["closes"][-1] * 0.99,
            take_profit=series["closes"][-1] * 1.01,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.9)],
            all_strategies=[],
        )


class AlwaysSellEngine:
    def evaluate(self, series, timeframe):
        return SimpleNamespace(
            action="SELL",
            confidence=0.9,
            stop_loss=series["closes"][-1] * 1.01,
            take_profit=series["closes"][-1] * 0.99,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.9)],
            all_strategies=[],
        )


async def test_run_cycle_creates_buy_trade_when_strategy_signals_buy() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        rsi_buy_threshold=35,
        rsi_sell_threshold=70,
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["action"] == "BUY"
    assert result["trade"]["side"] == "BUY"
    assert result["trade"]["intended_price"] is not None
    assert result["trade"]["fill_price"] is not None
    assert result["trade"]["fee_amount"] is not None
    assert result["trade"]["entry_stop_loss"] is not None
    assert result["trade"]["entry_take_profit"] is not None
    assert result["trade"]["confidence"] == 0.9
    assert result["trade"]["strategy_weights"] is not None
    assert result["trade"]["reconciliation_status"] == "SKIPPED"
    assert service.get_position_quantity(db) > 0
    db.close()


async def test_run_cycle_creates_sell_trade_when_strategy_flips_to_sell() -> None:
    db = build_session()
    buy_closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    sell_closes = [85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        rsi_buy_threshold=35,
        rsi_sell_threshold=65,
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(buy_closes))
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)
    service.binance = FakeBinanceClient(sell_closes)
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="SELL",
            confidence=0.88,
            stop_loss=series["closes"][-1] * 1.01,
            take_profit=series["closes"][-1] * 0.99,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy")],
            all_strategies=[],
        )
    )

    result = await service.run_cycle(db)

    assert result["action"] == "SELL"
    assert result["trade"]["side"] == "SELL"
    assert service.get_position_quantity(db) == 0
    db.close()


async def test_build_snapshot_supports_coinbase_market_data_for_dry_run() -> None:
    db = build_session()
    closes = [85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="coinbase",
        market_data_symbol="BTC-USD",
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes),
        market_data_client=FakeCoinbaseClient(closes),
    )

    service.strategy_engine = AlwaysBuyEngine()
    snapshot = await service.build_snapshot(db)

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.market_data_provider == "coinbase"
    assert snapshot.market_data_symbol == "BTC-USD"
    assert snapshot.suggested_action == "BUY"
    assert snapshot.confidence > 0
    db.close()


async def test_build_snapshot_uses_mt5_primary_symbol_for_mt5_market_data() -> None:
    db = build_session()
    settings = Settings(
        market_data_provider="mt5",
        execution_provider="mt5",
        trading_symbol="GBPUSD",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        market_data_symbol="",
        dry_run=True,
        candle_limit=120,
    )
    mt5_client = FakeMt5Client(closes=[1.2010 + (index * 0.0001) for index in range(120)])
    service = TradingService(settings, mt5_client=mt5_client)

    snapshot = await service.build_snapshot(db)

    assert snapshot.market_data_provider == "mt5"
    assert snapshot.market_data_symbol == "GBPUSDm"
    assert snapshot.signal_symbol == "GBPUSDm"
    db.close()


async def test_build_snapshot_auto_falls_back_to_next_provider() -> None:
    db = build_session()
    closes = [85, 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="auto",
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.register_market_data_client("okx", FailingMarketDataClient())
    service.register_market_data_client("kraken", FailingMarketDataClient())
    service.register_market_data_client("coinbase", FakeCoinbaseClient(closes))

    service.strategy_engine = AlwaysBuyEngine()
    snapshot = await service.build_snapshot(db)

    assert snapshot.market_data_provider == "coinbase"
    assert snapshot.market_data_symbol == "BTC-USDT"
    db.close()


async def test_mt5_atr_overlay_preserves_weighted_engine_direction() -> None:
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm,EURUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        atr_recovery_stop_loss_multiplier=1.5,
        atr_recovery_take_profit_multiplier=2.5,
        candle_limit=200,
    )
    service = TradingService(settings, mt5_client=FakeMt5Client(closes))
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.84,
            stop_loss=series["closes"][-1] * 0.998,
            take_profit=series["closes"][-1] * 1.002,
            regime="trend",
            selected_strategies=[SimpleNamespace(name="trend-following", confidence=0.84)],
            all_strategies=[],
        )
    )

    snapshot = await service.build_snapshot(db)

    assert snapshot.strategy_vote_action == "BUY"
    assert snapshot.suggested_action == "BUY"
    assert snapshot.atr_recovery_active is True
    assert snapshot.atr_recovery_symbol_enabled is True
    assert snapshot.atr_recovery_profile["execution_overlay_active"] is True
    assert snapshot.atr_recovery_profile["primary_signal_source"] == "weighted-multi-engine"
    assert snapshot.stop_loss == snapshot.atr_recovery_profile["stop_loss"]
    assert snapshot.take_profit == snapshot.atr_recovery_profile["take_profit"]
    db.close()


async def test_mt5_atr_overlay_stays_inactive_when_symbol_toggle_is_off() -> None:
    db = build_session()
    closes = [1.2000 + (index * 0.0005) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm,EURUSDm",
        atr_recovery_enabled_symbols="EURUSDm",
        candle_limit=200,
    )
    service = TradingService(settings, mt5_client=FakeMt5Client(closes))
    base_stop_loss = closes[-1] * 0.997
    base_take_profit = closes[-1] * 1.003
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.81,
            stop_loss=base_stop_loss,
            take_profit=base_take_profit,
            regime="trend",
            selected_strategies=[SimpleNamespace(name="trend-following", confidence=0.81)],
            all_strategies=[],
        )
    )

    snapshot = await service.build_snapshot(db)

    assert snapshot.strategy_vote_action == "BUY"
    assert snapshot.suggested_action == "BUY"
    assert snapshot.atr_recovery_active is False
    assert snapshot.atr_recovery_symbol_enabled is False
    assert snapshot.atr_recovery_profile["execution_overlay_active"] is False
    assert snapshot.stop_loss == base_stop_loss
    assert snapshot.take_profit == base_take_profit
    db.close()


async def test_mt5_buy_opens_persistent_atr_trade_cycle() -> None:
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm,EURUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        candle_limit=200,
    )
    service = TradingService(settings, mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)
    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()

    assert result["trade"] is not None
    assert cycle.status == "OPEN"
    assert cycle.base_direction == "BUY"
    assert cycle.atr_recovery_enabled is True
    assert cycle.overlay_active is True
    assert cycle.linked_trade_id == result["trade"]["id"]
    db.close()


async def test_mt5_sell_closes_open_atr_trade_cycle() -> None:
    db = build_session()
    buy_closes = [1.1000 + (index * 0.0008) for index in range(240)]
    sell_closes = [1.3000 - (index * 0.0007) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm,EURUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        candle_limit=200,
        strict_no_reentry_enabled=False,
    )
    fake_mt5 = FakeMt5Client(buy_closes)
    service = TradingService(settings, mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)

    service.mt5 = FakeMt5Client(sell_closes, open_volume=0.01)
    service.strategy_engine = AlwaysSellEngine()
    result = await service.run_cycle(db)
    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()

    assert result["trade"] is not None
    assert result["trade"]["side"] == "SELL"
    assert cycle.status == "CLOSED"
    assert cycle.close_reason == "SELL_EXIT"
    assert cycle.closed_at is not None
    db.close()


async def test_run_cycle_holds_when_strategy_confidence_below_threshold() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        strategy_min_confidence_threshold=0.7,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.69,
            stop_loss=series["closes"][-1] * 0.99,
            take_profit=series["closes"][-1] * 1.01,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.69)],
            all_strategies=[],
        )
    )

    result = await service.run_cycle(db)

    assert result["action"] == "HOLD"
    assert result["trade"] is None
    assert result["confidence_gate_blocked"] is True
    assert result["strategy_vote_action"] == "BUY"
    assert result["pre_confidence_action"] == "BUY"
    assert result["confidence_gate_threshold"] == 0.7
    db.close()


async def test_run_cycle_executes_when_strategy_confidence_above_threshold() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        strategy_min_confidence_threshold=0.7,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.71,
            stop_loss=series["closes"][-1] * 0.99,
            take_profit=series["closes"][-1] * 1.01,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.71)],
            all_strategies=[],
        )
    )

    result = await service.run_cycle(db)

    assert result["action"] == "BUY"
    assert result["trade"] is not None
    assert result["confidence_gate_blocked"] is False
    assert result["strategy_vote_action"] == "BUY"
    assert result["pre_confidence_action"] == "BUY"
    assert result["confidence_gate_threshold"] == 0.7
    db.close()


async def test_run_cycle_holds_when_strict_candle_close_enabled_and_candle_open() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        strict_candle_close_enabled=True,
        strategy_candle_mode="1h",
    )
    service = TradingService(settings, binance_client=InProgressCandleBinanceClient(closes))
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.9,
            stop_loss=series["closes"][-1] * 0.99,
            take_profit=series["closes"][-1] * 1.01,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.9)],
            all_strategies=[],
        )
    )

    result = await service.run_cycle(db)

    assert result["action"] == "HOLD"
    assert result["trade"] is None
    assert result["candle_close_gate_enabled"] is True
    assert result["candle_close_gate_blocked"] is True
    assert result["strategy_vote_action"] == "BUY"
    assert result["pre_candle_close_action"] == "BUY"
    assert (result["seconds_until_candle_close"] or 0) > 0
    db.close()


async def test_run_cycle_executes_when_strict_candle_close_disabled() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        strict_candle_close_enabled=False,
        strategy_candle_mode="1h",
    )
    service = TradingService(settings, binance_client=InProgressCandleBinanceClient(closes))
    service.strategy_engine = SimpleNamespace(
        evaluate=lambda series, timeframe: SimpleNamespace(
            action="BUY",
            confidence=0.9,
            stop_loss=series["closes"][-1] * 0.99,
            take_profit=series["closes"][-1] * 1.01,
            regime="test",
            selected_strategies=[SimpleNamespace(name="test-strategy", confidence=0.9)],
            all_strategies=[],
        )
    )

    result = await service.run_cycle(db)

    assert result["action"] == "BUY"
    assert result["trade"] is not None
    assert result["candle_close_gate_enabled"] is False
    assert result["candle_close_gate_blocked"] is False
    assert result["strategy_vote_action"] == "BUY"
    assert result["pre_candle_close_action"] == "BUY"
    db.close()


async def test_upgraded_execution_overlay_blocks_trade_when_quality_gate_fails() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD+",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Bybit-Live",
        mt5_symbol="GBPUSD+",
        mt5_symbols="GBPUSD+,EURUSD+",
        dry_run=True,
        pro_analysis_execution_enabled=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysBuyEngine()

    async def fake_plan(symbol: str) -> dict:
        return {
            "weighted_vote_action": "BUY",
            "final_action": "HOLD",
            "session_name": "Asia session",
            "session_allowed": False,
            "quality_gate_passed": False,
            "quality_gate_reasons": ["Session filter blocked trading during Asia session."],
            "trade_idea": {
                "stop_loss": 84.0,
                "take_profit_2": 88.0,
                "risk_to_reward_tp2": 1.5,
            },
        }

    service._generate_pro_analysis_execution_plan = fake_plan

    result = await service.run_cycle(db)

    assert result["strategy_vote_action"] == "BUY"
    assert result["action"] == "HOLD"
    assert result["trade"] is None
    assert result["pro_analysis_gate_blocked"] is True
    assert result["pro_analysis_final_action"] == "HOLD"
    assert "Session filter blocked" in result["pro_analysis_gate_reasons"][0]
    db.close()


async def test_upgraded_execution_overlay_updates_sl_tp_when_plan_confirms_trade() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD+",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Bybit-Live",
        mt5_symbol="GBPUSD+",
        mt5_symbols="GBPUSD+,EURUSD+",
        dry_run=True,
        pro_analysis_execution_enabled=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysBuyEngine()

    async def fake_plan(symbol: str) -> dict:
        return {
            "weighted_vote_action": "BUY",
            "final_action": "BUY",
            "session_name": "London session",
            "session_allowed": True,
            "quality_gate_passed": True,
            "quality_gate_reasons": [],
            "trade_idea": {
                "stop_loss": 82.0,
                "take_profit_2": 94.0,
                "risk_to_reward_tp2": 3.0,
            },
        }

    service._generate_pro_analysis_execution_plan = fake_plan

    snapshot = await service.build_snapshot(db)

    assert snapshot.suggested_action == "BUY"
    assert snapshot.pro_analysis_gate_blocked is False
    assert snapshot.pro_analysis_vote_action == "BUY"
    assert snapshot.pro_analysis_final_action == "BUY"
    assert snapshot.stop_loss == 82.0
    assert snapshot.take_profit == 94.0
    assert snapshot.pro_analysis_rr == 3.0
    db.close()


async def test_generate_execution_plan_allows_day_trading_during_london_open_overlap() -> None:
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        mt5_symbol="GBPUSD+",
        mt5_symbols="GBPUSD+,EURUSD+",
        pro_analysis_execution_require_session_filter=True,
    )
    service = ProfessionalAnalysisService(settings, FakeMt5Client(closes), AlwaysBuyEngine())

    async def fake_generate_report(
        symbols: list[str] | None = None,
        account_size: float | None = None,
        risk_tolerance: str = "MEDIUM",
        trading_style: str = "DAY TRADING",
    ) -> dict:
        return {
            "reports": [
                {
                    "market_overview": {
                        "timeframes": [
                            {"timeframe": "15m", "vote": "BUY", "price": 1.25, "vwap": 1.24},
                            {"timeframe": "1h", "vote": "BUY", "price": 1.25, "vwap": 1.24},
                            {"timeframe": "4h", "vote": "BUY", "price": 1.25, "vwap": 1.24},
                        ],
                        "smart_money": {"order_block_tone": "bullish"},
                        "market_structure": "bullish",
                    },
                    "strategy_logic": {
                        "weighted_vote": {
                            "action": "BUY",
                            "confidence": 0.9,
                            "score": 2.0,
                        }
                    },
                    "trade_ideas": [
                        {
                            "stop_loss": 1.2,
                            "take_profit_2": 1.35,
                            "risk_to_reward_tp2": 3.0,
                        }
                    ],
                }
            ]
        }

    service.generate_report = fake_generate_report

    plan = await service.generate_execution_plan(
        symbol="GBPUSD+",
        trading_style="DAY TRADING",
        as_of=datetime(2026, 4, 7, 8, 23, tzinfo=UTC),
    )

    assert plan["session_allowed"] is True
    assert plan["session_name"] == "London session"
    assert "London open" in plan["active_sessions"]
    assert plan["final_action"] == "BUY"


async def test_generate_execution_plan_uses_scalping_timeframes_for_execution_gate() -> None:
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        mt5_symbol="EURUSDm",
        mt5_symbols="EURUSDm,GBPUSDm",
        pro_analysis_execution_require_session_filter=True,
        pro_analysis_execution_require_higher_timeframe_alignment=True,
        pro_analysis_execution_require_vwap_alignment=True,
    )
    service = ProfessionalAnalysisService(settings, FakeMt5Client(closes), AlwaysBuyEngine())

    async def fake_generate_report(
        symbols: list[str] | None = None,
        account_size: float | None = None,
        risk_tolerance: str = "LOW",
        trading_style: str = "SCALPING",
    ) -> dict:
        return {
            "reports": [
                {
                    "market_overview": {
                        "timeframes": [
                            {"timeframe": "1m", "vote": "SELL", "price": 1.0990, "vwap": 1.0995},
                            {"timeframe": "5m", "vote": "SELL", "price": 1.0988, "vwap": 1.0994},
                            {"timeframe": "15m", "vote": "SELL", "price": 1.0987, "vwap": 1.0992},
                            {"timeframe": "1h", "vote": "BUY", "price": 1.1010, "vwap": 1.1005},
                            {"timeframe": "4h", "vote": "BUY", "price": 1.1030, "vwap": 1.1015},
                            {"timeframe": "1d", "vote": "BUY", "price": 1.1050, "vwap": 1.1025},
                        ],
                        "smart_money": {"order_block_tone": "bearish"},
                        "market_structure": "mixed",
                    },
                    "strategy_logic": {
                        "weighted_vote": {
                            "action": "SELL",
                            "confidence": 0.75,
                            "score": -2.4,
                        }
                    },
                    "trade_ideas": [
                        {
                            "stop_loss": 1.1005,
                            "take_profit_2": 1.0950,
                            "risk_to_reward_tp2": 3.0,
                        }
                    ],
                }
            ]
        }

    service.generate_report = fake_generate_report

    plan = await service.generate_execution_plan(
        symbol="EURUSDm",
        trading_style="SCALPING",
        risk_tolerance="LOW",
        as_of=datetime(2026, 4, 7, 8, 23, tzinfo=UTC),
    )

    assert plan["session_allowed"] is True
    assert plan["session_name"] == "London open"
    assert plan["higher_timeframe_alignment"] is True
    assert plan["entry_timeframe_confirmation"] is True
    assert plan["vwap_alignment"] is True
    assert plan["quality_gate_reasons"] == []
    assert plan["final_action"] == "SELL"


async def test_run_professional_analysis_execution_reports_all_swing_gate_reasons() -> None:
    db = build_session()
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSDm",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Broker-Live",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm,EURUSDm",
        dry_run=True,
        pro_analysis_execution_enabled=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysSellEngine()

    async def fake_generate_execution_plan(
        symbol: str,
        account_size: float | None = None,
        risk_tolerance: str | None = None,
        trading_style: str | None = None,
        as_of: datetime | None = None,
    ) -> dict:
        return {
            "symbol": symbol,
            "weighted_vote_action": "SELL",
            "final_action": "HOLD",
            "session_name": "London session confirmation",
            "session_allowed": True,
            "quality_gate_passed": False,
            "quality_gate_reasons": [
                "Weighted vote confidence 55.9% is below upgraded threshold 68.0%.",
                "1H entry timeframe does not confirm the weighted vote.",
                "4H order-block tone is bullish against the proposed SELL.",
            ],
            "trade_idea": {
                "risk_to_reward_tp2": 2.5,
            },
        }

    original_service_class = ProfessionalAnalysisService

    class FakeProfessionalAnalysisService:
        def __init__(self, settings, mt5_client, strategy_engine) -> None:
            self.settings = settings
            self.mt5 = mt5_client
            self.strategy_engine = strategy_engine

        async def generate_execution_plan(self, *args, **kwargs):
            return await fake_generate_execution_plan(*args, **kwargs)

    try:
        import app.services.trading as trading_module

        trading_module.ProfessionalAnalysisService = FakeProfessionalAnalysisService
        result = await service.run_professional_analysis_execution(
            db,
            symbol="GBPUSDm",
            trading_style="SWING",
            risk_tolerance="MEDIUM",
        )

        assert result["action"] == "HOLD"
        assert "execution_block" in result
        assert "Weighted vote confidence 55.9%" in result["execution_block"]
        assert "1H entry timeframe does not confirm" in result["execution_block"]
        assert "4H order-block tone is bullish" in result["execution_block"]
    finally:
        import app.services.trading as trading_module

        trading_module.ProfessionalAnalysisService = original_service_class
        db.close()


async def test_queue_worker_fails_job_when_active_profile_is_invalid() -> None:
    db = build_session()
    settings = Settings(
        database_url="sqlite:///:memory:",
        execution_provider="mt5",
        mt5_execution_mode="queue",
        mt5_runtime_owner_id="local",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Broker-Live",
    )
    execution_service = Mt5ExecutionService(settings, mt5_client=FakeMt5Client([100 + index for index in range(20)]))
    queued = execution_service._queue_job(
        db,
        owner_id="local",
        signal_symbol="GBPUSDm",
        execution_symbol="GBPUSDm",
        action="BUY",
        volume=Decimal("0.01"),
        stop_loss=None,
        take_profit=None,
        client_order_id="queue-test-1",
        execution_request_id=None,
    )

    class InvalidProfileService:
        def get_active_profile(self, db, owner_id: str | None = None):
            return SimpleNamespace(id=1, owner_id=owner_id or "local")

        async def apply_saved_runtime_profile_if_valid(self, db, runtime_settings=None, owner_id: str | None = None):
            return None

    result = await execution_service.process_next_job(
        db,
        runtime_settings=settings,
        profile_service=InvalidProfileService(),
        owner_id="local",
    )

    job = db.query(Mt5ExecutionJob).filter(Mt5ExecutionJob.id == queued["job_id"]).one()
    assert result is not None
    assert result["status"] == "FAILED"
    assert "active runtime profile failed validation" in result["error"].lower()
    assert job.status == "FAILED"
    assert "active runtime profile failed validation" in (job.error or "").lower()
    db.close()


async def test_queue_job_is_affined_to_active_profile_worker() -> None:
    db = build_session()
    now = datetime.now(UTC)
    settings = Settings(
        database_url="sqlite:///:memory:",
        execution_provider="mt5",
        mt5_execution_mode="queue",
        mt5_runtime_owner_id="owner-a",
        mt5_profile_encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    profile = Mt5Profile(
        owner_id="owner-a",
        label="Primary",
        login=123456,
        password_encrypted="encrypted",
        server="Broker-A",
        terminal_path=r"C:\MT5\terminal64.exe",
        symbols_csv="GBPUSDm",
        volume_lots=0.01,
        is_active=True,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    worker = Mt5Worker(
        worker_key="worker-a",
        owner_id="owner-a",
        profile_id=profile.id,
        label="Worker A",
        terminal_path=r"C:\MT5\terminal64.exe",
        status="ONLINE",
        heartbeat_at=now,
        updated_at=now,
    )
    db.add(worker)
    db.commit()

    execution_service = Mt5ExecutionService(settings, mt5_client=FakeMt5Client([100 + index for index in range(20)]))
    queued = execution_service._queue_job(
        db,
        owner_id="owner-a",
        signal_symbol="GBPUSDm",
        execution_symbol="GBPUSDm",
        action="BUY",
        volume=Decimal("0.01"),
        stop_loss=None,
        take_profit=None,
        client_order_id="queue-affinity-1",
        execution_request_id=None,
    )

    wrong_claim = execution_service.claim_next_job(db, owner_id="owner-a", profile_id=profile.id, worker_key="worker-b")
    correct_claim = execution_service.claim_next_job(db, owner_id="owner-a", profile_id=profile.id, worker_key="worker-a")
    job = db.query(Mt5ExecutionJob).filter(Mt5ExecutionJob.id == queued["job_id"]).one()

    assert queued["profile_id"] == profile.id
    assert queued["assigned_worker_key"] == "worker-a"
    assert wrong_claim is None
    assert correct_claim is not None
    assert correct_claim.id == job.id
    assert job.claimed_by_worker_key == "worker-a"
    assert job.status == "CLAIMED"
    db.close()


async def test_process_next_job_applies_specific_profile_for_bound_worker() -> None:
    db = build_session()
    now = datetime.now(UTC)
    settings = Settings(
        database_url="sqlite:///:memory:",
        execution_provider="mt5",
        mt5_execution_mode="queue",
        mt5_runtime_owner_id="owner-a",
        mt5_profile_encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    profile = Mt5Profile(
        owner_id="owner-a",
        label="Primary",
        login=777001,
        password_encrypted="encrypted",
        server="Broker-A",
        terminal_path=r"C:\MT5\terminal64.exe",
        symbols_csv="GBPUSDm",
        volume_lots=0.01,
        is_active=True,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    worker = Mt5Worker(
        worker_key="worker-a",
        owner_id="owner-a",
        profile_id=profile.id,
        label="Worker A",
        terminal_path=r"C:\MT5\terminal64.exe",
        status="ONLINE",
        heartbeat_at=now,
        updated_at=now,
    )
    db.add(worker)
    db.commit()

    class RecordingMt5Client:
        instances: list["RecordingMt5Client"] = []

        def __init__(self, runtime_settings: Settings):
            self.login = runtime_settings.mt5_login
            self.server = runtime_settings.mt5_server
            self.symbol = runtime_settings.mt5_symbol
            self.orders: list[dict[str, object]] = []
            self.__class__.instances.append(self)

        async def place_market_buy(
            self,
            symbol: str,
            volume: Decimal,
            stop_loss: float | None = None,
            take_profit: float | None = None,
            client_order_id: str | None = None,
        ) -> dict[str, object]:
            self.orders.append(
                {
                    "symbol": symbol,
                    "volume": float(volume),
                    "client_order_id": client_order_id,
                }
            )
            return {"order": "mt5-buy-1", "volume": float(volume), "price": 1.2345}

        async def place_market_sell(self, *args, **kwargs):
            raise AssertionError("SELL should not be called in this test")

    class SpecificProfileService:
        def __init__(self) -> None:
            self.applied: list[tuple[int, str | None]] = []

        async def apply_profile_if_valid(
            self,
            db,
            profile_id: int,
            runtime_settings: Settings | None = None,
            trading_service=None,
            owner_id: str | None = None,
        ):
            self.applied.append((profile_id, owner_id))
            assert runtime_settings is not None
            runtime_settings.mt5_login = 777001
            runtime_settings.mt5_server = "Broker-A"
            runtime_settings.mt5_symbol = "GBPUSDm"
            return {"id": profile_id, "owner_id": owner_id}

    execution_service = Mt5ExecutionService(settings, mt5_client_factory=RecordingMt5Client)
    queued = execution_service._queue_job(
        db,
        owner_id="owner-a",
        signal_symbol="GBPUSDm",
        execution_symbol="GBPUSDm",
        action="BUY",
        volume=Decimal("0.02"),
        stop_loss=None,
        take_profit=None,
        client_order_id="queue-affinity-2",
        execution_request_id=None,
    )
    profile_service = SpecificProfileService()

    result = await execution_service.process_next_job(
        db,
        runtime_settings=settings,
        profile_service=profile_service,
        owner_id="owner-a",
        profile_id=profile.id,
        worker_key="worker-a",
    )

    job = db.query(Mt5ExecutionJob).filter(Mt5ExecutionJob.id == queued["job_id"]).one()
    worker = db.query(Mt5Worker).filter(Mt5Worker.worker_key == "worker-a").one()

    assert result is not None
    assert result["order"] == "mt5-buy-1"
    assert profile_service.applied == [(profile.id, "owner-a")]
    assert RecordingMt5Client.instances
    assert RecordingMt5Client.instances[-1].login == 777001
    assert RecordingMt5Client.instances[-1].server == "Broker-A"
    assert RecordingMt5Client.instances[-1].symbol == "GBPUSDm"
    assert job.status == "FILLED"
    assert job.claimed_by_worker_key == "worker-a"
    assert worker.last_claimed_at is not None
    assert worker.status == "ONLINE"
    db.close()


async def test_queue_job_uses_provisioned_worker_binding_when_worker_is_not_online() -> None:
    db = build_session()
    now = datetime.now(UTC)
    settings = Settings(
        database_url="sqlite:///:memory:",
        execution_provider="mt5",
        mt5_execution_mode="queue",
        mt5_runtime_owner_id="owner-b",
    )
    profile = Mt5Profile(
        owner_id="owner-b",
        label="Provisioned",
        login=123001,
        password_encrypted="encrypted",
        server="Broker-B",
        terminal_path=r"C:\MT5\terminal64.exe",
        symbols_csv="EURUSDm",
        volume_lots=0.01,
        is_active=True,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    worker = Mt5Worker(
        worker_key="worker-b",
        owner_id="owner-b",
        profile_id=profile.id,
        label="Provisioned worker",
        terminal_path=r"C:\MT5\terminal64.exe",
        status="PROVISIONED",
        updated_at=now,
    )
    db.add(worker)
    db.commit()

    execution_service = Mt5ExecutionService(settings, mt5_client=FakeMt5Client([100 + index for index in range(20)]))
    queued = execution_service._queue_job(
        db,
        owner_id="owner-b",
        signal_symbol="EURUSDm",
        execution_symbol="EURUSDm",
        action="BUY",
        volume=Decimal("0.01"),
        stop_loss=None,
        take_profit=None,
        client_order_id="queue-provisioned-1",
        execution_request_id=None,
    )

    assert queued["profile_id"] == profile.id
    assert queued["assigned_worker_key"] == "worker-b"
    db.close()


async def test_duplicate_idempotency_key_is_blocked() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = AlwaysBuyEngine()

    first = await service.run_cycle(db, request_id="manual-1")
    second = await service.run_cycle(db, request_id="manual-1")

    assert first["trade"] is not None
    assert second["trade"] is None
    assert "duplicate idempotency key" in second["execution_block"].lower()
    db.close()


async def test_duplicate_idempotency_key_is_scoped_by_mt5_account() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Broker-A",
        mt5_symbol="BTCUSDm",
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysBuyEngine()

    first = await service.run_cycle(db, request_id="manual-1")

    settings.mt5_login = 654321
    settings.mt5_server = "Broker-B"

    second = await service.run_cycle(db, request_id="manual-1")
    requests = db.query(ExecutionRequest).order_by(ExecutionRequest.id.asc()).all()

    assert first["trade"] is not None
    assert second["trade"] is not None
    assert len(requests) == 2
    assert requests[0].account_scope == "mt5:123456:broker-a"
    assert requests[1].account_scope == "mt5:654321:broker-b"
    db.close()


async def test_pending_execution_request_is_scoped_by_mt5_account() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Broker-A",
        mt5_symbol="BTCUSDm",
        trade_amount_usdt=50,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDm",
        market_data_provider="binance",
        latest_price=85.0,
        rsi=25.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=SymbolRules(
            symbol="BTCUSDm",
            base_asset="BTC",
            quote_asset="USD",
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("0"),
        ),
    )

    service._register_execution_request(
        db,
        snapshot,
        action="BUY",
        account_scope=service._current_execution_account_scope(),
        idempotency_key="mt5:123456:broker-a:manual-pending",
        client_order_id="pending-a",
    )

    blocked_same_account = await service._execution_block_reason(db, snapshot, request_id="manual-2")

    settings.mt5_login = 654321
    settings.mt5_server = "Broker-B"

    blocked_other_account = await service._execution_block_reason(db, snapshot, request_id="manual-2")

    assert blocked_same_account is not None
    assert "pending request" in blocked_same_account.lower()
    assert blocked_other_account is None
    db.close()


async def test_run_cycle_places_live_mt5_order_when_enabled() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["action"] == "BUY"
    assert result["trade"]["side"] == "BUY"
    assert result["trade"]["is_dry_run"] is False
    assert result["execution_symbol"] == "BTCUSDm"
    assert fake_mt5.open_volume == 0.02
    assert fake_mt5.orders[-1]["symbol"] == "BTCUSDm"
    db.close()


async def test_run_cycle_queues_mt5_order_when_queue_mode_enabled() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_execution_mode="queue",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["action"] == "BUY"
    assert result["trade"]["status"] == "QUEUED"
    assert result["trade"]["reconciliation_status"] == "QUEUED"
    assert fake_mt5.orders == []
    jobs = db.query(Mt5ExecutionJob).all()
    assert len(jobs) == 1
    assert jobs[0].action == "BUY"
    assert jobs[0].status == "QUEUED"
    db.close()


async def test_strict_live_mt5_policy_blocks_duplicate_buy_when_position_already_open() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, open_volume=0.02)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["trade"] is None
    assert "execution_block" in result
    assert "duplicate re-entry" in result["execution_block"].lower()
    assert fake_mt5.orders == []
    db.close()


async def test_strict_live_mt5_policy_blocks_duplicate_buy_at_min_lot_position() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.01,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, open_volume=0.01)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["trade"] is None
    assert "execution_block" in result
    assert "duplicate re-entry" in result["execution_block"].lower()
    assert fake_mt5.orders == []
    db.close()


async def test_strict_live_mt5_policy_can_be_disabled_to_allow_reentry() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        strict_no_reentry_enabled=False,
    )
    fake_mt5 = FakeMt5Client(closes, open_volume=0.02)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert "execution_block" not in result
    assert result["trade"] is not None
    assert result["trade"]["side"] == "BUY"
    assert len(fake_mt5.orders) == 1
    db.close()


async def test_strict_live_mt5_policy_blocks_second_cycle_after_first_buy_fill() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    first = await service.run_cycle(db, request_id="cycle-1")
    second = await service.run_cycle(db, request_id="cycle-2")

    assert first["trade"] is not None
    assert second["trade"] is None
    assert "execution_block" in second
    assert "duplicate re-entry" in second["execution_block"].lower()
    assert len(fake_mt5.orders) == 1
    db.close()


async def test_mt5_active_position_cap_blocks_new_buy_when_limit_reached() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        risk_mt5_max_active_positions=5,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, active_positions_count=5)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db, request_id="cap-hit")

    assert result["trade"] is None
    assert "execution_block" in result
    assert "active positions" in result["execution_block"].lower()
    assert len(fake_mt5.orders) == 0
    db.close()


async def test_mt5_active_position_cap_allows_buy_below_limit() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        risk_mt5_max_active_positions=5,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, active_positions_count=4)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db, request_id="cap-ok")

    assert "execution_block" not in result
    assert result["trade"] is not None
    assert result["trade"]["side"] == "BUY"
    assert len(fake_mt5.orders) == 1
    db.close()


async def test_mt5_active_position_cap_blocks_new_sell_when_limit_reached() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="BTCUSDm",
        mt5_volume_lots=0.02,
        risk_mt5_max_active_positions=5,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, open_volume=0.02, active_positions_count=5)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysSellEngine()

    result = await service.run_cycle(db, request_id="cap-hit-sell")

    assert result["trade"] is None
    assert "execution_block" in result
    assert "active positions" in result["execution_block"].lower()
    assert len(fake_mt5.orders) == 0
    db.close()


async def test_run_auto_cycle_executes_all_configured_mt5_symbols() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        mt5_symbols="EURUSD,GBPUSD,USDJPY",
        mt5_volume_lots=0.02,
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_auto_cycle(db)

    assert result["mode"] == "multi-symbol"
    assert result["symbols"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert result["errors"] == {}
    assert len(result["results"]) == 3
    assert {item["symbol"] for item in result["results"]} == {"EURUSD", "GBPUSD", "USDJPY"}
    assert [order["symbol"] for order in fake_mt5.orders] == ["EURUSD", "GBPUSD", "USDJPY"]
    db.close()


async def test_run_backtest_returns_metrics() -> None:
    closes = [100 + ((index % 20) - 10) * 0.3 + (index * 0.02) for index in range(140)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        rsi_buy_threshold=35,
        rsi_sell_threshold=65,
        trade_amount_usdt=100,
        fee_rate=0.001,
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))

    result = await service.run_backtest(history_limit=120, initial_balance=1000)

    assert result["candles"] >= 120
    assert result["market_data_provider"] == "binance"
    assert result["strategy_mode"] == "adaptive-combined-10"
    assert result["simulation_mode"] == "event-driven-simulator"
    assert isinstance(result["roi_pct"], float)
    assert "avg_confidence" in result
    assert "max_drawdown_pct" in result
    assert "windows" in result
    assert "train" in result["windows"]
    assert "validation" in result["windows"]
    assert "out_of_sample" in result["windows"]
    assert "walk_forward" in result
    assert "monte_carlo" in result
    assert "simulation_assumptions" in result


async def test_run_backtest_honors_market_data_symbol_override() -> None:
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))

    result = await service.run_backtest(history_limit=80, market_data_symbol="EURUSD")

    assert result["market_data_symbol"] == "EURUSD"
    assert result["signal_symbol"] == "BTCUSDT"
    assert result["execution_symbol"] == "BTCUSDT"

async def test_spread_guard_blocks_trade_when_spread_exceeds_limit() -> None:
    closes = [100 + ((index % 20) - 10) * 0.3 + (index * 0.02) for index in range(140)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        can_place_live_orders=True,
        live_trading_armed=True,
        environment="production",
        risk_max_spread_pips=1.0,
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, spread_pips=2.5)
    database = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(database)
    db_session = sessionmaker(bind=database)()
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db_session)

    assert "execution_block" in result
    assert "spread" in result["execution_block"]
    assert result["trade"] is None
    db_session.close()


async def test_daily_loss_guard_blocks_trade_when_drawdown_exceeds_limit() -> None:
    closes = [100 + ((index % 20) - 10) * 0.3 + (index * 0.02) for index in range(140)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        can_place_live_orders=True,
        live_trading_armed=True,
        environment="production",
        risk_daily_loss_limit_pct=3.0,
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, equity=965.0, balance=1000.0)
    database = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(database)
    db_session = sessionmaker(bind=database)()
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    # Set the anchor to simulate trading day start at 1000 equity
    service._daily_equity_anchor_value = 1000.0
    service._daily_equity_anchor_date = datetime.now(UTC).date()

    result = await service.run_cycle(db_session)

    assert "execution_block" in result
    assert "drawdown" in result["execution_block"]
    assert result["trade"] is None
    db_session.close()


async def test_no_block_when_risk_limits_disabled() -> None:
    closes = [100 + ((index % 20) - 10) * 0.3 + (index * 0.02) for index in range(140)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        can_place_live_orders=True,
        live_trading_armed=True,
        environment="production",
        risk_max_spread_pips=0.0,
        risk_daily_loss_limit_pct=0.0,
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes, spread_pips=5.0, equity=970.0)
    database = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(database)
    db_session = sessionmaker(bind=database)()
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db_session)

    assert "execution_block" not in result
    assert result["trade"] is not None
    db_session.close()


async def test_trade_cooldown_blocks_immediate_reentry() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        risk_min_seconds_between_trades=300,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = AlwaysBuyEngine()

    first = await service.run_cycle(db, request_id="cooldown-on-1")
    second = await service.run_cycle(db, request_id="cooldown-on-2")

    assert first["trade"] is not None
    assert second["trade"] is None
    assert "cooldown" in second["execution_block"].lower()
    db.close()


async def test_trade_cooldown_disabled_allows_reentry() -> None:
    db = build_session()
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        trade_amount_usdt=50,
        dry_run=True,
        risk_min_seconds_between_trades=0,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))
    service.strategy_engine = AlwaysBuyEngine()

    first = await service.run_cycle(db, request_id="req-1")
    second = await service.run_cycle(db, request_id="req-2")

    assert first["trade"] is not None
    assert second["trade"] is not None
    db.close()


async def test_risk_exposure_cap_limits_live_binance_quote_size() -> None:
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=200,
        risk_max_quote_exposure_pct=10,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.0", quote_free="500.0"),
    )
    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=100.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
        stop_loss=95.0,
    )

    quote = await service._resolve_buy_quote_amount(snapshot, rules)

    assert quote == Decimal("50")


async def test_risk_stop_loss_budget_limits_live_binance_quote_size() -> None:
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=500,
        risk_max_loss_per_trade_pct=1,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.0", quote_free="1000.0"),
    )
    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=100.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
        stop_loss=95.0,
    )

    quote = await service._resolve_buy_quote_amount(snapshot, rules)

    assert quote == Decimal("200")


async def test_reconciliation_skips_when_live_orders_disabled() -> None:
    db = build_session()
    closes = [100 + index for index in range(80)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        dry_run=True,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes))

    result = await service.reconcile_broker_state(db)

    assert result["status"] == "skipped"
    db.close()


async def test_reconciliation_returns_binance_snapshot_fields() -> None:
    db = build_session()
    closes = [100 + index for index in range(80)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.5", quote_free="1200.0"),
    )

    result = await service.reconcile_broker_state(db)

    assert result["status"] == "ok"
    assert result["provider"] == "binance"
    assert "balances" in result
    assert "positions" in result
    assert "open_orders" in result
    assert "fills" in result
    assert "rejected_orders" in result
    assert "orphaned_stops" in result
    db.close()


async def test_portfolio_correlation_cap_blocks_new_buy() -> None:
    db = build_session()
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="EURUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="EURUSD",
        mt5_symbols="EURUSD,GBPUSD",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        risk_correlation_cap=0.6,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))

    async def fake_exposures(db_session, snapshot):
        return {"EURUSD": 1000.0, "GBPUSD": 900.0}

    async def fake_returns(symbols, lookback):
        return {
            "EURUSD": [0.010, 0.015, -0.005, 0.012, 0.009, -0.004, 0.011],
            "GBPUSD": [0.011, 0.016, -0.004, 0.013, 0.010, -0.003, 0.012],
        }

    service._build_portfolio_exposure_map = fake_exposures  # type: ignore[method-assign]
    service._build_symbol_returns_map = fake_returns  # type: ignore[method-assign]

    snapshot = MarketSnapshot(
        signal_symbol="EURUSD",
        market_data_symbol="EURUSD",
        execution_symbol="EURUSD",
        market_data_provider="mt5",
        latest_price=1.10,
        rsi=51.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=SymbolRules(
            symbol="EURUSD",
            base_asset="EURUSD",
            quote_asset="USD",
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("0"),
        ),
    )

    reason = await service._portfolio_risk_block_reason(db, snapshot)

    assert reason is not None
    assert "correlation cap" in reason.lower()
    db.close()


async def test_portfolio_var_limit_blocks_new_buy() -> None:
    db = build_session()
    closes = [100 + index for index in range(120)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="EURUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="EURUSD",
        mt5_symbols="EURUSD,GBPUSD",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        risk_correlation_cap=0.0,
        risk_portfolio_var_limit_pct=0.8,
        risk_var_confidence=0.95,
    )
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=FakeMt5Client(closes))

    async def fake_exposures(db_session, snapshot):
        return {"EURUSD": 1200.0, "GBPUSD": 800.0}

    async def fake_returns(symbols, lookback):
        return {
            "EURUSD": [-0.018, -0.010, 0.003, -0.021, -0.014, 0.002, -0.017, -0.011, -0.016, -0.009],
            "GBPUSD": [-0.014, -0.009, 0.002, -0.017, -0.012, 0.001, -0.013, -0.008, -0.011, -0.007],
        }

    service._build_portfolio_exposure_map = fake_exposures  # type: ignore[method-assign]
    service._build_symbol_returns_map = fake_returns  # type: ignore[method-assign]

    snapshot = MarketSnapshot(
        signal_symbol="EURUSD",
        market_data_symbol="EURUSD",
        execution_symbol="EURUSD",
        market_data_provider="mt5",
        latest_price=1.10,
        rsi=48.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=SymbolRules(
            symbol="EURUSD",
            base_asset="EURUSD",
            quote_asset="USD",
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("0"),
        ),
    )

    reason = await service._portfolio_risk_block_reason(db, snapshot)

    assert reason is not None
    assert "var" in reason.lower()
    db.close()


async def test_volatility_target_reduces_size_on_high_volatility() -> None:
    # Highly volatile closes: large swings produce high realized vol, capping position size.
    closes = [100, 120, 80, 130, 70, 110, 60, 120, 75, 115]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=200,
        risk_volatility_target_pct=5,
        risk_vol_lookback_candles=10,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.0", quote_free="500.0"),
    )
    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=115.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
    )

    quote = await service._resolve_buy_quote_amount(snapshot, rules)

    # Vol cap should be well below the configured 200 due to high realized volatility.
    assert quote < Decimal("200")


async def test_volatility_target_disabled_returns_configured_amount() -> None:
    closes = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=150,
        risk_volatility_target_pct=0,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.0", quote_free="500.0"),
    )
    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=101.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
    )

    quote = await service._resolve_buy_quote_amount(snapshot, rules)

    assert quote == Decimal("150")


async def test_spread_guard_blocks_binance_when_spread_too_wide() -> None:
    closes = [100, 101, 102, 103, 104, 105, 106, 107]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=100,
        risk_max_spread_pct=0.2,
    )
    client = FakeBinanceClient(closes, latest_price=100)
    service = TradingService(settings, binance_client=client)
    db = build_session()

    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=100.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
    )
    reason = await service._enforced_risk_block_reason(snapshot)

    assert reason is not None
    assert "spread" in reason.lower()
    db.close()


async def test_max_concurrent_positions_blocks_new_buy() -> None:
    closes = [100, 101, 102, 103, 104, 105, 106, 107]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
        trade_amount_usdt=50,
        risk_max_concurrent_positions=1,
    )
    client = FakeBinanceClient(closes)
    service = TradingService(settings, binance_client=client)
    service._broker_positions["BTCUSDT"] = 0.0

    async def fake_open_positions(snapshot):
        return 1

    service._count_open_positions = fake_open_positions  # type: ignore[method-assign]
    db = build_session()

    rules = await service.binance.get_exchange_info("BTCUSDT")
    snapshot = MarketSnapshot(
        signal_symbol="BTCUSDT",
        market_data_symbol="BTCUSDT",
        execution_symbol="BTCUSDT",
        market_data_provider="binance",
        latest_price=107.0,
        rsi=50.0,
        position_quantity=0.0,
        suggested_action="BUY",
        rules=rules,
    )
    reason = await service._enforced_risk_block_reason(snapshot)

    assert reason is not None
    assert "concurrent" in reason.lower()
    db.close()


async def test_reconciliation_persists_position_journal() -> None:
    db = build_session()
    closes = [100 + index for index in range(80)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="BTCUSDT",
        market_data_provider="binance",
        execution_provider="binance",
        binance_api_key="demo-key",
        binance_api_secret="demo-secret",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    service = TradingService(
        settings,
        binance_client=FakeBinanceClient(closes, base_free="0.75", quote_free="900.0"),
    )

    result = await service.reconcile_broker_state(db)
    journal_rows = db.query(BrokerPositionJournal).all()

    assert result["status"] == "ok"
    assert len(journal_rows) == 1
    assert journal_rows[0].provider == "binance"
    assert journal_rows[0].execution_symbol == "BTCUSDT"
    assert journal_rows[0].quantity == 0.75
    db.close()


async def test_startup_self_check_reports_mt5_auto_execution_readiness() -> None:
    db = build_session()
    closes = [100 + index for index in range(80)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)

    result = await service.run_startup_self_check(db)

    assert result["execution_provider"] == "mt5"
    assert "mt5_auto_execution_check" in result
    assert result["mt5_auto_execution_check"]["ready"] is True
    db.close()


async def test_startup_self_check_ignores_mt5_readiness_when_na() -> None:
    class NaReadinessMt5Client(FakeMt5Client):
        async def check_auto_execution_ready(self, symbol: str) -> dict[str, float | str | bool]:
            return {
                "ready": False,
                "symbol": symbol,
                "retcode": "N/A",
                "comment": "N/A",
            }

    db = build_session()
    closes = [100 + index for index in range(80)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        trading_symbol="GBPUSD",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
        mt5_symbol="GBPUSD",
        live_trading_armed=True,
        environment="production",
        dry_run=False,
    )
    fake_mt5 = NaReadinessMt5Client(closes)
    service = TradingService(settings, binance_client=FakeBinanceClient(closes), mt5_client=fake_mt5)

    result = await service.run_startup_self_check(db)

    assert result["execution_provider"] == "mt5"
    assert result["mt5_auto_execution_check"]["ready"] is False
    assert result["mt5_auto_execution_check_ignored"] is True
    db.close()


def test_can_place_live_orders_normalizes_mt5_execution_provider_case() -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        execution_provider="MT5",
        dry_run=False,
        mt5_login=123456,
        mt5_password="demo-password",
        mt5_server="Exness-MT5Trial",
    )

    assert settings.effective_execution_provider == "mt5"
    assert settings.can_place_live_orders is True


async def test_hedge_monitor_places_live_hedge_when_price_hits_trigger() -> None:
    """Area 1: run_cycle_hedge_monitor places a SELL hedge when bid <= hedge_trigger."""
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        atr_recovery_live_hedge_enabled=True,
        candle_limit=200,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)

    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()
    assert cycle.status == "OPEN"
    cycle.hedge_trigger = 1.2000
    db.add(cycle)
    db.commit()

    # Force bid below the trigger so the monitor places the hedge
    fake_mt5.closes = [1.1900] * 240

    result = await service.run_cycle_hedge_monitor(db)

    db.refresh(cycle)
    assert cycle.hedge_position_ticket == 9001, "Hedge ticket should be recorded"
    assert cycle.hedge_placed_at is not None, "Hedge placed-at timestamp should be set"
    assert cycle.planned_hedge_only is False, "planned_hedge_only cleared after live hedge"
    sell_orders = [o for o in fake_mt5.orders if o.get("side") == "SELL"]
    assert len(sell_orders) == 1, "Exactly one SELL order should have been placed"
    assert result["cycles_checked"] == 1
    db.close()


async def test_hedge_monitor_updates_trailing_sl_when_price_drops_below_activation() -> None:
    """Area 2: run_cycle_hedge_monitor trails the SL down as price falls."""
    db = build_session()
    closes = [1.3000 - (index * 0.0005) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        atr_recovery_live_hedge_enabled=False,
        atr_recovery_trailing_monitor_enabled=True,
        candle_limit=200,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)

    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()
    activation_price = 1.2000

    # Manually inject a placed hedge ticket so trailing logic kicks in
    cycle.hedge_position_ticket = 9001
    cycle.hedge_placed_at = datetime.now(UTC)
    cycle.planned_hedge_only = False
    cycle.trailing_activation_price = activation_price
    cycle.atr_value = 0.0100
    cycle.stop_loss = 1.2600
    db.add(cycle)
    db.commit()

    # Push bid below activation price
    fake_mt5.closes = [activation_price - 0.0100] * 240

    result = await service.run_cycle_hedge_monitor(db)

    db.refresh(cycle)
    assert len(fake_mt5.sl_modifications) == 1, "Trailing SL modify should have been called once"
    assert fake_mt5.sl_modifications[0]["ticket"] == 9001
    assert cycle.hedge_sl_last_modified is not None, "Tracked SL value should be recorded"
    processed = result["processed"]
    assert any("trailing_sl_updated" in p for p in processed), "Result should include trailing_sl_updated"
    db.close()


async def test_hedge_monitor_closes_hedge_on_reversal_confirmation() -> None:
    """Area 3: run_cycle_hedge_monitor auto-closes hedge when reversal is confirmed."""
    db = build_session()
    closes = [1.2000 + (index * 0.0010) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        atr_recovery_live_hedge_enabled=False,
        atr_recovery_auto_reversal_close=True,
        candle_limit=200,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)

    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()
    cycle.reversal_confirmation_price = 1.2500
    reversal_target = 1.2500

    # Manually inject a placed hedge ticket so reversal logic can close it
    cycle.hedge_position_ticket = 9001
    cycle.hedge_placed_at = datetime.now(UTC)
    cycle.planned_hedge_only = False
    db.add(cycle)
    db.commit()

    # Bid well above the reversal confirmation price
    fake_mt5.closes = [reversal_target + 0.0200] * 240

    result = await service.run_cycle_hedge_monitor(db)

    db.refresh(cycle)
    assert cycle.status == "CLOSED", "Cycle should be CLOSED after reversal confirmation"
    assert cycle.close_reason == "REVERSAL_CONFIRMED"
    assert cycle.closed_at is not None
    close_orders = [o for o in fake_mt5.orders if o.get("side") == "CLOSE_BY_TICKET"]
    assert len(close_orders) == 1, "close_position_by_ticket should have been called once"
    assert result["cycles_checked"] == 1
    db.close()


async def test_hedge_monitor_blocks_hedge_when_cooldown_active() -> None:
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        atr_recovery_enabled=True,
        atr_recovery_toggle_symbols="GBPUSDm",
        atr_recovery_enabled_symbols="GBPUSDm",
        atr_recovery_live_hedge_enabled=True,
        atr_recovery_hedge_cooldown_seconds=3600,
        candle_limit=200,
    )
    fake_mt5 = FakeMt5Client(closes)
    service = TradingService(settings, mt5_client=fake_mt5)
    service.strategy_engine = AlwaysBuyEngine()
    await service.run_cycle(db)

    cycle = db.query(Mt5TradeCycle).filter(Mt5TradeCycle.execution_symbol == "GBPUSDm").one()
    cycle.hedge_trigger = 1.2000
    cycle.hedge_cooldown_until = datetime.now(UTC) + timedelta(minutes=10)
    db.add(cycle)
    db.commit()

    fake_mt5.closes = [1.1900] * 240
    result = await service.run_cycle_hedge_monitor(db)
    db.refresh(cycle)

    assert cycle.hedge_position_ticket is None
    assert not [o for o in fake_mt5.orders if o.get("side") == "SELL"]
    assert result["processed"][0].get("hedge_block") == "cooldown_active"
    db.close()


async def test_news_filter_blocks_buy_execution_during_high_impact_window() -> None:
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    event_time = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        trading_symbol="GBPUSD",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        news_filter_enabled=True,
        news_filter_pre_event_minutes=30,
        news_filter_post_event_minutes=15,
        news_filter_min_impact="high",
        news_filter_events_utc=f"{event_time}|GBP|high|CPI Release",
        candle_limit=200,
    )
    service = TradingService(settings, mt5_client=FakeMt5Client(closes))
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["trade"] is None
    assert "NEWS_HIGH_IMPACT_WINDOW" in str(result.get("execution_block", ""))
    db.close()


async def test_news_filter_blocks_with_provider_events_when_inline_feed_empty() -> None:
    db = build_session()
    closes = [1.1000 + (index * 0.0008) for index in range(240)]
    settings = Settings(
        database_url="sqlite:///:memory:",
        market_data_provider="mt5",
        execution_provider="mt5",
        trading_symbol="GBPUSD",
        mt5_symbol="GBPUSDm",
        mt5_symbols="GBPUSDm",
        dry_run=True,
        news_filter_enabled=True,
        news_filter_pre_event_minutes=30,
        news_filter_post_event_minutes=15,
        news_filter_min_impact="high",
        news_filter_events_utc="",
        candle_limit=200,
    )
    provider_events = [
        {
            "timestamp": datetime.now(UTC),
            "currency": "GBP",
            "impact": "high",
            "title": "Provider CPI",
        }
    ]
    service = TradingService(
        settings,
        mt5_client=FakeMt5Client(closes),
        news_calendar_client=FakeNewsCalendarClient(provider_events),
    )
    service.strategy_engine = AlwaysBuyEngine()

    result = await service.run_cycle(db)

    assert result["trade"] is None
    assert "NEWS_HIGH_IMPACT_WINDOW" in str(result.get("execution_block", ""))
    db.close()

