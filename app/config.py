import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"


def resolve_env_file_path() -> str:
    candidate = os.getenv("BEACON_ENV_FILE", "").strip()
    if not candidate:
        return str(ENV_FILE_PATH)

    path = Path(candidate)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


class Settings(BaseSettings):
    app_name: str = "Beacon"
    environment: str = "development"
    database_url: str = "sqlite:///./beacon.db"
    database_run_migrations_on_startup: bool = True
    control_api_key: str = ""
    control_allowed_ips: str = "127.0.0.1,::1,testclient"
    trusted_hosts: str = "127.0.0.1,localhost,testserver,testclient"
    cors_allowed_origins: str = ""
    cors_allowed_origin_regex: str = ""
    cors_allow_credentials: bool = True
    https_redirect_enabled: bool = False
    gzip_minimum_size: int = 500
    live_trading_armed: bool = False
    allowed_live_environments: str = "production,live,production-live"
    startup_self_check_required: bool = True

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_base_url: str = "https://api.binance.com"
    binance_http_trust_env: bool = True
    coinbase_base_url: str = "https://api.exchange.coinbase.com"
    coinbase_http_trust_env: bool = True
    kraken_base_url: str = "https://api.kraken.com"
    kraken_http_trust_env: bool = True
    okx_base_url: str = "https://www.okx.com"
    okx_http_trust_env: bool = True
    market_data_provider: str = "auto"
    market_data_symbol: str = ""
    execution_provider: str = "binance"

    trading_symbol: str = "BTCUSDT"
    candle_interval: str = "1h"
    strategy_candle_mode: str = ""
    candle_limit: int = 150
    rsi_period: int = 14
    rsi_buy_threshold: float = 30.0
    rsi_sell_threshold: float = 70.0
    trade_amount_usdt: float = 50.0
    dry_run: bool = True

    auto_trading_enabled: bool = False
    poll_interval_seconds: int = 300
    reconciliation_enabled: bool = False
    reconciliation_interval_seconds: int = 120
    stale_market_data_seconds: int = 900

    request_timeout_seconds: float = 10.0
    fee_rate: float = 0.001
    news_sentiment_bias: str = "0"
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_targets: str = ""
    telegram_send_hold: bool = True
    risk_max_spread_pips: float = 0.0
    risk_daily_loss_limit_pct: float = 0.0
    risk_daily_loss_kill_switch_pct: float = 0.0
    risk_mt5_max_active_positions: int = 5
    strict_no_reentry_enabled: bool = True
    strict_candle_close_enabled: bool = False
    risk_min_seconds_between_trades: int = 0
    risk_max_quote_exposure_pct: float = 0.0
    risk_max_loss_per_trade_pct: float = 0.0
    risk_max_position_size_quote: float = 0.0
    risk_max_portfolio_exposure_quote: float = 0.0
    risk_max_concurrent_positions: int = 0
    risk_max_spread_pct: float = 0.0
    risk_max_slippage_pct: float = 0.0
    backtest_spread_bps: float = 5.0
    backtest_slippage_bps: float = 3.0
    backtest_latency_bars: int = 1
    backtest_partial_fill_min_pct: float = 60.0
    backtest_train_pct: float = 60.0
    backtest_validation_pct: float = 20.0
    backtest_walk_forward_steps: int = 4
    backtest_monte_carlo_paths: int = 200
    ml_enabled: bool = False
    ml_override_strategy: bool = False
    ml_auto_retrain_enabled: bool = False
    ml_retrain_interval_seconds: int = 3600
    ml_training_trade_limit: int = 2000
    ml_min_training_samples: int = 40
    ml_training_epochs: int = 250
    ml_learning_rate: float = 0.15
    ml_buy_probability_threshold: float = 0.58
    ml_sell_probability_threshold: float = 0.42
    ml_confirmation_threshold: float = 0.55
    strategy_min_confidence_threshold: float = 0.5
    strategy_side_confidence_thresholds_enabled: bool = False
    strategy_min_confidence_threshold_buy: float = 0.6
    strategy_min_confidence_threshold_sell: float = 0.3
    ml_model_path: str = "./artifacts/ml_model.json"
    risk_correlation_cap: float = 0.0
    risk_portfolio_var_limit_pct: float = 0.0
    risk_portfolio_es_limit_pct: float = 0.0
    risk_var_confidence: float = 0.95
    risk_var_lookback_candles: int = 120
    risk_volatility_target_pct: float = 0.0
    risk_vol_lookback_candles: int = 30
    enabled_strategies: str = (
        "trend-following,breakout,scalping,mean-reversion,momentum,"
        "smart-money-concepts,grid,sentiment-bias,pattern-heuristic,multi-timeframe-confluence"
    )
    mt5_terminal_path: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    mt5_profile_encryption_key: str = ""
    mt5_runtime_owner_id: str = "local"
    mt5_execution_mode: str = "direct"
    mt5_worker_poll_seconds: int = 5
    mt5_worker_claim_timeout_seconds: int = 120
    mt5_worker_key: str = "local-worker"
    mt5_worker_label: str = ""
    mt5_eager_startup_checks_enabled: bool = False
    mt5_operation_timeout_seconds: float = 5.0
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_symbol: str = "BTCUSDz"
    mt5_symbols: str = ""
    mt5_live_order_symbol_toggle_enabled: bool = False
    mt5_live_order_toggle_symbols: str = ""
    mt5_live_order_enabled_symbols: str = ""
    atr_recovery_enabled: bool = True
    atr_recovery_mt5_only: bool = True
    atr_recovery_toggle_symbols: str = ""
    atr_recovery_enabled_symbols: str = ""
    atr_recovery_atr_period: int = 14
    atr_recovery_min_atr_pct: float = 0.0
    atr_recovery_stop_loss_multiplier: float = 1.5
    atr_recovery_take_profit_multiplier: float = 2.0
    atr_recovery_hedge_trigger_multiplier: float = 0.6
    atr_recovery_trailing_multiplier: float = 0.4
    atr_recovery_trailing_activation_atr: float = 0.3
    atr_recovery_reversal_atr_threshold: float = 0.4
    atr_recovery_live_hedge_enabled: bool = False
    atr_recovery_trailing_monitor_enabled: bool = False
    atr_recovery_auto_reversal_close: bool = False
    atr_recovery_hedge_monitor_interval_seconds: int = 30
    atr_recovery_hedge_cooldown_seconds: int = 0
    atr_recovery_max_hedges_per_cycle: int = 1
    atr_recovery_min_price_delta_for_rehedge_atr: float = 0.25
    news_filter_enabled: bool = False
    news_filter_pre_event_minutes: int = 30
    news_filter_post_event_minutes: int = 15
    news_filter_min_impact: str = "high"
    news_filter_events_utc: str = ""
    news_calendar_provider_url: str = ""
    news_calendar_timeout_seconds: float = 3.0
    news_calendar_cache_seconds: int = 60
    mt5_volume_lots: float = 0.01
    mt5_deviation: int = 20
    mt5_magic: int = 20260318
    pro_analysis_execution_enabled: bool = False
    pro_analysis_execution_min_vote_confidence: float = 0.68
    pro_analysis_execution_min_rr: float = 2.0
    pro_analysis_execution_trading_style: str = "DAY TRADING"
    pro_analysis_execution_risk_tolerance: str = "LOW"
    pro_analysis_execution_require_session_filter: bool = True
    pro_analysis_execution_require_higher_timeframe_alignment: bool = True
    pro_analysis_execution_require_vwap_alignment: bool = True
    mobile_session_days: int = 30
    mobile_max_active_sessions_per_user: int = 5
    mobile_session_retention_days: int = 7
    mobile_auth_rate_limit_window_seconds: int = 60
    mobile_auth_rate_limit_max_attempts: int = 10

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **values):
        # Programmatic Settings(...) instances should use code defaults unless the
        # caller explicitly opts into env loading. App startup still loads .env
        # through get_settings(), which calls Settings() with no field overrides.
        has_field_overrides = any(not str(key).startswith("_") for key in values)
        if has_field_overrides and "_env_file" not in values:
            values["_env_file"] = None
        super().__init__(**values)

    @field_validator("mt5_login", mode="before")
    @classmethod
    def _normalize_mt5_login(cls, value: object) -> object:
        if value is None:
            return 0
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @property
    def has_live_credentials(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)

    @property
    def can_place_live_orders(self) -> bool:
        if self.dry_run:
            return False
        if self.effective_execution_provider == "mt5":
            return self.has_mt5_credentials
        return self.has_live_credentials

    @property
    def has_mt5_credentials(self) -> bool:
        return bool(self.mt5_login and self.mt5_password and self.mt5_server)

    @property
    def effective_execution_provider(self) -> str:
        return self.execution_provider.lower().strip() or "binance"

    @property
    def effective_market_data_provider(self) -> str:
        return self.market_data_provider.lower().strip() or "auto"

    @property
    def effective_market_data_symbol(self) -> str:
        if self.market_data_symbol:
            return self.market_data_symbol
        if self.effective_market_data_provider == "mt5":
            return self.mt5_symbol
        if self.effective_market_data_provider == "coinbase":
            return _to_coinbase_product_id(self.trading_symbol)
        if self.effective_market_data_provider == "okx":
            return _to_dash_symbol(self.trading_symbol)
        if self.effective_market_data_provider == "kraken":
            return _to_kraken_symbol(self.trading_symbol)
        return self.trading_symbol

    @property
    def auto_market_data_priority(self) -> list[str]:
        return ["okx", "kraken", "coinbase", "binance"]

    @property
    def effective_mt5_symbols(self) -> list[str]:
        if not self.mt5_symbols.strip():
            return [self.mt5_symbol]
        parsed = [item.strip() for item in self.mt5_symbols.split(",") if item.strip()]
        return parsed or [self.mt5_symbol]

    @property
    def effective_mt5_execution_mode(self) -> str:
        candidate = self.mt5_execution_mode.strip().lower()
        if candidate in {"direct", "queue"}:
            return candidate
        return "direct"

    @property
    def effective_mt5_live_order_toggle_symbols(self) -> list[str]:
        if not self.mt5_live_order_toggle_symbols.strip():
            return []
        return [item.strip() for item in self.mt5_live_order_toggle_symbols.split(",") if item.strip()]

    @property
    def effective_mt5_live_order_enabled_symbols(self) -> set[str]:
        if not self.mt5_live_order_enabled_symbols.strip():
            return set()
        return {item.strip() for item in self.mt5_live_order_enabled_symbols.split(",") if item.strip()}

    @property
    def effective_atr_recovery_toggle_symbols(self) -> list[str]:
        if not self.atr_recovery_toggle_symbols.strip():
            return list(self.effective_mt5_symbols)
        return [item.strip() for item in self.atr_recovery_toggle_symbols.split(",") if item.strip()]

    @property
    def effective_atr_recovery_enabled_symbols(self) -> set[str]:
        if not self.atr_recovery_enabled_symbols.strip():
            return set()
        return {item.strip() for item in self.atr_recovery_enabled_symbols.split(",") if item.strip()}

    @property
    def effective_enabled_strategies(self) -> list[str]:
        parsed = [item.strip() for item in self.enabled_strategies.split(",") if item.strip()]
        return parsed

    @property
    def effective_strategy_candle_interval(self) -> str:
        candidate = (self.strategy_candle_mode or self.candle_interval or "1h").strip().lower()
        if candidate in {"1h", "4h"}:
            return candidate
        return "1h"

    @property
    def effective_control_allowed_ips(self) -> set[str]:
        return {item.strip() for item in self.control_allowed_ips.split(",") if item.strip()}

    @property
    def effective_allowed_live_environments(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_live_environments.split(",") if item.strip()}

    @property
    def effective_trusted_hosts(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def effective_cors_allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    @property
    def control_api_key_is_configured(self) -> bool:
        return bool(self.control_api_key.strip())

    @property
    def effective_strategy_min_confidence_threshold(self) -> float:
        return min(max(float(self.strategy_min_confidence_threshold), 0.0), 1.0)

    @property
    def effective_strategy_min_confidence_threshold_buy(self) -> float:
        return min(max(float(self.strategy_min_confidence_threshold_buy), 0.0), 1.0)

    @property
    def effective_strategy_min_confidence_threshold_sell(self) -> float:
        return min(max(float(self.strategy_min_confidence_threshold_sell), 0.0), 1.0)

    @property
    def live_trading_enabled(self) -> bool:
        return self.can_place_live_orders and self.live_trading_armed

    @property
    def effective_pro_analysis_execution_trading_style(self) -> str:
        candidate = self.pro_analysis_execution_trading_style.strip().upper().replace("_", " ")
        if candidate in {"SCALPING", "DAY TRADING", "SWING"}:
            return candidate
        return "DAY TRADING"

    @property
    def effective_pro_analysis_execution_risk_tolerance(self) -> str:
        candidate = self.pro_analysis_execution_risk_tolerance.strip().upper()
        if candidate in {"LOW", "MEDIUM", "HIGH"}:
            return candidate
        return "LOW"

    def validate_live_trading_configuration(self) -> None:
        if not self.can_place_live_orders:
            return
        if not self.live_trading_armed:
            raise ValueError("Live trading requires LIVE_TRADING_ARMED=true.")
        if self.environment.lower() not in self.effective_allowed_live_environments:
            allowed = ", ".join(sorted(self.effective_allowed_live_environments))
            raise ValueError(
                f"Live trading is only allowed in these environments: {allowed}. Current environment: {self.environment}."
            )

    @property
    def telegram_is_configured(self) -> bool:
        return bool(self.effective_telegram_recipients)

    @property
    def effective_telegram_recipients(self) -> list[tuple[str, str]]:
        recipients: list[tuple[str, str]] = []

        raw_targets = self.telegram_targets.replace(";", ",")
        for item in [entry.strip() for entry in raw_targets.split(",") if entry.strip()]:
            token = ""
            chat_id = ""
            if "|" in item:
                token, chat_id = item.split("|", 1)
            elif ":" in item:
                token, chat_id = item.rsplit(":", 1)
            if token.strip() and chat_id.strip():
                recipients.append((token.strip(), chat_id.strip()))

        if self.telegram_bot_token.strip() and self.telegram_chat_id.strip():
            recipients.append((self.telegram_bot_token.strip(), self.telegram_chat_id.strip()))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for pair in recipients:
            if pair in seen:
                continue
            seen.add(pair)
            deduped.append(pair)
        return deduped


def _to_coinbase_product_id(symbol: str) -> str:
    if "-" in symbol:
        return symbol

    for quote in ("USDT", "USDC", "USD", "EUR", "GBP"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}-{quote}"
    return symbol


def _to_dash_symbol(symbol: str) -> str:
    if "-" in symbol:
        return symbol

    for quote in ("USDT", "USDC", "USD", "EUR", "GBP"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}-{quote}"
    return symbol


def _to_kraken_symbol(symbol: str) -> str:
    if "-" in symbol:
        symbol = symbol.replace("-", "")

    alias_map = {
        "BTC": "XBT",
    }
    for quote in ("USDT", "USD", "EUR", "GBP"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            kraken_base = alias_map.get(base, base)
            return f"{kraken_base}{quote}"
    return symbol


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=resolve_env_file_path())
