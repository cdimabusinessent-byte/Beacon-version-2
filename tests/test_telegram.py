from app.config import Settings
from app.services.telegram import TelegramNotifier


def test_multi_symbol_message_only_shows_buy_and_sell_with_sl_tp() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "mode": "multi-symbol",
        "results": [
            {
                "symbol": "EURUSD",
                "action": "BUY",
                "price": 1.15,
                "confidence": 0.81,
                "regime": "trend",
                "stop_loss": 1.1,
                "take_profit": 1.2,
            },
            {
                "symbol": "GBPUSD",
                "action": "SELL",
                "price": 1.32,
                "confidence": 0.73,
                "regime": "mean-reversion",
                "stop_loss": 1.35,
                "take_profit": 1.3,
            },
            {
                "symbol": "USDJPY",
                "action": "HOLD",
                "price": 151.5,
                "confidence": 0.66,
                "regime": "range",
                "stop_loss": 151.1,
                "take_profit": 152.2,
            },
        ],
        "errors": {"AUDUSD": "data timeout"},
    }

    message = notifier._format_message(result)

    assert "V2 Multi-Symbol Signal Update" in message
    assert "EURUSD: BUY | entry 1.15000 | conf 81.0% | SL 1.10000 | TP 1.20000" in message
    assert "EURUSD: BUY | entry 1.15000 | conf 81.0% | SL 1.10000 | TP 1.20000\n--------------------" in message
    assert "GBPUSD: SELL | entry 1.32000 | conf 73.0% | SL 1.35000 | TP 1.30000" in message
    assert "GBPUSD: SELL | entry 1.32000 | conf 73.0% | SL 1.35000 | TP 1.30000\n--------------------" in message
    assert "USDJPY" not in message
    assert "HOLD" not in message
    assert "ERROR" not in message


def test_multi_symbol_message_is_empty_when_only_hold_actions() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "mode": "multi-symbol",
        "results": [
            {
                "symbol": "USDJPY",
                "action": "HOLD",
                "stop_loss": 151.1,
                "take_profit": 152.2,
            }
        ],
    }

    assert notifier._format_message(result) == ""


def test_multi_symbol_message_includes_duplicate_reentry_block_warning() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "mode": "multi-symbol",
        "results": [
            {
                "symbol": "EURUSD",
                "action": "BUY",
                "execution_block": "Execution block: active broker position already open; strict idempotency policy prevents duplicate re-entry.",
            }
        ],
    }

    message = notifier._format_message(result)

    assert "V2 Multi-Symbol Signal Update" in message
    assert "EURUSD: BLOCKED | duplicate re-entry (position already open)" in message
    assert "EURUSD: BLOCKED | duplicate re-entry (position already open)\n--------------------" in message


def test_single_signal_message_includes_duplicate_reentry_block_warning() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "symbol": "GBPUSD",
        "action": "BUY",
        "execution_block": "Execution block: active broker position already open; strict idempotency policy prevents duplicate re-entry.",
    }

    message = notifier._format_message(result)

    assert message == (
        "📊 V2 Signal\n"
        "GBPUSD: BLOCKED | duplicate re-entry (position already open)\n"
        "--------------------"
    )


def test_settings_parses_multiple_telegram_targets() -> None:
    settings = Settings(
        telegram_enabled=True,
        telegram_targets="1111111111:token-a:1001,2222222222:token-b|1002",
        telegram_bot_token="3333333333:token-c",
        telegram_chat_id="1003",
    )

    assert settings.effective_telegram_recipients == [
        ("1111111111:token-a", "1001"),
        ("2222222222:token-b", "1002"),
        ("3333333333:token-c", "1003"),
    ]


def test_telegram_is_configured_with_targets_only() -> None:
    settings = Settings(
        telegram_enabled=True,
        telegram_targets="1111111111:token-a:1001",
        telegram_bot_token="",
        telegram_chat_id="",
    )

    assert settings.telegram_is_configured is True


def test_confidence_gate_message_for_multi_symbol_is_separate() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "mode": "multi-symbol",
        "results": [
            {
                "symbol": "EURUSD",
                "action": "HOLD",
                "pre_confidence_action": "BUY",
                "confidence": 0.64,
                "confidence_gate_blocked": True,
                "confidence_gate_threshold": 0.70,
            },
            {
                "symbol": "GBPUSD",
                "action": "BUY",
                "confidence": 0.81,
                "confidence_gate_blocked": False,
                "confidence_gate_threshold": 0.70,
            },
        ],
    }

    message = notifier._format_confidence_gate_message(result)

    assert "V2 Confidence Gate Filter (70.0%)" in message
    assert "EURUSD: BUY blocked at 64.0%" in message
    assert "EURUSD: BUY blocked at 64.0%\n--------------------" in message
    assert "GBPUSD" not in message


def test_confidence_gate_message_for_single_signal_is_separate() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "symbol": "USDJPY",
        "action": "HOLD",
        "pre_confidence_action": "SELL",
        "confidence": 0.66,
        "confidence_gate_blocked": True,
        "confidence_gate_threshold": 0.70,
    }

    message = notifier._format_confidence_gate_message(result)

    assert message == (
        "🧠 V2 Confidence Gate Filter (70.0%)\n"
        "USDJPY: SELL blocked at 66.0%\n"
        "--------------------"
    )


def test_raw_copy_message_for_multi_symbol_includes_pre_confidence_actions() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "mode": "multi-symbol",
        "results": [
            {
                "symbol": "EURUSD",
                "action": "HOLD",
                "strategy_vote_action": "BUY",
                "pre_confidence_action": "BUY",
                "price": 1.10123,
                "confidence": 0.65,
                "stop_loss": 1.095,
                "take_profit": 1.112,
                "confidence_gate_blocked": True,
            },
            {
                "symbol": "GBPUSD",
                "action": "BUY",
                "strategy_vote_action": "BUY",
                "pre_confidence_action": "BUY",
                "price": 1.27654,
                "confidence": 0.84,
                "stop_loss": 1.268,
                "take_profit": 1.291,
                "execution_block": "Execution block: active broker position already open; strict idempotency policy prevents duplicate re-entry.",
            },
            {
                "symbol": "USDJPY",
                "action": "HOLD",
                "pre_confidence_action": "HOLD",
                "price": 151.22,
                "confidence": 0.43,
                "stop_loss": 150.9,
                "take_profit": 151.8,
            },
        ],
    }

    message = notifier._format_raw_copy_message(result)

    assert "V2 Raw Copy (Pre-Execution Filter)" in message
    assert "EURUSD: BUY | entry 1.10123 | conf 65.0% | SL 1.09500 | TP 1.11200" in message
    assert "EURUSD: BUY | entry 1.10123 | conf 65.0% | SL 1.09500 | TP 1.11200\n--------------------" in message
    assert "GBPUSD: BUY | entry 1.27654 | conf 84.0% | SL 1.26800 | TP 1.29100" in message
    assert "GBPUSD: BUY | entry 1.27654 | conf 84.0% | SL 1.26800 | TP 1.29100\n--------------------" in message
    assert "USDJPY" not in message


def test_raw_copy_message_for_single_signal_uses_pre_confidence_action() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "symbol": "AUDUSD",
        "action": "HOLD",
        "strategy_vote_action": "SELL",
        "pre_confidence_action": "SELL",
        "price": 0.65789,
        "confidence": 0.61,
        "stop_loss": 0.662,
        "take_profit": 0.649,
        "confidence_gate_blocked": True,
    }

    message = notifier._format_raw_copy_message(result)

    assert message == (
        "🧾 V2 Raw Copy (Pre-Execution Filter)\n"
        "AUDUSD: SELL | entry 0.65789 | conf 61.0% | SL 0.66200 | TP 0.64900\n"
        "--------------------"
    )


def test_raw_copy_message_prefers_strategy_vote_action_over_later_filters() -> None:
    notifier = TelegramNotifier(
        Settings(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
    )

    result = {
        "symbol": "XAUUSD",
        "action": "HOLD",
        "strategy_vote_action": "BUY",
        "pre_confidence_action": "HOLD",
        "price": 3150.12,
        "confidence": 0.77,
        "stop_loss": 3135.0,
        "take_profit": 3188.0,
    }

    message = notifier._format_raw_copy_message(result)

    assert message == (
        "🧾 V2 Raw Copy (Pre-Execution Filter)\n"
        "XAUUSD: BUY | entry 3150.12000 | conf 77.0% | SL 3135.00000 | TP 3188.00000\n"
        "--------------------"
    )
