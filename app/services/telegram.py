from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send_cycle_summary(self, result: dict[str, Any]) -> None:
        if not self.settings.telegram_enabled:
            return
        if not self.settings.telegram_is_configured:
            raise RuntimeError("Telegram is enabled but no valid token/chat recipient is configured.")

        raw_copy_message = self._format_raw_copy_message(result)
        trade_signal_message = self._format_message(result)
        confidence_gate_message = self._format_confidence_gate_message(result)
        messages = [item for item in [raw_copy_message, trade_signal_message, confidence_gate_message] if item]
        if not messages:
            return

        recipients = self.settings.effective_telegram_recipients
        if not recipients:
            raise RuntimeError("Telegram is enabled but no valid token/chat recipient is configured.")

        timeout = max(5.0, float(self.settings.request_timeout_seconds))
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            for token, chat_id in recipients:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                for message in messages:
                    payload = {
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    }
                    try:
                        response = await client.post(url, json=payload)
                        response.raise_for_status()
                        data = response.json()
                        if not data.get("ok"):
                            raise RuntimeError(f"Telegram API error: {data}")
                    except Exception as exc:
                        errors.append(f"chat_id={chat_id}: {exc}")

        if errors:
            raise RuntimeError("; ".join(errors))

    def _format_price(self, value: Any) -> str:
        if value is None:
            return "--"
        try:
            return f"{float(value):.5f}"
        except (TypeError, ValueError):
            return "--"

    def _is_duplicate_reentry_block(self, detail: Any) -> bool:
        text = str(detail or "").lower()
        return "duplicate re-entry" in text

    def _separator(self) -> str:
        return "--------------------"

    def _format_raw_copy_message(self, result: dict[str, Any]) -> str:
        if result.get("mode") == "multi-symbol":
            rows = result.get("results", []) or []
            lines: list[str] = ["🧾 V2 Raw Copy (Pre-Execution Filter)"]
            for row in rows:
                raw_action = str(
                    row.get("strategy_vote_action", row.get("pre_confidence_action", row.get("action", "HOLD")))
                ).upper()
                if raw_action not in {"BUY", "SELL"}:
                    continue
                symbol = row.get("symbol", "?")
                entry_price = self._format_price(row.get("price"))
                confidence = float(row.get("confidence", 0.0)) * 100
                stop_loss = self._format_price(row.get("stop_loss"))
                take_profit = self._format_price(row.get("take_profit"))
                lines.append(
                    f"{symbol}: {raw_action} | entry {entry_price} | conf {confidence:.1f}% | SL {stop_loss} | TP {take_profit}"
                )
                lines.append(self._separator())

            if len(lines) == 1:
                return ""
            return "\n".join(lines)

        raw_action = str(
            result.get("strategy_vote_action", result.get("pre_confidence_action", result.get("action", "HOLD")))
        ).upper()
        if raw_action not in {"BUY", "SELL"}:
            return ""

        symbol = result.get("symbol", "?")
        entry_price = self._format_price(result.get("price"))
        confidence = float(result.get("confidence", 0.0)) * 100
        stop_loss = self._format_price(result.get("stop_loss"))
        take_profit = self._format_price(result.get("take_profit"))
        return (
            "🧾 V2 Raw Copy (Pre-Execution Filter)\n"
            f"{symbol}: {raw_action} | entry {entry_price} | conf {confidence:.1f}% | SL {stop_loss} | TP {take_profit}\n"
            f"{self._separator()}"
        )

    def _format_confidence_gate_message(self, result: dict[str, Any]) -> str:
        if result.get("mode") == "multi-symbol":
            rows = result.get("results", []) or []
            blocked_rows = [row for row in rows if bool(row.get("confidence_gate_blocked"))]
            if not blocked_rows:
                return ""

            threshold = float(blocked_rows[0].get("confidence_gate_threshold", 0.0)) * 100
            lines: list[str] = [f"🧠 V2 Confidence Gate Filter ({threshold:.1f}%)"]
            for row in blocked_rows:
                symbol = row.get("symbol", "?")
                action = str(row.get("pre_confidence_action", row.get("action", "?"))).upper()
                confidence = float(row.get("confidence", 0.0)) * 100
                lines.append(f"{symbol}: {action} blocked at {confidence:.1f}%")
                lines.append(self._separator())
            return "\n".join(lines)

        if not bool(result.get("confidence_gate_blocked")):
            return ""

        threshold = float(result.get("confidence_gate_threshold", 0.0)) * 100
        symbol = result.get("symbol", "?")
        action = str(result.get("pre_confidence_action", result.get("action", "?"))).upper()
        confidence = float(result.get("confidence", 0.0)) * 100
        return (
            f"🧠 V2 Confidence Gate Filter ({threshold:.1f}%)\n"
            f"{symbol}: {action} blocked at {confidence:.1f}%\n"
            f"{self._separator()}"
        )

    def _format_message(self, result: dict[str, Any]) -> str:
        if result.get("mode") == "multi-symbol":
            rows = result.get("results", []) or []

            lines: list[str] = ["📊 V2 Multi-Symbol Signal Update"]
            for row in rows:
                action = str(row.get("action", "HOLD")).upper()
                symbol = row.get("symbol", "?")
                execution_block = row.get("execution_block")

                if self._is_duplicate_reentry_block(execution_block):
                    lines.append(f"{symbol}: BLOCKED | duplicate re-entry (position already open)")
                    lines.append(self._separator())
                    continue

                if action not in {"BUY", "SELL"}:
                    continue
                entry_price = self._format_price(row.get("price"))
                confidence = float(row.get("confidence", 0.0)) * 100
                stop_loss = self._format_price(row.get("stop_loss"))
                take_profit = self._format_price(row.get("take_profit"))
                lines.append(
                    f"{symbol}: {action} | entry {entry_price} | conf {confidence:.1f}% | SL {stop_loss} | TP {take_profit}"
                )
                lines.append(self._separator())

            if len(lines) == 1:
                return ""
            return "\n".join(lines)

        action = str(result.get("action", "HOLD"))
        if self._is_duplicate_reentry_block(result.get("execution_block")):
            symbol = result.get("symbol", "?")
            return (
                f"📊 V2 Signal\n{symbol}: BLOCKED | duplicate re-entry (position already open)\n"
                f"{self._separator()}"
            )
        if not self.settings.telegram_send_hold and action == "HOLD":
            return ""
        symbol = result.get("symbol", "?")
        confidence = float(result.get("confidence", 0.0)) * 100
        regime = result.get("regime", "-")
        stop_loss = self._format_price(result.get("stop_loss"))
        take_profit = self._format_price(result.get("take_profit"))
        return (
            f"📊 V2 Signal\n"
            f"{symbol}: {action} | conf {confidence:.1f}% | regime {regime} | SL {stop_loss} | TP {take_profit}\n"
            f"{self._separator()}"
        )
