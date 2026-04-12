import os
import app.main as main_module
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import app, create_app, get_mobile_auth_service, get_mt5_profile_service, get_mt5_worker_service, get_trading_service
from app.models import AuthRateLimitEntry
from app.services.binance import SymbolRules
from app.services.rate_limit import AuthRateLimiter
from app.services.trading import MarketSnapshot


class FakeTradingService:
    def __init__(self) -> None:
        self.run_count = 0
        self.last_journal_args: dict | None = None
        self.last_preview_settings = None
        self._mt5_atr_recovery_enabled_symbols: set[str] = {"GBPUSDm"}
        self.settings = type("FakeSettings", (), {"ml_buy_probability_threshold": 0.58, "ml_sell_probability_threshold": 0.42, "ml_training_trade_limit": 2000})()
        self.ml_service = type(
            "FakeMLService",
            (),
            {
                "status": lambda self: {"ready": True, "model": {"samples": 120}},
                "train_from_trades": lambda self, **kwargs: {"status": "trained", "trained": True, "samples": 120, "validation_accuracy": 0.7},
                "predict_up_probability": lambda self, features: 0.73,
                "action_from_probability": lambda self, p, buy_threshold, sell_threshold: "BUY",
            },
        )()

    async def build_snapshot(self, db) -> MarketSnapshot:
        return MarketSnapshot(
            signal_symbol="BTCUSDT",
            market_data_symbol="BTC-USD",
            execution_symbol="BTCUSDT",
            market_data_provider="coinbase",
            latest_price=42000.0,
            rsi=28.5,
            position_quantity=0.0,
            suggested_action="BUY",
            rules=SymbolRules(
                symbol="BTCUSDT",
                base_asset="BTC",
                quote_asset="USDT",
                step_size=Decimal("0.00000100"),
                min_qty=Decimal("0.00001000"),
                min_notional=Decimal("10.00"),
            ),
        )

    async def build_snapshot_with_runtime_settings(self, db, runtime_settings, market_data_symbol: str | None = None) -> MarketSnapshot:
        self.last_preview_settings = runtime_settings
        resolved_symbol = market_data_symbol or getattr(runtime_settings, "mt5_symbol", "BTCUSDT")
        return MarketSnapshot(
            signal_symbol=resolved_symbol,
            market_data_symbol=resolved_symbol,
            execution_symbol=resolved_symbol,
            market_data_provider=getattr(runtime_settings, "effective_market_data_provider", "mt5"),
            latest_price=1.2456,
            rsi=47.2,
            position_quantity=0.0,
            suggested_action="HOLD",
            rules=SymbolRules(
                symbol=resolved_symbol,
                base_asset=resolved_symbol,
                quote_asset="USD",
                step_size=Decimal("0.01"),
                min_qty=Decimal("0.01"),
                min_notional=Decimal("0.00"),
            ),
        )

    async def run_cycle(self, db, request_id: str | None = None) -> dict:
        self.run_count += 1
        return {
            "symbol": "BTCUSDT",
            "signal_symbol": "BTCUSDT",
            "execution_symbol": "BTCUSDT",
            "market_data_symbol": "BTC-USD",
            "market_data_provider": "coinbase",
            "price": 42000.0,
            "rsi": 28.5,
            "action": "BUY",
            "strategy_vote_action": "BUY",
            "position_quantity": 0.0,
            "trade": {
                "id": self.run_count,
                "symbol": "BTCUSDT",
                "signal_symbol": "BTCUSDT",
                "execution_symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": 0.001,
                "price": 42000.0,
                "quote_amount": 42.0,
                "rsi_value": 28.5,
                "status": "SIMULATED",
                "is_dry_run": True,
                "created_at": "2026-03-18T00:00:00+00:00",
            },
        }

    async def run_auto_cycle(self, db, request_id: str | None = None) -> dict:
        return await self.run_cycle(db, request_id=request_id)

    async def reconcile_broker_state(self, db) -> dict:
        return {
            "status": "ok",
            "provider": "binance",
            "balances": {"BTC": "0", "USDT": "1000"},
            "positions": [],
            "open_orders": [],
            "fills": [],
            "rejected_orders": [],
            "orphaned_stops": [],
        }

    def list_recent_trades(self, db, limit: int = 20) -> list:
        return []

    async def hydrate_authoritative_state(self, db) -> dict:
        return {"status": "ok"}

    async def run_startup_self_check(self, db) -> dict:
        return {"status": "ok"}

    def list_execution_journal(
        self,
        db,
        limit: int = 100,
        symbol: str | None = None,
        reconciliation_status: str | None = None,
        since=None,
    ) -> list:
        self.last_journal_args = {
            "limit": limit,
            "symbol": symbol,
            "reconciliation_status": reconciliation_status,
            "since": since,
        }
        return []

    async def run_backtest(
        self,
        history_limit: int = 1000,
        initial_balance: float = 1000.0,
        trade_amount: float | None = None,
        fee_rate: float | None = None,
        market_data_symbol: str | None = None,
    ) -> dict:
        return {
            "symbol": "BTCUSDT",
            "market_data_symbol": market_data_symbol or "BTC-USD",
            "market_data_provider": "coinbase",
            "interval": "15m",
            "candles": history_limit,
            "rsi_period": 14,
            "buy_threshold": 30.0,
            "sell_threshold": 70.0,
            "initial_balance": initial_balance,
            "trade_amount": trade_amount or 50.0,
            "fee_rate": fee_rate or 0.001,
            "latest_price": 42000.0,
            "latest_rsi": 55.0,
            "open_position_quantity": 0.0,
            "completed_trades": 3,
            "winning_trades": 2,
            "win_rate_pct": 66.67,
            "buy_signals": 4,
            "sell_signals": 3,
            "ending_equity": 1042.0,
            "net_pnl": 42.0,
            "roi_pct": 4.2,
            "max_drawdown_pct": 3.1,
        }

    async def run_professional_analysis_execution(
        self,
        db,
        symbol: str,
        account_size: float | None = None,
        risk_tolerance: str = "MEDIUM",
        trading_style: str = "DAY TRADING",
        request_id: str | None = None,
    ) -> dict:
        return {
            "symbol": symbol,
            "signal_symbol": symbol,
            "execution_symbol": symbol,
            "market_data_symbol": symbol,
            "market_data_provider": "mt5",
            "price": 1.2456,
            "rsi": 47.2,
            "action": "BUY",
            "strategy_vote_action": "BUY",
            "position_quantity": 0.0,
            "pro_analysis_vote_action": "BUY",
            "pro_analysis_final_action": "BUY",
            "pro_analysis_gate_blocked": False,
            "pro_analysis_gate_reasons": [],
            "pro_analysis_session_name": "London session",
            "pro_analysis_quality_gate_passed": True,
            "requested_trading_style": trading_style,
            "requested_risk_tolerance": risk_tolerance,
            "analysis_plan": {
                "weighted_vote_action": "BUY",
                "final_action": "BUY",
                "session_name": "London session",
                "quality_gate_reasons": [],
            },
            "trade": {
                "id": 1,
                "symbol": symbol,
                "execution_symbol": symbol,
                "side": "BUY",
                "quantity": 0.01,
                "price": 1.2456,
                "status": "FILLED",
            },
        }

    def get_mt5_atr_recovery_toggle_states(self) -> list[dict]:
        return [
            {"symbol": "GBPUSDm", "enabled": "GBPUSDm" in self._mt5_atr_recovery_enabled_symbols},
            {"symbol": "EURUSDm", "enabled": "EURUSDm" in self._mt5_atr_recovery_enabled_symbols},
        ]

    def set_mt5_atr_recovery_symbol_enabled(self, symbol: str, enabled: bool) -> dict:
        allowed = {"GBPUSDm", "EURUSDm"}
        if symbol not in allowed:
            raise ValueError(f"Symbol {symbol} is not configured for MT5 ATR recovery toggles.")
        if enabled:
            self._mt5_atr_recovery_enabled_symbols.add(symbol)
        else:
            self._mt5_atr_recovery_enabled_symbols.discard(symbol)
        return {"symbol": symbol, "enabled": symbol in self._mt5_atr_recovery_enabled_symbols}

    async def build_startup_execution_review(self, db) -> dict:
        now = "2026-04-11T12:00:00+00:00"
        return {
            "status": "pending_confirmation",
            "generated_at": now,
            "message": "First boot review is holding MT5 automation.",
            "requires_confirmation": True,
            "actionable_count": 1,
            "queued_job_count": 1,
            "items": [
                {
                    "source": "startup_analysis",
                    "symbol": "GBPUSDm",
                    "signal_symbol": "GBPUSDm",
                    "execution_symbol": "GBPUSDm",
                    "action": "BUY",
                    "confidence": 0.9,
                    "latest_price": 1.2456,
                    "stop_loss": 1.2400,
                    "take_profit": 1.2550,
                    "execution_block": None,
                    "analysis_generated_at": now,
                    "message": "Current first-boot analysis preview.",
                },
                {
                    "source": "queued_job",
                    "symbol": "EURUSDm",
                    "signal_symbol": "EURUSDm",
                    "execution_symbol": "EURUSDm",
                    "action": "SELL",
                    "confidence": None,
                    "latest_price": None,
                    "stop_loss": 1.1100,
                    "take_profit": 1.0900,
                    "execution_block": None,
                    "analysis_generated_at": "2026-04-10T08:00:00+00:00",
                    "message": "Previously queued MT5 execution waiting for confirmation.",
                },
            ],
        }

    def cancel_startup_pending_execution_jobs(self, db) -> list[dict]:
        return [{"job_id": 1, "execution_symbol": "EURUSDm", "action": "SELL", "client_order_id": "queued-sell-1"}]


class FakeCancelableTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeMt5ProfileService:
    def __init__(self) -> None:
        self.encryption_ready = True
        self.items: list[dict] = []
        self.last_apply_args: dict | None = None
        self.runtime_activation_error: str | None = None
        self.startup_profile_error: str | None = None
        self.startup_apply_calls = 0

    def list_profiles(self, db, owner_id: str | None = None) -> list[dict]:
        if not owner_id:
            return list(self.items)
        return [item for item in self.items if item["owner_id"] == owner_id]

    def get_active_profile(self, db, owner_id: str | None = None):
        item = next(
            (item for item in self.items if item["is_active"] and (owner_id is None or item["owner_id"] == owner_id)),
            None,
        )
        return None if item is None else SimpleNamespace(**item)

    def create_profile(self, db, payload) -> dict:
        symbols = list(payload.symbols)
        primary_symbol = (payload.primary_symbol or "").strip() or (symbols[0] if symbols else None)
        if primary_symbol and primary_symbol not in symbols:
            symbols = [primary_symbol, *symbols]
        item = {
            "id": len(self.items) + 1,
            "owner_id": payload.owner_id,
            "label": payload.label,
            "login": payload.login,
            "server": payload.server,
            "terminal_path": payload.terminal_path,
            "primary_symbol": primary_symbol,
            "symbols": symbols,
            "volume_lots": payload.volume_lots,
            "is_active": payload.set_active,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": None,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        }
        if payload.set_active:
            for existing in self.items:
                existing["is_active"] = False
        self.items.append(item)
        return item

    async def test_connection(self, payload) -> dict:
        return {
            "ok": True,
            "account": {"equity": 12000.0, "balance": 11850.0},
            "active_positions": 1,
            "server": payload.server,
            "login": payload.login,
        }

    async def test_saved_profile(self, db, profile_id: int, owner_id: str | None = None) -> dict:
        for item in self.items:
            if item["id"] == profile_id:
                if owner_id and item["owner_id"] != owner_id:
                    raise ValueError("MT5 profile does not belong to the requested owner.")
                item["last_connection_ok"] = True
                item["last_connection_error"] = None
                item["last_validated_at"] = "2026-04-03T01:00:00+00:00"
                return {
                    "ok": True,
                    "profile": item,
                    "account": {"equity": 12000.0, "balance": 11850.0},
                    "active_positions": 1,
                }
        raise ValueError("MT5 profile not found.")

    def activate_profile(self, db, profile_id: int, owner_id: str | None = None) -> dict:
        for item in self.items:
            if item["id"] == profile_id:
                if owner_id and item["owner_id"] != owner_id:
                    raise ValueError("MT5 profile does not belong to the requested owner.")
                for existing in self.items:
                    if owner_id is None or existing["owner_id"] == owner_id:
                        existing["is_active"] = False
                item["is_active"] = True
                return item
        raise ValueError("MT5 profile not found.")

    def get_profile(self, db, profile_id: int, owner_id: str | None = None):
        for item in self.items:
            if item["id"] == profile_id:
                if owner_id and item["owner_id"] != owner_id:
                    raise ValueError("MT5 profile does not belong to the requested owner.")
                return item
        raise ValueError("MT5 profile not found.")

    def delete_profile(self, db, profile_id: int, owner_id: str | None = None) -> None:
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        if profile["is_active"]:
            raise ValueError("Cannot delete the active MT5 profile. Activate another profile first.")
        self.items = [item for item in self.items if item["id"] != profile_id]

    def build_preview_settings_from_payload(self, payload):
        settings = get_settings().model_copy(update={
            "mt5_login": payload.login,
            "mt5_password": payload.password,
            "mt5_server": payload.server,
            "mt5_symbol": payload.primary_symbol or (payload.symbols[0] if payload.symbols else get_settings().mt5_symbol),
            "mt5_symbols": ",".join(([payload.primary_symbol] if payload.primary_symbol else []) + [item for item in payload.symbols if item != payload.primary_symbol]),
            "market_data_symbol": payload.primary_symbol or (payload.symbols[0] if payload.symbols else get_settings().mt5_symbol),
        })
        return settings

    def build_preview_settings_from_profile(self, db, profile_id: int, owner_id: str | None = None):
        profile = self.get_profile(db, profile_id, owner_id=owner_id)
        settings = get_settings().model_copy(update={
            "mt5_login": profile["login"],
            "mt5_server": profile["server"],
            "mt5_symbol": profile.get("primary_symbol") or (profile["symbols"][0] if profile["symbols"] else get_settings().mt5_symbol),
            "mt5_symbols": ",".join(profile["symbols"]),
            "market_data_symbol": profile.get("primary_symbol") or (profile["symbols"][0] if profile["symbols"] else get_settings().mt5_symbol),
        })
        return settings, profile

    def apply_active_profile(self, db, runtime_settings=None, trading_service=None, owner_id: str | None = None) -> dict | None:
        self.last_apply_args = {
            "owner_id": owner_id,
            "runtime_settings": runtime_settings,
            "trading_service": trading_service,
        }
        active = next((item for item in self.items if item["is_active"] and (owner_id is None or item["owner_id"] == owner_id)), None)
        if active is None:
            return None
        if runtime_settings is not None:
            runtime_settings.mt5_login = active["login"]
            runtime_settings.mt5_server = active["server"]
            runtime_settings.mt5_symbol = active.get("primary_symbol") or (active["symbols"][0] if active["symbols"] else "")
            runtime_settings.mt5_symbols = ",".join(active["symbols"])
            runtime_settings.mt5_volume_lots = active["volume_lots"]
        if trading_service is not None:
            trading_service.runtime_mt5_profile = active
        return active

    async def activate_runtime_profile(self, db, profile_id: int, runtime_settings=None, trading_service=None, owner_id: str | None = None) -> dict:
        if self.runtime_activation_error:
            raise ValueError(self.runtime_activation_error)
        profile = self.activate_profile(db, profile_id, owner_id=owner_id)
        active = self.apply_active_profile(db, runtime_settings=runtime_settings, trading_service=trading_service, owner_id=owner_id)
        return active or profile

    async def apply_saved_runtime_profile_if_valid(self, db, runtime_settings=None, trading_service=None, owner_id: str | None = None) -> dict | None:
        self.startup_apply_calls += 1
        if self.startup_profile_error:
            active = next((item for item in self.items if item["is_active"] and (owner_id is None or item["owner_id"] == owner_id)), None)
            if active is not None:
                active["is_active"] = False
                active["last_connection_ok"] = False
                active["last_connection_error"] = self.startup_profile_error
            return None
        return self.apply_active_profile(db, runtime_settings=runtime_settings, trading_service=trading_service, owner_id=owner_id)


class FakeMt5WorkerService:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def list_workers(self, db, owner_id: str | None = None) -> list[dict]:
        if not owner_id:
            return list(self.items)
        return [item for item in self.items if item["owner_id"] == owner_id]

    def provision_worker(self, db, worker_key: str, owner_id: str, profile_id: int | None = None, label: str | None = None, terminal_path: str | None = None) -> dict:
        existing = next((item for item in self.items if item["worker_key"] == worker_key), None)
        if existing is not None and existing["owner_id"] != owner_id:
            raise ValueError("Worker key is already assigned to a different owner.")
        item = existing or {
            "id": len(self.items) + 1,
            "worker_key": worker_key,
            "owner_id": owner_id,
            "profile_id": None,
            "profile_label": None,
            "label": None,
            "terminal_path": None,
            "status": "PROVISIONED",
            "last_error": None,
            "heartbeat_at": None,
            "last_claimed_at": None,
            "created_at": "2026-04-11T00:00:00+00:00",
            "updated_at": "2026-04-11T00:00:00+00:00",
        }
        item["owner_id"] = owner_id
        item["profile_id"] = profile_id
        item["profile_label"] = None if profile_id is None else f"Profile {profile_id}"
        item["label"] = label or item.get("label") or worker_key
        item["terminal_path"] = terminal_path or item.get("terminal_path")
        item["status"] = item.get("status") or "PROVISIONED"
        if existing is None:
            self.items.append(item)
        return item

    def assign_worker(self, db, worker_key: str, owner_id: str, profile_id: int | None = None, label: str | None = None, terminal_path: str | None = None) -> dict:
        existing = next((item for item in self.items if item["worker_key"] == worker_key), None)
        if existing is None:
            raise ValueError("MT5 worker not found.")
        if existing["owner_id"] != owner_id:
            raise ValueError("MT5 worker does not belong to the requested owner.")
        existing["profile_id"] = profile_id
        existing["profile_label"] = None if profile_id is None else f"Profile {profile_id}"
        if label is not None:
            existing["label"] = label or existing["label"]
        if terminal_path is not None:
            existing["terminal_path"] = terminal_path or None
        return existing

    def auto_assign_owner_worker(self, db, owner_id: str, profile_id: int | None):
        if profile_id is None:
            return None
        existing = next((item for item in self.items if item["owner_id"] == owner_id and item["profile_id"] == profile_id), None)
        if existing is not None:
            return existing
        unassigned = [item for item in self.items if item["owner_id"] == owner_id and item["profile_id"] is None]
        if len(unassigned) != 1:
            return None
        unassigned[0]["profile_id"] = profile_id
        unassigned[0]["profile_label"] = f"Profile {profile_id}"
        return unassigned[0]


class FakeMobileAuthService:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}

    def register_user(self, db, payload) -> dict:
        normalized_email = payload.email.strip().lower()
        if normalized_email in self.users:
            raise ValueError("User already exists for that email.")
        owner_id = normalized_email.split("@")[0]
        user = {
            "owner_id": owner_id,
            "email": normalized_email,
            "display_name": payload.display_name or owner_id,
            "is_active": True,
            "created_at": "2026-04-03T00:00:00+00:00",
        }
        token = f"token-{owner_id}"
        session = {
            "owner_id": owner_id,
            "device_name": payload.device_name,
            "expires_at": "2026-05-03T00:00:00+00:00",
            "revoked_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "token": token,
        }
        self.users[normalized_email] = user
        self.sessions[token] = session
        return {"user": user, "session": session}

    def login_user(self, db, payload) -> dict:
        normalized_email = payload.email.strip().lower()
        user = self.users.get(normalized_email)
        if user is None:
            raise ValueError("Invalid email or password.")
        token = f"token-{user['owner_id']}-login"
        session = {
            "owner_id": user["owner_id"],
            "device_name": payload.device_name,
            "expires_at": "2026-05-03T00:00:00+00:00",
            "revoked_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "token": token,
        }
        self.sessions[token] = session
        return {"user": user, "session": session}

    def get_session_context(self, db, raw_token: str) -> dict:
        session = self.sessions.get(raw_token)
        if session is None or session.get("revoked_at") is not None:
            raise ValueError("Invalid session token.")
        user = next((item for item in self.users.values() if item["owner_id"] == session["owner_id"]), None)
        if user is None:
            raise ValueError("Session user is unavailable.")
        return {
            "owner_id": user["owner_id"],
            "user": user,
            "session": {key: value for key, value in session.items() if key != "token"},
        }

    def revoke_session(self, db, raw_token: str) -> dict:
        session = self.sessions.get(raw_token)
        if session is None:
            raise ValueError("Invalid session token.")
        session["revoked_at"] = "2026-04-03T02:00:00+00:00"
        return {key: value for key, value in session.items() if key != "token"}


def test_dashboard_and_run_endpoint() -> None:
    fake_service = FakeTradingService()
    fake_mt5_profile_service = FakeMt5ProfileService()
    fake_mobile_auth_service = FakeMobileAuthService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_mt5_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service
    original_bot_state = getattr(app.state, "bot_state", None)
    if hasattr(app.state, "settings"):
        app.state.bot_state = main_module.build_bot_state(app.state.settings)

    try:
        with TestClient(app) as client:
            health_response = client.get("/health")
            assert health_response.status_code == 200
            assert health_response.json() == {"status": "ok"}

            dashboard_response = client.get("/")
            assert dashboard_response.status_code == 200
            assert "Beacon" in dashboard_response.text
            assert "MT5 Profile Intake" in dashboard_response.text
            assert "Primary Broker Symbol" in dashboard_response.text
            assert "Run Strategy Now" in dashboard_response.text
            assert "Turn On Bot" in dashboard_response.text
            assert "Turn Off Bot" in dashboard_response.text
            assert "next-check-label" in dashboard_response.text
            assert "void refreshStatus();" in dashboard_response.text

            run_response = client.post("/api/bot/run")
            assert run_response.status_code == 200
            assert run_response.json()["action"] == "BUY"
    finally:
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        app.dependency_overrides.clear()


def test_stop_bot_endpoint_disables_runtime_tasks() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    original_bot_state = getattr(app.state, "bot_state", None)
    original_settings = getattr(app.state, "settings", None)

    if hasattr(app.state, "settings"):
        app.state.bot_state = main_module.build_bot_state(app.state.settings)

    try:
        with TestClient(app) as client:
            app.state.bot_task = FakeCancelableTask()
            app.state.reconcile_task = FakeCancelableTask()
            app.state.hedge_monitor_task = FakeCancelableTask()
            app.state.settings.auto_trading_enabled = True
            app.state.settings.live_trading_armed = True
            app.state.bot_state["auto_trading_enabled"] = True
            app.state.bot_state["live_trading_armed"] = True
            app.state.bot_state["mode"] = "LIVE_ARMED"

            stop_response = client.post("/api/bot/stop")
            assert stop_response.status_code == 200
            payload = stop_response.json()
            assert payload["status"] == "stopped"
            assert set(payload["cancelled_tasks"]) == {"bot_task", "reconcile_task", "hedge_monitor_task"}
            assert app.state.bot_task is None
            assert app.state.reconcile_task is None
            assert app.state.hedge_monitor_task is None
            assert app.state.bot_state["auto_trading_enabled"] is False
            assert app.state.bot_state["live_trading_armed"] is False
            expected_mode = "LIVE_DISARMED" if app.state.settings.can_place_live_orders else "DRY_RUN"
            assert app.state.bot_state["mode"] == expected_mode
    finally:
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        if original_settings is not None:
            app.state.settings.auto_trading_enabled = original_settings.auto_trading_enabled
            app.state.settings.live_trading_armed = original_settings.live_trading_armed
        app.dependency_overrides.clear()


def test_start_bot_endpoint_reenables_runtime_tasks() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    original_bot_state = getattr(app.state, "bot_state", None)
    original_settings = getattr(app.state, "settings", None)

    if hasattr(app.state, "settings"):
        app.state.bot_state = main_module.build_bot_state(app.state.settings)

    try:
        with TestClient(app) as client:
            app.state.bot_task = None
            app.state.reconcile_task = None
            app.state.hedge_monitor_task = None
            app.state.settings.auto_trading_enabled = False
            app.state.settings.live_trading_armed = False
            app.state.bot_state["auto_trading_enabled"] = False
            app.state.bot_state["live_trading_armed"] = False
            app.state.bot_state["mode"] = "LIVE_DISARMED"
            app.state.bot_state["startup_execution_review"] = {
                "status": "cancelled",
                "requires_confirmation": False,
                "items": [],
                "actionable_count": 0,
                "queued_job_count": 0,
            }

            start_response = client.post("/api/bot/start")
            assert start_response.status_code == 200
            payload = start_response.json()
            assert payload["status"] == "started"
            assert "bot_task" in payload["started_tasks"]
            assert app.state.bot_task is not None
            assert app.state.bot_state["auto_trading_enabled"] is True
            assert app.state.bot_state["live_trading_armed"] == bool(app.state.settings.live_trading_enabled)
            expected_mode = "LIVE_ARMED" if app.state.settings.live_trading_enabled else "DRY_RUN"
            assert app.state.bot_state["mode"] == expected_mode
    finally:
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        if original_settings is not None:
            app.state.settings.auto_trading_enabled = original_settings.auto_trading_enabled
            app.state.settings.live_trading_armed = original_settings.live_trading_armed
        app.dependency_overrides.clear()


def test_dashboard_shows_startup_execution_review_and_endpoints_handle_actions() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    original_bot_state = getattr(app.state, "bot_state", None)

    try:
        with TestClient(app) as client:
            app.state.bot_state["startup_execution_review"] = {
                "status": "pending_confirmation",
                "generated_at": "2026-04-11T12:00:00+00:00",
                "message": "First boot review is holding MT5 automation.",
                "requires_confirmation": True,
                "actionable_count": 1,
                "queued_job_count": 1,
                "items": [
                    {
                        "source": "startup_analysis",
                        "execution_symbol": "GBPUSDm",
                        "action": "BUY",
                        "confidence": 0.9,
                        "latest_price": 1.2456,
                        "stop_loss": 1.2400,
                        "take_profit": 1.2550,
                        "analysis_generated_at": "2026-04-11T12:00:00+00:00",
                        "execution_block": None,
                        "message": "Current first-boot analysis preview.",
                    }
                ],
            }
            dashboard_response = client.get("/")
            assert dashboard_response.status_code == 200
            assert "Startup Execution Review" in dashboard_response.text
            assert "Allow Execution" in dashboard_response.text
            assert "Cancel Pending Execution" in dashboard_response.text
            assert "2026-04-11T12:00:00+00:00" in dashboard_response.text

            approve_response = client.post("/api/bot/startup-review/approve")
            assert approve_response.status_code == 200
            assert approve_response.json()["status"] == "approved"

            cancel_response = client.post("/api/bot/startup-review/cancel")
            assert cancel_response.status_code == 200
            cancel_payload = cancel_response.json()
            assert cancel_payload["status"] == "cancelled"
            assert len(cancel_payload["cancelled_jobs"]) == 1
    finally:
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        app.dependency_overrides.clear()


class FailingTradingService(FakeTradingService):
    async def build_snapshot(self, db):
        raise RuntimeError("network timeout")


class FailingSnapshotMt5TradingService(FakeTradingService):
    def __init__(self) -> None:
        super().__init__()
        self.mt5 = self
        self.active_positions_count_calls = 0
        self.readiness_calls = 0

    async def build_snapshot(self, db):
        raise RuntimeError("MT5 initialize failed (-10005): IPC timeout")

    async def get_active_positions_count(self) -> int:
        self.active_positions_count_calls += 1
        return 3

    async def check_auto_execution_ready(self, symbol: str) -> dict:
        self.readiness_calls += 1
        return {"ready": True, "symbol": symbol, "retcode": 0, "comment": "ok"}


class FakeProAnalysisMt5:
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[list[float]]:
        rows: list[list[float]] = []
        for index in range(limit):
            close = 1.2000 + (index * 0.0001)
            rows.append([index, close - 0.0002, close + 0.0004, close - 0.0004, close, 1000 + index])
        return rows

    async def get_symbol_market_state(self, symbol: str) -> dict[str, float]:
        return {"spread_pips": 0.3, "bid": 1.2298, "ask": 1.2300, "point": 0.0001, "digits": 5.0}

    async def get_symbol_specifications(self, symbol: str) -> dict[str, float | int | str]:
        return {
            "symbol": symbol,
            "point": 0.0001,
            "digits": 5,
            "volume_min": 0.01,
            "volume_max": 5.0,
            "volume_step": 0.01,
            "trade_contract_size": 100000.0,
            "trade_tick_size": 0.0001,
            "trade_tick_value": 10.0,
            "trade_tick_value_profit": 10.0,
            "trade_tick_value_loss": 10.0,
            "currency_profit": "USD",
        }

    async def get_account_info(self) -> dict[str, float]:
        return {"equity": 15000.0, "balance": 15000.0}


class FakeProAnalysisTradingService(FakeTradingService):
    def __init__(self) -> None:
        super().__init__()
        self.mt5 = FakeProAnalysisMt5()
        self.strategy_engine = SimpleNamespace(
            evaluate=lambda series, timeframe: SimpleNamespace(
                action="BUY" if timeframe != "1m" else "HOLD",
                confidence=0.76 if timeframe in {"1h", "4h", "1d"} else 0.62,
                stop_loss=series["closes"][-1] * 0.995,
                take_profit=series["closes"][-1] * 1.015,
                regime="trend",
                selected_strategies=[],
                all_strategies=[],
            )
        )


def test_dashboard_handles_market_data_errors_gracefully() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FailingTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    original_bot_state = getattr(app.state, "bot_state", None)
    if hasattr(app.state, "settings"):
        app.state.bot_state = main_module.build_bot_state(app.state.settings)

    try:
        with TestClient(app) as client:
            dashboard_response = client.get("/")
            assert dashboard_response.status_code == 200
            assert "Market Data Error" in dashboard_response.text
            assert (
                "network timeout" in dashboard_response.text
                or "frontend can load while the terminal reconnects" in dashboard_response.text
            )

            status_response = client.get("/api/status")
            assert status_response.status_code == 200
            assert status_response.json()["snapshot"] is None
            assert (
                "network timeout" in status_response.json()["snapshot_error"]
                or "frontend can load while the terminal reconnects" in status_response.json()["snapshot_error"]
            )
    finally:
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        app.dependency_overrides.clear()


def test_status_skips_mt5_fanout_after_snapshot_failure() -> None:
    fake_service = FailingSnapshotMt5TradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    original_settings = app.state.settings
    original_bot_state = getattr(app.state, "bot_state", None)
    app.state.settings = original_settings.model_copy(
        update={
            "execution_provider": "MT5",
            "market_data_provider": "MT5",
            "mt5_symbol": "GBPUSDm",
            "mt5_symbols": "EURUSDm,GBPUSDm,XAUUSDm",
        }
    )
    app.state.bot_state = main_module.build_bot_state(app.state.settings)

    try:
        with TestClient(app) as client:
            response = client.get("/api/status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["snapshot"] is None
        assert (
            "IPC timeout" in payload["snapshot_error"]
            or "frontend can load while the terminal reconnects" in payload["snapshot_error"]
        )
        assert payload["mt5_active_positions"] is None
        assert payload["mt5_symbol_readiness"] == []
        assert fake_service.active_positions_count_calls == 0
        assert fake_service.readiness_calls == 0
    finally:
        app.state.settings = original_settings
        if original_bot_state is not None:
            app.state.bot_state = original_bot_state
        app.dependency_overrides.clear()


def test_professional_analysis_endpoint_returns_structured_report() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeProAnalysisTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    analysis_symbol = app.state.settings.effective_mt5_symbols[0] if hasattr(app.state, "settings") else get_settings().effective_mt5_symbols[0]

    with TestClient(app) as client:
        response = client.post(
            "/api/analysis/pro",
            json={
                "symbols": [analysis_symbol],
                "account_size": 25000,
                "risk_tolerance": "MEDIUM",
                "trading_style": "DAY TRADING",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbols"] == [analysis_symbol]
        assert payload["reports"][0]["trade_ideas"][0]["risk_to_reward_tp2"] >= 2.0
        assert "Market Overview" in payload["reports"][0]["formatted_report"]

    app.dependency_overrides.clear()


def test_professional_analysis_execute_endpoint_returns_trade_payload() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        response = client.post(
            "/api/analysis/pro/execute",
            json={
                "symbol": "GBPUSDm",
                "account_size": 25000,
                "risk_tolerance": "MEDIUM",
                "trading_style": "SCALPING",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_symbol"] == "GBPUSDm"
        assert payload["requested_trading_style"] == "SCALPING"
        assert payload["trade"]["side"] == "BUY"
        assert payload["trade"]["status"] == "FILLED"

    app.dependency_overrides.clear()


def test_backtest_endpoint_returns_metrics() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        response = client.post("/api/backtest/run?history_limit=500&initial_balance=2000")
        assert response.status_code == 200
        payload = response.json()
        assert payload["candles"] == 500
        assert payload["initial_balance"] == 2000
        assert payload["roi_pct"] == 4.2

    app.dependency_overrides.clear()


def test_write_endpoint_requires_control_key_when_configured() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        original_key = app.state.settings.control_api_key
        try:
            app.state.settings.control_api_key = "top-secret"

            unauthorized = client.post("/api/bot/run")
            assert unauthorized.status_code == 401

            authorized = client.post("/api/bot/run", headers={"X-Control-Key": "top-secret"})
            assert authorized.status_code == 200
        finally:
            app.state.settings.control_api_key = original_key

    app.dependency_overrides.clear()


def test_backtest_mt5_batch_endpoint_returns_results() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        response = client.post("/api/backtest/run-mt5-batch?symbols=EURUSD,GBPUSD&history_limit=400")
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbols"] == ["EURUSD", "GBPUSD"]
        assert len(payload["results"]) == 2
        assert payload["errors"] == {}
        assert payload["results"][0]["candles"] == 400

    app.dependency_overrides.clear()


def test_metrics_and_reconciliation_status_endpoints() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        assert "bot_cycles_total" in metrics_response.text
        assert "bot_reconciliation_runs_total" in metrics_response.text
        assert "bot_hedge_cooldown_blocks_total" in metrics_response.text
        assert "bot_hedge_max_attempt_blocks_total" in metrics_response.text
        assert "bot_hedge_min_delta_blocks_total" in metrics_response.text

        reconciliation_response = client.get("/api/reconciliation/status")
        assert reconciliation_response.status_code == 200
        payload = reconciliation_response.json()
        assert "last_run" in payload
        assert "last_error" in payload
        assert "last_result" in payload

    app.dependency_overrides.clear()


def test_mt5_atr_recovery_toggle_endpoint_updates_symbol_state() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    original_settings = app.state.settings
    app.state.settings = original_settings.model_copy(
        update={
            "execution_provider": "MT5",
            "market_data_provider": "MT5",
            "mt5_symbol": "GBPUSDm",
            "mt5_symbols": "GBPUSDm,EURUSDm",
        }
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/settings/mt5-atr-recovery-symbol",
                json={"symbol": "EURUSDm", "enabled": True},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["updated"] == {"symbol": "EURUSDm", "enabled": True}
        items = {item["symbol"]: item["enabled"] for item in payload["items"]}
        assert items["GBPUSDm"] is True
        assert items["EURUSDm"] is True
    finally:
        app.state.settings = original_settings
        app.dependency_overrides.clear()


def test_sensitive_operator_endpoints_require_control_key_when_configured() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        original_key = app.state.settings.control_api_key
        try:
            app.state.settings.control_api_key = "top-secret"

            status_response = client.get("/api/status")
            journal_response = client.get("/api/journal/executions")
            metrics_response = client.get("/metrics")

            assert status_response.status_code == 401
            assert journal_response.status_code == 401
            assert metrics_response.status_code == 401

            headers = {"X-Control-Key": "top-secret"}
            assert client.get("/api/status", headers=headers).status_code == 200
            assert client.get("/api/journal/executions", headers=headers).status_code == 200
            assert client.get("/metrics", headers=headers).status_code == 200
        finally:
            app.state.settings.control_api_key = original_key

    app.dependency_overrides.clear()


def test_execution_journal_endpoint_forwards_filters() -> None:
    fake_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        response = client.get(
            "/api/journal/executions?limit=25&symbol=BTCUSDT&reconciliation_status=matched&since=2026-03-20T00:00:00Z"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 0
        assert payload["filters"]["limit"] == 25
        assert payload["filters"]["symbol"] == "BTCUSDT"
        assert payload["filters"]["reconciliation_status"] == "MATCHED"
        assert payload["filters"]["since"] == "2026-03-20T00:00:00+00:00"
        assert fake_service.last_journal_args is not None
        assert fake_service.last_journal_args["symbol"] == "BTCUSDT"
        assert fake_service.last_journal_args["reconciliation_status"] == "matched"
        assert fake_service.last_journal_args["since"] is not None

    app.dependency_overrides.clear()


def test_execution_journal_endpoint_rejects_invalid_since() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    with TestClient(app) as client:
        response = client.get("/api/journal/executions?since=not-a-date")
        assert response.status_code == 400
        assert "Invalid 'since' timestamp" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_mt5_profile_endpoints_support_save_test_and_activate() -> None:
    fake_profile_service = FakeMt5ProfileService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()
    original_settings = {
        "mt5_login": getattr(app.state, "settings", None).mt5_login if hasattr(app.state, "settings") else None,
        "mt5_server": getattr(app.state, "settings", None).mt5_server if hasattr(app.state, "settings") else None,
        "mt5_symbol": getattr(app.state, "settings", None).mt5_symbol if hasattr(app.state, "settings") else None,
        "mt5_symbols": getattr(app.state, "settings", None).mt5_symbols if hasattr(app.state, "settings") else None,
        "mt5_volume_lots": getattr(app.state, "settings", None).mt5_volume_lots if hasattr(app.state, "settings") else None,
    }

    payload = {
        "owner_id": "android-user-1",
        "label": "Primary account",
        "login": 12345678,
        "password": "super-secret",
        "server": "Broker-MT5Live",
        "terminal_path": "C:/MT5/terminal64.exe",
        "primary_symbol": "GBPUSD+",
        "symbols": ["EURUSD+", "XAUUSD+"],
        "volume_lots": 0.05,
        "set_active": False,
    }

    try:
        with TestClient(app) as client:
            list_response = client.get("/api/mt5/profiles", headers={"X-Owner-Id": "android-user-1"})
            assert list_response.status_code == 200
            assert list_response.json()["items"] == []

            test_response = client.post("/api/mt5/profiles/test", headers={"X-Owner-Id": "android-user-1"}, json=payload)
            assert test_response.status_code == 200
            assert test_response.json()["ok"] is True
            assert test_response.json()["account"]["equity"] == 12000.0

            create_response = client.post("/api/mt5/profiles", headers={"X-Owner-Id": "android-user-1"}, json=payload)
            assert create_response.status_code == 200
            create_payload = create_response.json()
            assert create_payload["item"]["password_mask"] == "********"
            assert create_payload["items"][0]["server"] == "Broker-MT5Live"
            assert create_payload["item"]["primary_symbol"] == "GBPUSD+"
            assert create_payload["item"]["symbols"][0] == "GBPUSD+"

            saved_test_response = client.post(
                "/api/mt5/profiles/1/test",
                headers={"X-Owner-Id": "android-user-1"},
                json={"owner_id": "android-user-1"},
            )
            assert saved_test_response.status_code == 200
            assert saved_test_response.json()["ok"] is True
            assert saved_test_response.json()["profile"]["last_connection_ok"] is True

            activate_response = client.post(
                "/api/mt5/profiles/1/activate",
                headers={"X-Owner-Id": "android-user-1"},
                json={"owner_id": "android-user-1"},
            )
            assert activate_response.status_code == 200
            activate_payload = activate_response.json()
            assert activate_payload["item"]["is_active"] is True
            assert activate_payload["items"][0]["is_active"] is True
            assert activate_payload["active_profile"] is None
            assert fake_profile_service.last_apply_args is None
            if original_settings["mt5_login"] is not None:
                assert app.state.settings.mt5_login == original_settings["mt5_login"]
            if original_settings["mt5_server"] is not None:
                assert app.state.settings.mt5_server == original_settings["mt5_server"]
    finally:
        if hasattr(app.state, "settings"):
            for key, value in original_settings.items():
                if value is not None:
                    setattr(app.state.settings, key, value)
        app.dependency_overrides.clear()


def test_mt5_profile_preview_endpoint_returns_snapshot_for_draft_symbol() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_trading_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    payload = {
        "owner_id": "android-user-1",
        "label": "Draft account",
        "login": 12345678,
        "password": "super-secret",
        "server": "Broker-MT5Live",
        "terminal_path": "C:/MT5/terminal64.exe",
        "primary_symbol": "XAUUSDz",
        "symbols": ["EURUSDz", "GBPUSDz"],
        "volume_lots": 0.05,
        "set_active": False,
    }

    try:
        with TestClient(app) as client:
            response = client.post("/api/mt5/profiles/preview", headers={"X-Owner-Id": "android-user-1"}, json=payload)
            assert response.status_code == 200
            preview = response.json()
            assert preview["preview_profile"]["primary_symbol"] == "XAUUSDz"
            assert preview["snapshot"]["market_data_symbol"] == "XAUUSDz"
            assert fake_trading_service.last_preview_settings is not None
            assert fake_trading_service.last_preview_settings.mt5_symbol == "XAUUSDz"
    finally:
        app.dependency_overrides.clear()


def test_saved_mt5_profile_preview_endpoint_returns_snapshot_for_profile_symbol() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Operator account",
            "login": 87654321,
            "server": "Broker-Operator",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSD+",
            "symbols": ["GBPUSD+", "EURUSD+"],
            "volume_lots": 0.10,
            "is_active": False,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": "2026-04-03T01:00:00+00:00",
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T01:00:00+00:00",
        }
    ]
    fake_trading_service = FakeTradingService()
    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/mt5/profiles/1/preview",
                headers={"X-Owner-Id": "local"},
                json={"owner_id": "local"},
            )
            assert response.status_code == 200
            preview = response.json()
            assert preview["profile"]["primary_symbol"] == "GBPUSD+"
            assert preview["snapshot"]["market_data_symbol"] == "GBPUSD+"
            assert fake_trading_service.last_preview_settings is not None
            assert fake_trading_service.last_preview_settings.mt5_symbol == "GBPUSD+"
    finally:
        app.dependency_overrides.clear()


def test_mt5_profile_endpoints_require_owner_header_and_scope_results() -> None:
    fake_profile_service = FakeMt5ProfileService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    payload_one = {
        "owner_id": "android-user-1",
        "label": "Owner one",
        "login": 111,
        "password": "secret-1",
        "server": "Broker-One",
        "terminal_path": "C:/MT5/terminal64.exe",
        "primary_symbol": "EURUSDm",
        "symbols": ["EURUSD"],
        "volume_lots": 0.01,
        "set_active": False,
    }
    payload_two = {
        "owner_id": "android-user-2",
        "label": "Owner two",
        "login": 222,
        "password": "secret-2",
        "server": "Broker-Two",
        "terminal_path": "C:/MT5/terminal64.exe",
        "symbols": ["GBPUSD"],
        "volume_lots": 0.02,
        "set_active": False,
    }

    try:
        with TestClient(app) as client:
            missing_owner = client.post("/api/mt5/profiles", json=payload_one)
            assert missing_owner.status_code == 400
            assert "X-Owner-Id" in missing_owner.json()["detail"]

            create_one = client.post("/api/mt5/profiles", headers={"X-Owner-Id": "android-user-1"}, json=payload_one)
            assert create_one.status_code == 200
            create_two = client.post("/api/mt5/profiles", headers={"X-Owner-Id": "android-user-2"}, json=payload_two)
            assert create_two.status_code == 200

            list_one = client.get("/api/mt5/profiles", headers={"X-Owner-Id": "android-user-1"})
            assert list_one.status_code == 200
            assert len(list_one.json()["items"]) == 1
            assert list_one.json()["items"][0]["owner_id"] == "android-user-1"

            wrong_owner_activate = client.post(
                "/api/mt5/profiles/1/activate",
                headers={"X-Owner-Id": "android-user-2"},
                json={"owner_id": "android-user-2"},
            )
            assert wrong_owner_activate.status_code == 400

            mismatch_owner = client.post(
                "/api/mt5/profiles/1/test",
                headers={"X-Owner-Id": "android-user-1"},
                json={"owner_id": "android-user-2"},
            )
            assert mismatch_owner.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_mt5_profile_delete_endpoint_removes_inactive_profile() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Active profile",
            "login": 111111,
            "server": "Broker-One",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "EURUSDm",
            "symbols": ["EURUSDm"],
            "volume_lots": 0.01,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        },
        {
            "id": 2,
            "owner_id": "local",
            "label": "Old profile",
            "login": 222222,
            "server": "Broker-Two",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSDm",
            "symbols": ["GBPUSDm"],
            "volume_lots": 0.01,
            "is_active": False,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": None,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        },
    ]
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    try:
        with TestClient(app) as client:
            response = client.request(
                "DELETE",
                "/api/mt5/profiles/2",
                headers={"X-Owner-Id": "local", "Content-Type": "application/json"},
                json={"owner_id": "local"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["deleted_id"] == 2
            assert len(payload["items"]) == 1
            assert payload["items"][0]["id"] == 1
    finally:
        app.dependency_overrides.clear()


def test_mt5_profile_delete_endpoint_rejects_active_profile() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Active profile",
            "login": 111111,
            "server": "Broker-One",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "EURUSDm",
            "symbols": ["EURUSDm"],
            "volume_lots": 0.01,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        }
    ]
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    try:
        with TestClient(app) as client:
            response = client.request(
                "DELETE",
                "/api/mt5/profiles/1",
                headers={"X-Owner-Id": "local", "Content-Type": "application/json"},
                json={"owner_id": "local"},
            )
            assert response.status_code == 400
            assert "Activate another profile first" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_mobile_mt5_profile_contract_returns_owner_scoped_bootstrap() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_mobile_auth_service = FakeMobileAuthService()
    fake_mobile_auth_service.users = {
        "android-user-1@example.com": {
            "owner_id": "android-user-1",
            "email": "android-user-1@example.com",
            "display_name": "Android One",
            "is_active": True,
            "created_at": "2026-04-03T00:00:00+00:00",
        }
    }
    fake_mobile_auth_service.sessions = {
        "session-1": {
            "owner_id": "android-user-1",
            "device_name": "Pixel",
            "expires_at": "2026-05-03T00:00:00+00:00",
            "revoked_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "token": "session-1",
        }
    }
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "android-user-1",
            "label": "Primary account",
            "login": 12345678,
            "server": "Broker-MT5Live",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSD+",
            "symbols": ["GBPUSD+", "EURUSD+"],
            "volume_lots": 0.05,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": "2026-04-03T01:00:00+00:00",
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T01:00:00+00:00",
        },
        {
            "id": 2,
            "owner_id": "android-user-2",
            "label": "Other owner",
            "login": 87654321,
            "server": "Broker-Other",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "USDJPYm",
            "symbols": ["USDJPY"],
            "volume_lots": 0.02,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": "2026-04-03T01:00:00+00:00",
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T01:00:00+00:00",
        },
    ]
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service

    try:
        with TestClient(app) as client:
            response = client.get("/api/mobile/bootstrap", headers={"X-Session-Token": "session-1"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["auth"]["owner_id"] == "android-user-1"
            assert payload["owner_id"] == "android-user-1"
            assert payload["runtime_source"] == "active_profile"
            assert payload["active_profile"]["owner_id"] == "android-user-1"
            assert payload["active_profile"]["primary_symbol"] == "GBPUSD+"
            assert payload["mt5_runtime"]["symbol"] == "GBPUSD+"
            assert len(payload["profiles"]) == 1
            assert payload["profiles"][0]["server"] == "Broker-MT5Live"
    finally:
        app.dependency_overrides.clear()


def test_mobile_auth_and_profile_endpoints_require_session_token() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_mobile_auth_service = FakeMobileAuthService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service

    register_payload = {
        "email": "mobile-user@example.com",
        "password": "super-secret-123",
        "display_name": "Mobile User",
        "device_name": "Pixel 9",
    }
    profile_payload = {
        "label": "Primary mobile account",
        "login": 12345678,
        "password": "mt5-secret",
        "server": "Broker-MT5Live",
        "terminal_path": "C:/MT5/terminal64.exe",
        "primary_symbol": "GBPUSDz",
        "symbols": ["EURUSDz", "XAUUSDz"],
        "volume_lots": 0.05,
        "set_active": True,
    }

    try:
        with TestClient(app) as client:
            register_response = client.post("/api/mobile/auth/register", json=register_payload)
            assert register_response.status_code == 200
            register_result = register_response.json()
            token = register_result["session"]["token"]
            assert register_result["user"]["owner_id"] == "mobile-user"

            session_response = client.get("/api/mobile/auth/session", headers={"X-Session-Token": token})
            assert session_response.status_code == 200
            assert session_response.json()["owner_id"] == "mobile-user"

            missing_token_response = client.get("/api/mobile/bootstrap")
            assert missing_token_response.status_code == 401

            create_profile_response = client.post(
                "/api/mobile/mt5/profiles",
                headers={"X-Session-Token": token},
                json=profile_payload,
            )
            assert create_profile_response.status_code == 200
            create_payload = create_profile_response.json()
            assert create_payload["owner_id"] == "mobile-user"
            assert create_payload["active_profile"]["owner_id"] == "mobile-user"
            assert create_payload["mt5_runtime"]["symbol"] == "GBPUSDz"
            assert getattr(app.state.settings, "mt5_login", 0) != 12345678

            logout_response = client.post("/api/mobile/auth/logout", headers={"X-Session-Token": token})
            assert logout_response.status_code == 200
            assert logout_response.json()["revoked"] is True

            revoked_session_response = client.get("/api/mobile/auth/session", headers={"X-Session-Token": token})
            assert revoked_session_response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_mobile_mt5_snapshot_uses_owner_active_primary_symbol() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_trading_service = FakeTradingService()
    fake_mobile_auth_service = FakeMobileAuthService()
    fake_mobile_auth_service.users = {
        "mobile-user@example.com": {
            "owner_id": "mobile-user",
            "email": "mobile-user@example.com",
            "display_name": "Mobile User",
            "is_active": True,
            "created_at": "2026-04-03T00:00:00+00:00",
        }
    }
    fake_mobile_auth_service.sessions = {
        "session-mobile": {
            "owner_id": "mobile-user",
            "device_name": "Android",
            "expires_at": "2026-05-03T00:00:00+00:00",
            "revoked_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "token": "session-mobile",
        }
    }
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "mobile-user",
            "label": "Primary mobile account",
            "login": 12345678,
            "server": "Broker-MT5Live",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSD+",
            "symbols": ["GBPUSD+", "EURUSD+", "XAUUSD+"],
            "volume_lots": 0.05,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": "2026-04-03T01:00:00+00:00",
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T01:00:00+00:00",
        }
    ]

    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service

    try:
        with TestClient(app) as client:
            response = client.get("/api/mobile/mt5/snapshot", headers={"X-Session-Token": "session-mobile"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["runtime_source"] == "active_profile"
            assert payload["active_profile"]["primary_symbol"] == "GBPUSD+"
            assert payload["mt5_runtime"]["symbol"] == "GBPUSD+"
            assert payload["snapshot"]["market_data_symbol"] == "GBPUSD+"
            assert fake_trading_service.last_preview_settings is not None
            assert fake_trading_service.last_preview_settings.mt5_symbol == "GBPUSD+"
    finally:
        app.dependency_overrides.clear()


def test_mobile_auth_rate_limit_blocks_repeated_failures() -> None:
    fake_mobile_auth_service = FakeMobileAuthService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service

    original_max_attempts = app.state.settings.mobile_auth_rate_limit_max_attempts
    original_window = app.state.settings.mobile_auth_rate_limit_window_seconds
    original_limiter = app.state.auth_rate_limiter
    try:
        app.state.settings.mobile_auth_rate_limit_max_attempts = 2
        app.state.settings.mobile_auth_rate_limit_window_seconds = 60
        app.state.auth_rate_limiter = AuthRateLimiter(max_attempts=2, window_seconds=60)
        db = SessionLocal()
        try:
            for row in db.query(AuthRateLimitEntry).all():
                db.delete(row)
            db.commit()
        finally:
            db.close()
        with TestClient(app) as client:
            payload = {"email": "missing@example.com", "password": "wrong-password"}
            assert client.post("/api/mobile/auth/login", json=payload).status_code == 401
            assert client.post("/api/mobile/auth/login", json=payload).status_code == 401
            blocked = client.post("/api/mobile/auth/login", json=payload)
            assert blocked.status_code == 429
    finally:
        db = SessionLocal()
        try:
            for row in db.query(AuthRateLimitEntry).all():
                db.delete(row)
            db.commit()
        finally:
            db.close()
        app.state.settings.mobile_auth_rate_limit_max_attempts = original_max_attempts
        app.state.settings.mobile_auth_rate_limit_window_seconds = original_window
        app.state.auth_rate_limiter = original_limiter
        app.dependency_overrides.clear()


def test_mobile_auth_rate_limit_persists_entries_in_database() -> None:
    fake_mobile_auth_service = FakeMobileAuthService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
    app.dependency_overrides[get_mobile_auth_service] = lambda: fake_mobile_auth_service

    try:
        db = SessionLocal()
        try:
            for row in db.query(AuthRateLimitEntry).all():
                db.delete(row)
            db.commit()
        finally:
            db.close()
        with TestClient(app) as client:
            payload = {"email": "missing@example.com", "password": "wrong-password"}
            first = client.post("/api/mobile/auth/login", json=payload)
            assert first.status_code == 401

        db = SessionLocal()
        try:
            rows = db.query(AuthRateLimitEntry).all()
            assert len(rows) == 1
            assert rows[0].key.endswith("missing@example.com")
            assert rows[0].attempts == 1
        finally:
            for row in db.query(AuthRateLimitEntry).all():
                db.delete(row)
            db.commit()
            db.close()
    finally:
        app.dependency_overrides.clear()



def test_create_app_applies_cors_and_trusted_host_settings() -> None:
    original_env = {
        "CORS_ALLOWED_ORIGINS": os.environ.get("CORS_ALLOWED_ORIGINS"),
        "TRUSTED_HOSTS": os.environ.get("TRUSTED_HOSTS"),
    }

    try:
        os.environ["CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        os.environ["TRUSTED_HOSTS"] = "testserver,app.example.com"
        get_settings.cache_clear()
        configured_app = create_app()
        configured_app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
        configured_app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
        configured_app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

        with TestClient(configured_app) as client:
            preflight = client.options(
                "/api/mobile/auth/login",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "https://app.example.com"

            bad_host = client.get("/health", headers={"host": "evil.example.com"})
            assert bad_host.status_code == 400
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_create_app_skips_mt5_startup_reconciliation_when_live_trading_disarmed(monkeypatch) -> None:
    class FakeStartupTradingService(FakeTradingService):
        hydrate_calls = 0

        def __init__(self, settings) -> None:
            super().__init__()
            self.settings = settings

        async def hydrate_authoritative_state(self, db) -> dict:
            type(self).hydrate_calls += 1
            return {"status": "ok"}

    original_env = {
        "EXECUTION_PROVIDER": os.environ.get("EXECUTION_PROVIDER"),
        "DRY_RUN": os.environ.get("DRY_RUN"),
        "LIVE_TRADING_ARMED": os.environ.get("LIVE_TRADING_ARMED"),
        "MT5_LOGIN": os.environ.get("MT5_LOGIN"),
        "MT5_PASSWORD": os.environ.get("MT5_PASSWORD"),
        "MT5_SERVER": os.environ.get("MT5_SERVER"),
        "RECONCILIATION_ENABLED": os.environ.get("RECONCILIATION_ENABLED"),
        "AUTO_TRADING_ENABLED": os.environ.get("AUTO_TRADING_ENABLED"),
        "STARTUP_SELF_CHECK_REQUIRED": os.environ.get("STARTUP_SELF_CHECK_REQUIRED"),
        "MT5_EAGER_STARTUP_CHECKS_ENABLED": os.environ.get("MT5_EAGER_STARTUP_CHECKS_ENABLED"),
    }

    try:
        os.environ["EXECUTION_PROVIDER"] = "MT5"
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_TRADING_ARMED"] = "false"
        os.environ["MT5_LOGIN"] = "12345678"
        os.environ["MT5_PASSWORD"] = "secret"
        os.environ["MT5_SERVER"] = "Broker-MT5Live"
        os.environ["RECONCILIATION_ENABLED"] = "true"
        os.environ["AUTO_TRADING_ENABLED"] = "false"
        os.environ["STARTUP_SELF_CHECK_REQUIRED"] = "false"
        os.environ["MT5_EAGER_STARTUP_CHECKS_ENABLED"] = "true"

        FakeStartupTradingService.hydrate_calls = 0
        monkeypatch.setattr(main_module, "TradingService", FakeStartupTradingService)
        get_settings.cache_clear()
        configured_app = create_app()
        configured_app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
        configured_app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

        with TestClient(configured_app) as client:
            response = client.get("/health")
            assert response.status_code == 200

        assert FakeStartupTradingService.hydrate_calls == 0
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_create_app_survives_invalid_mt5_runtime_credentials_on_startup(monkeypatch) -> None:
    class FailingStartupTradingService(FakeTradingService):
        self_check_calls = 0
        hydrate_calls = 0

        def __init__(self, settings) -> None:
            super().__init__()
            self.settings = settings

        async def run_startup_self_check(self, db) -> dict:
            type(self).self_check_calls += 1
            raise RuntimeError("MT5 initialize failed (-6): authorization failed")

        async def hydrate_authoritative_state(self, db) -> dict:
            type(self).hydrate_calls += 1
            raise RuntimeError("MT5 account info failed (-6): authorization failed")

    original_env = {
        "EXECUTION_PROVIDER": os.environ.get("EXECUTION_PROVIDER"),
        "MARKET_DATA_PROVIDER": os.environ.get("MARKET_DATA_PROVIDER"),
        "DRY_RUN": os.environ.get("DRY_RUN"),
        "LIVE_TRADING_ARMED": os.environ.get("LIVE_TRADING_ARMED"),
        "MT5_LOGIN": os.environ.get("MT5_LOGIN"),
        "MT5_PASSWORD": os.environ.get("MT5_PASSWORD"),
        "MT5_SERVER": os.environ.get("MT5_SERVER"),
        "RECONCILIATION_ENABLED": os.environ.get("RECONCILIATION_ENABLED"),
        "AUTO_TRADING_ENABLED": os.environ.get("AUTO_TRADING_ENABLED"),
        "STARTUP_SELF_CHECK_REQUIRED": os.environ.get("STARTUP_SELF_CHECK_REQUIRED"),
        "MT5_EAGER_STARTUP_CHECKS_ENABLED": os.environ.get("MT5_EAGER_STARTUP_CHECKS_ENABLED"),
    }

    try:
        os.environ["EXECUTION_PROVIDER"] = "MT5"
        os.environ["MARKET_DATA_PROVIDER"] = "MT5"
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_TRADING_ARMED"] = "true"
        os.environ["MT5_LOGIN"] = "12345678"
        os.environ["MT5_PASSWORD"] = "wrong-secret"
        os.environ["MT5_SERVER"] = "Broker-MT5Live"
        os.environ["RECONCILIATION_ENABLED"] = "true"
        os.environ["AUTO_TRADING_ENABLED"] = "true"
        os.environ["STARTUP_SELF_CHECK_REQUIRED"] = "true"
        os.environ["MT5_EAGER_STARTUP_CHECKS_ENABLED"] = "true"

        FailingStartupTradingService.self_check_calls = 0
        FailingStartupTradingService.hydrate_calls = 0
        monkeypatch.setattr(main_module, "TradingService", FailingStartupTradingService)
        get_settings.cache_clear()
        configured_app = create_app()
        configured_app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
        configured_app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

        with TestClient(configured_app) as client:
            response = client.get("/health")
            assert response.status_code == 200

        assert FailingStartupTradingService.self_check_calls == 1
        assert FailingStartupTradingService.hydrate_calls == 1
        assert configured_app.state.bot_task is None
        assert configured_app.state.reconcile_task is None
        assert configured_app.state.bot_state["startup_self_check"]["status"] == "failed"
        assert configured_app.state.bot_state["startup_reconciliation"]["status"] == "failed"
        assert "authorization failed" in configured_app.state.bot_state["last_error"].lower()
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_create_app_defers_eager_mt5_startup_checks_by_default(monkeypatch) -> None:
    class HangingStartupTradingService(FakeTradingService):
        self_check_calls = 0
        hydrate_calls = 0

        def __init__(self, settings) -> None:
            super().__init__()
            self.settings = settings

        async def run_startup_self_check(self, db) -> dict:
            type(self).self_check_calls += 1
            raise AssertionError("startup self check should be deferred")

        async def hydrate_authoritative_state(self, db) -> dict:
            type(self).hydrate_calls += 1
            raise AssertionError("startup reconciliation should be deferred")

    original_env = {
        "EXECUTION_PROVIDER": os.environ.get("EXECUTION_PROVIDER"),
        "MARKET_DATA_PROVIDER": os.environ.get("MARKET_DATA_PROVIDER"),
        "DRY_RUN": os.environ.get("DRY_RUN"),
        "LIVE_TRADING_ARMED": os.environ.get("LIVE_TRADING_ARMED"),
        "MT5_LOGIN": os.environ.get("MT5_LOGIN"),
        "MT5_PASSWORD": os.environ.get("MT5_PASSWORD"),
        "MT5_SERVER": os.environ.get("MT5_SERVER"),
        "RECONCILIATION_ENABLED": os.environ.get("RECONCILIATION_ENABLED"),
        "AUTO_TRADING_ENABLED": os.environ.get("AUTO_TRADING_ENABLED"),
        "STARTUP_SELF_CHECK_REQUIRED": os.environ.get("STARTUP_SELF_CHECK_REQUIRED"),
        "MT5_EAGER_STARTUP_CHECKS_ENABLED": os.environ.get("MT5_EAGER_STARTUP_CHECKS_ENABLED"),
    }

    try:
        os.environ["EXECUTION_PROVIDER"] = "MT5"
        os.environ["MARKET_DATA_PROVIDER"] = "MT5"
        os.environ["DRY_RUN"] = "false"
        os.environ["LIVE_TRADING_ARMED"] = "true"
        os.environ["MT5_LOGIN"] = "12345678"
        os.environ["MT5_PASSWORD"] = "secret"
        os.environ["MT5_SERVER"] = "Broker-MT5Live"
        os.environ["RECONCILIATION_ENABLED"] = "true"
        os.environ["AUTO_TRADING_ENABLED"] = "true"
        os.environ["STARTUP_SELF_CHECK_REQUIRED"] = "true"
        os.environ.pop("MT5_EAGER_STARTUP_CHECKS_ENABLED", None)

        HangingStartupTradingService.self_check_calls = 0
        HangingStartupTradingService.hydrate_calls = 0
        monkeypatch.setattr(main_module, "TradingService", HangingStartupTradingService)
        get_settings.cache_clear()
        configured_app = create_app()
        configured_app.dependency_overrides[get_mt5_profile_service] = lambda: FakeMt5ProfileService()
        configured_app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

        with TestClient(configured_app) as client:
            response = client.get("/health")
            assert response.status_code == 200

        assert HangingStartupTradingService.self_check_calls == 0
        assert HangingStartupTradingService.hydrate_calls == 0
        assert configured_app.state.bot_task is None
        assert configured_app.state.reconcile_task is None
        assert configured_app.state.bot_state["startup_self_check"]["reason"] == "mt5_startup_checks_deferred"
        assert configured_app.state.bot_state["startup_reconciliation"]["reason"] == "mt5_startup_checks_deferred"
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_ml_endpoints_status_train_predict() -> None:
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()

    with TestClient(app) as client:
        status_response = client.get("/api/ml/status")
        assert status_response.status_code == 200
        assert status_response.json()["ready"] is True

        train_response = client.post(
            "/api/ml/train",
            json={"min_samples": 20, "epochs": 100, "learning_rate": 0.1, "validation_size": 0.2, "random_state": 42},
        )
        assert train_response.status_code == 200
        assert train_response.json()["trained"] is True

        predict_response = client.post(
            "/api/ml/predict",
            json={
                "rsi_norm": 0.4,
                "confidence": 0.8,
                "momentum_5": 0.01,
                "momentum_20": 0.03,
                "volatility_20": 0.02,
                "atr_pct": 0.015,
                "strategy_count_norm": 0.5,
            },
        )
        assert predict_response.status_code == 200
        payload = predict_response.json()
        assert payload["model_ready"] is True
        assert payload["action"] == "BUY"

    app.dependency_overrides.clear()


def test_mt5_profile_runtime_owner_activation_updates_process_runtime() -> None:
    fake_profile_service = FakeMt5ProfileService()
    app.dependency_overrides[get_trading_service] = lambda: FakeTradingService()
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    original_settings = {
        "mt5_runtime_owner_id": getattr(app.state, "settings", None).mt5_runtime_owner_id if hasattr(app.state, "settings") else None,
        "mt5_login": getattr(app.state, "settings", None).mt5_login if hasattr(app.state, "settings") else None,
        "mt5_server": getattr(app.state, "settings", None).mt5_server if hasattr(app.state, "settings") else None,
        "mt5_symbol": getattr(app.state, "settings", None).mt5_symbol if hasattr(app.state, "settings") else None,
        "mt5_symbols": getattr(app.state, "settings", None).mt5_symbols if hasattr(app.state, "settings") else None,
        "mt5_volume_lots": getattr(app.state, "settings", None).mt5_volume_lots if hasattr(app.state, "settings") else None,
        "bot_state": getattr(app.state, "bot_state", None),
    }

    payload = {
        "owner_id": "local",
        "label": "Operator account",
        "login": 87654321,
        "password": "super-secret",
        "server": "Broker-Operator",
        "terminal_path": "C:/MT5/terminal64.exe",
        "primary_symbol": "XAUUSDm",
        "symbols": ["XAUUSD", "EURUSDm"],
        "volume_lots": 0.10,
        "set_active": False,
    }

    try:
        with TestClient(app) as client:
            app.state.settings.mt5_runtime_owner_id = "local"
            app.state.bot_state = {
                **main_module.build_bot_state(app.state.settings),
                "startup_self_check": {"status": "skipped", "reason": "mt5_startup_checks_deferred", "error": "deferred"},
                "startup_reconciliation": {"status": "skipped", "reason": "mt5_startup_checks_deferred", "error": "deferred"},
                "last_error": "deferred",
            }

            create_response = client.post("/api/mt5/profiles", headers={"X-Owner-Id": "local"}, json=payload)
            assert create_response.status_code == 200

            activate_response = client.post(
                "/api/mt5/profiles/1/activate",
                headers={"X-Owner-Id": "local"},
                json={"owner_id": "local"},
            )
            assert activate_response.status_code == 200
            activate_payload = activate_response.json()
            assert activate_payload["active_profile"]["server"] == "Broker-Operator"
            assert activate_payload["snapshot"]["market_data_symbol"] == "XAUUSDm"
            assert activate_payload["snapshot"]["market_data_provider"] == "mt5"
            assert fake_profile_service.last_apply_args is not None
            assert fake_profile_service.last_apply_args["owner_id"] == "local"
            assert app.state.settings.mt5_login == 87654321
            assert app.state.settings.mt5_server == "Broker-Operator"
            assert app.state.settings.mt5_symbol == "XAUUSDm"
            assert app.state.bot_state["startup_self_check"]["reason"] == "runtime_profile_activated"
            assert app.state.bot_state["startup_reconciliation"]["reason"] == "runtime_profile_activated"
            assert app.state.bot_state["last_error"] is None
    finally:
        if hasattr(app.state, "settings"):
            for key, value in original_settings.items():
                if key == "bot_state":
                    if value is not None:
                        app.state.bot_state = value
                elif value is not None:
                    setattr(app.state.settings, key, value)
        app.dependency_overrides.clear()


def test_mt5_profile_runtime_owner_activation_failure_preserves_existing_runtime() -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Healthy runtime",
            "login": 87654321,
            "server": "Broker-Good",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "EURUSDm",
            "symbols": ["EURUSDm"],
            "volume_lots": 0.10,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": "2026-04-03T01:00:00+00:00",
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T01:00:00+00:00",
        },
        {
            "id": 2,
            "owner_id": "local",
            "label": "Broken runtime",
            "login": 99999999,
            "server": "Broker-Bad",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSDm",
            "symbols": ["GBPUSDm"],
            "volume_lots": 0.05,
            "is_active": False,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": None,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        },
    ]
    fake_profile_service.runtime_activation_error = "MT5 profile connection test failed: invalid account credentials"

    class FakeStartupTradingService(FakeTradingService):
        def __init__(self, settings) -> None:
            super().__init__()
            self.settings = settings

    monkeypatch_trading = None
    monkeypatch_profile = None

    try:
        monkeypatch_trading = FakeStartupTradingService
        monkeypatch_profile = lambda settings: fake_profile_service
        original_trading_service = main_module.TradingService
        original_mt5_profile_service = main_module.Mt5ProfileService
        main_module.TradingService = monkeypatch_trading
        main_module.Mt5ProfileService = monkeypatch_profile
        get_settings.cache_clear()
        configured_app = create_app()

        with TestClient(configured_app) as client:
            configured_app.state.settings.mt5_runtime_owner_id = "local"
            configured_app.state.settings.mt5_login = 87654321
            configured_app.state.settings.mt5_server = "Broker-Good"
            configured_app.state.settings.mt5_symbol = "EURUSDm"
            configured_app.state.settings.mt5_symbols = "EURUSDm"
            configured_app.state.settings.mt5_volume_lots = 0.10

            activate_response = client.post(
                "/api/mt5/profiles/2/activate",
                headers={"X-Owner-Id": "local"},
                json={"owner_id": "local"},
            )

            assert activate_response.status_code == 400
            assert "connection test failed" in activate_response.json()["detail"].lower()
            assert fake_profile_service.items[0]["is_active"] is True
            assert fake_profile_service.items[1]["is_active"] is False
            assert configured_app.state.settings.mt5_login == 87654321
            assert configured_app.state.settings.mt5_server == "Broker-Good"
            assert configured_app.state.settings.mt5_symbol == "EURUSDm"
    finally:
        if monkeypatch_trading is not None:
            main_module.TradingService = original_trading_service
        if monkeypatch_profile is not None:
            main_module.Mt5ProfileService = original_mt5_profile_service
        get_settings.cache_clear()


def test_mt5_worker_api_provisions_and_assigns_worker_for_owner() -> None:
    fake_trading_service = FakeTradingService()
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "android-user-1",
            "label": "Primary FX",
            "login": 123456,
            "server": "Broker-A",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSDm",
            "symbols": ["GBPUSDm"],
            "volume_lots": 0.01,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        }
    ]
    fake_worker_service = FakeMt5WorkerService()

    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mt5_worker_service] = lambda: fake_worker_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    try:
        with TestClient(app) as client:
            provision_response = client.post(
                "/api/mt5/workers/provision",
                headers={"X-Owner-Id": "android-user-1"},
                json={
                    "owner_id": "android-user-1",
                    "worker_key": "worker-android-1",
                    "label": "Android worker",
                    "terminal_path": "C:/MT5/terminal64.exe",
                },
            )

            assert provision_response.status_code == 200
            assert provision_response.json()["item"]["worker_key"] == "worker-android-1"
            assert provision_response.json()["item"]["status"] == "PROVISIONED"

            assign_response = client.post(
                "/api/mt5/workers/worker-android-1/assign",
                headers={"X-Owner-Id": "android-user-1"},
                json={"owner_id": "android-user-1", "profile_id": 1},
            )

            assert assign_response.status_code == 200
            assert assign_response.json()["item"]["profile_id"] == 1

            list_response = client.get("/api/mt5/workers", headers={"X-Owner-Id": "android-user-1"})
            assert list_response.status_code == 200
            assert list_response.json()["items"][0]["worker_key"] == "worker-android-1"
            assert list_response.json()["items"][0]["profile_id"] == 1
    finally:
        app.dependency_overrides.clear()


def test_dashboard_uses_selected_owner_scope_for_mt5_profiles() -> None:
    fake_trading_service = FakeTradingService()
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Local runtime",
            "login": 111111,
            "server": "Broker-Local",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "EURUSDm",
            "symbols": ["EURUSDm"],
            "volume_lots": 0.01,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        },
        {
            "id": 2,
            "owner_id": "android-user-1",
            "label": "Android scoped",
            "login": 222222,
            "server": "Broker-Android",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "GBPUSDm",
            "symbols": ["GBPUSDm"],
            "volume_lots": 0.02,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": True,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        },
    ]
    fake_worker_service = FakeMt5WorkerService()
    fake_worker_service.provision_worker(None, "worker-android-1", "android-user-1", profile_id=2, label="Android worker")

    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_mt5_profile_service] = lambda: fake_profile_service
    app.dependency_overrides[get_mt5_worker_service] = lambda: fake_worker_service
    app.dependency_overrides[get_mobile_auth_service] = lambda: FakeMobileAuthService()

    try:
        with TestClient(app) as client:
            response = client.get("/?owner_id=android-user-1")

            assert response.status_code == 200
            assert 'value="android-user-1"' in response.text
            assert "Android scoped" in response.text
            assert "Local runtime" not in response.text
            assert "worker-android-1" in response.text
    finally:
        app.dependency_overrides.clear()


def test_create_app_skips_invalid_saved_mt5_runtime_profile_on_startup(monkeypatch) -> None:
    fake_profile_service = FakeMt5ProfileService()
    fake_profile_service.items = [
        {
            "id": 1,
            "owner_id": "local",
            "label": "Broken startup profile",
            "login": 55555555,
            "server": "Broker-Bad",
            "terminal_path": "C:/MT5/terminal64.exe",
            "primary_symbol": "XAUUSDm",
            "symbols": ["XAUUSDm"],
            "volume_lots": 0.03,
            "is_active": True,
            "password_configured": True,
            "password_mask": "********",
            "last_connection_ok": None,
            "last_connection_error": None,
            "last_validated_at": None,
            "created_at": "2026-04-03T00:00:00+00:00",
            "updated_at": "2026-04-03T00:00:00+00:00",
        }
    ]
    fake_profile_service.startup_profile_error = "MT5 initialize failed (-6): authorization failed"

    class FakeStartupTradingService(FakeTradingService):
        def __init__(self, settings) -> None:
            super().__init__()
            self.settings = settings

    monkeypatch.setattr(main_module, "TradingService", FakeStartupTradingService)
    monkeypatch.setattr(main_module, "Mt5ProfileService", lambda settings: fake_profile_service)
    get_settings.cache_clear()

    try:
        configured_app = create_app()
        with TestClient(configured_app) as client:
            response = client.get("/health")
            assert response.status_code == 200

        assert fake_profile_service.startup_apply_calls == 1
        assert fake_profile_service.items[0]["is_active"] is False
        assert configured_app.state.bot_state["startup_self_check"]["reason"] == "invalid_mt5_runtime_profile"
        assert "authorization failed" in configured_app.state.bot_state["startup_self_check"]["error"].lower()
    finally:
        get_settings.cache_clear()
