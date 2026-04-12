from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    signal_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    execution_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    side: Mapped[str] = mapped_column(String(8), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    quote_amount: Mapped[float] = mapped_column(Float)
    rsi_value: Mapped[float] = mapped_column(Float)
    signal: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20))
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    broker_position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intended_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_weights: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class Mt5Profile(Base):
    __tablename__ = "mt5_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    label: Mapped[str] = mapped_column(String(100), index=True)
    login: Mapped[int] = mapped_column(index=True)
    password_encrypted: Mapped[str] = mapped_column(Text)
    server: Mapped[str] = mapped_column(String(100), index=True)
    terminal_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbols_csv: Mapped[str] = mapped_column(Text, default="")
    volume_lots: Mapped[float] = mapped_column(Float, default=0.01)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_connection_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_connection_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )


class Mt5ExecutionJob(Base):
    __tablename__ = "mt5_execution_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    profile_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    assigned_worker_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    execution_request_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    execution_symbol: Mapped[str] = mapped_column(String(20), index=True)
    action: Mapped[str] = mapped_column(String(8), index=True)
    volume: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_by_worker_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )


class Mt5Worker(Base):
    __tablename__ = "mt5_workers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    worker_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    profile_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terminal_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ONLINE", index=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )


class Mt5TradeCycle(Base):
    __tablename__ = "mt5_trade_cycles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    signal_symbol: Mapped[str] = mapped_column(String(20), index=True)
    execution_symbol: Mapped[str] = mapped_column(String(20), index=True)
    cycle_type: Mapped[str] = mapped_column(String(32), default="ATR_RECOVERY", index=True)
    base_direction: Mapped[str] = mapped_column(String(8), index=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    atr_recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    hedge_trigger: Mapped[float | None] = mapped_column(Float, nullable=True)
    trailing_activation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reversal_confirmation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    overlay_active: Mapped[bool] = mapped_column(Boolean, default=False)
    planned_hedge_only: Mapped[bool] = mapped_column(Boolean, default=True)
    hedge_position_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    hedge_placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hedge_sl_last_modified: Mapped[float | None] = mapped_column(Float, nullable=True)
    hedge_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hedge_attempt_count: Mapped[int] = mapped_column(default=0)
    hedge_last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hedge_last_action_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    linked_trade_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )


class MobileSession(Base):
    __tablename__ = "mobile_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class AuthRateLimitEntry(Base):
    __tablename__ = "auth_rate_limit_entries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        index=True,
    )


class ExecutionRequest(Base):
    __tablename__ = "execution_requests"
    __table_args__ = (UniqueConstraint("account_scope", "idempotency_key", name="uq_execution_requests_account_scope_idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    account_scope: Mapped[str] = mapped_column(String(160), index=True, default="default")
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_symbol: Mapped[str] = mapped_column(String(20), index=True)
    execution_symbol: Mapped[str] = mapped_column(String(20), index=True)
    action: Mapped[str] = mapped_column(String(8), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrokerFillJournal(Base):
    __tablename__ = "broker_fill_journal"
    __table_args__ = (
        UniqueConstraint("provider", "execution_symbol", "broker_fill_id", name="uq_fill_journal_entry"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    execution_symbol: Mapped[str] = mapped_column(String(20), index=True)
    broker_fill_id: Mapped[str] = mapped_column(String(128), index=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class BrokerPositionJournal(Base):
    __tablename__ = "broker_position_journal"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    execution_symbol: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    raw_payload: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
