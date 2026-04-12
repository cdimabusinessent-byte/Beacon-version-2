from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.pro_analysis import ProfessionalAnalysisService


class FakeMt5Client:
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float]]:
        base_price = {
            "1m": 1.1000,
            "5m": 1.1010,
            "15m": 1.1025,
            "1h": 1.1050,
            "4h": 1.1100,
            "1d": 1.1150,
        }[interval]
        rows: list[list[float]] = []
        for index in range(limit):
            close = base_price + (index * 0.0001)
            rows.append([index, close - 0.0002, close + 0.0004, close - 0.0004, close, 1000 + index])
        return rows

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        return {
            "bid": 1.1298,
            "ask": 1.1300,
            "point": 0.0001,
            "digits": 5.0,
            "spread_points": 2.0,
            "spread_pips": 0.2,
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

    async def get_account_info(self) -> dict[str, float]:
        return {"equity": 12500.0, "balance": 12000.0}


class FakeStrategyEngine:
    def evaluate(self, series: dict[str, list[float]], timeframe: str):
        action_map = {
            "1m": "HOLD",
            "5m": "BUY",
            "15m": "BUY",
            "1h": "BUY",
            "4h": "BUY",
            "1d": "BUY",
        }
        confidence_map = {
            "1m": 0.40,
            "5m": 0.62,
            "15m": 0.71,
            "1h": 0.79,
            "4h": 0.82,
            "1d": 0.77,
        }
        return SimpleNamespace(
            action=action_map[timeframe],
            confidence=confidence_map[timeframe],
            stop_loss=series["closes"][-1] * 0.995,
            take_profit=series["closes"][-1] * 1.015,
            regime="trend",
            selected_strategies=[],
            all_strategies=[],
        )


@pytest.mark.asyncio
async def test_generate_report_returns_structured_sections() -> None:
    settings = Settings(
        mt5_symbol="GBPUSD+",
        mt5_symbols="EURUSD+,GBPUSD+,XAUUSD+",
        enabled_strategies="trend-following,breakout,scalping,mean-reversion,momentum,smart-money-concepts,grid,news-trading,ai-pattern-recognition,multi-timeframe-confluence,pivot-breakout",
    )
    service = ProfessionalAnalysisService(settings, FakeMt5Client(), FakeStrategyEngine())

    payload = await service.generate_report(
        symbols=["GBPUSD+"],
        account_size=20000,
        risk_tolerance="MEDIUM",
        trading_style="DAY TRADING",
    )

    assert payload["symbols"] == ["GBPUSD+"]
    assert payload["account_size"] == 20000.0
    assert len(payload["reports"]) == 1
    report = payload["reports"][0]
    assert report["market_overview"]["headline"].startswith("GBPUSD+")
    assert report["trade_ideas"][0]["risk_to_reward_tp2"] >= 2.0
    assert report["strategy_logic"]["configured_strategies"]
    assert "Market Overview" in report["formatted_report"]
    assert "Trade Ideas" in report["formatted_report"]
    assert "Strategy Logic" in report["formatted_report"]
    assert "Bot Code / Pseudocode" in report["formatted_report"]
    assert "Risk Notes" in report["formatted_report"]
    assert "Deployment Guide" in report["formatted_report"]


@pytest.mark.asyncio
async def test_generate_report_rejects_symbol_outside_existing_mt5_list() -> None:
    settings = Settings(
        mt5_symbol="GBPUSD+",
        mt5_symbols="EURUSD+,GBPUSD+,XAUUSD+",
    )
    service = ProfessionalAnalysisService(settings, FakeMt5Client(), FakeStrategyEngine())

    with pytest.raises(ValueError):
        await service.generate_report(symbols=["USDNOK+"], account_size=10000)