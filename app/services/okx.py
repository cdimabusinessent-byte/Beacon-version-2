from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.services.binance import SymbolRules


OKX_BAR_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}


class OkxMarketDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.okx_base_url,
                timeout=self.settings.request_timeout_seconds,
                trust_env=self.settings.okx_http_trust_env,
                headers={"Accept": "application/json"},
            ) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OKX API returned HTTP {exc.response.status_code} for {path}.") from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "OKX request timed out. Try setting OKX_HTTP_TRUST_ENV=false in .env."
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(f"OKX request failed for {path}: {detail}. Try setting OKX_HTTP_TRUST_ENV=false in .env.") from exc

        if payload.get("code") not in (None, "0", 0):
            raise RuntimeError(f"OKX API error for {path}: {payload.get('msg', 'unknown error')}")
        return payload

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        bar = OKX_BAR_MAP.get(interval)
        if bar is None:
            supported = ", ".join(sorted(OKX_BAR_MAP))
            raise ValueError(f"Unsupported OKX interval '{interval}'. Supported values: {supported}.")

        payload = await self._request(
            "/api/v5/market/history-candles",
            params={"instId": symbol, "bar": bar, "limit": min(limit, 100)},
        )
        candles = []
        for item in reversed(payload.get("data", [])):
            candles.append([item[0], item[1], item[2], item[3], item[4], item[5]])
        if len(candles) < limit:
            return candles
        return candles[-limit:]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        payload = await self._request(
            "/api/v5/public/instruments",
            params={"instType": "SPOT", "instId": symbol},
        )
        instrument = payload["data"][0]
        return SymbolRules(
            symbol=instrument["instId"],
            base_asset=instrument["baseCcy"],
            quote_asset=instrument["quoteCcy"],
            step_size=Decimal(instrument.get("lotSz", "0.00000001")),
            min_qty=Decimal(instrument.get("minSz", instrument.get("lotSz", "0.00000001"))),
            min_notional=Decimal("0"),
        )
