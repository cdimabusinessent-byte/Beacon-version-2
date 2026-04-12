from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings


@dataclass(slots=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


class BinanceClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        params = params.copy() if params else {}
        headers: dict[str, str] = {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            query_string = urlencode(params, doseq=True)
            signature = hmac.new(
                self.settings.binance_api_secret.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.settings.binance_api_key

        try:
            async with httpx.AsyncClient(
                base_url=self.settings.binance_base_url,
                timeout=self.settings.request_timeout_seconds,
                trust_env=self.settings.binance_http_trust_env,
            ) as client:
                response = await client.request(method=method, url=path, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Binance API returned HTTP {exc.response.status_code} for {path}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Binance request timed out. Try setting BINANCE_HTTP_TRUST_ENV=false in .env if your network proxy is blocking it."
            ) from exc
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise RuntimeError(
                f"Binance request failed for {path}: {detail}. Try setting BINANCE_HTTP_TRUST_ENV=false in .env."
            ) from exc

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        return await self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    async def get_ticker_price(self, symbol: str) -> float:
        payload = await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return float(payload["price"])

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        payload = await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        symbol_info = payload["symbols"][0]
        filters = {item["filterType"]: item for item in symbol_info["filters"]}

        lot_filter = filters["LOT_SIZE"]
        notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL", {})

        return SymbolRules(
            symbol=symbol_info["symbol"],
            base_asset=symbol_info["baseAsset"],
            quote_asset=symbol_info["quoteAsset"],
            step_size=Decimal(lot_filter["stepSize"]),
            min_qty=Decimal(lot_filter["minQty"]),
            min_notional=Decimal(notional_filter.get("minNotional", "0")),
        )

    async def get_account_info(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/account", signed=True)

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        payload = await self._request("GET", "/api/v3/openOrders", params=params, signed=True)
        return list(payload)

    async def get_recent_fills(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v3/myTrades",
            params={"symbol": symbol, "limit": max(1, min(limit, 1000))},
            signed=True,
        )
        return list(payload)

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        payload = await self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        bid = float(payload.get("bidPrice") or 0.0)
        ask = float(payload.get("askPrice") or 0.0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        spread = max(0.0, ask - bid)
        spread_pct = (spread / mid * 100.0) if mid > 0 else 0.0
        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
        }

    async def place_market_buy(
        self,
        symbol: str,
        quote_order_qty: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": decimal_to_string(quote_order_qty),
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return await self._request("POST", "/api/v3/order", params=params, signed=True)

    async def place_market_sell(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": decimal_to_string(quantity),
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return await self._request("POST", "/api/v3/order", params=params, signed=True)

    async def place_stop_loss_limit_sell(
        self,
        symbol: str,
        quantity: Decimal,
        stop_price: Decimal,
        limit_price: Decimal,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "quantity": decimal_to_string(quantity),
            "stopPrice": decimal_to_string(stop_price),
            "price": decimal_to_string(limit_price),
            "timeInForce": "GTC",
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return await self._request("POST", "/api/v3/order", params=params, signed=True)


def round_step_size(quantity: Decimal, step_size: Decimal) -> Decimal:
    if step_size <= 0:
        return quantity
    return (quantity // step_size) * step_size


def decimal_to_string(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN).normalize()
    return format(normalized, "f").rstrip("0").rstrip(".") or "0"
