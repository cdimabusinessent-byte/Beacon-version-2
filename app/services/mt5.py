from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.services.binance import SymbolRules

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None


MT5_TIMEFRAME_MAP = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}


class Mt5TradingClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def operation_timeout_seconds(self) -> float:
        return max(1.0, float(getattr(self.settings, "mt5_operation_timeout_seconds", 5.0) or 5.0))

    def _build_order_comment(self, client_order_id: str | None = None) -> str:
        # Keep the MT5 comment minimal for compatibility across broker bridges.
        return "v2"

    def _ensure_library(self) -> Any:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 Python package is not installed.")
        return mt5

    def _connect(self) -> Any:
        mt5_lib = self._ensure_library()
        initialized = mt5_lib.initialize(
            path=self.settings.mt5_terminal_path,
            login=self.settings.mt5_login or None,
            password=self.settings.mt5_password or None,
            server=self.settings.mt5_server or None,
        )
        if not initialized:
            code, message = mt5_lib.last_error()
            raise RuntimeError(f"MT5 initialize failed ({code}): {message}")
        return mt5_lib

    def _shutdown(self, mt5_lib: Any) -> None:
        mt5_lib.shutdown()

    def _execute_with_connection(self, operation: Any) -> Any:
        mt5_lib = self._connect()
        try:
            return operation(mt5_lib)
        finally:
            self._shutdown(mt5_lib)

    async def _run_operation(self, label: str, operation: Any) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_with_connection, operation),
                timeout=self.operation_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            timeout = self.operation_timeout_seconds
            raise RuntimeError(
                f"MT5 {label} timed out after {timeout:.1f}s. Check terminal connectivity, login, and broker server."
            ) from exc

    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[Any]]:
        def operation(mt5_lib: Any) -> list[list[Any]]:
            selected = mt5_lib.symbol_select(symbol, True)
            if not selected:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol selection failed for {symbol} ({code}): {message}")
            timeframe_name = MT5_TIMEFRAME_MAP.get(interval)
            if timeframe_name is None:
                supported = ", ".join(sorted(MT5_TIMEFRAME_MAP))
                raise ValueError(f"Unsupported MT5 interval '{interval}'. Supported values: {supported}.")
            timeframe = getattr(mt5_lib, timeframe_name)
            rates = mt5_lib.copy_rates_from_pos(symbol, timeframe, 0, limit)
            if rates is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 rates request failed ({code}): {message}")
            return [[row["time"], row["open"], row["high"], row["low"], row["close"], row["tick_volume"]] for row in rates]
        return await self._run_operation("rates request", operation)

    async def get_exchange_info(self, symbol: str) -> SymbolRules:
        def operation(mt5_lib: Any) -> SymbolRules:
            selected = mt5_lib.symbol_select(symbol, True)
            if not selected:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol selection failed for {symbol} ({code}): {message}")
            info = mt5_lib.symbol_info(symbol)
            if info is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol info failed ({code}): {message}")
            volume_step = Decimal(str(info.volume_step or 0.01))
            volume_min = Decimal(str(info.volume_min or volume_step))
            contract_size = Decimal(str(info.trade_contract_size or 1))
            return SymbolRules(
                symbol=symbol,
                base_asset=symbol,
                quote_asset="USD",
                step_size=volume_step,
                min_qty=volume_min,
                min_notional=contract_size * volume_min,
            )
        return await self._run_operation("exchange info lookup", operation)

    async def get_symbol_specifications(self, symbol: str) -> dict[str, float | int | str]:
        def operation(mt5_lib: Any) -> dict[str, float | int | str]:
            selected = mt5_lib.symbol_select(symbol, True)
            if not selected:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol selection failed for {symbol} ({code}): {message}")
            info = mt5_lib.symbol_info(symbol)
            if info is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol info failed for {symbol} ({code}): {message}")

            return {
                "symbol": symbol,
                "point": float(getattr(info, "point", 0.0) or 0.0),
                "digits": int(getattr(info, "digits", 0) or 0),
                "volume_min": float(getattr(info, "volume_min", 0.0) or 0.0),
                "volume_max": float(getattr(info, "volume_max", 0.0) or 0.0),
                "volume_step": float(getattr(info, "volume_step", 0.0) or 0.0),
                "trade_contract_size": float(getattr(info, "trade_contract_size", 0.0) or 0.0),
                "trade_tick_size": float(getattr(info, "trade_tick_size", 0.0) or 0.0),
                "trade_tick_value": float(getattr(info, "trade_tick_value", 0.0) or 0.0),
                "trade_tick_value_profit": float(getattr(info, "trade_tick_value_profit", 0.0) or 0.0),
                "trade_tick_value_loss": float(getattr(info, "trade_tick_value_loss", 0.0) or 0.0),
                "currency_profit": str(getattr(info, "currency_profit", "") or ""),
            }
        return await self._run_operation("symbol specification lookup", operation)

    async def get_account_info(self) -> dict[str, Any]:
        def operation(mt5_lib: Any) -> dict[str, Any]:
            info = mt5_lib.account_info()
            if info is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 account info failed ({code}): {message}")
            return info._asdict()
        return await self._run_operation("account info lookup", operation)

    async def get_open_position_volume(self, symbol: str) -> float:
        def operation(mt5_lib: Any) -> float:
            mt5_lib.symbol_select(symbol, True)
            positions = mt5_lib.positions_get(symbol=symbol)
            if positions is None:
                return 0.0
            return float(sum(position.volume for position in positions))
        return await self._run_operation("open position lookup", operation)

    async def get_active_positions_count(self) -> int:
        def operation(mt5_lib: Any) -> int:
            positions = mt5_lib.positions_get()
            if positions is None:
                return 0
            return int(len(positions))
        return await self._run_operation("active position count lookup", operation)

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        def operation(mt5_lib: Any) -> list[dict[str, Any]]:
            if symbol:
                mt5_lib.symbol_select(symbol, True)
                orders = mt5_lib.orders_get(symbol=symbol)
            else:
                orders = mt5_lib.orders_get()
            if orders is None:
                return []
            return [order._asdict() for order in orders]
        return await self._run_operation("open orders lookup", operation)

    async def get_recent_deals(self, lookback_hours: int = 24) -> list[dict[str, Any]]:
        def operation(mt5_lib: Any) -> list[dict[str, Any]]:
            to_time = datetime.now(UTC)
            from_time = to_time - timedelta(hours=max(1, lookback_hours))
            deals = mt5_lib.history_deals_get(from_time, to_time)
            if deals is None:
                return []
            return [deal._asdict() for deal in deals]
        return await self._run_operation("deal history lookup", operation)

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        def operation(mt5_lib: Any) -> dict[str, float]:
            mt5_lib.symbol_select(symbol, True)
            info = mt5_lib.symbol_info(symbol)
            tick = mt5_lib.symbol_info_tick(symbol)
            if info is None or tick is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 market state failed ({code}): {message}")

            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            point = float(getattr(info, "point", 0.00001) or 0.00001)
            digits = int(getattr(info, "digits", 5) or 5)
            spread_points = max(0.0, (ask - bid) / max(point, 1e-12))
            pip_factor = 10.0 if digits in {3, 5} else 1.0
            spread_pips = spread_points / pip_factor

            return {
                "bid": bid,
                "ask": ask,
                "point": point,
                "digits": float(digits),
                "spread_points": spread_points,
                "spread_pips": spread_pips,
            }
        return await self._run_operation("market state lookup", operation)

    async def check_auto_execution_ready(self, symbol: str) -> dict[str, Any]:
        def operation(mt5_lib: Any) -> dict[str, Any]:
            selected = mt5_lib.symbol_select(symbol, True)
            if not selected:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol selection failed ({code}): {message}")

            account = mt5_lib.account_info()
            if account is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 account info failed ({code}): {message}")

            symbol_info = mt5_lib.symbol_info(symbol)
            tick = mt5_lib.symbol_info_tick(symbol)
            if symbol_info is None or tick is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 symbol readiness failed ({code}): {message}")

            volume = float(getattr(symbol_info, "volume_min", 0.01) or 0.01)
            price = float(getattr(tick, "ask", 0.0) or 0.0)
            if price <= 0:
                raise RuntimeError("MT5 symbol tick has no valid ask price.")

            request = {
                "action": mt5_lib.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5_lib.ORDER_TYPE_BUY,
                "price": price,
                "deviation": self.settings.mt5_deviation,
                "magic": self.settings.mt5_magic,
                "type_time": mt5_lib.ORDER_TIME_GTC,
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
                "comment": "readiness-check",
            }

            if not hasattr(mt5_lib, "order_check"):
                return {
                    "ready": True,
                    "symbol": symbol,
                    "reason": "order_check_unavailable",
                }

            check_result = mt5_lib.order_check(request)
            if check_result is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 order_check failed ({code}): {message}")

            check_dict = check_result._asdict()
            retcode = int(check_dict.get("retcode") or 0)
            success_codes = {
                0,
                int(getattr(mt5_lib, "TRADE_RETCODE_DONE", 10009)),
                int(getattr(mt5_lib, "TRADE_RETCODE_PLACED", 10008)),
                int(getattr(mt5_lib, "TRADE_RETCODE_DONE_PARTIAL", 10010)),
            }
            return {
                "ready": retcode in success_codes,
                "symbol": symbol,
                "retcode": retcode,
                "comment": check_dict.get("comment", ""),
            }
        return await self._run_operation("readiness check", operation)

    async def place_market_buy(
        self,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._place_market_order(
            symbol=symbol,
            volume=volume,
            order_type="BUY",
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
        )

    async def place_market_sell(
        self,
        symbol: str,
        volume: Decimal,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._place_market_order(
            symbol=symbol,
            volume=volume,
            order_type="SELL",
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
        )

    async def modify_position_sl(self, ticket: int, symbol: str, new_sl: float) -> dict[str, Any]:
        """Update the stop-loss on an open MT5 position using TRADE_ACTION_SLTP.

        Preserves the existing take-profit value.  Safe to call repeatedly as
        price moves; the broker will reject a no-change request gracefully.
        """

        def operation(mt5_lib: Any) -> dict[str, Any]:
            mt5_lib.symbol_select(symbol, True)
            positions = mt5_lib.positions_get(symbol=symbol)
            if positions is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 positions_get failed for {symbol} ({code}): {message}")
            pos = next((p for p in positions if p.ticket == ticket), None)
            if pos is None:
                raise RuntimeError(f"MT5 position ticket {ticket} not found for {symbol}.")
            request = {
                "action": mt5_lib.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": symbol,
                "sl": float(new_sl),
                "tp": float(getattr(pos, "tp", 0.0) or 0.0),
            }
            result = mt5_lib.order_send(request)
            if result is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 modify SL failed ({code}): {message}")
            result_dict = result._asdict()
            if result_dict.get("retcode") != mt5_lib.TRADE_RETCODE_DONE:
                raise RuntimeError(f"MT5 modify SL rejected: {result_dict.get('comment', 'unknown error')}")
            return result_dict

        return await self._run_operation("modify position SL", operation)

    async def close_position_by_ticket(self, ticket: int, symbol: str, volume: float) -> dict[str, Any]:
        """Close an existing MT5 position by ticket using an opposite-direction market order.

        The position type (BUY=0 / SELL=1) is read from the broker to determine
        the correct close direction automatically.
        """

        def operation(mt5_lib: Any) -> dict[str, Any]:
            mt5_lib.symbol_select(symbol, True)
            tick = mt5_lib.symbol_info_tick(symbol)
            if tick is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 tick request failed for {symbol} ({code}): {message}")
            positions = mt5_lib.positions_get(symbol=symbol)
            if positions is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 positions_get failed for {symbol} ({code}): {message}")
            pos = next((p for p in positions if p.ticket == ticket), None)
            if pos is None:
                raise RuntimeError(f"MT5 position ticket {ticket} not found for {symbol}.")
            # POSITION_TYPE_BUY = 0, POSITION_TYPE_SELL = 1
            close_order_type = mt5_lib.ORDER_TYPE_SELL if pos.type == 0 else mt5_lib.ORDER_TYPE_BUY
            price = float(tick.bid) if close_order_type == mt5_lib.ORDER_TYPE_SELL else float(tick.ask)
            request = {
                "action": mt5_lib.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": close_order_type,
                "price": price,
                "position": ticket,
                "deviation": self.settings.mt5_deviation,
                "magic": self.settings.mt5_magic,
                "comment": self._build_order_comment(),
                "type_time": mt5_lib.ORDER_TIME_GTC,
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
            }
            result = mt5_lib.order_send(request)
            if result is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 close position failed ({code}): {message}")
            result_dict = result._asdict()
            if result_dict.get("retcode") != mt5_lib.TRADE_RETCODE_DONE:
                raise RuntimeError(f"MT5 close position rejected: {result_dict.get('comment', 'unknown error')}")
            return result_dict

        return await self._run_operation("close position by ticket", operation)

    async def _place_market_order(
        self,
        symbol: str,
        volume: Decimal,
        order_type: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        def operation(mt5_lib: Any) -> dict[str, Any]:
            mt5_lib.symbol_select(symbol, True)
            tick = mt5_lib.symbol_info_tick(symbol)
            if tick is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 tick request failed ({code}): {message}")

            type_map = {
                "BUY": mt5_lib.ORDER_TYPE_BUY,
                "SELL": mt5_lib.ORDER_TYPE_SELL,
            }
            price = tick.ask if order_type == "BUY" else tick.bid
            request = {
                "action": mt5_lib.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": type_map[order_type],
                "price": price,
                "deviation": self.settings.mt5_deviation,
                "magic": self.settings.mt5_magic,
                "comment": self._build_order_comment(client_order_id),
                "type_time": mt5_lib.ORDER_TIME_GTC,
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
            }
            if stop_loss is not None:
                request["sl"] = float(stop_loss)
            if take_profit is not None:
                request["tp"] = float(take_profit)
            result = mt5_lib.order_send(request)
            if result is None:
                code, message = mt5_lib.last_error()
                raise RuntimeError(f"MT5 order_send failed ({code}): {message}")
            result_dict = result._asdict()
            if result_dict.get("retcode") != mt5_lib.TRADE_RETCODE_DONE:
                raise RuntimeError(f"MT5 order rejected: {result_dict.get('comment', 'unknown error')}")
            return result_dict
        return await self._run_operation(f"{order_type.lower()} order", operation)
