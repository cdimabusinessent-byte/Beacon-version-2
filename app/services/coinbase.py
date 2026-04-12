from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.services.binance import SymbolRules


COINBASE_GRANULARITY_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}


class CoinbaseMarketDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.coinbase_base_url,
                timeout=self.settings.request_timeout_seconds,
                trust_env=self.settings.coinbase_http_trust_env,
                headers={"Accept": "application/json"},
            ) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Coinbase API returned HTTP {exc.response.status_code} for {path}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Coinbase request timed out. Try setting COINBASE_HTTP_TRUST_ENV=false in .env if your network proxy is blocking it."
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(
                f"Coinbase request failed for {path}: {detail}. Try setting COINBASE_HTTP_TRUST_ENV=false in .env."
            ) from exc

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        granularity = COINBASE_GRANULARITY_MAP.get(interval)
        if granularity is None:
            supported = ", ".join(sorted(COINBASE_GRANULARITY_MAP))
            raise ValueError(f"Unsupported Coinbase interval '{interval}'. Supported values: {supported}.")
        if limit > 300:
            raise ValueError("Coinbase public candles support a maximum of 300 data points per request.")

        payload = await self._request(
            f"/products/{symbol}/candles",
            params={"granularity": granularity},
        )
        candles = sorted(payload, key=lambda item: item[0])
        if len(candles) < limit:
            return candles
        return candles[-limit:]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        payload = await self._request(f"/products/{symbol}")
        base_increment = Decimal(payload["base_increment"])
        return SymbolRules(
            symbol=payload["id"],
            base_asset=payload["base_currency"],
            quote_asset=payload["quote_currency"],
            step_size=base_increment,
            min_qty=base_increment,
            min_notional=Decimal(payload.get("min_market_funds", "0")),
        )
