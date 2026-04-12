from datetime import datetime

from pydantic import BaseModel, Field


class TradeRead(BaseModel):
    id: int
    symbol: str
    signal_symbol: str | None
    execution_symbol: str | None
    side: str
    quantity: float
    price: float
    quote_amount: float
    rsi_value: float
    signal: str
    status: str
    exchange_order_id: str | None
    realized_pnl: float | None
    realized_pnl_pct: float | None
    outcome: str | None
    is_dry_run: bool
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MLTrainRequest(BaseModel):
    min_samples: int = 40
    epochs: int = 250
    learning_rate: float = 0.15
    validation_size: float = 0.2
    random_state: int = 42


class MLPredictRequest(BaseModel):
    rsi_norm: float
    confidence: float
    momentum_5: float
    momentum_20: float
    volatility_20: float
    atr_pct: float
    strategy_count_norm: float


class ProAnalysisRequest(BaseModel):
    symbols: list[str] | None = None
    account_size: float | None = None
    risk_tolerance: str = "MEDIUM"
    trading_style: str = "DAY TRADING"


class ProAnalysisExecuteRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    account_size: float | None = None
    risk_tolerance: str = "MEDIUM"
    trading_style: str = "DAY TRADING"


class Mt5ProfileCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    login: int = Field(gt=0)
    password: str = Field(min_length=1, max_length=255)
    server: str = Field(min_length=1, max_length=100)
    terminal_path: str | None = Field(default=None, max_length=255)
    primary_symbol: str | None = Field(default=None, max_length=32)
    symbols: list[str] = Field(default_factory=list)
    volume_lots: float = Field(default=0.01, gt=0)
    set_active: bool = False


class Mt5ProfileActivateRequest(BaseModel):
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)


class OwnerScopedRequest(BaseModel):
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)


class Mt5WorkerProvisionRequest(BaseModel):
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    worker_key: str = Field(min_length=1, max_length=100)
    profile_id: int | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=100)
    terminal_path: str | None = Field(default=None, max_length=255)


class Mt5WorkerAssignRequest(BaseModel):
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_id: int | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=100)
    terminal_path: str | None = Field(default=None, max_length=255)


class MobileAuthRegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    device_name: str | None = Field(default=None, max_length=120)


class MobileAuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    device_name: str | None = Field(default=None, max_length=120)
