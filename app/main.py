from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, get_settings
from app.database import SessionLocal, ensure_database_schema, get_db
from app.schemas import MLPredictRequest, MLTrainRequest, MobileAuthLoginRequest, MobileAuthRegisterRequest, Mt5ProfileActivateRequest, Mt5ProfileCreateRequest, Mt5WorkerAssignRequest, Mt5WorkerProvisionRequest, OwnerScopedRequest, ProAnalysisExecuteRequest, ProAnalysisRequest
from app.services.auth import MobileAuthService
from app.services.observability import ObservabilityStore
from app.services.mt5_profiles import Mt5ProfileService
from app.services.mt5_workers import Mt5WorkerService
from app.services.pro_analysis import ProfessionalAnalysisService
from app.services.rate_limit import AuthRateLimiter
from app.services.telegram import TelegramNotifier
from app.services.trading import TradingService


templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("beacon.bot")


def _log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, default=str))


def build_bot_state(settings: Settings) -> dict[str, Any]:
    if settings.can_place_live_orders:
        mode = "LIVE_ARMED" if settings.live_trading_enabled else "LIVE_DISARMED"
    else:
        mode = "DRY_RUN"

    return {
        "last_run": None,
        "last_result": None,
        "last_error": None,
        "mode": mode,
        "auto_trading_enabled": settings.auto_trading_enabled,
        "live_trading_armed": settings.live_trading_armed,
        "startup_self_check": None,
        "reconciliation_last_run": None,
        "reconciliation_last_error": None,
        "hedge_monitor_last_run": None,
        "hedge_monitor_last_error": None,
        "hedge_monitor_block_stats": {},
    }


def build_snapshot_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"Could not load live market data: {detail}"


def build_mt5_position_fallback(settings: Settings) -> dict[str, int | None]:
    return {
        "active_positions": None,
        "cap": max(0, int(settings.risk_mt5_max_active_positions)),
    }


def should_defer_mt5_startup_checks(settings: Settings) -> bool:
    uses_mt5 = settings.effective_execution_provider == "mt5" or settings.effective_market_data_provider == "mt5"
    return uses_mt5 and settings.live_trading_enabled and not settings.mt5_eager_startup_checks_enabled


def should_start_hedge_monitor(settings: Settings) -> bool:
    if settings.effective_execution_provider != "mt5":
        return False
    return any(
        [
            settings.atr_recovery_live_hedge_enabled,
            settings.atr_recovery_trailing_monitor_enabled,
            settings.atr_recovery_auto_reversal_close,
        ]
    )


def should_require_startup_execution_review(settings: Settings) -> bool:
    if settings.effective_execution_provider != "mt5":
        return False
    if not settings.auto_trading_enabled:
        return False
    return settings.live_trading_enabled


def build_empty_startup_execution_review() -> dict[str, Any]:
    return {
        "status": "not_required",
        "generated_at": None,
        "message": None,
        "requires_confirmation": False,
        "items": [],
        "actionable_count": 0,
        "queued_job_count": 0,
    }


def _runtime_tasks_active(app: FastAPI) -> bool:
    return any(
        [
            getattr(app.state, "bot_task", None) is not None,
            getattr(app.state, "reconcile_task", None) is not None,
            getattr(app.state, "hedge_monitor_task", None) is not None,
        ]
    )


def _start_runtime_automation(app: FastAPI) -> list[str]:
    settings: Settings = app.state.settings
    started: list[str] = []
    if app.state.bot_task is None and settings.auto_trading_enabled and (not settings.can_place_live_orders or settings.live_trading_enabled):
        app.state.bot_task = asyncio.create_task(_background_trading_loop(app))
        started.append("bot_task")
    if app.state.reconcile_task is None and settings.reconciliation_enabled and settings.live_trading_enabled:
        app.state.reconcile_task = asyncio.create_task(_background_reconciliation_loop(app))
        started.append("reconcile_task")
    if app.state.hedge_monitor_task is None and should_start_hedge_monitor(settings):
        app.state.hedge_monitor_task = asyncio.create_task(_background_hedge_monitor_loop(app))
        started.append("hedge_monitor_task")
    return started


def build_deferred_mt5_startup_state() -> dict[str, str]:
    return {
        "status": "skipped",
        "reason": "mt5_startup_checks_deferred",
        "error": "MT5 terminal checks were deferred so the frontend can load while the terminal reconnects.",
    }


def has_deferred_mt5_startup(request: Request) -> bool:
    startup_self_check = getattr(request.app.state, "bot_state", {}).get("startup_self_check") or {}
    return startup_self_check.get("reason") == "mt5_startup_checks_deferred"


def mark_mt5_runtime_ready(request: Request, profile: dict[str, Any] | None = None) -> None:
    bot_state = getattr(request.app.state, "bot_state", None)
    if not isinstance(bot_state, dict):
        return

    profile_id = None
    if isinstance(profile, dict):
        profile_id = profile.get("id")

    ready_state = {
        "status": "ready",
        "reason": "runtime_profile_activated",
        "profile_id": profile_id,
        "error": None,
    }
    bot_state["startup_self_check"] = ready_state
    bot_state["startup_reconciliation"] = ready_state.copy()
    bot_state["last_error"] = None


def serialize_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "symbol": snapshot.symbol,
        "signal_symbol": snapshot.signal_symbol,
        "execution_symbol": snapshot.execution_symbol,
        "market_data_symbol": snapshot.market_data_symbol,
        "market_data_provider": snapshot.market_data_provider,
        "latest_price": snapshot.latest_price,
        "rsi": snapshot.rsi,
        "position_quantity": snapshot.position_quantity,
        "suggested_action": snapshot.suggested_action,
        "strategy_vote_action": snapshot.strategy_vote_action,
        "confidence": snapshot.confidence,
        "regime": snapshot.regime,
        "strategies": snapshot.strategy_names,
        "atr_pct": snapshot.atr_pct,
        "atr_value": snapshot.atr_value,
        "atr_recovery_active": snapshot.atr_recovery_active,
        "atr_recovery_symbol_enabled": snapshot.atr_recovery_symbol_enabled,
        "atr_recovery_profile": snapshot.atr_recovery_profile,
    }


async def build_runtime_activation_snapshot(
    db: Session,
    trading_service: TradingService,
    settings: Settings,
) -> dict[str, Any]:
    if hasattr(trading_service, "build_snapshot_with_runtime_settings"):
        snapshot = await trading_service.build_snapshot_with_runtime_settings(
            db,
            settings,
            market_data_symbol=settings.mt5_symbol,
        )
    else:
        snapshot = await trading_service.build_snapshot(db)
    return serialize_snapshot(snapshot)


async def load_mt5_position_status(settings: Settings, trading_service: TradingService) -> dict[str, int | None]:
    cap = max(0, int(settings.risk_mt5_max_active_positions))
    if settings.effective_execution_provider != "mt5":
        return {"active_positions": None, "cap": cap}

    try:
        active = int(await trading_service.mt5.get_active_positions_count())
    except Exception:
        active = None
    return {"active_positions": active, "cap": cap}


async def load_mt5_symbol_readiness(settings: Settings, trading_service: TradingService) -> list[dict[str, Any]]:
    if settings.effective_execution_provider != "mt5":
        return []

    readiness: list[dict[str, Any]] = []
    for symbol in settings.effective_mt5_symbols:
        try:
            payload = await trading_service.mt5.check_auto_execution_ready(symbol)
            readiness.append(
                {
                    "symbol": symbol,
                    "ready": bool(payload.get("ready", False)),
                    "retcode": payload.get("retcode"),
                    "comment": payload.get("comment", ""),
                    "error": None,
                }
            )
        except Exception as exc:
            readiness.append(
                {
                    "symbol": symbol,
                    "ready": False,
                    "retcode": None,
                    "comment": "",
                    "error": str(exc),
                }
            )
    return readiness


def load_mt5_live_order_toggle_states(settings: Settings, trading_service: TradingService) -> list[dict[str, Any]]:
    if settings.effective_execution_provider != "mt5":
        return []
    if not hasattr(trading_service, "get_mt5_live_order_toggle_states"):
        return []
    return trading_service.get_mt5_live_order_toggle_states()


def load_mt5_atr_recovery_toggle_states(settings: Settings, trading_service: TradingService) -> list[dict[str, Any]]:
    if settings.effective_execution_provider != "mt5":
        return []
    if not hasattr(trading_service, "get_mt5_atr_recovery_toggle_states"):
        return []
    return trading_service.get_mt5_atr_recovery_toggle_states()


def parse_symbol_list(raw_symbols: str | None) -> list[str]:
    if not raw_symbols:
        return []
    return [item.strip() for item in raw_symbols.split(",") if item.strip()]


def _extract_client_host(request: Request) -> str:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip

    return request.client.host if request.client else ""


def enforce_write_access(
    request: Request,
    x_control_key: str | None = Header(default=None, alias="X-Control-Key"),
) -> None:
    settings: Settings = request.app.state.settings
    client_host = _extract_client_host(request)

    allowed = settings.effective_control_allowed_ips
    if "*" not in allowed and client_host not in allowed:
        raise HTTPException(status_code=403, detail="Write access denied for this client IP.")

    if settings.control_api_key_is_configured and x_control_key != settings.control_api_key:
        raise HTTPException(status_code=401, detail="Invalid control API key.")


def enforce_operator_access(
    request: Request,
    x_control_key: str | None = Header(default=None, alias="X-Control-Key"),
) -> None:
    enforce_write_access(request, x_control_key)


def _build_auth_rate_limit_key(request: Request, action: str, identifier: str | None = None) -> str:
    client_host = _extract_client_host(request) or "unknown"
    normalized_identifier = (identifier or "").strip().lower() or "anonymous"
    return f"{action}:{client_host}:{normalized_identifier}"


def _owner_uses_process_runtime(settings: Settings, owner_id: str | None) -> bool:
    return (owner_id or "").strip() == settings.mt5_runtime_owner_id


def _configure_http_middleware(app: FastAPI, settings: Settings) -> None:
    if settings.https_redirect_enabled:
        app.add_middleware(HTTPSRedirectMiddleware)

    if settings.effective_trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.effective_trusted_hosts)

    if settings.gzip_minimum_size > 0:
        app.add_middleware(GZipMiddleware, minimum_size=max(100, int(settings.gzip_minimum_size)))

    if settings.effective_cors_allowed_origins or settings.cors_allowed_origin_regex.strip():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.effective_cors_allowed_origins,
            allow_origin_regex=settings.cors_allowed_origin_regex.strip() or None,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Control-Key", "X-Owner-Id", "X-Session-Token", "X-Idempotency-Key"],
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    ensure_database_schema()
    startup_runtime_ready = True
    app.state.settings = settings
    app.state.trading_service = TradingService(settings)
    app.state.bot_state = build_bot_state(settings)
    app.state.bot_state["startup_execution_review"] = build_empty_startup_execution_review()
    app.state.bot_task = None
    app.state.reconcile_task = None
    app.state.hedge_monitor_task = None
    app.state.telegram_notifier = TelegramNotifier(settings)
    app.state.observability = ObservabilityStore()
    app.state.mt5_profile_service = Mt5ProfileService(settings)
    app.state.mt5_worker_service = Mt5WorkerService(settings)
    app.state.mobile_auth_service = MobileAuthService(
        session_days=settings.mobile_session_days,
        max_active_sessions_per_user=settings.mobile_max_active_sessions_per_user,
        revoked_retention_days=settings.mobile_session_retention_days,
    )
    app.state.auth_rate_limiter = AuthRateLimiter(
        max_attempts=settings.mobile_auth_rate_limit_max_attempts,
        window_seconds=settings.mobile_auth_rate_limit_window_seconds,
    )

    db = SessionLocal()
    try:
        runtime_owner_id = settings.mt5_runtime_owner_id
        startup_active_profile = app.state.mt5_profile_service.get_active_profile(db, owner_id=runtime_owner_id)
        app.state.active_mt5_profile = await app.state.mt5_profile_service.apply_saved_runtime_profile_if_valid(
            db,
            runtime_settings=settings,
            trading_service=app.state.trading_service,
            owner_id=runtime_owner_id,
        )
        if startup_active_profile is not None and app.state.active_mt5_profile is None:
            failed_profile = app.state.mt5_profile_service.get_profile(db, startup_active_profile.id, owner_id=runtime_owner_id)
            failed_profile_id = getattr(failed_profile, "id", None)
            failed_profile_error = getattr(failed_profile, "last_connection_error", None)
            if isinstance(failed_profile, dict):
                failed_profile_id = failed_profile.get("id")
                failed_profile_error = failed_profile.get("last_connection_error")
            app.state.bot_state["startup_self_check"] = {
                "status": "skipped",
                "reason": "invalid_mt5_runtime_profile",
                "profile_id": failed_profile_id,
                "error": failed_profile_error,
            }
            app.state.bot_state["last_error"] = failed_profile_error
            _log_event(
                "startup_mt5_profile_skipped",
                owner_id=runtime_owner_id,
                profile_id=failed_profile_id,
                error=failed_profile_error,
            )
    finally:
        db.close()

    if should_defer_mt5_startup_checks(settings):
        startup_runtime_ready = False
        deferred_state = build_deferred_mt5_startup_state()
        if app.state.bot_state.get("startup_self_check") is None:
            app.state.bot_state["startup_self_check"] = deferred_state
        app.state.bot_state["startup_reconciliation"] = deferred_state.copy()
        if not app.state.bot_state.get("last_error"):
            app.state.bot_state["last_error"] = deferred_state["error"]
        _log_event("startup_mt5_checks_deferred", execution_provider=settings.effective_execution_provider)
    else:
        if settings.live_trading_enabled and settings.startup_self_check_required:
            db = SessionLocal()
            try:
                app.state.bot_state["startup_self_check"] = await app.state.trading_service.run_startup_self_check(db)
            except Exception as exc:
                startup_runtime_ready = False
                detail = str(exc).strip() or exc.__class__.__name__
                app.state.bot_state["startup_self_check"] = {
                    "status": "failed",
                    "reason": "invalid_mt5_runtime_credentials",
                    "error": detail,
                }
                app.state.bot_state["last_error"] = detail
                _log_event("startup_self_check_failed", error=detail)
            finally:
                db.close()

        if settings.live_trading_enabled:
            db = SessionLocal()
            try:
                app.state.bot_state["startup_reconciliation"] = await app.state.trading_service.hydrate_authoritative_state(db)
            except Exception as exc:
                startup_runtime_ready = False
                detail = str(exc).strip() or exc.__class__.__name__
                app.state.bot_state["startup_reconciliation"] = {
                    "status": "failed",
                    "reason": "invalid_mt5_runtime_credentials",
                    "error": detail,
                }
                app.state.bot_state["reconciliation_last_error"] = detail
                app.state.bot_state["last_error"] = detail
                _log_event("startup_reconciliation_failed", error=detail)
            finally:
                db.close()

    startup_review_pending = False
    if startup_runtime_ready and should_require_startup_execution_review(settings):
        db = SessionLocal()
        try:
            review = await app.state.trading_service.build_startup_execution_review(db)
            app.state.bot_state["startup_execution_review"] = review
            startup_review_pending = bool(review.get("requires_confirmation"))
        finally:
            db.close()

    if startup_runtime_ready and not startup_review_pending:
        _start_runtime_automation(app)

    try:
        yield
    finally:
        bot_task = app.state.bot_task
        if bot_task:
            bot_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot_task
        reconcile_task = app.state.reconcile_task
        if reconcile_task:
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconcile_task
        hedge_monitor_task = app.state.hedge_monitor_task
        if hedge_monitor_task:
            hedge_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await hedge_monitor_task


async def _background_trading_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    observability: ObservabilityStore = app.state.observability
    previous_cycle_started = monotonic()
    while True:
        cycle_started = monotonic()
        if cycle_started - previous_cycle_started > (settings.poll_interval_seconds * 1.5):
            observability.increment("bot_missed_cycles_total")
            _log_event("missed_cycle_detected", elapsed_seconds=cycle_started - previous_cycle_started)
        previous_cycle_started = cycle_started

        db = SessionLocal()
        try:
            result = await app.state.trading_service.run_auto_cycle(db)
            app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["last_result"] = result
            app.state.bot_state["last_error"] = None
            observability.increment("bot_cycles_total")
            observability.touch_cycle()
            _log_event("trading_cycle_ok", mode=result.get("mode", "single"))

            if isinstance(result, dict) and result.get("execution_block"):
                block = str(result.get("execution_block", "")).lower()
                if "spread" in block:
                    observability.increment("bot_spread_blowouts_total")
                if "drawdown" in block:
                    observability.increment("bot_drawdown_blocks_total")
                if "rejected" in block:
                    observability.increment("bot_order_rejects_total")

            notifier: TelegramNotifier | None = getattr(app.state, "telegram_notifier", None)
            if notifier and settings.telegram_enabled:
                await notifier.send_cycle_summary(result)
        except Exception as exc:  # pragma: no cover - safety net for the loop
            app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["last_error"] = str(exc)
            observability.increment("bot_cycle_failures_total")
            _log_event("trading_cycle_failed", error=str(exc))
        finally:
            db.close()
        await asyncio.sleep(settings.poll_interval_seconds)


async def _background_reconciliation_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    observability: ObservabilityStore = app.state.observability
    while True:
        db = SessionLocal()
        try:
            result = await app.state.trading_service.reconcile_broker_state(db)
            app.state.bot_state["reconciliation_last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["reconciliation_last_error"] = None
            app.state.bot_state["reconciliation_last_result"] = result
            observability.increment("bot_reconciliation_runs_total")
            observability.touch_reconciliation()
            _log_event("reconciliation_ok", provider=result.get("provider"), status=result.get("status"))
        except Exception as exc:  # pragma: no cover - safety net for the loop
            app.state.bot_state["reconciliation_last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["reconciliation_last_error"] = str(exc)
            observability.increment("bot_reconciliation_failures_total")
            _log_event("reconciliation_failed", error=str(exc))
        finally:
            db.close()
        await asyncio.sleep(max(15, settings.reconciliation_interval_seconds))


async def _cancel_runtime_task(task: Any) -> bool:
    if task is None:
        return False
    cancel = getattr(task, "cancel", None)
    if callable(cancel):
        cancel()
    if isinstance(task, asyncio.Task):
        with suppress(asyncio.CancelledError):
            await task
    return True


async def _background_hedge_monitor_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    observability: ObservabilityStore = app.state.observability
    interval_seconds = max(5, int(settings.atr_recovery_hedge_monitor_interval_seconds))
    while True:
        db = SessionLocal()
        try:
            result = await app.state.trading_service.run_cycle_hedge_monitor(db)
            app.state.bot_state["hedge_monitor_last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["hedge_monitor_last_error"] = None
            app.state.bot_state["hedge_monitor_last_result"] = result
            block_stats: dict[str, int] = {
                "cooldown_active": 0,
                "max_hedges_per_cycle": 0,
                "min_rehedge_delta_not_met": 0,
            }
            for item in result.get("processed", []):
                block = str(item.get("hedge_block") or "").strip()
                if not block:
                    continue
                block_stats[block] = int(block_stats.get(block, 0)) + 1

            if block_stats.get("cooldown_active", 0) > 0:
                observability.increment("bot_hedge_cooldown_blocks_total", float(block_stats["cooldown_active"]))
            if block_stats.get("max_hedges_per_cycle", 0) > 0:
                observability.increment("bot_hedge_max_attempt_blocks_total", float(block_stats["max_hedges_per_cycle"]))
            if block_stats.get("min_rehedge_delta_not_met", 0) > 0:
                observability.increment("bot_hedge_min_delta_blocks_total", float(block_stats["min_rehedge_delta_not_met"]))
            app.state.bot_state["hedge_monitor_block_stats"] = block_stats
            observability.increment("bot_hedge_monitor_runs_total")
            _log_event("hedge_monitor_ok", cycles_checked=result.get("cycles_checked", 0))
        except Exception as exc:  # pragma: no cover - safety net for the loop
            app.state.bot_state["hedge_monitor_last_run"] = datetime.now(UTC).isoformat()
            app.state.bot_state["hedge_monitor_last_error"] = str(exc)
            observability.increment("bot_hedge_monitor_failures_total")
            _log_event("hedge_monitor_failed", error=str(exc))
        finally:
            db.close()
        await asyncio.sleep(interval_seconds)


def get_trading_service(request: Request) -> TradingService:
    return request.app.state.trading_service


def get_mt5_profile_service(request: Request) -> Mt5ProfileService:
    return request.app.state.mt5_profile_service


def get_mt5_worker_service(request: Request) -> Mt5WorkerService:
    return request.app.state.mt5_worker_service


def get_mobile_auth_service(request: Request) -> MobileAuthService:
    return request.app.state.mobile_auth_service


def require_profile_owner(x_owner_id: str | None = Header(default=None, alias="X-Owner-Id")) -> str:
    owner_id = (x_owner_id or "").strip()
    if not owner_id:
        raise HTTPException(status_code=400, detail="X-Owner-Id header is required for MT5 profile operations.")
    return owner_id


def _extract_session_token(
    x_session_token: str | None = Header(default=None, alias="X-Session-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    direct_token = (x_session_token or "").strip()
    if direct_token:
        return direct_token
    auth_header = (authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    raise HTTPException(status_code=401, detail="Session token is required.")


def resolve_profile_owner(
    x_owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    owner_id: str | None = Query(default=None),
) -> str:
    header_owner = (x_owner_id or "").strip()
    query_owner = (owner_id or "").strip()
    return header_owner or query_owner or "local"


def _coerce_owner_scoped_payload[T: Any](payload: T, owner_id: str) -> T:
    payload_owner = (getattr(payload, "owner_id", None) or "").strip()
    if payload_owner and payload_owner != owner_id:
        raise HTTPException(status_code=400, detail="Payload owner_id must match X-Owner-Id.")
    return payload.model_copy(update={"owner_id": owner_id})


async def _maybe_auto_assign_worker(
    request: Request,
    db: Session,
    owner_id: str,
    profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return None
    profile_id = profile.get("id")
    if not profile_id:
        return None
    if _owner_uses_process_runtime(request.app.state.settings, owner_id):
        return None
    worker_service: Mt5WorkerService = request.app.state.mt5_worker_service
    try:
        return worker_service.auto_assign_owner_worker(db, owner_id=owner_id, profile_id=int(profile_id))
    except ValueError:
        return None


def _build_mobile_profile_contract(
    request: Request,
    db: Session,
    mt5_profile_service: Mt5ProfileService,
    owner_id: str,
) -> dict[str, Any]:
    items = mt5_profile_service.list_profiles(db, owner_id=owner_id)
    active_profile = next((item for item in items if item.get("is_active")), None)
    settings: Settings = request.app.state.settings
    runtime_terminal_path = settings.mt5_terminal_path
    runtime_symbol = settings.mt5_symbol
    runtime_symbols = settings.effective_mt5_symbols
    runtime_volume_lots = settings.mt5_volume_lots
    runtime_has_credentials = settings.has_mt5_credentials

    if active_profile:
        runtime_terminal_path = active_profile.get("terminal_path") or settings.mt5_terminal_path
        runtime_symbols = active_profile.get("symbols") or settings.effective_mt5_symbols
        runtime_symbol = active_profile.get("primary_symbol") or (runtime_symbols[0] if runtime_symbols else settings.mt5_symbol)
        runtime_volume_lots = float(active_profile.get("volume_lots") or settings.mt5_volume_lots)
        runtime_has_credentials = bool(active_profile.get("password_configured") and active_profile.get("login") and active_profile.get("server"))

    return {
        "owner_id": owner_id,
        "runtime_source": "active_profile" if active_profile else "env",
        "encryption_ready": mt5_profile_service.encryption_ready,
        "active_profile": active_profile,
        "profiles": items,
        "mt5_runtime": {
            "terminal_path": runtime_terminal_path,
            "symbol": runtime_symbol,
            "symbols": runtime_symbols,
            "volume_lots": runtime_volume_lots,
            "has_credentials": runtime_has_credentials,
        },
    }


def require_mobile_session(
    token: str = Depends(_extract_session_token),
    db: Session = Depends(get_db),
    mobile_auth_service: MobileAuthService = Depends(get_mobile_auth_service),
) -> dict[str, Any]:
    try:
        return mobile_auth_service.get_session_context(db, token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    _configure_http_middleware(app, settings)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request,
        _: None = Depends(enforce_operator_access),
        owner_id: str = Depends(resolve_profile_owner),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
        mt5_worker_service: Mt5WorkerService = Depends(get_mt5_worker_service),
    ) -> HTMLResponse:
        snapshot = None
        snapshot_error = None
        symbol_indications: list[dict[str, Any]] = []
        strategy_catalog = trading_service.get_strategy_catalog() if hasattr(trading_service, "get_strategy_catalog") else []
        settings: Settings = request.app.state.settings
        is_runtime_owner = _owner_uses_process_runtime(settings, owner_id)
        owner_contract = _build_mobile_profile_contract(request, db, mt5_profile_service, owner_id)
        owner_active_profile = owner_contract["active_profile"]
        if has_deferred_mt5_startup(request):
            snapshot_error = str(request.app.state.bot_state.get("last_error") or build_deferred_mt5_startup_state()["error"])
        else:
            try:
                if owner_active_profile and not is_runtime_owner:
                    preview_settings, _ = mt5_profile_service.build_preview_settings_from_profile(
                        db,
                        int(owner_active_profile["id"]),
                        owner_id=owner_id,
                    )
                    snapshot = await trading_service.build_snapshot_with_runtime_settings(db, preview_settings)
                elif not is_runtime_owner and owner_active_profile is None:
                    snapshot_error = f"No active MT5 profile is selected for owner '{owner_id}'."
                else:
                    snapshot = await trading_service.build_snapshot(db)
            except Exception as exc:
                snapshot_error = build_snapshot_error_message(exc)
                request.app.state.bot_state["last_error"] = snapshot_error

        dashboard_mt5_symbols = owner_contract["mt5_runtime"]["symbols"] or settings.effective_mt5_symbols
        dashboard_mt5_primary_symbol = owner_contract["mt5_runtime"]["symbol"] or settings.mt5_symbol
        mt5_position_status = build_mt5_position_fallback(settings)
        mt5_symbol_readiness: list[dict[str, Any]] = []
        mt5_live_order_toggles = load_mt5_live_order_toggle_states(settings, trading_service) if is_runtime_owner else []
        mt5_atr_recovery_toggles = load_mt5_atr_recovery_toggle_states(settings, trading_service) if is_runtime_owner else []
        mt5_trade_cycles = trading_service.list_mt5_trade_cycles(db, owner_id=owner_id) if hasattr(trading_service, "list_mt5_trade_cycles") else []
        startup_execution_review = request.app.state.bot_state.get("startup_execution_review") or build_empty_startup_execution_review()
        if snapshot_error is None and is_runtime_owner:
            mt5_position_status = await load_mt5_position_status(settings, trading_service)
            mt5_symbol_readiness = await load_mt5_symbol_readiness(settings, trading_service)

        if snapshot_error is None and is_runtime_owner and settings.effective_market_data_provider == "mt5" and hasattr(trading_service, "build_snapshot_for_symbol"):
            for symbol in dashboard_mt5_symbols:
                try:
                    symbol_snapshot = await trading_service.build_snapshot_for_symbol(db, symbol)
                    symbol_indications.append(
                        {
                            "symbol": symbol,
                            "latest_price": symbol_snapshot.latest_price,
                            "rsi": symbol_snapshot.rsi,
                            "suggested_action": symbol_snapshot.suggested_action,
                            "strategy_vote_action": symbol_snapshot.strategy_vote_action,
                            "position_quantity": symbol_snapshot.position_quantity,
                            "confidence": symbol_snapshot.confidence,
                            "regime": symbol_snapshot.regime,
                            "stop_loss": symbol_snapshot.stop_loss,
                            "take_profit": symbol_snapshot.take_profit,
                            "pro_analysis_gate_blocked": symbol_snapshot.pro_analysis_gate_blocked,
                            "pro_analysis_gate_reasons": symbol_snapshot.pro_analysis_gate_reasons,
                            "pro_analysis_vote_action": symbol_snapshot.pro_analysis_vote_action,
                            "pro_analysis_final_action": symbol_snapshot.pro_analysis_final_action,
                            "pro_analysis_session_name": symbol_snapshot.pro_analysis_session_name,
                            "pro_analysis_rr": symbol_snapshot.pro_analysis_rr,
                            "atr_pct": symbol_snapshot.atr_pct,
                            "atr_value": symbol_snapshot.atr_value,
                            "atr_recovery_active": symbol_snapshot.atr_recovery_active,
                            "atr_recovery_symbol_enabled": symbol_snapshot.atr_recovery_symbol_enabled,
                            "atr_recovery_profile": symbol_snapshot.atr_recovery_profile,
                            "strategies": symbol_snapshot.strategy_names,
                            "strategy_details": symbol_snapshot.strategy_details,
                            "error": None,
                        }
                    )
                except Exception as exc:
                    symbol_indications.append(
                        {
                            "symbol": symbol,
                            "latest_price": None,
                            "rsi": None,
                            "suggested_action": "N/A",
                            "position_quantity": None,
                            "confidence": None,
                            "regime": None,
                            "stop_loss": None,
                            "take_profit": None,
                            "strategies": [],
                            "strategy_details": [],
                            "error": str(exc),
                        }
                    )

        trades = trading_service.list_recent_trades(db) if is_runtime_owner else []
        mt5_profiles = mt5_profile_service.list_profiles(db, owner_id=owner_id)
        mt5_workers = mt5_worker_service.list_workers(db, owner_id=owner_id)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "settings": settings,
                "bot_state": request.app.state.bot_state,
                "snapshot": snapshot,
                "snapshot_error": snapshot_error,
                "symbol_indications": symbol_indications,
                "strategy_catalog": strategy_catalog,
                "trades": trades,
                "mt5_active_positions": mt5_position_status["active_positions"],
                "mt5_active_positions_cap": mt5_position_status["cap"],
                "mt5_symbol_readiness": mt5_symbol_readiness,
                "mt5_live_order_toggles": mt5_live_order_toggles,
                "mt5_atr_recovery_toggles": mt5_atr_recovery_toggles,
                "mt5_trade_cycles": mt5_trade_cycles,
                "mt5_profiles": mt5_profiles,
                "mt5_workers": mt5_workers,
                "mt5_profile_encryption_ready": mt5_profile_service.encryption_ready,
                "dashboard_owner_id": owner_id,
                "dashboard_is_runtime_owner": is_runtime_owner,
                "dashboard_mt5_symbols": dashboard_mt5_symbols,
                "dashboard_mt5_primary_symbol": dashboard_mt5_primary_symbol,
                "startup_execution_review": startup_execution_review if is_runtime_owner else build_empty_startup_execution_review(),
            },
        )

    @app.post("/api/bot/run")
    async def run_bot(
        request: Request,
        x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        try:
            if hasattr(trading_service, "run_auto_cycle"):
                result = await trading_service.run_auto_cycle(db, request_id=x_idempotency_key)
            else:
                result = await trading_service.run_cycle(db, request_id=x_idempotency_key)

            notifier: TelegramNotifier | None = getattr(request.app.state, "telegram_notifier", None)
            settings: Settings = request.app.state.settings
            if notifier and settings.telegram_enabled:
                await notifier.send_cycle_summary(result)
        except Exception as exc:
            request.app.state.bot_state["last_error"] = str(exc)
            request.app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        request.app.state.bot_state["last_result"] = result
        request.app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
        request.app.state.bot_state["last_error"] = None
        return result

    @app.post("/api/bot/stop")
    async def stop_bot(
        request: Request,
        _: None = Depends(enforce_write_access),
    ) -> dict[str, Any]:
        cancelled: list[str] = []

        if await _cancel_runtime_task(getattr(request.app.state, "bot_task", None)):
            cancelled.append("bot_task")
        request.app.state.bot_task = None

        if await _cancel_runtime_task(getattr(request.app.state, "reconcile_task", None)):
            cancelled.append("reconcile_task")
        request.app.state.reconcile_task = None

        if await _cancel_runtime_task(getattr(request.app.state, "hedge_monitor_task", None)):
            cancelled.append("hedge_monitor_task")
        request.app.state.hedge_monitor_task = None

        settings: Settings = request.app.state.settings
        settings.auto_trading_enabled = False
        settings.live_trading_armed = False

        request.app.state.bot_state["auto_trading_enabled"] = False
        request.app.state.bot_state["live_trading_armed"] = False
        request.app.state.bot_state["mode"] = "LIVE_DISARMED" if settings.can_place_live_orders else "DRY_RUN"
        request.app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
        request.app.state.bot_state["last_error"] = None
        request.app.state.bot_state["last_result"] = {
            "status": "stopped",
            "cancelled_tasks": cancelled,
        }

        return {
            "status": "stopped",
            "cancelled_tasks": cancelled,
            "bot_state": request.app.state.bot_state,
        }

    @app.post("/api/bot/start")
    async def start_bot(
        request: Request,
        _: None = Depends(enforce_write_access),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        startup_review = request.app.state.bot_state.get("startup_execution_review") or build_empty_startup_execution_review()

        if startup_review.get("requires_confirmation") and startup_review.get("status") not in {"approved", "cancelled", "not_required"}:
            raise HTTPException(
                status_code=409,
                detail="Startup execution review must be approved or cancelled before bot automation can be started.",
            )

        settings.auto_trading_enabled = True
        if settings.can_place_live_orders:
            settings.live_trading_armed = True

        request.app.state.bot_state["auto_trading_enabled"] = True
        request.app.state.bot_state["live_trading_armed"] = bool(settings.live_trading_enabled)
        request.app.state.bot_state["mode"] = "LIVE_ARMED" if settings.live_trading_enabled else "DRY_RUN"
        request.app.state.bot_state["last_error"] = None

        started_tasks = _start_runtime_automation(request.app)

        request.app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
        request.app.state.bot_state["last_result"] = {
            "status": "started",
            "started_tasks": started_tasks,
        }

        return {
            "status": "started",
            "started_tasks": started_tasks,
            "bot_state": request.app.state.bot_state,
        }

    @app.post("/api/bot/startup-review/approve")
    async def approve_startup_execution_review(
        request: Request,
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        review = request.app.state.bot_state.get("startup_execution_review") or build_empty_startup_execution_review()
        review["status"] = "approved"
        review["approved_at"] = datetime.now(UTC).isoformat()
        request.app.state.bot_state["startup_execution_review"] = review

        result: dict[str, Any] | None = None
        if hasattr(trading_service, "run_auto_cycle"):
            result = await trading_service.run_auto_cycle(db, request_id=f"startup-review-approve-{int(datetime.now(UTC).timestamp())}")
            request.app.state.bot_state["last_result"] = result
            request.app.state.bot_state["last_run"] = datetime.now(UTC).isoformat()
            request.app.state.bot_state["last_error"] = None

        started_tasks = _start_runtime_automation(request.app)
        return {
            "status": "approved",
            "started_tasks": started_tasks,
            "result": result,
            "startup_execution_review": review,
        }

    @app.post("/api/bot/startup-review/cancel")
    async def cancel_startup_execution_review(
        request: Request,
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        cancelled_jobs: list[dict[str, Any]] = []
        if hasattr(trading_service, "cancel_startup_pending_execution_jobs"):
            cancelled_jobs = trading_service.cancel_startup_pending_execution_jobs(db)

        review = request.app.state.bot_state.get("startup_execution_review") or build_empty_startup_execution_review()
        review["status"] = "cancelled"
        review["cancelled_at"] = datetime.now(UTC).isoformat()
        review["cancelled_job_count"] = len(cancelled_jobs)
        request.app.state.bot_state["startup_execution_review"] = review

        return {
            "status": "cancelled",
            "cancelled_jobs": cancelled_jobs,
            "startup_execution_review": review,
        }

    @app.post("/api/analysis/pro")
    async def run_professional_analysis(
        payload: ProAnalysisRequest,
        request: Request,
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        service = ProfessionalAnalysisService(settings, trading_service.mt5, trading_service.strategy_engine)
        try:
            return await service.generate_report(
                symbols=payload.symbols,
                account_size=payload.account_size,
                risk_tolerance=payload.risk_tolerance,
                trading_style=payload.trading_style,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/analysis/pro/execute")
    async def execute_professional_analysis(
        payload: ProAnalysisExecuteRequest,
        request: Request,
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        try:
            return await trading_service.run_professional_analysis_execution(
                db,
                symbol=payload.symbol,
                account_size=payload.account_size,
                risk_tolerance=payload.risk_tolerance,
                trading_style=payload.trading_style,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/status")
    async def get_status(
        request: Request,
        _: None = Depends(enforce_operator_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        mt5_position_status = build_mt5_position_fallback(settings)
        mt5_symbol_readiness: list[dict[str, Any]] = []
        mt5_live_order_toggles = load_mt5_live_order_toggle_states(settings, trading_service)
        mt5_atr_recovery_toggles = load_mt5_atr_recovery_toggle_states(settings, trading_service)
        mt5_trade_cycles = trading_service.list_mt5_trade_cycles(db) if hasattr(trading_service, "list_mt5_trade_cycles") else []
        if has_deferred_mt5_startup(request):
            return {
                "bot_state": request.app.state.bot_state,
                "snapshot": None,
                "snapshot_error": str(request.app.state.bot_state.get("last_error") or build_deferred_mt5_startup_state()["error"]),
                "recent_trades": [trade.id for trade in trading_service.list_recent_trades(db, limit=5)],
                "mt5_active_positions": mt5_position_status["active_positions"],
                "mt5_active_positions_cap": mt5_position_status["cap"],
                "mt5_symbol_readiness": mt5_symbol_readiness,
                "mt5_live_order_toggles": mt5_live_order_toggles,
                "mt5_atr_recovery_toggles": mt5_atr_recovery_toggles,
                "mt5_trade_cycles": mt5_trade_cycles,
            }
        try:
            snapshot = await trading_service.build_snapshot(db)
        except Exception as exc:
            observability: ObservabilityStore = request.app.state.observability
            observability.increment("bot_stale_market_data_total")
            return {
                "bot_state": request.app.state.bot_state,
                "snapshot": None,
                "snapshot_error": build_snapshot_error_message(exc),
                "recent_trades": [trade.id for trade in trading_service.list_recent_trades(db, limit=5)],
                "mt5_active_positions": mt5_position_status["active_positions"],
                "mt5_active_positions_cap": mt5_position_status["cap"],
                "mt5_symbol_readiness": mt5_symbol_readiness,
                "mt5_live_order_toggles": mt5_live_order_toggles,
                "mt5_atr_recovery_toggles": mt5_atr_recovery_toggles,
                "mt5_trade_cycles": mt5_trade_cycles,
            }
        mt5_position_status = await load_mt5_position_status(settings, trading_service)
        mt5_symbol_readiness = await load_mt5_symbol_readiness(settings, trading_service)
        return {
            "bot_state": request.app.state.bot_state,
            "snapshot": {
                "symbol": snapshot.symbol,
                "signal_symbol": snapshot.signal_symbol,
                "execution_symbol": snapshot.execution_symbol,
                "market_data_symbol": snapshot.market_data_symbol,
                "market_data_provider": snapshot.market_data_provider,
                "latest_price": snapshot.latest_price,
                "rsi": snapshot.rsi,
                "position_quantity": snapshot.position_quantity,
                "suggested_action": snapshot.suggested_action,
                "strategy_vote_action": snapshot.strategy_vote_action,
                "confidence": snapshot.confidence,
                "stop_loss": snapshot.stop_loss,
                "take_profit": snapshot.take_profit,
                "regime": snapshot.regime,
                "strategies": snapshot.strategy_names,
                "pro_analysis_gate_blocked": snapshot.pro_analysis_gate_blocked,
                "pro_analysis_gate_reasons": snapshot.pro_analysis_gate_reasons,
                "pro_analysis_vote_action": snapshot.pro_analysis_vote_action,
                "pro_analysis_final_action": snapshot.pro_analysis_final_action,
                "pro_analysis_session_name": snapshot.pro_analysis_session_name,
                "pro_analysis_session_allowed": snapshot.pro_analysis_session_allowed,
                "pro_analysis_quality_gate_passed": snapshot.pro_analysis_quality_gate_passed,
                "pro_analysis_rr": snapshot.pro_analysis_rr,
                "atr_pct": snapshot.atr_pct,
                "atr_value": snapshot.atr_value,
                "atr_recovery_active": snapshot.atr_recovery_active,
                "atr_recovery_symbol_enabled": snapshot.atr_recovery_symbol_enabled,
                "atr_recovery_profile": snapshot.atr_recovery_profile,
            },
            "strategy_configuration": trading_service.get_strategy_catalog() if hasattr(trading_service, "get_strategy_catalog") else [],
            "recent_trades": [trade.id for trade in trading_service.list_recent_trades(db, limit=5)],
            "mt5_active_positions": mt5_position_status["active_positions"],
            "mt5_active_positions_cap": mt5_position_status["cap"],
            "mt5_symbol_readiness": mt5_symbol_readiness,
            "mt5_live_order_toggles": mt5_live_order_toggles,
            "mt5_atr_recovery_toggles": mt5_atr_recovery_toggles,
            "mt5_trade_cycles": mt5_trade_cycles,
        }

    @app.post("/api/settings/mt5-live-order-symbol")
    async def set_mt5_live_order_symbol(
        request: Request,
        _: None = Depends(enforce_write_access),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        payload = await request.json()
        symbol = str(payload.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="'symbol' is required.")

        enabled_raw = payload.get("enabled")
        if not isinstance(enabled_raw, bool):
            raise HTTPException(status_code=400, detail="'enabled' must be boolean.")

        try:
            updated = trading_service.set_mt5_live_order_symbol_enabled(symbol, enabled_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "updated": updated,
            "items": trading_service.get_mt5_live_order_toggle_states(),
        }

    @app.post("/api/settings/mt5-atr-recovery-symbol")
    async def set_mt5_atr_recovery_symbol(
        request: Request,
        _: None = Depends(enforce_write_access),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        payload = await request.json()
        symbol = str(payload.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="'symbol' is required.")

        enabled_raw = payload.get("enabled")
        if not isinstance(enabled_raw, bool):
            raise HTTPException(status_code=400, detail="'enabled' must be boolean.")

        try:
            updated = trading_service.set_mt5_atr_recovery_symbol_enabled(symbol, enabled_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "updated": updated,
            "items": trading_service.get_mt5_atr_recovery_toggle_states(),
        }

    @app.post("/api/mt5/hedge-monitor")
    async def run_mt5_hedge_monitor(
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        return await trading_service.run_cycle_hedge_monitor(db)

    @app.get("/api/mt5/profiles")
    async def list_mt5_profiles(
        _: None = Depends(enforce_operator_access),
        owner_id: str = Depends(resolve_profile_owner),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        items = mt5_profile_service.list_profiles(db, owner_id=owner_id)
        return {
            "owner_id": owner_id,
            "encryption_ready": mt5_profile_service.encryption_ready,
            "active_profile": next((item for item in items if item.get("is_active")), None),
            "items": items,
        }

    @app.get("/api/mt5/workers")
    async def list_mt5_workers(
        _: None = Depends(enforce_operator_access),
        owner_id: str = Depends(resolve_profile_owner),
        db: Session = Depends(get_db),
        mt5_worker_service: Mt5WorkerService = Depends(get_mt5_worker_service),
    ) -> dict[str, Any]:
        return {
            "owner_id": owner_id,
            "items": mt5_worker_service.list_workers(db, owner_id=owner_id),
        }

    @app.post("/api/mt5/workers/provision")
    async def provision_mt5_worker(
        payload: Mt5WorkerProvisionRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        mt5_worker_service: Mt5WorkerService = Depends(get_mt5_worker_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            item = mt5_worker_service.provision_worker(
                db,
                worker_key=payload.worker_key,
                owner_id=payload.owner_id,
                profile_id=payload.profile_id,
                label=payload.label,
                terminal_path=payload.terminal_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "owner_id": owner_id,
            "item": item,
            "items": mt5_worker_service.list_workers(db, owner_id=owner_id),
        }

    @app.post("/api/mt5/workers/{worker_key}/assign")
    async def assign_mt5_worker(
        worker_key: str,
        payload: Mt5WorkerAssignRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        mt5_worker_service: Mt5WorkerService = Depends(get_mt5_worker_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            item = mt5_worker_service.assign_worker(
                db,
                worker_key=worker_key,
                owner_id=payload.owner_id,
                profile_id=payload.profile_id,
                label=payload.label,
                terminal_path=payload.terminal_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "owner_id": owner_id,
            "item": item,
            "items": mt5_worker_service.list_workers(db, owner_id=owner_id),
        }

    @app.post("/api/mt5/profiles")
    async def create_mt5_profile(
        request: Request,
        payload: Mt5ProfileCreateRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            should_apply_runtime = payload.set_active and _owner_uses_process_runtime(request.app.state.settings, payload.owner_id)
            create_payload = payload.model_copy(update={"set_active": False}) if should_apply_runtime else payload
            profile = mt5_profile_service.create_profile(db, create_payload)
            active_profile = None
            assigned_worker = None
            activation_snapshot = None
            activation_snapshot_error = None
            if should_apply_runtime:
                active_profile = await mt5_profile_service.activate_runtime_profile(
                    db,
                    profile["id"],
                    runtime_settings=request.app.state.settings,
                    trading_service=request.app.state.trading_service,
                    owner_id=payload.owner_id,
                )
                profile = active_profile
                request.app.state.active_mt5_profile = active_profile
                mark_mt5_runtime_ready(request, active_profile if isinstance(active_profile, dict) else None)
                try:
                    activation_snapshot = await build_runtime_activation_snapshot(db, trading_service, request.app.state.settings)
                except Exception as exc:
                    activation_snapshot_error = str(exc)
            assigned_worker = await _maybe_auto_assign_worker(request, db, owner_id, profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "item": profile,
            "items": mt5_profile_service.list_profiles(db, owner_id=owner_id),
            "encryption_ready": mt5_profile_service.encryption_ready,
            "active_profile": active_profile,
            "assigned_worker": assigned_worker,
            "snapshot": activation_snapshot,
            "snapshot_error": activation_snapshot_error,
        }

    @app.post("/api/mt5/profiles/test")
    async def test_mt5_profile(
        payload: Mt5ProfileCreateRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        payload = _coerce_owner_scoped_payload(payload, owner_id)
        return await mt5_profile_service.test_connection(payload)

    @app.post("/api/mt5/profiles/preview")
    async def preview_mt5_profile_snapshot(
        payload: Mt5ProfileCreateRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            preview_settings = mt5_profile_service.build_preview_settings_from_payload(payload)
            snapshot = await trading_service.build_snapshot_with_runtime_settings(
                db,
                preview_settings,
                market_data_symbol=preview_settings.mt5_symbol,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "owner_id": owner_id,
            "preview_profile": {
                "label": payload.label,
                "server": payload.server,
                "primary_symbol": preview_settings.mt5_symbol,
                "symbols": preview_settings.effective_mt5_symbols,
            },
            "snapshot": serialize_snapshot(snapshot),
        }

    @app.post("/api/mt5/profiles/{profile_id}/test")
    async def test_saved_mt5_profile(
        profile_id: int,
        payload: OwnerScopedRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            return await mt5_profile_service.test_saved_profile(db, profile_id, owner_id=payload.owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/mt5/profiles/{profile_id}/preview")
    async def preview_saved_mt5_profile(
        profile_id: int,
        payload: OwnerScopedRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            preview_settings, profile = mt5_profile_service.build_preview_settings_from_profile(db, profile_id, owner_id=payload.owner_id)
            snapshot = await trading_service.build_snapshot_with_runtime_settings(
                db,
                preview_settings,
                market_data_symbol=preview_settings.mt5_symbol,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "owner_id": owner_id,
            "profile": profile,
            "snapshot": serialize_snapshot(snapshot),
        }

    @app.post("/api/mt5/profiles/{profile_id}/activate")
    async def activate_mt5_profile(
        request: Request,
        profile_id: int,
        payload: Mt5ProfileActivateRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            should_apply_runtime = _owner_uses_process_runtime(request.app.state.settings, payload.owner_id)
            assigned_worker = None
            activation_snapshot = None
            activation_snapshot_error = None
            if should_apply_runtime:
                profile = await mt5_profile_service.activate_runtime_profile(
                    db,
                    profile_id,
                    runtime_settings=request.app.state.settings,
                    trading_service=request.app.state.trading_service,
                    owner_id=payload.owner_id,
                )
            else:
                profile = mt5_profile_service.activate_profile(db, profile_id, owner_id=payload.owner_id)
            active_profile = None
            if should_apply_runtime:
                active_profile = profile
                request.app.state.active_mt5_profile = active_profile
                mark_mt5_runtime_ready(request, active_profile if isinstance(active_profile, dict) else None)
                try:
                    activation_snapshot = await build_runtime_activation_snapshot(db, trading_service, request.app.state.settings)
                except Exception as exc:
                    activation_snapshot_error = str(exc)
            assigned_worker = await _maybe_auto_assign_worker(request, db, owner_id, profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "item": profile,
            "items": mt5_profile_service.list_profiles(db, owner_id=owner_id),
            "encryption_ready": mt5_profile_service.encryption_ready,
            "active_profile": active_profile,
            "assigned_worker": assigned_worker,
            "snapshot": activation_snapshot,
            "snapshot_error": activation_snapshot_error,
        }

    @app.delete("/api/mt5/profiles/{profile_id}")
    async def delete_mt5_profile(
        profile_id: int,
        payload: OwnerScopedRequest,
        _: None = Depends(enforce_write_access),
        owner_id: str = Depends(require_profile_owner),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        try:
            payload = _coerce_owner_scoped_payload(payload, owner_id)
            mt5_profile_service.delete_profile(db, profile_id, owner_id=payload.owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        items = mt5_profile_service.list_profiles(db, owner_id=owner_id)
        return {
            "deleted_id": profile_id,
            "items": items,
            "active_profile": next((item for item in items if item.get("is_active")), None),
            "encryption_ready": mt5_profile_service.encryption_ready,
        }

    @app.get("/api/mobile/bootstrap")
    async def mobile_bootstrap(
        request: Request,
        session_context: dict[str, Any] = Depends(require_mobile_session),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        return {
            "auth": session_context,
            **_build_mobile_profile_contract(request, db, mt5_profile_service, session_context["owner_id"]),
        }

    @app.get("/api/mobile/mt5/profiles")
    async def mobile_list_mt5_profiles(
        request: Request,
        session_context: dict[str, Any] = Depends(require_mobile_session),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        return {
            "auth": session_context,
            **_build_mobile_profile_contract(request, db, mt5_profile_service, session_context["owner_id"]),
        }

    @app.post("/api/mobile/mt5/profiles")
    async def mobile_create_mt5_profile(
        request: Request,
        payload: Mt5ProfileCreateRequest,
        session_context: dict[str, Any] = Depends(require_mobile_session),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        owner_id = session_context["owner_id"]
        payload = _coerce_owner_scoped_payload(payload, owner_id)
        should_apply_runtime = payload.set_active and _owner_uses_process_runtime(request.app.state.settings, payload.owner_id)
        create_payload = payload.model_copy(update={"set_active": False}) if should_apply_runtime else payload
        profile = mt5_profile_service.create_profile(db, create_payload)
        if should_apply_runtime:
            try:
                request.app.state.active_mt5_profile = await mt5_profile_service.activate_runtime_profile(
                    db,
                    profile["id"],
                    runtime_settings=request.app.state.settings,
                    trading_service=request.app.state.trading_service,
                    owner_id=payload.owner_id,
                )
                mark_mt5_runtime_ready(request, request.app.state.active_mt5_profile if isinstance(request.app.state.active_mt5_profile, dict) else None)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _maybe_auto_assign_worker(request, db, owner_id, profile)
        return {
            "auth": session_context,
            **_build_mobile_profile_contract(request, db, mt5_profile_service, owner_id),
        }

    @app.post("/api/mobile/mt5/profiles/{profile_id}/activate")
    async def mobile_activate_mt5_profile(
        request: Request,
        profile_id: int,
        payload: OwnerScopedRequest,
        session_context: dict[str, Any] = Depends(require_mobile_session),
        db: Session = Depends(get_db),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        owner_id = session_context["owner_id"]
        payload = _coerce_owner_scoped_payload(payload, owner_id)
        try:
            if _owner_uses_process_runtime(request.app.state.settings, payload.owner_id):
                request.app.state.active_mt5_profile = await mt5_profile_service.activate_runtime_profile(
                    db,
                    profile_id,
                    runtime_settings=request.app.state.settings,
                    trading_service=request.app.state.trading_service,
                    owner_id=payload.owner_id,
                )
                mark_mt5_runtime_ready(request, request.app.state.active_mt5_profile if isinstance(request.app.state.active_mt5_profile, dict) else None)
            else:
                mt5_profile_service.activate_profile(db, profile_id, owner_id=payload.owner_id)
            active_profile = mt5_profile_service.get_active_profile(db, owner_id=owner_id)
            if active_profile is not None:
                await _maybe_auto_assign_worker(request, db, owner_id, {"id": active_profile.id})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "auth": session_context,
            **_build_mobile_profile_contract(request, db, mt5_profile_service, owner_id),
        }

    @app.get("/api/mobile/mt5/snapshot")
    async def mobile_mt5_snapshot(
        request: Request,
        market_data_symbol: str | None = None,
        session_context: dict[str, Any] = Depends(require_mobile_session),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
        mt5_profile_service: Mt5ProfileService = Depends(get_mt5_profile_service),
    ) -> dict[str, Any]:
        owner_id = session_context["owner_id"]
        preview_settings = request.app.state.settings.model_copy()
        active_profile = mt5_profile_service.apply_active_profile(
            db,
            runtime_settings=preview_settings,
            owner_id=owner_id,
        )
        symbol_override = (market_data_symbol or "").strip() or None

        try:
            if hasattr(trading_service, "build_snapshot_with_runtime_settings"):
                snapshot = await trading_service.build_snapshot_with_runtime_settings(
                    db,
                    preview_settings,
                    market_data_symbol=symbol_override,
                )
            else:
                preview_trading_service = TradingService(preview_settings)
                snapshot = await (
                    preview_trading_service.build_snapshot_for_symbol(db, symbol_override)
                    if symbol_override
                    else preview_trading_service.build_snapshot(db)
                )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "auth": session_context,
            "runtime_source": "active_profile" if active_profile else "env",
            "active_profile": active_profile,
            "mt5_runtime": {
                "symbol": preview_settings.mt5_symbol,
                "symbols": preview_settings.effective_mt5_symbols,
                "volume_lots": preview_settings.mt5_volume_lots,
                "has_credentials": preview_settings.has_mt5_credentials,
            },
            "snapshot": {
                "symbol": snapshot.symbol,
                "signal_symbol": snapshot.signal_symbol,
                "execution_symbol": snapshot.execution_symbol,
                "market_data_symbol": snapshot.market_data_symbol,
                "market_data_provider": snapshot.market_data_provider,
                "latest_price": snapshot.latest_price,
                "rsi": snapshot.rsi,
                "position_quantity": snapshot.position_quantity,
                "suggested_action": snapshot.suggested_action,
                "strategy_vote_action": snapshot.strategy_vote_action,
                "confidence": snapshot.confidence,
                "stop_loss": snapshot.stop_loss,
                "take_profit": snapshot.take_profit,
                "regime": snapshot.regime,
                "strategies": snapshot.strategy_names,
                "pro_analysis_gate_blocked": snapshot.pro_analysis_gate_blocked,
                "pro_analysis_gate_reasons": snapshot.pro_analysis_gate_reasons,
                "pro_analysis_vote_action": snapshot.pro_analysis_vote_action,
                "pro_analysis_final_action": snapshot.pro_analysis_final_action,
                "pro_analysis_session_name": snapshot.pro_analysis_session_name,
                "pro_analysis_session_allowed": snapshot.pro_analysis_session_allowed,
                "pro_analysis_quality_gate_passed": snapshot.pro_analysis_quality_gate_passed,
                "pro_analysis_rr": snapshot.pro_analysis_rr,
            },
        }

    @app.post("/api/mobile/auth/register")
    async def mobile_register(
        request: Request,
        payload: MobileAuthRegisterRequest,
        db: Session = Depends(get_db),
        mobile_auth_service: MobileAuthService = Depends(get_mobile_auth_service),
    ) -> dict[str, Any]:
        rate_key = _build_auth_rate_limit_key(request, "register", payload.email)
        retry_after = request.app.state.auth_rate_limiter.retry_after_seconds(db, rate_key)
        if retry_after is not None:
            raise HTTPException(status_code=429, detail=f"Too many register attempts. Retry in {retry_after} seconds.")
        try:
            result = mobile_auth_service.register_user(db, payload)
        except ValueError as exc:
            request.app.state.auth_rate_limiter.record_failure(db, rate_key)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        request.app.state.auth_rate_limiter.reset(db, rate_key)
        return result

    @app.post("/api/mobile/auth/login")
    async def mobile_login(
        request: Request,
        payload: MobileAuthLoginRequest,
        db: Session = Depends(get_db),
        mobile_auth_service: MobileAuthService = Depends(get_mobile_auth_service),
    ) -> dict[str, Any]:
        rate_key = _build_auth_rate_limit_key(request, "login", payload.email)
        retry_after = request.app.state.auth_rate_limiter.retry_after_seconds(db, rate_key)
        if retry_after is not None:
            raise HTTPException(status_code=429, detail=f"Too many login attempts. Retry in {retry_after} seconds.")
        try:
            result = mobile_auth_service.login_user(db, payload)
        except ValueError as exc:
            request.app.state.auth_rate_limiter.record_failure(db, rate_key)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        request.app.state.auth_rate_limiter.reset(db, rate_key)
        return result

    @app.get("/api/mobile/auth/session")
    async def mobile_session_status(
        session_context: dict[str, Any] = Depends(require_mobile_session),
    ) -> dict[str, Any]:
        return session_context

    @app.post("/api/mobile/auth/logout")
    async def mobile_logout(
        token: str = Depends(_extract_session_token),
        db: Session = Depends(get_db),
        mobile_auth_service: MobileAuthService = Depends(get_mobile_auth_service),
    ) -> dict[str, Any]:
        try:
            session = mobile_auth_service.revoke_session(db, token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"session": session, "revoked": True}

    @app.get("/api/reconciliation/status")
    async def get_reconciliation_status(request: Request, _: None = Depends(enforce_operator_access)) -> dict[str, Any]:
        return {
            "last_run": request.app.state.bot_state.get("reconciliation_last_run"),
            "last_error": request.app.state.bot_state.get("reconciliation_last_error"),
            "last_result": request.app.state.bot_state.get("reconciliation_last_result"),
        }

    @app.get("/api/journal/executions")
    async def get_execution_journal(
        limit: int = 100,
        symbol: str | None = None,
        reconciliation_status: str | None = None,
        since: str | None = None,
        _: None = Depends(enforce_operator_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        parsed_since: datetime | None = None
        if since:
            try:
                normalized = since.replace("Z", "+00:00")
                parsed_since = datetime.fromisoformat(normalized)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid 'since' timestamp. Use ISO-8601 format.") from exc

        trades = trading_service.list_execution_journal(
            db,
            limit=limit,
            symbol=symbol,
            reconciliation_status=reconciliation_status,
            since=parsed_since,
        )
        return {
            "filters": {
                "limit": max(1, min(limit, 500)),
                "symbol": symbol,
                "reconciliation_status": reconciliation_status.upper() if reconciliation_status else None,
                "since": parsed_since.isoformat() if parsed_since else None,
            },
            "count": len(trades),
            "items": [
                {
                    "id": trade.id,
                    "symbol": trade.symbol,
                    "signal_symbol": trade.signal_symbol or trade.symbol,
                    "execution_symbol": trade.execution_symbol or trade.symbol,
                    "side": trade.side,
                    "quantity": trade.quantity,
                    "price": trade.price,
                    "intended_price": trade.intended_price,
                    "fill_price": trade.fill_price,
                    "slippage_pct": trade.slippage_pct,
                    "fee_amount": trade.fee_amount,
                    "quote_amount": trade.quote_amount,
                    "entry_stop_loss": trade.entry_stop_loss,
                    "entry_take_profit": trade.entry_take_profit,
                    "strategy_weights": trade.strategy_weights,
                    "confidence": trade.confidence,
                    "equity_before": trade.equity_before,
                    "equity_after": trade.equity_after,
                    "realized_pnl": trade.realized_pnl,
                    "realized_pnl_pct": trade.realized_pnl_pct,
                    "outcome": trade.outcome,
                    "broker_position_id": trade.broker_position_id,
                    "reconciliation_status": trade.reconciliation_status,
                    "status": trade.status,
                    "is_dry_run": trade.is_dry_run,
                    "created_at": trade.created_at.isoformat(),
                }
                for trade in trades
            ],
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics(request: Request, _: None = Depends(enforce_operator_access)) -> PlainTextResponse:
        observability: ObservabilityStore = request.app.state.observability
        return PlainTextResponse(content=observability.render_prometheus(), media_type="text/plain; version=0.0.4")

    @app.post("/api/backtest/run")
    async def run_backtest(
        history_limit: int = 1000,
        initial_balance: float = 1000.0,
        trade_amount: float | None = None,
        fee_rate: float | None = None,
        market_data_symbol: str | None = None,
        _: None = Depends(enforce_write_access),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        try:
            return await trading_service.run_backtest(
                history_limit=history_limit,
                initial_balance=initial_balance,
                trade_amount=trade_amount,
                fee_rate=fee_rate,
                market_data_symbol=market_data_symbol,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/backtest/run-mt5-batch")
    async def run_mt5_backtest_batch(
        request: Request,
        history_limit: int = 1000,
        initial_balance: float = 1000.0,
        trade_amount: float | None = None,
        fee_rate: float | None = None,
        symbols: str | None = None,
        _: None = Depends(enforce_write_access),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        target_symbols = parse_symbol_list(symbols) or settings.effective_mt5_symbols

        if not target_symbols:
            raise HTTPException(status_code=400, detail="No MT5 symbols configured.")

        results: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        for symbol in target_symbols:
            try:
                result = await trading_service.run_backtest(
                    history_limit=history_limit,
                    initial_balance=initial_balance,
                    trade_amount=trade_amount,
                    fee_rate=fee_rate,
                    market_data_symbol=symbol,
                )
                results.append(result)
            except Exception as exc:
                errors[symbol] = str(exc)

        return {
            "symbols": target_symbols,
            "results": results,
            "errors": errors,
        }

    @app.get("/api/ml/status")
    async def ml_status(
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        return trading_service.ml_service.status()

    @app.post("/api/ml/train")
    async def ml_train(
        request: MLTrainRequest,
        _: None = Depends(enforce_write_access),
        db: Session = Depends(get_db),
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        trades = trading_service.list_execution_journal(
            db,
            limit=max(100, int(trading_service.settings.ml_training_trade_limit)),
        )
        try:
            return trading_service.ml_service.train_from_trades(
                trades=trades,
                min_samples=max(5, int(request.min_samples)),
                epochs=max(50, int(request.epochs)),
                learning_rate=max(1e-6, float(request.learning_rate)),
                validation_size=float(request.validation_size),
                random_state=int(request.random_state),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/ml/predict")
    async def ml_predict(
        request: MLPredictRequest,
        trading_service: TradingService = Depends(get_trading_service),
    ) -> dict[str, Any]:
        features = request.model_dump()
        probability_up = trading_service.ml_service.predict_up_probability(features)
        action = trading_service.ml_service.action_from_probability(
            probability_up,
            buy_threshold=float(trading_service.settings.ml_buy_probability_threshold),
            sell_threshold=float(trading_service.settings.ml_sell_probability_threshold),
        )
        return {
            "probability_up": probability_up,
            "action": action,
            "model_ready": probability_up is not None,
            "features": features,
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
