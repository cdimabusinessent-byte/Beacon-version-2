from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.services.binance import SymbolRules


KRAKEN_INTERVAL_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


class KrakenMarketDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.kraken_base_url,
                timeout=self.settings.request_timeout_seconds,
                trust_env=self.settings.kraken_http_trust_env,
            ) as client:
                response = await client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Kraken API returned HTTP {exc.response.status_code} for {path}.") from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Kraken request timed out. Try setting KRAKEN_HTTP_TRUST_ENV=false in .env."
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(f"Kraken request failed for {path}: {detail}. Try setting KRAKEN_HTTP_TRUST_ENV=false in .env.") from exc

        errors = payload.get("error", [])
        if errors:
            raise RuntimeError(f"Kraken API error for {path}: {', '.join(errors)}")
        return payload

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        kraken_interval = KRAKEN_INTERVAL_MAP.get(interval)
        if kraken_interval is None:
            supported = ", ".join(sorted(KRAKEN_INTERVAL_MAP))
            raise ValueError(f"Unsupported Kraken interval '{interval}'. Supported values: {supported}.")

        payload = await self._request("/0/public/OHLC", params={"pair": symbol, "interval": kraken_interval})
        result = payload["result"]
        pair_key = next(key for key in result if key != "last")
        candles = []
        for item in result[pair_key]:
            candles.append([item[0], item[1], item[2], item[3], item[4], item[6]])
        if len(candles) < limit:
            return candles
        return candles[-limit:]

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        payload = await self._request("/0/public/AssetPairs", params={"pair": symbol})
        pair_key, pair_payload = next(iter(payload["result"].items()))
        lot_decimals = int(pair_payload.get("lot_decimals", 8))
        step = Decimal("1").scaleb(-lot_decimals)
        min_qty = Decimal(pair_payload.get("ordermin", str(step)))
        return SymbolRules(
            symbol=pair_key,
            base_asset=pair_payload.get("base", symbol[:-4]),
            quote_asset=pair_payload.get("quote", symbol[-4:]),
            step_size=step,
            min_qty=min_qty,
            min_notional=Decimal("0"),
        )
